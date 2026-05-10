"""Tests for backend/router.py — heuristic gates + classifier outcomes."""
import os
os.environ.setdefault("OPENROUTER_API_KEY", "test-key-for-import-only")

import pytest

from backend import router
from backend.errors import LLMError, LLMErrorKind


@pytest.mark.asyncio
async def test_short_query_skips_council_and_classifier(monkeypatch):
    called = {"n": 0}
    async def never_called(*a, **kw):
        called["n"] += 1
        return ("nope", {}, None)
    monkeypatch.setattr(router, "query_model", never_called)

    decision = await router.should_engage_council("hi")
    assert decision.use_council is False
    assert decision.classifier_used is False
    assert "short" in decision.reason
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_long_query_engages_council_skipping_classifier(monkeypatch):
    called = {"n": 0}
    async def never_called(*a, **kw):
        called["n"] += 1
        return ("nope", {}, None)
    monkeypatch.setattr(router, "query_model", never_called)

    long_q = "x" * 1500
    decision = await router.should_engage_council(long_q)
    assert decision.use_council is True
    assert decision.classifier_used is False
    assert "long" in decision.reason
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_classifier_factual_simple_skips_council(monkeypatch):
    async def fake(*args, **kwargs):
        return ("FACTUAL_SIMPLE", {"prompt_tokens": 50, "completion_tokens": 1}, None)
    monkeypatch.setattr(router, "query_model", fake)

    q = "What is the capital of France in two words?"  # length > 30
    decision = await router.should_engage_council(q)
    assert decision.use_council is False
    assert decision.classifier_used is True
    assert decision.reason == "classified_factual_simple"


@pytest.mark.asyncio
async def test_classifier_complex_engages_council(monkeypatch):
    async def fake(*args, **kwargs):
        return ("COMPLEX_OR_SUBJECTIVE", {}, None)
    monkeypatch.setattr(router, "query_model", fake)

    q = "Should I migrate my Postgres database to MongoDB for our use case?"
    decision = await router.should_engage_council(q)
    assert decision.use_council is True
    assert decision.classifier_used is True
    assert decision.reason == "classified_complex_or_subjective"


@pytest.mark.asyncio
async def test_classifier_garbage_output_engages_council(monkeypatch):
    async def fake(*args, **kwargs):
        return ("I'm sorry, I cannot classify this.", {}, None)
    monkeypatch.setattr(router, "query_model", fake)

    q = "Write me a haiku about software architecture choices."
    decision = await router.should_engage_council(q)
    # Conservative: anything we can't parse engages the council
    assert decision.use_council is True
    assert decision.classifier_used is True
    assert decision.reason == "classifier_unparseable_output"


@pytest.mark.asyncio
async def test_classifier_failure_engages_council(monkeypatch):
    """Conservative: never silently degrade quality to save the classifier cost."""
    async def fake(*args, **kwargs):
        return (None, None, LLMError(
            kind=LLMErrorKind.RATE_LIMIT, model="m",
            detail="HTTP 429", retryable=True, upstream_status=429,
        ))
    monkeypatch.setattr(router, "query_model", fake)

    q = "Recommend a database for a 100-user product."
    decision = await router.should_engage_council(q)
    assert decision.use_council is True
    assert decision.classifier_used is True
    assert "rate_limit" in decision.reason


@pytest.mark.asyncio
async def test_decision_serializable(monkeypatch):
    async def fake(*args, **kwargs):
        return ("FACTUAL_SIMPLE", {}, None)
    monkeypatch.setattr(router, "query_model", fake)

    q = "What is the boiling point of water at sea level?"
    decision = await router.should_engage_council(q)
    d = decision.to_dict()
    assert set(d.keys()) == {
        "use_council", "reason", "classifier_used", "classifier_output",
    }
    # Round-trip JSON
    import json
    json.dumps(d)  # should not raise
