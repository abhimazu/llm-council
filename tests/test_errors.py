"""Tests for the typed error system in backend/errors.py."""
import os
os.environ.setdefault("OPENROUTER_API_KEY", "test-key-for-import-only")

import pytest

from backend.errors import (
    LLMError,
    LLMErrorKind,
    RETRYABLE_KINDS,
    classify_http_status,
)


class TestClassifyHttpStatus:
    def test_auth_401(self):
        assert classify_http_status(401) is LLMErrorKind.AUTH

    def test_auth_403(self):
        assert classify_http_status(403) is LLMErrorKind.AUTH

    def test_rate_limit_429(self):
        assert classify_http_status(429) is LLMErrorKind.RATE_LIMIT

    def test_timeout_408(self):
        assert classify_http_status(408) is LLMErrorKind.TIMEOUT

    def test_upstream_5xx(self):
        for s in (500, 502, 503, 504, 520):
            assert classify_http_status(s) is LLMErrorKind.UPSTREAM, f"status {s}"

    def test_payload_other_4xx(self):
        for s in (400, 404, 410, 422):
            assert classify_http_status(s) is LLMErrorKind.PAYLOAD, f"status {s}"

    def test_unknown_2xx(self):
        # 2xx never reaches classify in production code, but be explicit.
        assert classify_http_status(200) is LLMErrorKind.UNKNOWN
        assert classify_http_status(204) is LLMErrorKind.UNKNOWN


class TestRetryableKinds:
    def test_retryable_set_correct(self):
        assert LLMErrorKind.RATE_LIMIT in RETRYABLE_KINDS
        assert LLMErrorKind.TIMEOUT in RETRYABLE_KINDS
        assert LLMErrorKind.UPSTREAM in RETRYABLE_KINDS
        assert LLMErrorKind.NETWORK in RETRYABLE_KINDS

    def test_non_retryable_set_correct(self):
        assert LLMErrorKind.AUTH not in RETRYABLE_KINDS
        assert LLMErrorKind.PAYLOAD not in RETRYABLE_KINDS
        assert LLMErrorKind.PARSE not in RETRYABLE_KINDS
        assert LLMErrorKind.CANCELLED not in RETRYABLE_KINDS
        assert LLMErrorKind.UNKNOWN not in RETRYABLE_KINDS


class TestLLMErrorSerialization:
    def test_to_dict_kind_is_string(self):
        e = LLMError(kind=LLMErrorKind.AUTH, model="m", detail="d",
                     retryable=False, upstream_status=401)
        d = e.to_dict()
        assert d["kind"] == "auth"  # JSON-friendly string, not enum repr
        assert d["upstream_status"] == 401

    def test_roundtrip_preserves_fields(self):
        e = LLMError(kind=LLMErrorKind.RATE_LIMIT, model="x/y",
                     detail="HTTP 429", retryable=True,
                     upstream_status=429, attempt=2)
        roundtripped = LLMError.from_dict(e.to_dict())
        assert roundtripped == e

    def test_from_dict_tolerates_unknown_kind(self):
        # Forward-compatibility: deserializing a kind we don't know about
        # shouldn't crash — it should map to UNKNOWN.
        e = LLMError.from_dict({"kind": "future_unknown_kind", "model": "m",
                                "detail": "", "retryable": False})
        assert e.kind is LLMErrorKind.UNKNOWN

    def test_from_dict_tolerates_missing_optional_fields(self):
        e = LLMError.from_dict({"kind": "timeout", "model": "m"})
        assert e.kind is LLMErrorKind.TIMEOUT
        assert e.detail == ""
        assert e.retryable is False
        assert e.upstream_status is None
