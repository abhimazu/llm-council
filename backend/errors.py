"""Typed error system for LLM calls.

Replaces the silent-None-on-failure pattern in the original openrouter.py
(audit findings A1, A3, A4, A5). Every LLM call returns either a successful
result or a structured ``LLMError`` that callers can route on, log, retry,
or surface to clients without leaking internals.

Why this exists:
    - The original code used ``except Exception: print(...); return None``,
      which collapses 401, 429, timeout, payload error, and network error into
      one indistinguishable signal. Callers had no way to retry, distinguish,
      or surface accurate failure information.
    - User-reported issues #27 and #113 ("Unable to generate final synthesis")
      are downstream of this collapse: a chairman call that 401's becomes a
      string masquerading as a real assistant answer.

Design notes:
    - ``LLMError`` is a small, JSON-serializable dataclass. It is *not* an
      exception; it is a return value. We deliberately do not raise across the
      stage boundaries because partial-failure (e.g. 3 of 4 council members
      succeeded) is a first-class outcome, not an exception.
    - ``detail`` is intended to be safe-to-log and safe-to-show. Raw exception
      messages may contain provider host names, tracebacks, or partial
      payload contents and are *not* placed in ``detail`` directly.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from enum import Enum
from typing import Any, Dict, Optional


class LLMErrorKind(str, Enum):
    """Coarse classification of a failed LLM call.

    The string values are stable contract — they appear in SSE events,
    persisted conversation JSON, and structured logs. Renaming any value is
    a breaking change.
    """
    AUTH = "auth"                # 401 / 403 — bad or missing API key
    RATE_LIMIT = "rate_limit"    # 429 — provider asked us to back off
    TIMEOUT = "timeout"          # client timeout or upstream slow-loris
    PAYLOAD = "payload"          # 4xx (not auth/rate) — request was malformed
    UPSTREAM = "upstream"        # 5xx — provider problem, not us
    PARSE = "parse"              # response shape did not match expected schema
    NETWORK = "network"          # transport-level failure
    CANCELLED = "cancelled"      # caller-side cancellation
    UNKNOWN = "unknown"          # genuinely unclassifiable


# Which kinds are worth retrying with exponential backoff. Auth errors are
# never retried (they will fail identically); payload errors are never
# retried (the request itself is wrong); parse errors are never retried
# (the model gave us something we can't read and is unlikely to do better
# on a second try at the same prompt).
RETRYABLE_KINDS = frozenset({
    LLMErrorKind.RATE_LIMIT,
    LLMErrorKind.TIMEOUT,
    LLMErrorKind.UPSTREAM,
    LLMErrorKind.NETWORK,
})


@dataclass(frozen=True)
class LLMError:
    """A structured error from a single LLM call.

    Attributes:
        kind: Coarse classification (see ``LLMErrorKind``).
        model: Model identifier the call was made against. Always populated.
        detail: Short, safe-to-log summary (no raw tracebacks, no PII).
        retryable: Whether the orchestration layer should retry this call.
        upstream_status: HTTP status if the failure was an HTTP response;
            ``None`` for transport / parse / cancellation failures.
        attempt: Which attempt this error is from (1-indexed). Useful for
            distinguishing "first try failed" from "all retries exhausted".
    """
    kind: LLMErrorKind
    model: str
    detail: str
    retryable: bool
    upstream_status: Optional[int] = None
    attempt: int = 1

    def to_dict(self) -> Dict[str, Any]:
        """JSON-serializable representation for SSE events and persistence."""
        d = asdict(self)
        # Coerce enum to its string value for JSON friendliness.
        d["kind"] = self.kind.value
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LLMError":
        """Inverse of ``to_dict``. Tolerant of unknown ``kind`` strings."""
        try:
            kind = LLMErrorKind(data["kind"])
        except (KeyError, ValueError):
            kind = LLMErrorKind.UNKNOWN
        return cls(
            kind=kind,
            model=data.get("model", "unknown"),
            detail=data.get("detail", ""),
            retryable=bool(data.get("retryable", False)),
            upstream_status=data.get("upstream_status"),
            attempt=int(data.get("attempt", 1)),
        )


def classify_http_status(status: int) -> LLMErrorKind:
    """Map an HTTP status code from OpenRouter to an ``LLMErrorKind``.

    Centralized so the mapping is consistent across the codebase and
    testable in isolation.
    """
    if status in (401, 403):
        return LLMErrorKind.AUTH
    if status == 429:
        return LLMErrorKind.RATE_LIMIT
    if status == 408:  # Some providers return 408 for client-side slow request
        return LLMErrorKind.TIMEOUT
    if 500 <= status < 600:
        return LLMErrorKind.UPSTREAM
    if 400 <= status < 500:
        return LLMErrorKind.PAYLOAD
    return LLMErrorKind.UNKNOWN


__all__ = [
    "LLMErrorKind",
    "LLMError",
    "RETRYABLE_KINDS",
    "classify_http_status",
]
