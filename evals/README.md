# Eval framework for `llm-council`

A measurement layer for the multi-LLM council pipeline. Designed to answer:

> **Under what query distributions does the council's chairman-cross-check catch hallucinations or errors that the chairman would miss when called alone? What is the cost-per-caught-error?**

This framework is a **specification + scaffolding**. The dataset (100 questions across three categories) and the runner / judge / metric module skeletons are committed; the actual sweep has not been run as part of this submission. See `06_proposed_changes.md` §3 for the full rationale and `FUTURE_SCOPE.md` for prioritisation.

## Why this is the right framing

Karpathy's `llm-council` ships a 9-call multi-LLM pipeline with no measurement of whether it produces better answers than the chairman alone. The paid verification pass surfaced one positive datapoint (the chairman caught a council member's hallucination on a follow-up turn — see `05_paid_verification.md` §B8). This framework turns that anecdotal positive into a measurable claim by comparing four conditions across a 100-question dataset.

## Conditions to compare

| Condition | Pipeline | Approx. cost per query (May 2026 flagship) |
|---|---|---:|
| `C0` | Chairman model called once, no council | ~$0.02 |
| `C1` | 2-member council + chairman | ~$0.07 |
| `C2` | 4-member council + chairman (current default) | ~$0.20 |
| `C3` | 6-member council + chairman | ~$0.30 |
| `R`  | The branch's `should_engage_council` router (SOLO or council depending on classification) | varies |

Comparing `R` against `C0`/`C2` is the **routing-quality measurement** — does the router pick the right path?

## Datasets

100 questions in 3 JSONL files under `datasets/`:

| File | Count | What it measures |
|---|---:|---|
| `datasets/factual.jsonl` | 50 | Verifiable-answer questions (capitals, arithmetic, dates, technical acronyms). Used for **correctness rate**. |
| `datasets/open_ended.jsonl` | 25 | Subjective / multi-perspective questions (recommendations, trade-offs, advice). Used for **rubric-graded quality** and **council uplift**. |
| `datasets/trap.jsonl` | 25 | Context-dependent / underspecified queries that should trigger refusal or clarification. Used for **hallucination rate** — the question is whether the system fabricates context that isn't there. |

Each row has an `id`, a `question`, and either an `expected` array of acceptable answers (factual) or a `rubric` of dimensions to score (open-ended) or `expected_behavior` + `fabrication_signals` (trap).

## Metrics

| Metric | Module | Per-condition signal |
|---|---|---|
| Correctness rate | `metrics/correctness.py` | % of factual questions with judge-validated correct answer |
| Hallucination rate | `metrics/hallucination_rate.py` | % of trap questions where the system fabricated context |
| Council uplift | `metrics/council_uplift.py` | Net delta when council corrects vs corrupts vs `C0` baseline |
| Cost per correct | `metrics/cost_per_correct.py` | Total LLM spend / number of judge-verified correct answers |
| Self-favouritism | `metrics/self_favoritism.py` | % of Stage-2 rankings where ranker placed own response first |
| Latency profile | `metrics/latency.py` | p50 / p95 / p99 wall-clock per condition |

The most important new metric is **routing quality**: when the router decides `use_council=False`, what fraction of those answers were judge-graded correct? When `use_council=True`, what fraction would have been *equally* correct without the council? That ratio answers the routing-boundary question.

## How to run a sweep (when budget approved)

```bash
# Cheap dev sweep against the 100-question dataset, 3 runs per condition.
# Uses gemini-2.5-flash + gpt-4o-mini + claude-3.5-haiku. Cost ~$5.
python evals/run_sweep.py --conditions C0,C1,C2,R --runs 3 --models cheap

# Confirmation sweep against the latest May 2026 flagship lineup. Cost ~$60.
python evals/run_sweep.py --conditions C0,C1,C2,R --runs 1 --models flagship

# Aggregate the results into a markdown report.
python evals/report.py runs/ > runs/report.md
```

## Status of this framework

| Component | Status |
|---|---|
| `datasets/factual.jsonl` (50 questions) | Committed |
| `datasets/open_ended.jsonl` (25 questions) | Committed |
| `datasets/trap.jsonl` (25 questions) | Committed |
| Runner skeletons (`runners/`) | Committed (callable stubs that import from `backend/`) |
| Judge skeletons (`judges/`) | Committed (prompt templates + scoring functions) |
| Metric calculators (`metrics/`) | Committed (compute against captured run JSONLs) |
| `run_sweep.py` orchestrator | Committed (CLI; not yet executed as part of submission) |
| `report.py` aggregator | Committed (markdown table generator) |
| Calibration data on 20-question sample | Pending — needs human review pass |
| Full sweep results (`runs/*.jsonl`) | Pending — needs ~$65 LLM budget |

The framework is **runnable** — pass an OpenRouter key in `.env` at the repo root and `python evals/run_sweep.py` will execute. The empty `runs/` directory is intentional; it fills on first execution.
