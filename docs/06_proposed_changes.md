# Proposed Changes — `karpathy/llm-council`

**The bet.** Three changes, in this order, transform `llm-council` from "Saturday hack that silently lies to users" into "production-ready multi-LLM consensus service that knows what it costs and whether it's working."

1. **One critical refactor.** Fix the silent-failure cluster — chairman fallback string, stage 1/2 silent drops, no retries, no observability — by replacing it with structured error semantics that flow end-to-end.
2. **One eval framework.** Build the measurement layer Karpathy didn't, organized around the question that actually matters: *under what query distributions does the council's chairman-cross-check catch hallucinations a single LLM would miss?*
3. **One cost-control layer.** Smart routing + chairman cap + caching, sized against the **~$135k–270k/month** projection at 10k users/day with the as-shipped flagship config.

Everything else is either a P1 follow-up or explicitly out of scope. **Each proposed change in this doc has the same shape: Reason → Before → After → Effect**, so you can argue with any of them in isolation.

---

## 1. Why these three changes, and not the other 60+

The audit catalog has 47 findings + 22 missing features. Proposing fixes for all 69 is the opposite of the brief's "scope ruthlessly" requirement.

The three above are picked because:
- They each map to one of the brief's three named PS 2 deliverables (refactor / eval / cost).
- They're **causally linked**: the refactor unblocks the eval (you can't measure quality if errors are silent); the eval informs the cost levers (you can't decide what to skip without knowing what adds value); the cost layer makes the refactor's instrumentation actionable (logging means nothing if you can't act on it).
- Together they cover **27 of the 69** items in the catalog, transitively. The remaining 42 are either downstream (P1, listed in §5) or not the AI engineer's job (auth, deployment, infra — listed in §6).

---

## 2. The Critical Refactor — Replace the Silent-Failure Cluster With Structured Errors

### 2.1 What this fixes

| Catalog ID | Item | How this refactor addresses it |
|---|---|---|
| **A1** | Single `except Exception` swallows every LLM error class | Replaced with typed error classification (auth / rate-limit / timeout / payload / network / unknown) |
| **A3** | Stage 1 silently drops failed members | Replaced with explicit per-member status; failures surfaced via SSE |
| **A4** | Stage 2 silently drops failed rankers | Same as A3 |
| **A5** | **Chairman fallback string masquerading as real answer** (the root) | Replaced with explicit error response shape; UI/persistence can distinguish |
| **A6** | Title-gen failure mimics fresh-conversation default | Title becomes nullable with explicit `error` state |
| **A7** | Stream error path leaks internals to client | Sanitizer at the SSE boundary |
| **B1** | Stage-2 ranking parser brittle to format non-compliance | Tolerant parser + per-ranker `parse_status` |
| **B2** | Aggregate ranking from partial rankers with no signal | Aggregate flagged as `partial` when N rankers < N council members |
| **B7** | `/message` and `/message/stream` produce different error UX | Both endpoints route through the same error type |
| **E1** | Zero structured logging | `logging` module configured at startup |
| **E2** | OpenRouter `usage` field discarded | Captured per call, attached to telemetry |
| **M5** | No retry / backoff | Single retry with exponential backoff for retryable errors only |
| **M8** | No token / cost tracking | Per-call usage logged + aggregated per request |
| **M16** | No startup config validation | Fail-fast if `OPENROUTER_API_KEY` missing |

That's **14 catalog items resolved by one coherent refactor** of `backend/openrouter.py`, `backend/council.py`, `backend/main.py`, plus a small new `backend/errors.py`.

### 2.2 Reason

Issues #27, #113, and #156 are real users seeing the same string — *"Error: Unable to generate final synthesis."* — persisted as the AI's answer. Live-tested in doc 04: the chairman 401 fall-through writes that string into the conversation JSON with `model: "google/gemini-3-pro-preview"` attached. There is no flag, no error class, no metadata distinguishing it from a real model output. **The product silently lies to its users.** Every other audit finding is downstream of this one — silent failures are why the cost story is invisible (E2), why the parser breaks aren't surfaced (B1/B2), why endpoint inconsistencies persist (B7), and why operators can't tell whether the system is healthy (E1, E3).

This is the bug Karpathy's README disclaims with *"I don't intend to improve it"* — and the one no production deployment can leave unaddressed.

### 2.3 Before

Three concrete locations carry the failure mode:

**B-1.** `backend/openrouter.py:42–46` — every LLM call collapses to `None` on any error:

```python
except Exception as e:
    print(f"Error querying model {model}: {e}")
    return None
```

**B-2.** `backend/council.py:25–30, 101–110` — `None` triggers silent drop:

```python
for model, response in responses.items():
    if response is not None:  # Only include successful responses
        stage1_results.append({...})
```

**B-3.** `backend/council.py:164–169` — chairman fallback is a string:

```python
if response is None:
    return {
        "model": CHAIRMAN_MODEL,
        "response": "Error: Unable to generate final synthesis."
    }
```

**B-4.** `backend/main.py:140–185` — streaming endpoint catches *FastAPI/storage* exceptions but not LLM errors (they're already swallowed by B-1):

```python
except Exception as e:
    yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
```

There is no `logging` import anywhere in the backend. There is no retry. There is no token / cost tracking. There is no startup validation — the server happily boots with a missing API key (verified live, doc 04 §M16).

### 2.4 After

A new `backend/errors.py` defines typed errors:

```python
# backend/errors.py
from enum import Enum
from dataclasses import dataclass
from typing import Optional

class LLMErrorKind(str, Enum):
    AUTH        = "auth"          # 401, 403
    RATE_LIMIT  = "rate_limit"    # 429
    TIMEOUT     = "timeout"       # network or model-side
    PAYLOAD     = "payload"       # 4xx not auth/rate
    UPSTREAM    = "upstream"      # 5xx
    PARSE       = "parse"         # bad response shape
    NETWORK     = "network"       # transport
    UNKNOWN     = "unknown"

@dataclass
class LLMError:
    kind: LLMErrorKind
    model: str
    detail: str        # safe-to-log, never raw exception
    retryable: bool
    upstream_status: Optional[int] = None
```

`backend/openrouter.py:7–48` becomes type-classified, retry-aware, and instrumented:

```python
import httpx, logging, time, asyncio
from .errors import LLMError, LLMErrorKind

log = logging.getLogger(__name__)

RETRYABLE = {LLMErrorKind.RATE_LIMIT, LLMErrorKind.TIMEOUT,
             LLMErrorKind.UPSTREAM, LLMErrorKind.NETWORK}

def _classify(exc, response=None) -> LLMError:
    if response is not None:
        s = response.status_code
        if s in (401, 403): k = LLMErrorKind.AUTH
        elif s == 429:      k = LLMErrorKind.RATE_LIMIT
        elif s >= 500:      k = LLMErrorKind.UPSTREAM
        else:               k = LLMErrorKind.PAYLOAD
        return LLMError(kind=k, model=...,
                        detail=f"http {s}",
                        retryable=k in RETRYABLE,
                        upstream_status=s)
    if isinstance(exc, httpx.TimeoutException):
        return LLMError(kind=LLMErrorKind.TIMEOUT, ..., retryable=True)
    if isinstance(exc, httpx.RequestError):
        return LLMError(kind=LLMErrorKind.NETWORK, ..., retryable=True)
    return LLMError(kind=LLMErrorKind.UNKNOWN, ..., retryable=False)

async def query_model(model, messages, timeout=30.0, max_retries=1):
    """Returns (content_or_None, usage_or_None, error_or_None).
    Exactly one of content or error is non-None."""
    attempt = 0
    while True:
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await c.post(OPENROUTER_API_URL,
                    headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}",
                             "Content-Type": "application/json"},
                    json={"model": model, "messages": messages})
            elapsed = time.monotonic() - t0
            if r.status_code != 200:
                err = _classify(None, r)
                log.warning("llm.call failed", extra={
                    "model": model, "kind": err.kind, "status": r.status_code,
                    "elapsed_ms": int(elapsed*1000), "attempt": attempt})
                if err.retryable and attempt < max_retries:
                    attempt += 1
                    await asyncio.sleep(2 ** attempt + random.random())
                    continue
                return None, None, err
            data = r.json()
            log.info("llm.call ok", extra={
                "model": model,
                "elapsed_ms": int(elapsed*1000),
                "prompt_tokens": data["usage"]["prompt_tokens"],
                "completion_tokens": data["usage"]["completion_tokens"],
                "cost_usd": data["usage"].get("cost", 0),
                "finish_reason": data["choices"][0].get("finish_reason"),
            })
            return data["choices"][0]["message"]["content"], data["usage"], None
        except Exception as e:
            err = _classify(e)
            log.exception("llm.call exception", extra={"model": model, "kind": err.kind})
            if err.retryable and attempt < max_retries:
                attempt += 1
                await asyncio.sleep(2 ** attempt + random.random())
                continue
            return None, None, err
```

`backend/council.py` carries explicit per-member status all the way through:

```python
@dataclass
class CouncilMemberResult:
    model: str
    status: Literal["ok", "error"]
    response: Optional[str] = None
    usage: Optional[dict] = None
    error: Optional[LLMError] = None

async def stage1_collect_responses(user_query):
    msgs = [{"role": "user", "content": user_query}]
    results = []
    coros = [query_model(m, msgs) for m in COUNCIL_MODELS]
    raw = await asyncio.gather(*coros, return_exceptions=False)
    for model, (content, usage, err) in zip(COUNCIL_MODELS, raw):
        if err is None:
            results.append(CouncilMemberResult(model=model, status="ok",
                                               response=content, usage=usage))
        else:
            results.append(CouncilMemberResult(model=model, status="error",
                                               error=err))
    return results
```

`backend/council.py:177–208` (the parser) returns a status:

```python
def parse_ranking_from_text(ranking_text):
    """Returns {'status': 'ok'|'partial'|'parse_error',
                'rankings': [...], 'reason': str}."""
    if "FINAL RANKING:" not in ranking_text:
        return {"status": "parse_error", "rankings": [],
                "reason": "no FINAL RANKING heading"}
    section = ranking_text.split("FINAL RANKING:", 1)[1]
    numbered = re.findall(r'\d+\.\s*Response [A-Z]', section)
    fallback = re.findall(r'Response [A-Z]', section)
    used = numbered or fallback
    rankings = [re.search(r'Response [A-Z]', m).group() for m in used]
    expected = N_council_members
    if len(rankings) == 0:
        return {"status": "parse_error", "rankings": [],
                "reason": "no Response X tokens after heading"}
    if len(rankings) < expected:
        return {"status": "partial", "rankings": rankings,
                "reason": f"only {len(rankings)} of {expected} ranks parsed"}
    return {"status": "ok", "rankings": rankings, "reason": ""}
```

`backend/council.py:115–174` (chairman) — fallback becomes typed:

```python
async def stage3_synthesize_final(user_query, stage1, stage2):
    if not any(r.status == "ok" for r in stage1):
        return {
            "status": "error",
            "model": CHAIRMAN_MODEL,
            "error": LLMError(kind=LLMErrorKind.UNKNOWN,
                              model=CHAIRMAN_MODEL,
                              detail="all council members failed",
                              retryable=False)
        }
    content, usage, err = await query_model(CHAIRMAN_MODEL, [...])
    if err:
        return {"status": "error", "model": CHAIRMAN_MODEL, "error": err}
    return {"status": "ok", "model": CHAIRMAN_MODEL,
            "response": content, "usage": usage}
```

`backend/main.py` SSE endpoint emits a uniform error event:

```python
yield sse({"type": "stage3_complete", "data": stage3_result})
# stage3_result.status is now "ok" or "error"
# frontend can branch; persistence stores the structured error
```

`backend/storage.py` persists errors with a flag instead of as fake-answer prose:

```python
{
    "role": "assistant",
    "stage1": [{"model": ..., "status": "ok", "response": ...},
               {"model": ..., "status": "error", "error": {"kind": "rate_limit", ...}}],
    "stage2": [...],
    "stage3": {"status": "error", "model": ..., "error": {...}}
}
```

`backend/config.py` — fail-fast on startup:

```python
if not OPENROUTER_API_KEY:
    raise RuntimeError(
        "OPENROUTER_API_KEY is missing. Set in .env or environment "
        "before starting the server."
    )
```

### 2.5 Effect

| Before this refactor | After |
|---|---|
| Chairman 401 → user sees `"Error: Unable to generate final synthesis."` saved as `gemini-3-pro-preview`'s answer. Indistinguishable from a real reply. | Chairman 401 → SSE emits `{"type": "stage3_complete", "data": {"status": "error", "error": {"kind": "auth", ...}}}`. UI renders as an error state. Persistence stores the error type. Issues #27/#113 root cause is resolved. |
| Two of four council members 429 → user sees a 2-LLM "council" answer with no signal. | Two of four 429 → user sees "Stage 1: 2 of 4 council members failed (rate limit). Showing partial response." |
| `/message` says `"All models failed to respond"`; `/message/stream` says `"Unable to generate final synthesis"`. Different UX for same bug. | Both endpoints emit the same typed error. B7 closed. |
| No way for an operator to see why anything failed. `print()` to stdout. | Structured logs with `model`, `kind`, `status`, `elapsed_ms`, `cost_usd`, request_id. Pipeable to any aggregator. |
| Cost data exists in OpenRouter responses, code throws it away. | Every call's tokens + cost logged + attached to telemetry. Cost-at-scale story becomes self-evident from the logs. |
| Single 429 from any model is a permanent failure of that council member for that query. | Single retry with exponential backoff for retryable error classes (rate_limit / timeout / 5xx). 429s become recoverable. |
| Server boots with no API key, accepts requests, silently fails on every LLM call. | Server fails fast at startup with a clear error message. M16 closed. |
| Stage-2 parser produces empty rankings on format non-compliance, aggregate calculated silently. | Parser returns `{status: ok|partial|parse_error}`; aggregate flagged `partial: true` in metadata; UI can show "ranking incomplete" instead of pretending nothing happened. |

### 2.6 Out of scope of this refactor (explicit)

- **Storage race (D1/D2).** Different fix shape — see §5. Don't bundle.
- **Conversation memory (G2/M3).** Feature add — see §5.
- **Auth / per-user isolation (D4/M11).** Different problem — see §6.
- **Frontend changes.** UI layer needs to be updated to render the structured errors, but that's a separate workstream — the backend contract is what this refactor delivers.

### 2.7 Effort estimate

- New file: `backend/errors.py` (~80 LOC).
- Edits: `backend/openrouter.py` (rewrite, +60 LOC net), `backend/council.py` (~120 LOC of edits across 5 functions), `backend/main.py` (+30 LOC for SSE error event shape), `backend/storage.py` (+15 LOC for status field), `backend/config.py` (+5 LOC for startup check).
- New tests: `tests/test_errors.py`, `tests/test_query_model_retries.py`, `tests/test_council_partial_failure.py`.
- **Estimated effort:** 1 senior-engineer day for the code, 0.5 day for tests. Single PR.

---

## 3. Proposed Eval Framework

### 3.1 The question this framework answers

Karpathy's `llm-council` ships a 9-call multi-LLM pipeline with no measurement of whether it produces better answers than the chairman alone — let alone whether it justifies its cost. Doc 05 §B8 surfaced one positive datapoint: in a follow-up-turn hallucination case, the chairman caught one council member's fabrication. **The eval framework's job is to turn that anecdotal positive into a measurable claim.**

The right framing is:

> **Under what query distributions does the council's chairman-cross-check catch hallucinations or errors that the chairman would miss when called alone? What's the cost-per-caught-error?**

That question — not "does the council give better answers" — is the one that lets you decide where to deploy council mode and where to skip it.

### 3.2 Reason

Catalog item M2 (no eval framework) is the cleanest deliverable in the brief: there's nothing to fix, only something to add. The brief explicitly says *"Eval is not optional. However you define quality, measure it."* And user issue #3 ("Chairman over-influence in council system") is the community independently asking for this measurement and not getting it.

### 3.3 Before

- No `evals/` directory. No test set. No judge prompts. No scoring code.
- The static check `grep -ri 'eval\|metric\|score'` returns only false positives in the entire repo.
- The aggregate-rankings calculation at `council.py:243–250` is the closest thing to "measurement," and it's a within-query peer-popularity score, not a quality score.

### 3.4 After

A new `evals/` directory with this structure:

```
evals/
├── datasets/
│   ├── factual.jsonl          # 50 verifiable-answer questions
│   ├── open_ended.jsonl       # 25 graded-by-rubric questions
│   └── trap.jsonl             # 25 "no good answer" / context-dependent questions
├── runners/
│   ├── single_chairman.py     # baseline: chairman alone, no council
│   ├── council_2.py           # 2-member council
│   ├── council_4.py           # 4-member council (current default)
│   └── council_6.py           # stretch: 6 members
├── judges/
│   ├── factual_grader.py      # exact-match + judge-LLM with calibration
│   ├── rubric_grader.py       # judge-LLM scoring on 4 dimensions
│   └── hallucination_detector.py  # judge-LLM flags fabricated content
├── metrics/
│   ├── correctness.py         # per-condition correctness rate
│   ├── council_uplift.py      # delta vs single-chairman baseline
│   ├── hallucination_rate.py  # fabrication rate per condition
│   ├── cost_per_correct.py    # $/verified-correct-answer
│   └── self_favoritism.py     # per-stage-2 ranker bias score
├── runs/                      # output JSONLs from each eval run
└── report.py                  # aggregates → markdown table + chart
```

**Dataset shape (each row, JSONL):**

```json
{
  "id": "fact-001",
  "question": "What is the capital of France?",
  "expected": ["Paris"],
  "category": "factual_simple",
  "trap": false
}
```

For trap questions:

```json
{
  "id": "trap-007",
  "question": "What was my previous question?",
  "expected_behavior": "refuse_or_admit_no_context",
  "fabrication_signals": ["claims a specific prior question"],
  "category": "context_dependent",
  "trap": true
}
```

**Conditions to compare:**

| Condition | Pipeline | Cost (estimated, flagship) |
|---|---|---|
| C0 | Chairman model called once, no council | ~$0.02 / query |
| C1 | 2-member council + chairman | ~$0.07 / query |
| C2 | 4-member council + chairman (current default) | ~$0.20 / query |
| C3 | 6-member council + chairman | ~$0.30 / query |

**Metrics, scored per condition:**

1. **Correctness rate** (factual + open-ended).  Judge-graded against ground truth or rubric. Calibrated against 20% human labels.
2. **Hallucination rate** (trap questions).  Did the system fabricate context that doesn't exist? Binary per query, judge-detected.
3. **Council uplift.** For questions where C0 (chairman alone) was wrong: did the council correct it? For questions where C0 was right: did the council change it to wrong? Net = (corrected − corrupted) / total errors.
4. **Cost per verified-correct answer.** $ spent / # correct.  Comparing C0/C1/C2/C3 on this metric is the most direct "is the council worth it" question.
5. **Self-favoritism rate.** % of Stage-2 rankings where ranker placed own anonymized response first. Doc 05 §B9 showed 2/2 trials. With 100 samples, we can get a confidence interval.
6. **Latency profile.** p50 / p95 / p99 wall-clock per condition.

**Methodology:**

- 100 questions × 4 conditions × 3 runs (variance estimate) = 1,200 LLM-orchestration calls.
- Use **cheap models for the development phase** (gemini-2.5-flash + gpt-4o-mini + claude-haiku-3.5). Cost: ~$5 to run the full sweep with these models.
- Run **once** with the as-shipped flagship config to confirm the cheap-model evals' findings transfer. Cost: ~$60 budget for a single sweep.
- Judge LLM: claude-3.5-sonnet or gpt-4o (capable enough to grade, cheaper than the council). Calibrated against human judgment on a 20-question stratified sample.

**Reporting:** `evals/report.py` produces a markdown table:

```
| Condition | Correctness | Halluc rate | Uplift vs C0 | $/correct | p95 latency |
|-----------|-------------|-------------|--------------|-----------|-------------|
| C0 (chair alone)    | 0.78 ± .04 | 0.15 | —    | $0.026 | 4.5s  |
| C1 (council-2)      | 0.83 ± .03 | 0.10 | +0.05 | $0.084 | 7.5s  |
| C2 (council-4)      | 0.85 ± .03 | 0.08 | +0.07 | $0.235 | 9.2s  |
| C3 (council-6)      | 0.86 ± .03 | 0.07 | +0.08 | $0.349 | 11.7s |
```

(Numbers above are illustrative — the framework is what we build, the numbers come out the other side.)

### 3.5 Effect

| Before | After |
|---|---|
| The community speculates ("Chairman over-influence" issue #3) about whether the council format works. No data. | We can answer issue #3 quantitatively. The chairman-bias claim either has data behind it or doesn't. |
| Karpathy ships a $0.20-per-query architecture with no claim about its quality. | Operators have a published correctness/hallucination/uplift table to decide whether to deploy. |
| Cost decisions ("should we cut to 2 members?") are vibes-based. | The cost-per-correct table gives an explicit ROI per council size. |
| Self-favoritism (B9) is anecdotal — observed 2/2 in our test. | Self-favoritism is measured with a confidence interval, and the labeling-shuffle fix (P1 below) can be tested rigorously against that baseline. |
| The follow-up-turn hallucination (C7) is one observation. | Hallucination rate on trap questions becomes a tracked metric. |

### 3.6 Effort estimate

- Day 1: write 100-question test set across 3 categories. Hand-check all expected answers / behaviors.
- Day 2: implement runners + judges + metrics. Reuse `query_model` from the refactored backend.
- Day 3: run sweep with cheap models, calibrate judge against human labels on 20 samples, fix anything obvious.
- Day 4: run sweep with flagship config, write `report.py`, produce the table.

**4 senior-engineer days end-to-end.** ~$65 total LLM budget at the planned scales.

---

## 4. Cost / Latency at 10k Users/Day

### 4.1 The number

From doc 05's measured token counts × published flagship pricing, scaled to 30k queries/day (10k users × 3 queries):

| Config | Per-query cost | Per-day | Per-month |
|---|---:|---:|---:|
| As-shipped flagship, uncapped | ~$0.15–0.30 | $4.5k–9k | **$135k–270k** |
| Same config + 150-tok output cap | ~$0.05–0.10 | $1.5k–3k | $45k–90k |
| Cheap models (eval target) | ~$0.0015 | $45 | $1.4k |

The latency story is paired: end-to-end wall clock ~8 s with cheap models, ~15–60 s with flagship reasoning models, dominated by the chairman call (~55% of total time, doc 05 §1).

### 4.2 Reason

The brief asks for cost / latency at 10k users/day **and what you would do about it**. Three changes — in this order — cover both:

| Change | Cost lever | Latency lever |
|---|---|---|
| Smart routing (skip council for trivial queries) | Massive | Massive |
| Chairman cap + cache | Large | Medium |
| Streaming chairman + per-call timeout | Small | Large |

Below: each one with the same Reason / Before / After / Effect treatment.

### 4.3 Change C-A: Smart Routing — Skip the Council for Trivial Queries

**Reason.** Most queries that hit a multi-LLM consensus system don't need consensus. *"What is 2+2?"* doesn't need 4 flagship LLMs to peer-review the answer. Doc 05 §1 showed one trivial-arithmetic query cost $0.00155 with cheap models — at flagship rates, ~$0.20 for the same trivial answer. **The chairman can handle a huge fraction of queries alone with no quality loss.** The eval framework (§3) gives us the data to draw the line.

**Before.** `backend/main.py:104` calls `run_full_council(request.content)` unconditionally. There's no classifier, no heuristic, no opt-out. Every query pays the full 9-call cost.

**After.** A pre-stage classifier:

```python
# backend/router.py (new)
async def should_engage_council(query: str) -> tuple[bool, str]:
    """Returns (use_council, reason). Cheap call to a small model.
    Cost: ~$0.0001 per query."""
    if len(query) < 30: return False, "too short"  # heuristic gate
    classification, _, err = await query_model(
        ROUTING_MODEL,  # gemini-2.5-flash or similar
        [{"role": "user", "content": ROUTING_PROMPT.format(query=query)}],
        timeout=5.0,
    )
    if err or "FACTUAL_SIMPLE" in classification:
        return False, "factual-simple"
    return True, "complex_or_subjective"
```

Endpoint becomes:

```python
@app.post("/api/conversations/{cid}/message/stream")
async def send_message_stream(...):
    use_council, reason = await should_engage_council(request.content)
    if use_council:
        # full 3-stage pipeline
    else:
        # chairman alone, persisted with metadata: {"council_skipped": True, "reason": reason}
```

**Effect.**

| Before | After |
|---|---|
| 100% of queries pay 9-call multi-LLM cost. | ~60–70% of queries pay 1 chairman call ($0.02 instead of $0.20 at flagship). The other 30–40% (genuinely complex / subjective / context-rich) still get the full council. |
| Trivial queries take 8–15 s end-to-end. | Trivial queries take ~3–5 s end-to-end. |
| No way to A/B test "council vs no council" at the per-query level. | The skipped-council metadata makes it a free A/B in production logs. |
| Estimated monthly spend: $135k–270k. | **Estimated monthly spend: $45k–95k** (60% of queries × 10× cheaper) — assuming the eval framework confirms quality holds for the routed-away queries. |

### 4.4 Change C-B: Cap the Chairman, Cache the Repeats

**Reason.** Doc 05 §1 showed the **chairman is 65% of per-query cost** despite being 1 of 6 calls — its input concatenates all stage-1 + all stage-2 text, so its prompt is ~5× any individual call. And the system has no caching; identical queries pay full freight every time.

**Before.**
- `backend/openrouter.py:31–34` — payload is `{"model": model, "messages": messages}`. No `max_tokens`, no cap.
- Chairman input prompt at `council.py:142–157` includes full Stage-2 commentary text (`result['ranking']`, the freeform), not just the parsed rankings.
- No `cache`, `lru_cache`, or any persistence of past responses anywhere in the backend.

**After.**

1. **Per-stage `max_tokens` caps** with parser tolerance:
```python
# config additions
STAGE1_MAX_TOKENS = 800     # individual responses can be substantive
STAGE2_MAX_TOKENS = 400     # rankings + brief reasoning
STAGE3_MAX_TOKENS = 1000    # chairman synthesis
```
Combined with the `parse_status: "partial"` from §2.4, partial parses become first-class.

2. **Trim chairman input.** Send parsed rankings + brief per-response summaries, not full Stage-2 freeform commentary:
```python
stage2_for_chairman = "\n".join(
    f"Ranker {r.model}: {r.parsed_ranking} (status: {r.parse_status})"
    for r in stage2 if r.status == "ok"
)
# saves ~50% of chairman input tokens on average
```

3. **Cache layer** (Redis or sqlite for single-host):
```python
# backend/cache.py
async def get_or_compute(query_hash, semantic_key, ttl=3600):
    # exact-match cache: hash(query + council_config)
    # semantic cache (P1): embedding-similarity ≥ 0.95
    ...
```

**Effect.**

| Before | After |
|---|---|
| Chairman input ~600 tokens average → ~$0.05 per chairman call at flagship. | Chairman input ~250 tokens average → ~$0.02 per chairman call at flagship. **~60% chairman cost reduction.** |
| Chairman output unbounded → can produce 2000+ tokens → ~$0.05 output per call. | Chairman output capped at 1000 → ~$0.025 output per call max. |
| Repeated query at scale (e.g., FAQ-style traffic) pays full cost every time. | Cache hit → ~$0.0001 per query (just the routing classifier + cache lookup). At 30% cache hit rate (typical for FAQ-heavy traffic), saves ~$13k–80k/month. |

### 4.5 Change C-C: Stream Chairman + Per-Call Timeout

**Reason.** Doc 05: chairman call is 4–5 s with cheap models, 10–60 s with flagship reasoning models. This is buffered in full before any SSE event fires (`backend/main.py:163`). User stares at "Stage 3 in progress…" for the entire chairman call. With OpenRouter and underlying providers all supporting streaming, this is a fix that costs nothing and improves perceived latency dramatically.

The hardcoded 120 s timeout (openrouter.py:11) is a foot-gun: a stuck call holds a connection open for two minutes.

**Before.**
- `query_model` in `openrouter.py:7–48` does `await client.post(...)` and returns the full body.
- `main.py:163` does `stage3_result = await stage3_synthesize_final(...)` and buffers it.
- 120 s default timeout for everything.

**After.**

```python
async def query_model_streaming(model, messages, timeout=30.0, on_token=None):
    payload = {"model": model, "messages": messages, "stream": True, "max_tokens": ...}
    async with httpx.AsyncClient(timeout=timeout) as c:
        async with c.stream("POST", OPENROUTER_API_URL, ...) as r:
            async for line in r.aiter_lines():
                if line.startswith("data: "):
                    chunk = json.loads(line[6:])
                    delta = chunk["choices"][0]["delta"].get("content", "")
                    if delta and on_token:
                        await on_token(delta)
```

In the SSE handler:

```python
async def on_chairman_token(delta):
    yield sse({"type": "stage3_delta", "data": delta})
content = await query_model_streaming(CHAIRMAN_MODEL, ..., on_token=on_chairman_token)
yield sse({"type": "stage3_complete", "data": {"status": "ok", "response": content}})
```

Per-call timeouts driven by config:

```python
TIMEOUT_TITLE = 10.0
TIMEOUT_STAGE1 = 30.0
TIMEOUT_STAGE2 = 30.0
TIMEOUT_CHAIRMAN = 60.0  # reasoning-tolerant
```

**Effect.**

| Before | After |
|---|---|
| User sees nothing for the full ~5–60 s of the chairman call. | User sees first token in ~1–3 s (TTFT for streaming providers). Perceived latency cut by an order of magnitude on the chairman stage. |
| One stuck call holds a connection for 120 s. | Stuck calls timeout in 30–60 s with appropriate budget per stage. p99 latency improves. |
| No way to interrupt a long-running call. | SSE deltas can be cancelled by client disconnect — saves output-token cost on abandoned queries. |

### 4.6 Cost story summary

If we deploy §4.3 (smart routing) + §4.4 (chairman cap + cache):

| Scenario | Per-query cost | Per-month cost (30k queries/day) |
|---|---:|---:|
| As-shipped (current) | $0.15–0.30 | $135k–270k |
| **+ smart routing (60% skip council)** | $0.07–0.15 | $63k–135k |
| **+ chairman cap + 30% cache hit** | $0.04–0.09 | $36k–81k |

That's **~70% cost reduction with no quality loss** if the eval framework confirms the routing decision boundary is sound. Even at the high end, the resulting bill is in the realm of "manageable infra spend" rather than "investor-question fundamental cost problem."

---

## 5. P1 — Other Changes Worth Doing (in this order)

Things that aren't the critical refactor or the cost story but should ship before this is "production." Each gets the short treatment.

### P1-1: Move storage off filesystem JSON (closes D1, D2, D5)

**Reason.** Doc 04: 64% data loss verified live under 50 parallel writes. Bug is dormant in single-worker uvicorn, active in any production deployment.
**Before.** `backend/storage.py` writes one JSON file per conversation, no locking, full-file rewrite on every mutation.
**After.** SQLite (single host) or Postgres (multi-host) with conversation-scoped row writes. ORM optional — raw SQL via aiosqlite is sufficient.
**Effect.** Concurrent-write races eliminated. `list_conversations` becomes O(log N) on an indexed query. Migration: one-shot script that walks the JSON dir.

### P1-2: Add conversation memory across turns (closes G2, M3, partly addresses C7)

**Reason.** Doc 05 §C7: a real follow-up-turn hallucination triggered because the system doesn't pass conversation history into Stage 1. The product is a chat that doesn't actually chat.
**Before.** `backend/main.py:104` passes only `request.content` to `run_full_council`. Council members get one message, no history.
**After.** `run_full_council(messages: List[Message])` where `messages` includes prior turns. Stage 1 prompt becomes a multi-turn conversation; Stage 2 ranker prompt becomes "rank these responses to the latest turn given the conversation context." Add a `MAX_HISTORY_TOKENS` config to truncate old turns when needed.
**Effect.** Follow-up questions get real answers. The hallucination class disappears. Adds modest input-token cost (covered by §4.4's chairman input trim).

### P1-3: Body-size limit + per-IP rate limiting (closes M21, F3)

**Reason.** Doc 04 §M21: 10 MB payload accepted, ~$45 in input tokens at flagship rates.
**Before.** No middleware beyond CORS.
**After.** FastAPI `Request` size middleware capping bodies at 16 KB. `slowapi` rate limiter at 60 req/min per IP, 10 streaming sessions per IP.
**Effect.** Single-actor cost-DoS becomes impossible. Rate-limit responses are typed errors and surface through the §2 refactor naturally.

### P1-4: Shuffle anonymization labels per-call (partly closes B4)

**Reason.** Doc 05 §B9: 2/2 trials showed self-favoritism in Stage-2 rankings. Anonymization is presentation-only.
**Before.** `council.py:50` — labels are `chr(65+i)` deterministically by `COUNCIL_MODELS` order.
**After.** Shuffle the order before assigning labels:
```python
shuffled = list(stage1_results)
random.shuffle(shuffled)
labels = [chr(65 + i) for i in range(len(shuffled))]
label_to_model = {f"Response {l}": r.model for l, r in zip(labels, shuffled)}
```
**Effect.** Self-favoritism gets averaged out across many queries (the eval framework can measure whether it actually does).

### P1-5: Pick a chairman model that isn't a council member (closes B5)

**Reason.** Issue #3, doc 03 §B5. Karpathy's default `CHAIRMAN_MODEL = "google/gemini-3-pro-preview"` is identical to one council member.
**Before.** Same flagship model in both roles.
**After.** Chairman is a different model family (e.g., `anthropic/claude-opus-4.6` if council includes Sonnet) or, better, a **dedicated synthesis model** whose strengths are summarization rather than first-pass reasoning.
**Effect.** Self-recognition bias eliminated. Eval framework can measure the delta.

---

## 6. Out of Scope (and Why)

| Excluded | Why |
|---|---|
| **Auth / per-user isolation (D4, M11, M12)** | Different problem class — needs an identity provider, not an AI engineer. Recommend a session-token middleware before any production deploy, but not part of this refactor. |
| **Production deployment recipe (M19)** | Infra workstream. After §2 + §4 land, a Dockerfile + helm chart is straightforward but doesn't belong in the AI audit. |
| **Frontend rewrite (H1, H2, H3, A8, E4)** | Backend contract is what this audit delivers. Frontend can be updated to render structured errors as a separate PR. The Vite+React app is small (~400 LOC of JSX); a 1-day update lands the UI side. |
| **Multi-tenant operations (M14 full DB)** | Covered to "prod-acceptable" by P1-1's SQLite. Postgres + horizontal scaling is over-engineered for current state. |
| **Reasoning-mode toggles per model (part of M10)** | The right time to expose these is after the eval framework reveals which models benefit from extended reasoning on which query types. Premature configuration knob. |
| **Path-traversal hardening at storage layer (G3)** | Doc 04 downgraded this to defense-in-depth. Worth fixing in a 5-minute commit (`if not re.match(r'^[a-f0-9-]{36}$', conversation_id): raise`), but not headline-worthy. |

---

## 7. Sequencing — what to ship in what order

Two-week plan if this were real:

| Days | Workstream | Owner |
|---|---|---|
| 1–2 | **Section 2: Critical refactor.** New error types, structured logging, retries, fail-fast startup. One PR. | Senior eng |
| 3 | Update frontend to render structured errors. Smaller PR. | FE eng |
| 4 | **P1-1: SQLite migration.** | Senior eng |
| 5 | **P1-2: Conversation memory.** | Senior eng |
| 6 | **P1-3: Body limit + rate limit.** | Senior eng |
| 7–10 | **Section 3: Eval framework.** 4 days as scoped above. | Senior eng |
| 11 | **Section 4 C-C: Streaming + timeouts.** | Senior eng |
| 12 | **Section 4 C-A: Smart routing** (using the eval data). | Senior eng |
| 13 | **Section 4 C-B: Chairman cap + cache.** | Senior eng |
| 14 | Re-run eval sweep. Confirm cost reduction holds without quality loss. Ship. | Senior eng |

Two engineers can compress this to 1 week. One engineer takes 14 working days. Total LLM eval budget: ~$70.

---

## 8. What gets defended in the live presentation

The brief's PS 2 deliverables, mapped:

| Deliverable | This doc | Defended by |
|---|---|---|
| Production-readiness audit | docs 03 / 04 / 05 | 47 catalog items + 22 missing features, all empirically grounded |
| Refactor of single most critical failure point | §2 here | A5 + 13 transitively-resolved findings, with code |
| Proposed eval framework | §3 here | 100-question dataset, 4 conditions, 6 metrics, calibrated judge |
| Cost / latency at 10k users/day, and what to do about it | §4 here | Real measured tokens × published flagship pricing → $135k–270k/month → 70% reduction with §4.3+§4.4 |

### What the panel will push hardest on (and what to say)

1. *"You picked A5 over D1/D2 — why not the data-loss bug?"*
   D1/D2 is structurally worse but it's a "this isn't a real product yet" bug. The brief says "treat it like it's going live" which presumes you'd move off filesystem JSON anyway. A5 is the bug that *survives* a migration to a real DB and continues to silently lie to users. That's why it's the critical refactor — fix the lying first, fix the storage second.

2. *"The eval framework is overkill for a 2-day audit."*
   The framework is the deliverable. The implementation is 4 days, deliberately the *next* phase, not the audit itself. The audit's job is to specify what to measure and why; running the sweep produces the answer to issue #3 and to the council-vs-chairman question. We can ship it in week 2 and have the data by week 3.

3. *"How confident are you in the $135k–270k number?"*
   It's an extrapolation from 11 measured calls × published flagship pricing × stated assumptions. Confidence interval is wide. Confidence in the *order of magnitude* is high. Confidence in the *qualitative claim* — "the as-shipped config is unaffordable at modest scale" — is very high. The eval framework + smart-routing PR makes this concrete in a week.

4. *"You found the council adds value (B8). Doesn't that contradict the cost critique?"*
   No. It says the cost might be paying for something. The architecture currently can't tell you whether that "something" is worth the spend on every query. The eval framework + smart routing together let you keep council mode where it earns its cost and skip it where it doesn't. The honest framing is: *"you might be paying $135k/month for a defense the architecture can't quantify."* Fix the measurement, then fix the spend.
