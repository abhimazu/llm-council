"""3-stage LLM Council orchestration with structured per-stage results.

This module replaces the original council.py which silently dropped failed
council members (audit findings A3, A4) and used a string-fallback for
chairman failures (audit finding A5). Every stage now returns a structured
result that distinguishes success, partial-success, and failure — and the
information needed to act on each.

Architectural changes vs. the original:
    1. Per-member results carry an explicit ``status`` field instead of being
       silently omitted from the list. A council that loses 2 of 4 members to
       rate-limit errors is no longer indistinguishable from a 2-member
       council.
    2. The Stage-2 ranking parser returns a discriminated result with
       ``parse_status`` (ok / partial / parse_error) so the aggregate-rankings
       calculation can flag itself as ``partial`` when it is derived from
       incomplete data.
    3. The chairman fallback is no longer a string masquerading as a real
       answer. Stage-3 returns either ``status="ok"`` with a real response or
       ``status="error"`` with a structured ``LLMError``. Persistence and the
       UI can now distinguish.
    4. Anonymization labels in Stage 2 are randomly permuted per-call rather
       than order-stable. This reduces the position-bias contribution to
       self-favoritism (audit finding B4 / B9). It does not eliminate
       self-favoritism — the eval framework will need to measure that
       independently.
    5. Errors flow up rather than being caught and dropped at the call
       boundary. The orchestration layer is the *only* place that decides
       what to surface to the user.
"""

from __future__ import annotations

import logging
import random
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .config import (
    CHAIRMAN_MODEL,
    CHAIRMAN_MAX_TOKENS,
    COUNCIL_MODELS,
    STAGE1_MAX_TOKENS,
    STAGE2_MAX_TOKENS,
    TITLE_MODEL,
)
from .errors import LLMError, LLMErrorKind
from .openrouter import query_model, query_models_parallel

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass
class CouncilMemberResult:
    """One council member's outcome at one stage.

    Always has ``model`` and ``status``. ``status="ok"`` carries
    ``response`` (and optionally ``usage``). ``status="error"`` carries
    ``error``. The two are mutually exclusive — exactly one is non-None.
    """
    model: str
    status: str  # "ok" | "error"
    response: Optional[str] = None
    usage: Optional[Dict[str, Any]] = None
    error: Optional[Dict[str, Any]] = None  # serialized LLMError

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class RankingResult:
    """One council member's Stage-2 ranking outcome.

    Carries the raw ranking text (for the chairman to consume) plus a
    parsed list and a parse status that distinguishes a clean parse, a
    partial parse, and a complete parse failure.
    """
    model: str
    status: str  # "ok" | "error"
    ranking: Optional[str] = None
    parsed_ranking: Optional[List[str]] = None
    parse_status: Optional[str] = None  # "ok" | "partial" | "parse_error"
    parse_reason: Optional[str] = None
    usage: Optional[Dict[str, Any]] = None
    error: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class ChairmanResult:
    """Stage-3 chairman synthesis outcome.

    ``status="ok"`` => ``response`` is the synthesized answer.
    ``status="error"`` => ``error`` is a structured LLMError; ``response``
    is None and the UI/persistence layer should render this as an error
    state, not as if the chairman literally answered with an error string.
    """
    model: str
    status: str  # "ok" | "error"
    response: Optional[str] = None
    usage: Optional[Dict[str, Any]] = None
    error: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class AggregateRanking:
    """Aggregate (cross-ranker) ranking for one council member.

    ``partial`` flags whether the aggregate is based on a complete set of
    rankings or a subset. Downstream UI should surface this — the original
    code computed the aggregate silently from whatever rankings happened
    to parse, with no signal to the user.
    """
    model: str
    average_rank: float
    rankings_count: int
    partial: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Stage 1: collect individual responses
# ---------------------------------------------------------------------------


