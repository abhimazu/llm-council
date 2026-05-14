"""Tests for council.py: silent-drop is now surfaced.

Replaces the original behavior where failed council members were
silently omitted from the result list.
"""
import os
os.environ.setdefault("OPENROUTER_API_KEY", "test-key-for-import-only")

import pytest

from backend import council
from backend.errors import LLMError, LLMErrorKind


def _ok_tuple(model, content="response from " + "...", usage=None):
    return (content, usage or {"prompt_tokens": 1, "completion_tokens": 1}, None)


def _err_tuple(model, kind=LLMErrorKind.AUTH):
    return (None, None, LLMError(kind=kind, model=model,
                                  detail="mocked", retryable=False))


@pytest.mark.asyncio
async def test_stage1_surfaces_errors_not_drops_them(monkeypatch):
    """Two of four council members fail. Result list still has 4 entries."""
    monkeypatch.setattr(council, "COUNCIL_MODELS",
                        ["a/x", "b/y", "c/z", "d/w"])

    async def fake_parallel(models, messages, *, max_tokens=None,
                            timeout=None):
        return {
            "a/x": _ok_tuple("a/x", "alpha"),
            "b/y": _err_tuple("b/y", LLMErrorKind.RATE_LIMIT),
            "c/z": _ok_tuple("c/z", "gamma"),
            "d/w": _err_tuple("d/w", LLMErrorKind.TIMEOUT),
        }

    monkeypatch.setattr(council, "query_models_parallel", fake_parallel)
    results = await council.stage1_collect_responses("test query")
    assert len(results) == 4, "all 4 entries must be present, not silently dropped"
    statuses = [r.status for r in results]
    assert statuses == ["ok", "error", "ok", "error"]
    assert results[0].response == "alpha"
    assert results[1].error["kind"] == "rate_limit"
    assert results[3].error["kind"] == "timeout"


@pytest.mark.asyncio
async def test_stage3_chairman_failure_is_structured_error_not_string(monkeypatch):
    """Chairman fallback no longer masquerades as a real model answer."""
    # Simulate stage1 with one ok entry so stage3 actually runs.
    s1 = [council.CouncilMemberResult(model="m1", status="ok",
                                       response="answer A")]

    async def fake_query(model, messages, *, timeout=None, max_tokens=None,
                         max_retries=1):
        return _err_tuple(model, LLMErrorKind.AUTH)

    monkeypatch.setattr(council, "query_model", fake_query)
    result = await council.stage3_synthesize_final("q", s1, [])
    assert result.status == "error"
    assert result.response is None
    assert result.error is not None
    assert result.error["kind"] == "auth"
    # Confirm the misleading string is NOT what we surface anywhere
    assert "Error: Unable to generate final synthesis" not in str(result.to_dict())


@pytest.mark.asyncio
async def test_stage3_no_successful_stage1_short_circuits(monkeypatch):
    """If every stage1 member failed, chairman is not even called."""
    s1 = [council.CouncilMemberResult(
        model="m1", status="error",
        error={"kind": "rate_limit", "model": "m1", "detail": "",
               "retryable": True, "upstream_status": 429, "attempt": 1},
    )]

    called = {"n": 0}

    async def fake_query(*a, **kw):
        called["n"] += 1
        return _ok_tuple("m1")

    monkeypatch.setattr(council, "query_model", fake_query)
    result = await council.stage3_synthesize_final("q", s1, [])
    assert result.status == "error"
    assert result.error["kind"] == "unknown"
    assert "no successful Stage-1" in result.error["detail"]
    assert called["n"] == 0, "chairman must not be called when stage1 was empty"


def test_aggregate_partial_flag_set_when_any_ranker_partial():
    """Aggregate from a partially-parsed ranker is flagged."""
    from backend.council import RankingResult, calculate_aggregate_rankings

    label_to_model = {"Response A": "m1", "Response B": "m2"}
    s2 = [
        # Clean ranker
        RankingResult(model="ra", status="ok", parsed_ranking=["Response A", "Response B"],
                      parse_status="ok"),
        # Partial ranker (truncated)
        RankingResult(model="rb", status="ok", parsed_ranking=["Response B"],
                      parse_status="partial"),
    ]
    agg = calculate_aggregate_rankings(s2, label_to_model)
    by_model = {a.model: a for a in agg}
    # m1 appears in 1 ranker (ra=1st), avg=1, partial=False
    assert by_model["m1"].rankings_count == 1
    assert by_model["m1"].partial is False
    # m2 appears in 2 rankers (ra=2nd, rb=1st), avg=1.5, partial=True (rb was partial)
    assert by_model["m2"].rankings_count == 2
    assert by_model["m2"].average_rank == 1.5
    assert by_model["m2"].partial is True


def test_aggregate_excludes_failed_rankers():
    """A ranker with status='error' contributes nothing to the aggregate."""
    from backend.council import RankingResult, calculate_aggregate_rankings

    label_to_model = {"Response A": "m1"}
    s2 = [
        RankingResult(model="r_ok", status="ok",
                      parsed_ranking=["Response A"], parse_status="ok"),
        RankingResult(model="r_err", status="error", error={"kind": "auth"}),
    ]
    agg = calculate_aggregate_rankings(s2, label_to_model)
    assert len(agg) == 1
    assert agg[0].model == "m1"
