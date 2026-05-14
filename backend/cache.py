"""In-memory LRU cache for council pipeline envelopes.

Why this exists (audit findings C1, M4):
    The original code re-runs the full ``2N + 1`` LLM fan-out for every
    user query, including identical queries. Doc 05 §1 measured per-query
    cost ranging from $0.0015 (cheap models) to ~$0.20 (flagship). Even
    a 30 % cache hit rate at 10k users/day is tens of thousands of dollars
    a month avoided.

What's implemented here:
    - Thread-safe LRU cache, in-process, bounded by entry count.
    - Keyed by SHA-256 over normalized query + sorted council models +
      chairman model. Identical config + query => one entry.
    - Only stores envelopes whose chairman returned ``status="ok"``.
      Errors are never cached — re-running may succeed.
    - Exposes hit/miss/store/eviction counters via ``CacheStats`` for
      structured logging and an eventual ``/metrics`` endpoint.

What is NOT implemented here (intentionally — see FUTURE_SCOPE.md):
    - Cross-process / cross-host caching. LRU is in-process only;
      multi-worker uvicorn deploys will have one cache per worker. To
      share across workers, swap the storage backend for SQLite or
      Redis behind the same ``CouncilCache`` interface.
    - Semantic similarity caching (embedding-based). Exact-string
      matches catch FAQ-shaped traffic; semantic catches paraphrases.
      Requires an embedding model and a vector store.
    - TTL-based expiration. Current entries live until LRU evicts.
      For most use cases LRU is sufficient; if you need TTL (e.g. for
      rapidly-changing factual queries), wrap each entry's value with
      a timestamp and check on get.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Optional

log = logging.getLogger(__name__)


@dataclass
class CacheStats:
    """Counters for cache observability.

    Updated atomically under the cache lock. ``hit_rate`` is computed
    on read, not stored, so it always reflects current hit/miss totals.
    """
    hits: int = 0
    misses: int = 0
    stores: int = 0
    evictions: int = 0
    skipped_error_envelopes: int = 0  # tried to put() a status=error envelope

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "stores": self.stores,
            "evictions": self.evictions,
            "skipped_error_envelopes": self.skipped_error_envelopes,
            "hit_rate": round(self.hit_rate, 4),
        }


class CouncilCache:
    """LRU cache for the full ``run_full_council`` envelope.

    The envelope shape is whatever ``run_full_council`` produces:

        {
            "stage1": [...],
            "stage2": [...],
            "stage3": {"status": "ok"|"error", ...},
            "metadata": {...},
        }

    Only successful envelopes (``stage3.status == "ok"``) are cached;
    errors are deliberately *not* cached because retrying may succeed
    (rate-limit windows close, transient upstream errors clear). This
    avoids the failure mode where a brief outage poisons the cache for
    a long-lived process.
    """

    def __init__(self, max_entries: int = 1000):
        self._cache: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self._lock = threading.Lock()
        self._max = max_entries
        self.stats = CacheStats()

    @staticmethod
    def _make_key(
        query: str,
        council_models: Iterable[str],
        chairman_model: str,
    ) -> str:
        """Stable hash over the inputs that determine a pipeline result.

        Council model order does NOT affect the key — we sort. The
        original query is normalized (stripped + lowercased) so trivial
        whitespace/case variations hit the same entry.
        """
        normalized = (query or "").strip().lower()
        sorted_council = ",".join(sorted(council_models))
        h = hashlib.sha256()
        h.update(normalized.encode("utf-8"))
        h.update(b"|")
        h.update(sorted_council.encode("utf-8"))
        h.update(b"|")
        h.update(chairman_model.encode("utf-8"))
        return h.hexdigest()[:32]

    def get(
        self,
        query: str,
        council_models: Iterable[str],
        chairman_model: str,
    ) -> Optional[Dict[str, Any]]:
        """Return the cached envelope or None. Updates LRU position on hit."""
        key = self._make_key(query, council_models, chairman_model)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                self.stats.hits += 1
                log.debug("cache.hit", extra={"key": key, "hits": self.stats.hits})
                return self._cache[key]
            self.stats.misses += 1
            log.debug("cache.miss", extra={"key": key, "misses": self.stats.misses})
            return None

    def put(
        self,
        query: str,
        council_models: Iterable[str],
        chairman_model: str,
        envelope: Dict[str, Any],
    ) -> bool:
        """Store an envelope. Returns True on store, False if skipped.

        Skips silently if the envelope's chairman status isn't 'ok' —
        we never want to cache an error response.
        """
        stage3 = envelope.get("stage3") or {}
        if stage3.get("status") != "ok":
            with self._lock:
                self.stats.skipped_error_envelopes += 1
            return False

        key = self._make_key(query, council_models, chairman_model)
        with self._lock:
            if key in self._cache:
                # Refresh existing entry rather than allowing a duplicate.
                self._cache.move_to_end(key)
                self._cache[key] = envelope
                return True
            self._cache[key] = envelope
            self.stats.stores += 1
            while len(self._cache) > self._max:
                self._cache.popitem(last=False)
                self.stats.evictions += 1
            log.debug(
                "cache.store",
                extra={
                    "key": key,
                    "size": len(self._cache),
                    "stores": self.stats.stores,
                },
            )
            return True

    def clear(self) -> None:
        """Drop all entries; reset stats. Intended for tests / admin endpoints."""
        with self._lock:
            self._cache.clear()
            self.stats = CacheStats()

    def __len__(self) -> int:
        with self._lock:
            return len(self._cache)


# Process-global default cache instance. Tests should call ``clear()`` on
# this between runs or instantiate their own CouncilCache.
_default_cache: Optional[CouncilCache] = None


def get_cache() -> CouncilCache:
    """Return the lazily-created process-global cache."""
    global _default_cache
    if _default_cache is None:
        _default_cache = CouncilCache()
    return _default_cache


__all__ = ["CouncilCache", "CacheStats", "get_cache"]