async def stage1_collect_responses(user_query: str) -> List[CouncilMemberResult]:
    """Stage 1: ask each council member the user's question independently.

    Returns one ``CouncilMemberResult`` per ``COUNCIL_MODELS`` entry. Members
    that errored are *not* dropped — they appear with ``status="error"`` so
    callers can render them, log them, and decide whether to proceed.
    """
    messages = [{"role": "user", "content": user_query}]
    raw = await query_models_parallel(
        COUNCIL_MODELS, messages, max_tokens=STAGE1_MAX_TOKENS
    )

    results: List[CouncilMemberResult] = []
    for model in COUNCIL_MODELS:
        content, usage, err = raw[model]
        if err is None:
            results.append(CouncilMemberResult(
                model=model, status="ok", response=content, usage=usage,
            ))
        else:
            results.append(CouncilMemberResult(
                model=model, status="error", error=err.to_dict(),
            ))

    ok_n = sum(1 for r in results if r.status == "ok")
    log.info(
        "stage1.complete",
        extra={"council_size": len(COUNCIL_MODELS), "ok": ok_n,
               "errors": len(results) - ok_n},
    )
    return results


# ---------------------------------------------------------------------------
# Stage 2: peer-ranking with anonymized labels
# ---------------------------------------------------------------------------


def _build_label_mapping(
    successful_stage1: List[CouncilMemberResult],
) -> Tuple[Dict[str, str], List[Tuple[str, CouncilMemberResult]]]:
    """Build a randomly-permuted ``label -> model`` mapping for Stage 2.

    Returns:
        - label_to_model: e.g. ``{"Response A": "openai/gpt-5.1", ...}``.
        - labelled: ``[(label, member), ...]`` in shuffled order, used to
          build the prompt.

    Why shuffle: the original code used ``chr(65 + i)`` deterministically by
    ``COUNCIL_MODELS`` order. If any model exhibits position bias (judges
    "Response A" more favorably than "Response D"), the bias compounds
    rather than cancelling. Per-call shuffling distributes position
    independently of model identity. (Audit findings B4 / B9.)
    """
    shuffled = list(successful_stage1)
    random.shuffle(shuffled)
    labels = [chr(65 + i) for i in range(len(shuffled))]
    label_to_model = {f"Response {l}": r.model for l, r in zip(labels, shuffled)}
    labelled = list(zip(labels, shuffled))
    return label_to_model, labelled


def _build_ranking_prompt(
    user_query: str,
    labelled: List[Tuple[str, CouncilMemberResult]],
) -> str:
    """Construct the Stage-2 ranking prompt.

    Identical wording to the original prompt for behavioral continuity —
    we are not changing the council's *judgment* prompt, only the
    parsing tolerance and labelling scheme around it.
    """
    responses_text = "\n\n".join(
        f"Response {label}:\n{r.response}" for label, r in labelled
    )
    return f"""You are evaluating different responses to the following question:

Question: {user_query}

Here are the responses from different models (anonymized):

{responses_text}

Your task:
1. First, evaluate each response individually. For each response, explain what it does well and what it does poorly.
2. Then, at the very end of your response, provide a final ranking.

IMPORTANT: Your final ranking MUST be formatted EXACTLY as follows:
- Start with the line "FINAL RANKING:" (all caps, with colon)
- Then list the responses from best to worst as a numbered list
- Each line should be: number, period, space, then ONLY the response label (e.g., "1. Response A")
- Do not add any other text or explanations in the ranking section

Example of the correct format for your ENTIRE response:

Response A provides good detail on X but misses Y...
Response B is accurate but lacks depth on Z...
Response C offers the most comprehensive answer...

FINAL RANKING:
1. Response C
2. Response A
3. Response B

Now provide your evaluation and ranking:"""


