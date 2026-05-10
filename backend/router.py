"""Smart routing: decide whether a query needs the full council.

Why this exists (audit findings C1, M13):
    Most queries don't need a 4-LLM fan-out. Trivial factual queries
    (arithmetic, lookups, definitions) can be answered correctly by
    the chairman model alone at roughly 1/10th the cost and 1/3rd the
    latency of the full pipeline. The current code runs the full
    pipeline unconditionally.

How it works:
    1. Heuristic gates: short queries (< 30 chars) skip the council
       (they're almost never complex enough to need consensus); very
       long queries (> 1000 chars) skip the classifier and go directly
       to the council (paying for the classifier on a giant prompt is
       wasteful).
    2. For queries between those bounds, an LLM classifier picks one
       of two labels: FACTUAL_SIMPLE or COMPLEX_OR_SUBJECTIVE. Cost is
       a single small-model call (~$0.0001).
    3. On classifier failure (timeout, rate limit, parse error), we
       conservatively engage the council — paying for what the
       original code always paid for, never silently degrading.

Caveat (must be acknowledged in the doc):
    The proposed-changes doc said this layer reduces cost "with no
    quality loss assuming the eval framework confirms the routing
    boundary is sound." The eval framework is in FUTURE_SCOPE.md, not
    in this branch. Until the eval framework runs against the routing
    decision, this module is a *cost hypothesis*, not a measured
    optimization. Treat the routing prompt as adjustable until eval
    data exists.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any, Dict

from .config import ROUTING_MODEL
from .openrouter import query_model

log = logging.getLogger(__name__)


# Heuristic gates. Tune after eval data exists.
SHORT_QUERY_THRESHOLD_CHARS = 30
LONG_QUERY_THRESHOLD_CHARS = 1000


@dataclass(frozen=True)
class RoutingDecision:
    """Result of a routing decision.

    Attributes:
        use_council: True => run full pipeline. False => chairman alone.
        reason: Stable string explaining why; appears in SSE events,
            structured logs, and persisted metadata for A/B analysis.
        classifier_used: Whether the LLM classifier was invoked. False
            means a heuristic gate decided. Useful for cost accounting.
        classifier_output: Raw classifier output (truncated). None if
            the classifier wasn't called.
    """
    use_council: bool
    reason: str
    classifier_used: bool
    classifier_output: str | None = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# The classifier prompt is intentionally short (low input-token cost)
# and demands a one-word output (low output-token cost).
CLASSIFIER_PROMPT = """\
Classify the following user query into exactly ONE of two categories:

- FACTUAL_SIMPLE: the query has a single objectively-correct answer that any \
capable LLM can produce. Examples: arithmetic, factual lookup, simple \
definition, basic conversion, who/what/when questions with definite answers.

- COMPLEX_OR_SUBJECTIVE: the query benefits from multiple perspectives or \
involves judgment. Examples: recommendations, open-ended analysis, design \
decisions, technical advice with trade-offs, creative work, opinion requests.

Respond with ONLY one word: FACTUAL_SIMPLE or COMPLEX_OR_SUBJECTIVE.

Query: {query}

Classification:"""


async def should_engage_council(query: str) -> RoutingDecision:
    """Decide whether ``query`` should engage the full council.

    Network-bounded (one cheap LLM call in the worst case). Returns a
    ``RoutingDecision`` rather than raising — callers branch on
    ``use_council`` and persist the full decision for analysis.
    """
    text = query.strip() if query else ""

    # Heuristic gate 1: very short — skip council, no classifier call.
    if len(text) < SHORT_QUERY_THRESHOLD_CHARS:
        decision = RoutingDecision(
            use_council=False,
            reason=f"short_query<{SHORT_QUERY_THRESHOLD_CHARS}",
            classifier_used=False,
        )
        log.info("router.decision", extra=decision.to_dict())
        return decision

    # Heuristic gate 2: very long — engage council without paying for
    # a classifier on a giant prompt.
    if len(text) > LONG_QUERY_THRESHOLD_CHARS:
        decision = RoutingDecision(
            use_council=True,
            reason=f"long_query>{LONG_QUERY_THRESHOLD_CHARS}",
            classifier_used=False,
        )
        log.info("router.decision", extra=decision.to_dict())
        return decision

    # Run the classifier.
    content, _usage, err = await query_model(
        ROUTING_MODEL,
        [{"role": "user", "content": CLASSIFIER_PROMPT.format(query=text)}],
        timeout=8.0,
        max_tokens=8,
        max_retries=0,  # classifier is best-effort; don't retry on failure
    )

    if err is not None:
        # Conservative: engage the council on any classifier failure.
        # Never silently degrade quality to save cost.
        decision = RoutingDecision(
            use_council=True,
            reason=f"classifier_error:{err.kind.value}",
            classifier_used=True,
            classifier_output=None,
        )
        log.warning("router.classifier_failed", extra=decision.to_dict())
        return decision

    output = (content or "").strip().upper()
    output_truncated = output[:32]

    if "FACTUAL_SIMPLE" in output:
        decision = RoutingDecision(
            use_council=False,
            reason="classified_factual_simple",
            classifier_used=True,
            classifier_output=output_truncated,
        )
    elif "COMPLEX" in output:
        decision = RoutingDecision(
            use_council=True,
            reason="classified_complex_or_subjective",
            classifier_used=True,
            classifier_output=output_truncated,
        )
    else:
        # Classifier returned something we didn't expect — engage council.
        decision = RoutingDecision(
            use_council=True,
            reason="classifier_unparseable_output",
            classifier_used=True,
            classifier_output=output_truncated,
        )

    log.info("router.decision", extra=decision.to_dict())
    return decision


__all__ = [
    "RoutingDecision",
    "should_engage_council",
    "SHORT_QUERY_THRESHOLD_CHARS",
    "LONG_QUERY_THRESHOLD_CHARS",
]
