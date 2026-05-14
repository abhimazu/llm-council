"""Tests for openrouter.query_model with mocked httpx.

Covers the cases that previously collapsed to None and broke
issues #27/#113/#133/#156 downstream.
"""
import os
os.environ.setdefault("OPENROUTER_API_KEY", "test-key-for-import-only")

import json

import httpx
import pytest

from backend import openrouter
from backend.errors import LLMErrorKind


def _patch_httpx(monkeypatch, handler):
    """Replace httpx.AsyncClient with one that uses a MockTransport.

    We patch only the class that openrouter.py imports, so other tests
    that import httpx remain unaffected.
    """
    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient

    def _patched(*args, **kwargs):
        kwargs.pop("transport", None)
        return real(transport=transport, **kwargs)

    monkeypatch.setattr(openrouter.httpx, "AsyncClient", _patched)


@pytest.mark.asyncio
async def test_success_returns_content_and_usage(monkeypatch):
    def handler(request):
        body = json.dumps({
            "choices": [{
                "message": {"content": "Paris"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 5, "completion_tokens": 1, "cost": 0.0001},
        }).encode()
        return httpx.Response(200, content=body, headers={"content-type": "application/json"})

    _patch_httpx(monkeypatch, handler)
    content, usage, err = await openrouter.query_model(
        "test/m", [{"role": "user", "content": "capital of france"}],
    )
    assert err is None, f"unexpected error: {err}"
    assert content == "Paris"
    assert usage["cost"] == 0.0001


@pytest.mark.asyncio
async def test_401_returns_auth_error_no_retry(monkeypatch):
    call_count = {"n": 0}

    def handler(request):
        call_count["n"] += 1
        return httpx.Response(401, json={"error": "bad key"})

    _patch_httpx(monkeypatch, handler)
    content, usage, err = await openrouter.query_model("test/m", [])
    assert content is None and usage is None
    assert err is not None
    assert err.kind is LLMErrorKind.AUTH
    assert err.upstream_status == 401
    assert err.retryable is False
    assert call_count["n"] == 1, "AUTH must NOT retry"


@pytest.mark.asyncio
async def test_429_retried_once_then_returned(monkeypatch):
    call_count = {"n": 0}

    def handler(request):
        call_count["n"] += 1
        return httpx.Response(429, json={"error": "slow down"})

    _patch_httpx(monkeypatch, handler)
    monkeypatch.setattr(openrouter, "_backoff_seconds", lambda attempt: 0)

    content, usage, err = await openrouter.query_model(
        "test/m", [], max_retries=1,
    )
    assert err is not None
    assert err.kind is LLMErrorKind.RATE_LIMIT
    assert err.retryable is True
    assert err.attempt == 2  # second attempt failed
    assert call_count["n"] == 2  # one retry happened


@pytest.mark.asyncio
async def test_429_then_success_returns_success(monkeypatch):
    call_count = {"n": 0}

    def handler(request):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return httpx.Response(429)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        })

    _patch_httpx(monkeypatch, handler)
    monkeypatch.setattr(openrouter, "_backoff_seconds", lambda attempt: 0)

    content, _u, err = await openrouter.query_model(
        "test/m", [], max_retries=1,
    )
    assert err is None
    assert content == "ok"
    assert call_count["n"] == 2


@pytest.mark.asyncio
async def test_5xx_classified_as_upstream_and_retried(monkeypatch):
    call_count = {"n": 0}

    def handler(request):
        call_count["n"] += 1
        return httpx.Response(503, content=b"upstream down")

    _patch_httpx(monkeypatch, handler)
    monkeypatch.setattr(openrouter, "_backoff_seconds", lambda attempt: 0)

    _c, _u, err = await openrouter.query_model("test/m", [], max_retries=1)
    assert err is not None
    assert err.kind is LLMErrorKind.UPSTREAM
    assert err.upstream_status == 503
    assert call_count["n"] == 2


@pytest.mark.asyncio
async def test_400_payload_error_no_retry(monkeypatch):
    call_count = {"n": 0}

    def handler(request):
        call_count["n"] += 1
        return httpx.Response(400, json={"error": "bad request"})

    _patch_httpx(monkeypatch, handler)
    _c, _u, err = await openrouter.query_model("test/m", [], max_retries=2)
    assert err is not None
    assert err.kind is LLMErrorKind.PAYLOAD
    assert err.retryable is False
    assert call_count["n"] == 1


@pytest.mark.asyncio
async def test_malformed_response_yields_parse_error(monkeypatch):
    def handler(request):
        # 200 OK but missing 'choices' key
        return httpx.Response(200, json={"unexpected": "shape"})

    _patch_httpx(monkeypatch, handler)
    _c, _u, err = await openrouter.query_model("test/m", [])
    assert err is not None
    assert err.kind is LLMErrorKind.PARSE
    assert err.retryable is False


@pytest.mark.asyncio
async def test_query_models_parallel_isolates_failures(monkeypatch):
    """Mixed success/failure across parallel models — each model gets
    its own typed result, no failure cascade."""
    def handler(request):
        body = request.read()
        payload = json.loads(body)
        model = payload["model"]
        if model == "fail/auth":
            return httpx.Response(401)
        if model == "fail/server":
            return httpx.Response(500)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": f"reply from {model}"},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        })

    _patch_httpx(monkeypatch, handler)
    monkeypatch.setattr(openrouter, "_backoff_seconds", lambda attempt: 0)

    results = await openrouter.query_models_parallel(
        ["ok/m1", "fail/auth", "ok/m2", "fail/server"],
        [{"role": "user", "content": "hi"}],
    )
    c, _u, e = results["ok/m1"]
    assert e is None and c == "reply from ok/m1"
    c, _u, e = results["fail/auth"]
    assert c is None and e.kind is LLMErrorKind.AUTH
    c, _u, e = results["ok/m2"]
    assert e is None and c == "reply from ok/m2"
    c, _u, e = results["fail/server"]
    assert c is None and e.kind is LLMErrorKind.UPSTREAM


@pytest.mark.asyncio
async def test_max_tokens_passed_in_payload(monkeypatch):
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.read())
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "x"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        })

    _patch_httpx(monkeypatch, handler)
    await openrouter.query_model("m", [], max_tokens=42)
    assert captured["body"]["max_tokens"] == 42

    # And no max_tokens key when None (preserves original uncapped behavior)
    captured.clear()
    await openrouter.query_model("m", [], max_tokens=None)
    assert "max_tokens" not in captured["body"]