def parse_ranking_from_text(
    ranking_text: str, expected_n: int,
) -> Tuple[List[str], str, str]:
    """Parse the FINAL RANKING section.

    Returns:
        (parsed_list, parse_status, parse_reason)
        - parse_status is "ok" if the parse found exactly ``expected_n``
          unique ranks, "partial" if it found 1 <= k < expected_n (such as
          when the model's output was truncated mid-list — observed live
          in doc 05), or "parse_error" if it found 0 ranks.
        - parse_reason is a short string explaining the status.

    The parser is *tolerant*: if the FINAL RANKING heading is missing,
    we fall back to extracting any "Response X" tokens in order. This is
    deliberately permissive — it's better to surface a partial ranking
    with a status flag than to silently drop the whole thing (which is
    the original behavior, downstream of issues #27 / #113).
    """
    section: Optional[str] = None
    if "FINAL RANKING:" in ranking_text:
        section = ranking_text.split("FINAL RANKING:", 1)[1]
    else:
        # Tolerant fallback: try to find a numbered list anywhere.
        section = ranking_text

    numbered = re.findall(r"\d+\.\s*Response [A-Z]", section)
    if numbered:
        parsed = [re.search(r"Response [A-Z]", m).group() for m in numbered]
    else:
        # Last-resort fallback: any "Response X" token order.
        parsed = re.findall(r"Response [A-Z]", section)

    # Deduplicate while preserving order — some models repeat labels.
    seen = set()
    unique: List[str] = []
    for label in parsed:
        if label not in seen:
            seen.add(label)
            unique.append(label)

    if len(unique) == 0:
        return [], "parse_error", (
            "no ranking labels found"
            if "FINAL RANKING:" in ranking_text
            else "FINAL RANKING heading missing and no labels found"
        )
    if len(unique) < expected_n:
        return unique, "partial", (
            f"parsed {len(unique)} of {expected_n} expected ranks "
            f"(possible truncation or format deviation)"
        )
    return unique[:expected_n], "ok", ""


async def stage2_collect_rankings(
    user_query: str,
    stage1_results: List[CouncilMemberResult],
) -> Tuple[List[RankingResult], Dict[str, str]]:
    """Stage 2: each council member ranks the anonymized peer responses.

    Only members that succeeded in Stage 1 contribute responses to be
    ranked, but every council member (including those that failed Stage 1)
    is still asked to rank — they may have recovered. Errors are surfaced
    rather than dropped.
    """
    successful = [r for r in stage1_results if r.status == "ok"]
    if not successful:
        # No responses to rank — return a structured empty result.
        return [], {}

    label_to_model, labelled = _build_label_mapping(successful)
    ranking_prompt = _build_ranking_prompt(user_query, labelled)
    messages = [{"role": "user", "content": ranking_prompt}]

    raw = await query_models_parallel(
        COUNCIL_MODELS, messages, max_tokens=STAGE2_MAX_TOKENS
    )

    expected_n = len(successful)
    results: List[RankingResult] = []
    for model in COUNCIL_MODELS:
        content, usage, err = raw[model]
        if err is not None:
            results.append(RankingResult(
                model=model, status="error", error=err.to_dict(),
            ))
            continue
        parsed, parse_status, parse_reason = parse_ranking_from_text(
            content or "", expected_n=expected_n,
        )
        results.append(RankingResult(
            model=model,
            status="ok",
            ranking=content,
            parsed_ranking=parsed,
            parse_status=parse_status,
            parse_reason=parse_reason,
            usage=usage,
        ))

    log.info(
        "stage2.complete",
        extra={
            "rankers_attempted": len(COUNCIL_MODELS),
            "rankers_ok": sum(1 for r in results if r.status == "ok"),
            "parse_ok": sum(1 for r in results if r.parse_status == "ok"),
            "parse_partial": sum(
                1 for r in results if r.parse_status == "partial"
            ),
            "parse_error": sum(
                1 for r in results if r.parse_status == "parse_error"
            ),
        },
    )
    return results, label_to_model


