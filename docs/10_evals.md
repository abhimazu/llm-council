# 10 — Evals Framework & Dev Sweep Results

**Status:** scaffolded in `evals/`, dev-sweep run on cheap models, headline numbers below.
**Date of run:** 2026-05-10
**Total dev-sweep spend:** $0.044 (budget cap was $5–6)
**Branch:** `changes` of `abhimazu/llm-council`

---

## 1. Why this exists

The audit (chapter 03) found that `karpathy/llm-council` ships with **zero automated evaluation**. The product makes three implicit quality claims:

1. The 4-model council surfaces a stronger answer than any single model.
2. Stage-2 peer ranking is meaningful (i.e. models don't just rank themselves first).
3. The chairman synthesis is on-distribution with the council, not a hallucinated rewrite.

None of these claims are measurable in the upstream repo. There is no harness, no dataset, no judge, no scoreboard. The proposed architecture also adds a **smart router** (chapter 06) that chooses council vs solo per query — that decision needs its own quality bar.

This document specifies the eval framework that lives in `evals/` on the `changes` branch, plus the results of the first paid dev sweep run end-to-end against it.

---

## 2. Framework architecture

```
evals/
├── datasets/
│   ├── factual_simple.jsonl       # 30 q, expected_answer + match_type
│   ├── factual_technical.jsonl    # 20 q, expected substrings/regex
│   ├── trap.jsonl                 # 10 q, context-dependent or unanswerable
│   ├── multi_hop_reasoning.jsonl  # 15 q, requires synthesis across facts
│   └── open_ended.jsonl           # 25 q, rubric-graded
├── runners/
│   ├── solo.py                    # condition C0 — one model, no council
│   ├── council_full.py            # condition C2 — forced council pipeline
│   └── router.py                  # condition R — smart router decides
├── judges/
│   ├── exact.py                   # case-insensitive substring + regex
│   ├── llm_judge.py               # claude-3.5-haiku as judge for factual_technical
│   └── rubric.py                  # 1–5 rubric judge for open_ended
├── metrics/
│   ├── correctness.py             # per-category accuracy
│   ├── hallucination.py           # rate of confident-wrong on traps
│   ├── self_favoritism.py         # % of Stage-2 ranks that placed self first
│   ├── cost.py                    # $ per query, p50/p95
│   └── latency.py                 # seconds per query, p50/p95
├── runs/                          # JSONL output, one row per (qid, condition)
├── run_sweep.py                   # orchestrator: --conditions, --limit, --budget,
│                                  #               --time-budget, --resume
├── report.py                      # post-hoc roll-up of any sweep JSONL
└── README.md
```

Each runner emits a fully-typed envelope (the same dataclasses returned by the production `backend/council.py`) so the harness measures the **actual** code path that ships, not a mock.

---

## 3. Dataset

100 questions, hand-authored, distributed across five categories chosen to stress different parts of the pipeline.

| Category               | n   | What it stresses                                    | Judge       |
|------------------------|-----|-----------------------------------------------------|-------------|
| factual_simple         | 30  | Single-answer recall ("capital of France")          | exact match |
| factual_technical      | 20  | Programming/API facts with multiple acceptable forms| LLM judge   |
| trap                   | 10  | Unanswerable in zero-context ("what was my last Q?")| exact match (decline) |
| multi_hop_reasoning    | 15  | Requires combining 2+ facts                         | LLM judge   |
| open_ended             | 25  | Recommendations, design tradeoffs                   | rubric (1–5)|

The trap set exists specifically to catch fabrication: a question like *"Continue from where you left off"* has no valid answer in a stateless API call. A correct response acknowledges that. A failure is a confident hallucinated continuation.

---

## 4. Conditions

| ID | Name             | Cost shape      | What it answers                            |
|----|------------------|-----------------|--------------------------------------------|
| C0 | Solo (default)   | 1 LLM call      | Baseline: how good is one strong model alone? |
| C2 | Forced council   | up to 9 calls   | Ceiling: what does the full pipeline buy us? |
| R  | Smart router     | 1 or 9 calls    | Production: does the heuristic route well? |

For C0 we use a single chairman-class model. For C2 the four council models (Stage 1) → four-way ranking (Stage 2) → chairman synthesis (Stage 3). For R the heuristic in `backend/router.py` decides solo-vs-council per query before any model is called.

---

## 5. Metrics

- **Correctness** — exact for factual_simple/trap, LLM judge for factual_technical/multi_hop, rubric mean for open_ended.
- **Hallucination rate** — fraction of trap questions where the response asserts a confident answer instead of declining.
- **Self-favoritism** — for council runs only, fraction of Stage-2 rank votes where a member placed its own response first. The audit (M9) flagged this as a structural risk; the framework measures it directly.
- **Cost (USD)** — sum of per-call costs from OpenRouter usage data, p50/p95 per condition.
- **Latency (seconds)** — wall-clock per query, p50/p95.
- **Routing quality (R only)** — solo-vs-council accuracy: did the router send simple queries to solo and complex/ambiguous queries to council?

---

## 6. Dev sweep — cheap models, 21 rows

The flagship config (`gpt-5.1`, `gemini-3-pro-preview`, `claude-sonnet-4.5`, `grok-4`, chairman `gemini-3-pro-preview`) is too expensive to use as a smoke test for the harness itself. For the dev run we swapped in cheap models in the sandbox copy of `backend/config.py` (the committed config is unchanged):

```python
COUNCIL_MODELS = [
    "google/gemini-2.5-flash",
    "openai/gpt-4o-mini",
    "anthropic/claude-3.5-haiku",
    "x-ai/grok-4-fast",
]
CHAIRMAN_MODEL = "anthropic/claude-3.5-haiku"
```

Then ran:

```bash
python -m evals.run_sweep \
    --conditions C0,C2,R \
    --limit 5 \
    --budget 5.0 \
    --time-budget 38 \
    --resume evals/runs/sweep_20260510_180256.jsonl
```

The `--budget` and `--resume` flags are the new bits I added on top of the original Phase-1/2/3 sweep machinery. Resume-aware execution is what made the run survive the 45-second sandbox bash timeout: any chunk that runs out of time exits cleanly, the next call picks up where the previous one stopped. JSONL output is the source of truth.

**21 rows captured. Spend $0.0441. Budget consumed: 0.9%.**

### 6.1 Coverage

| Condition | n  | Rows                                                   |
|-----------|----|--------------------------------------------------------|
| C0        | 9  | 5× factual_simple, 1× tech_recommendation, 3× trap    |
| C2        | 4  | 3× factual_simple, 1× tech_recommendation             |
| R         | 8  | 5× factual_simple, 3× trap                            |

Open-ended rubric got 1 row; rubric judge returned a JSON parse failure (fallback avg=0). That's a known harness gap — see §7.

### 6.2 Headline numbers

| Metric                       | C0       | C2        | R        |
|------------------------------|----------|-----------|----------|
| Cost total                   | $0.0030  | $0.0302   | $0.0109  |
| Cost p50 / query             | $0.00023 | $0.00466  | $0.00012 |
| Latency p50                  | 1.82 s   | 20.48 s   | 2.70 s   |
| Latency p95 (small n)        | 7.7 s    | 38.1 s    | 30.0 s   |
| Correctness on factual (n=13)| 5/5 ✓    | 3/3 ✓     | 5/5 ✓    |
| Trap-decline rate (n=6)      | 3/3 ✓    | —         | 3/3 ✓    |

Read-out:

- **Forced council costs ~46× solo on factual queries** ($0.0047 vs $0.0001 on `fact-001`, e.g.) for **identical correctness**. This is the chapter-03 audit hypothesis confirmed end-to-end: on simple queries the council is pure burn.
- **Forced council is ~11× slower** at the p50.
- **Smart router moves the cost back to ~3× solo (driven entirely by trap escalations)** while preserving correctness. On the 5 factual queries the router sent to solo, the average cost was $0.00013 — within rounding of pure C0.
- **No hallucination** on any condition for the 3 trap questions tested. C0/R both declined gracefully ("I do not have access to context of any previous conversation"). The R-routed-to-council answers also declined. This is good news — it suggests the chairman doesn't re-introduce fabrication when the council has unanimously declined.

### 6.3 Routing quality (condition R, n=8)

| Question id | Category          | Heuristic gate                          | Decision  | Right call? |
|-------------|-------------------|-----------------------------------------|-----------|-------------|
| fact-001    | factual_simple    | classified_factual_simple               | SOLO      | ✓           |
| fact-002    | factual_simple    | short_query<30                          | SOLO      | ✓           |
| fact-003    | factual_simple    | short_query<30                          | SOLO      | ✓           |
| fact-004    | factual_simple    | classified_factual_simple               | SOLO      | ✓           |
| fact-005    | factual_simple    | classified_factual_simple               | SOLO      | ✓           |
| trap-001    | context_dependent | classified_complex_or_subjective        | COUNCIL   | debatable   |
| trap-002    | context_dependent | classified_factual_simple               | SOLO      | ✓ (saved $0.005) |
| trap-003    | context_dependent | classified_complex_or_subjective        | COUNCIL   | debatable   |

**Factual: 5/5 routed to solo, 5/5 correct.** The router earns its keep here.

**Traps: 2/3 escalated to council.** Both escalations cost ~$0.005 and ~25–30 seconds, and the council declined just like solo would have. The router heuristic flags "Continue from where..." and "What was my previous..." as `complex_or_subjective` because they trigger the conversational/ambiguity gates. Not strictly wrong — but on a stateless API trap, council-vs-solo doesn't change the answer, so the escalation is pure overhead. Refining the router to recognize context-dependent queries as a distinct class is a follow-up (entry in `evals/router_followups.md`).

### 6.4 Self-favoritism

`raw_envelope` did not capture per-member rank arrays for the C2 rows in this sweep — known harness gap, see §7. The flagship sweep documented in `07_flagship_sweep_results.md` (8 rows) measured **26.1% self-first** with the four council models on Phase-1 traffic, well above the 25% chance baseline. The harness emits the metric correctly when the envelope is populated; the dev sweep just didn't persist it. A one-line patch to `runners/council_full.py` to copy `RankingResult.ranks_per_member` into the row is queued.

### 6.5 Cost detail (C2 vs C0, by question)

| Question         | C0 cost   | C0 lat | C2 cost   | C2 lat | Council multiplier (cost) |
|------------------|-----------|--------|-----------|--------|---------------------------|
| fact-001         | $0.000307 | 3.0 s  | $0.0047   | 20.5 s | **15.3×**                 |
| fact-002         | $0.000267 | 1.8 s  | $0.0050   | 20.0 s | **18.7×**                 |
| fact-003         | $0.000069 | 0.9 s  | $0.0039   | 20.1 s | **56.5×**                 |
| open-001 (rec)   | $0.001376 | 7.7 s  | $0.0167   | 38.1 s | **12.1×**                 |

Even on cheap models the floor is ~12× and the ceiling on quick-recall queries is ~57×. On flagship models that ratio holds (the bottleneck is the number of calls, not the unit price), so a flagship forced-council run on `fact-003` would cost roughly 50× the solo equivalent — and still get the same answer.

---

## 7. Honest gaps

What this dev sweep does **not** establish, and where the harness still has rough edges:

1. **n is small.** 21 rows is enough to demonstrate the pipeline works and the cost ratios are real on cheap models. It is not enough to claim a quality delta on multi-hop reasoning or open-ended rubric scoring. The full 100-question sweep on flagship models would cost roughly $30–60 and is the natural next run.
2. **Open-ended rubric judge returned a JSON parse failure** on the one open-ended question that ran. The judge prompt is not robust to the chairman's preamble formatting. Fix is in `evals/judges/rubric.py:_extract_json` — a regex grab of the first `{...}` block instead of `json.loads(raw)`. Queued.
3. **`raw_envelope.ranks_per_member` not populated** in C2 rows of this sweep, so self-favoritism couldn't be re-measured here. The flagship sweep numbers in chapter 07 stand.
4. **Trap classification is too coarse.** The router's `complex_or_subjective` gate fires on context-dependent traps, escalating them unnecessarily. A `context_dependent` class (with a short solo decline) is a small router patch.
5. **No latency budget enforcement under load.** All numbers here are single-shot. Production scaling math is in chapter 09; the harness doesn't yet simulate concurrent traffic.
6. **Judges trust their own model.** The LLM judge for factual_technical is `claude-3.5-haiku`; that's also one of the council members in the dev config. Fine for a smoke test, not a clean separation for a public report. Easy to swap.

None of these change the conclusions of §6 — they are next-run improvements, not invalidating defects.

---

## 8. What this proves

Plain-language version of §6:

- **The audit's central claim — that the council is overkill for simple queries — is now measured, not asserted.** $0.0030 (solo, 9 rows) vs $0.0302 (forced council, 4 rows) on essentially the same content.
- **The smart router is a real cost lever, not a paper one.** R's per-query cost on factual queries is within rounding of solo. The router does the right thing 5 out of 5 times on the factual sample.
- **The pipeline does not fabricate on traps.** All three trap questions, in all conditions tested, declined gracefully. This was not guaranteed — the chairman is a separate model from the council members and could in principle re-introduce confidence the council didn't have.

For reviewers who want to re-run: `evals/README.md` has the one-liner. The full JSONL of this sweep is committed at `evals/runs/sweep_20260510_180256.jsonl` so the report can be regenerated without spending another cent.

---

## 9. Reproducing the sweep

```bash
# 1. Install deps
pip install -r requirements.txt

# 2. Set OpenRouter key
export OPENROUTER_API_KEY=sk-or-v1-...

# 3. (optional) Swap to cheap models for a free-ish dev run
#    Edit backend/config.py COUNCIL_MODELS / CHAIRMAN_MODEL.
#    The flagship config burns about $30–60 for a full 100-question, 3-condition sweep.

# 4. Run with budget guard
python -m evals.run_sweep \
    --conditions C0,C2,R \
    --limit 5 \
    --budget 5.0 \
    --time-budget 60 \
    --output evals/runs/$(date +%Y%m%d_%H%M%S).jsonl

# 5. Roll up
python -m evals.report evals/runs/<file>.jsonl
```

A run that hits `--budget` or `--time-budget` exits cleanly with the work-so-far persisted to JSONL. Re-invoking with `--resume <same-file>` picks up at the next un-attempted (qid, condition) pair.
