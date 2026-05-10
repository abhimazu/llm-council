"""Tests for backend/cache.py — LRU semantics, error rejection, key stability."""
import os
os.environ.setdefault("OPENROUTER_API_KEY", "test-key-for-import-only")

import pytest

from backend.cache import CouncilCache


COUNCIL = ["openai/gpt-5.1", "anthropic/claude-sonnet-4.5"]
CHAIRMAN = "anthropic/claude-3.5-haiku"


def _ok_envelope(text="hello"):
    return {
        "stage1": [{"model": "m", "status": "ok", "response": text}],
        "stage2": [],
        "stage3": {"model": CHAIRMAN, "status": "ok", "response": text},
        "metadata": {},
    }


def _err_envelope():
    return {
        "stage1": [],
        "stage2": [],
        "stage3": {"model": CHAIRMAN, "status": "error",
                   "error": {"kind": "auth", "detail": "401"}},
        "metadata": {},
    }


class TestKeyStability:
    def test_council_order_doesnt_change_key(self):
        c = CouncilCache()
        key1 = c._make_key("hello", ["a/x", "b/y"], "c/z")
        key2 = c._make_key("hello", ["b/y", "a/x"], "c/z")
        assert key1 == key2, "council models are sorted before hashing"

    def test_query_normalization_strips_and_lowercases(self):
        c = CouncilCache()
        key1 = c._make_key("  Hello World  ", ["a/x"], "c/z")
        key2 = c._make_key("hello world", ["a/x"], "c/z")
        assert key1 == key2

    def test_chairman_change_changes_key(self):
        c = CouncilCache()
        key1 = c._make_key("hello", ["a/x"], "chairman1")
        key2 = c._make_key("hello", ["a/x"], "chairman2")
        assert key1 != key2

    def test_council_change_changes_key(self):
        c = CouncilCache()
        key1 = c._make_key("hello", ["a/x"], "c/z")
        key2 = c._make_key("hello", ["a/x", "extra/m"], "c/z")
        assert key1 != key2


class TestBasicCRUD:
    def test_miss_then_put_then_hit(self):
        c = CouncilCache()
        assert c.get("q", COUNCIL, CHAIRMAN) is None
        assert c.stats.misses == 1

        env = _ok_envelope("answer")
        ok = c.put("q", COUNCIL, CHAIRMAN, env)
        assert ok is True

        got = c.get("q", COUNCIL, CHAIRMAN)
        assert got == env
        assert c.stats.hits == 1

    def test_put_skips_error_envelopes(self):
        c = CouncilCache()
        ok = c.put("q", COUNCIL, CHAIRMAN, _err_envelope())
        assert ok is False
        assert c.stats.skipped_error_envelopes == 1
        assert c.stats.stores == 0
        assert c.get("q", COUNCIL, CHAIRMAN) is None

    def test_clear_resets_state(self):
        c = CouncilCache()
        c.put("q", COUNCIL, CHAIRMAN, _ok_envelope())
        assert len(c) == 1
        c.clear()
        assert len(c) == 0
        assert c.stats.hits == 0


class TestLRUEviction:
    def test_eviction_when_max_exceeded(self):
        c = CouncilCache(max_entries=3)
        for i in range(5):
            c.put(f"q{i}", COUNCIL, CHAIRMAN, _ok_envelope(f"ans{i}"))
        assert len(c) == 3
        assert c.stats.evictions == 2
        # Oldest two (q0, q1) evicted
        assert c.get("q0", COUNCIL, CHAIRMAN) is None
        assert c.get("q1", COUNCIL, CHAIRMAN) is None
        # Newest three (q2, q3, q4) present
        assert c.get("q2", COUNCIL, CHAIRMAN) is not None
        assert c.get("q3", COUNCIL, CHAIRMAN) is not None
        assert c.get("q4", COUNCIL, CHAIRMAN) is not None

    def test_get_refreshes_lru_position(self):
        c = CouncilCache(max_entries=3)
        c.put("a", COUNCIL, CHAIRMAN, _ok_envelope("A"))
        c.put("b", COUNCIL, CHAIRMAN, _ok_envelope("B"))
        c.put("c", COUNCIL, CHAIRMAN, _ok_envelope("C"))
        # Touch 'a' so it becomes most-recently-used
        assert c.get("a", COUNCIL, CHAIRMAN) is not None
        # Add 'd' — should evict 'b' (now LRU), not 'a'
        c.put("d", COUNCIL, CHAIRMAN, _ok_envelope("D"))
        assert c.get("a", COUNCIL, CHAIRMAN) is not None, "a was refreshed by get"
        assert c.get("b", COUNCIL, CHAIRMAN) is None, "b was LRU"

    def test_put_existing_key_does_not_grow_or_evict(self):
        c = CouncilCache(max_entries=2)
        c.put("a", COUNCIL, CHAIRMAN, _ok_envelope("A1"))
        c.put("a", COUNCIL, CHAIRMAN, _ok_envelope("A2"))  # update
        assert len(c) == 1
        assert c.stats.evictions == 0
        assert c.get("a", COUNCIL, CHAIRMAN)["stage3"]["response"] == "A2"


class TestStats:
    def test_hit_rate_zero_when_no_calls(self):
        assert CouncilCache().stats.hit_rate == 0.0

    def test_hit_rate_after_mixed_calls(self):
        c = CouncilCache()
        c.put("q", COUNCIL, CHAIRMAN, _ok_envelope())
        c.get("q", COUNCIL, CHAIRMAN)  # hit
        c.get("other", COUNCIL, CHAIRMAN)  # miss
        c.get("q", COUNCIL, CHAIRMAN)  # hit
        assert c.stats.hit_rate == pytest.approx(2 / 3)

    def test_to_dict_shape(self):
        c = CouncilCache()
        d = c.stats.to_dict()
        assert set(d.keys()) == {
            "hits", "misses", "stores", "evictions",
            "skipped_error_envelopes", "hit_rate",
        }