def calculate_aggregate_rankings(
    stage2_results: List[RankingResult],
    label_to_model: Dict[str, str],
) -> List[AggregateRanking]:
    """Aggregate rankings across rankers; flag if derived from partial data.

    A given member's aggregate is marked ``partial=True`` if any contributing
    ranker produced a non-"ok" parse_status (i.e. ``partial`` or
    ``parse_error``). The original code silently averaged whatever it
    could parse with no signal to downstream consumers.
    """
    positions: Dict[str, List[int]] = defaultdict(list)
    contributing_partial: Dict[str, bool] = defaultdict(bool)

    for ranker in stage2_results:
        if ranker.status != "ok" or not ranker.parsed_ranking:
            continue
        is_partial = ranker.parse_status != "ok"
        for pos, label in enumerate(ranker.parsed_ranking, start=1):
            model_name = label_to_model.get(label)
            if model_name is None:
                continue
            positions[model_name].append(pos)
            if is_partial:
                contributing_partial[model_name] = True

    aggregate: List[AggregateRanking] = []
    for model, ps in positions.items():
        if not ps:
            continue
        aggregate.append(AggregateRanking(
            model=model,
            average_rank=round(sum(ps) / len(ps), 2),
            rankings_count=len(ps),
            partial=contributing_partial[model],
        ))
    aggregate.sort(key=lambda x: x.average_rank)
    return aggregate


# ---------------------------------------------------------------------------
# Stage 3: chairman synthesis
# ---------------------------------------------------------------------------


def _build_chairman_prompt(
    user_query: str,
    stage1_results: List[CouncilMemberResult],
    stage2_results: List[RankingResult],
) -> str:
    """Construct the chairman synthesis prompt.

    Note: we feed the chairman only the *parsed* rankings plus the rankers'
    short evaluations, not the full freeform Stage-2 text. This is a
    cost-control measure (audit finding C-B in proposed-changes §4.4) and
    also reduces the surface area for one model's self-advocacy prose to
    bias the chairman.
    """
    s1_lines = []
    for r in stage1_results:
        if r.status == "ok":
            s1_lines.append(f"Model: {r.model}\nResponse: {r.response}")
        else:
            s1_lines.append(
                f"Model: {r.model}\nResponse: [unavailable: "
                f"{r.error.get('kind', 'unknown') if r.error else 'unknown'}]"
            )
    s1_text = "\n\n".join(s1_lines)

    s2_lines = []
    for r in stage2_results:
        if r.status == "ok":
            ranks = r.parsed_ranking or []
            s2_lines.append(
                f"Ranker: {r.model}\nParsed ranking: {ranks}\n"
                f"Parse status: {r.parse_status}"
            )
    s2_text = "\n\n".join(s2_lines) if s2_lines else "(no rankings available)"

    return f"""You are the Chairman of an LLM Council. Multiple AI models have provided responses to a user's question, and then ranked each other's responses.

Original Question: {user_query}

STAGE 1 - Individual Responses:
{s1_text}

STAGE 2 - Peer Rankings (parsed only):
{s2_text}

Your task as Chairman is to synthesize all of this information into a single, comprehensive, accurate answer to the user's original question. Consider:
- The individual responses and their insights
- The peer rankings and what they reveal about response quality
- Any patterns of agreement or disagreement

Provide a clear, well-reasoned final answer that represents the council's collective wisdom:"""


async def stage3_synthesize_final(
    user_query: str,
    stage1_results: List[CouncilMemberResult],
    stage2_results: List[RankingResult],
) -> ChairmanResult:
    """Stage 3: chairman synthesis with structured success/failure result.

    If no Stage-1 responses succeeded, returns a structured error
    immediately rather than asking the chairman to synthesize from
    nothing. (Original code asked anyway, which is wasteful and
    produced the misleading ``"Error: Unable to generate final synthesis."``
    string-as-answer.)
    """
    successful_s1 = [r for r in stage1_results if r.status == "ok"]
    if not successful_s1:
        err = LLMError(
            kind=LLMErrorKind.UNKNOWN,
            model=CHAIRMAN_MODEL,
            detail="no successful Stage-1 responses to synthesize",
            retryable=False,
        )
        return ChairmanResult(
            model=CHAIRMAN_MODEL, status="error", error=err.to_dict(),
        )

    prompt = _build_chairman_prompt(user_query, stage1_results, stage2_results)
    messages = [{"role": "user", "content": prompt}]
    content, usage, err = await query_model(
        CHAIRMAN_MODEL, messages, max_tokens=CHAIRMAN_MAX_TOKENS,
    )
    if err is not None:
        return ChairmanResult(
            model=CHAIRMAN_MODEL, status="error", error=err.to_dict(),
        )
    return ChairmanResult(
        model=CHAIRMAN_MODEL, status="ok", response=content, usage=usage,
    )


# ---------------------------------------------------------------------------
# Title generation (best-effort, never blocks the main flow)
# ---------------------------------------------------------------------------


async def generate_conversation_title(user_query: str) -> Optional[str]:
    """Generate a short title for a conversation; best-effort.

    Returns ``None`` (not the literal string ``"New Conversation"``) on
    failure so callers can distinguish "title-gen failed" from "title was
    explicitly set". Original code returned ``"New Conversation"`` on
    failure, which is the same string used for the default-on-creation
    title — making the failure invisible (audit finding A6).
    """
    title_prompt = (
        "Generate a very short title (3-5 words maximum) that summarizes "
        "the following question. The title should be concise and "
        "descriptive. Do not use quotes or punctuation in the title.\n\n"
        f"Question: {user_query}\n\nTitle:"
    )
    messages = [{"role": "user", "content": title_prompt}]
    content, _usage, err = await query_model(
        TITLE_MODEL, messages, timeout=15.0, max_tokens=24, max_retries=0,
    )
    if err is not None or not content:
        log.info("title.failed",
                 extra={"kind": err.kind.value if err else "no_content"})
        return None
    title = content.strip().strip('"\'')
    if len(title) > 50:
        title = title[:47] + "..."
    return title


# ---------------------------------------------------------------------------
# Top-level orchestrator
# ---------------------------------------------------------------------------


async def run_full_council(user_query: str) -> Dict[str, Any]:
    """Run the complete 3-stage council and return a structured envelope.

    The envelope shape is intentionally identical between this function
    and the streaming endpoint so that ``/message`` and ``/message/stream``
    produce the same logical result for the same input (audit finding B7,
    which doc 04 surfaced as a new finding live).

    Returns a dict with keys:
        stage1: List[dict] — serialized CouncilMemberResult per council member
        stage2: List[dict] — serialized RankingResult per council member
        stage3: dict       — serialized ChairmanResult
        metadata: dict     — label_to_model + aggregate_rankings
    """
    stage1 = await stage1_collect_responses(user_query)
    stage2, label_to_model = await stage2_collect_rankings(user_query, stage1)
    aggregate = calculate_aggregate_rankings(stage2, label_to_model)
    stage3 = await stage3_synthesize_final(user_query, stage1, stage2)

    return {
        "stage1": [r.to_dict() for r in stage1],
        "stage2": [r.to_dict() for r in stage2],
        "stage3": stage3.to_dict(),
        "metadata": {
            "label_to_model": label_to_model,
            "aggregate_rankings": [a.to_dict() for a in aggregate],
        },
    }


__all__ = [
    "CouncilMemberResult",
    "RankingResult",
    "ChairmanResult",
    "AggregateRanking",
    "stage1_collect_responses",
    "stage2_collect_rankings",
    "stage3_synthesize_final",
    "calculate_aggregate_rankings",
    "parse_ranking_from_text",
    "generate_conversation_title",
    "run_full_council",
]
