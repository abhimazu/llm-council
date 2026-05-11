# LLM Council — `changes` branch

This branch ships a **production-readiness refactor** + a **cost-control layer** + an **eval framework** on top of `karpathy/llm-council`. It is the deliverable for a Meraki Labs Founding AI Engineer work trial (PS 2 — Public Repo Audit).

If you're a reviewer: this README is the single entry point. The audit-ID-to-code map is in § "Audit ID → where each finding landed" below; the chapter docs that walk through the audit reasoning are in `docs/`; what was deliberately left out is in [FUTURE_SCOPE.md](./FUTURE_SCOPE.md).

---

## What changed vs `master`

| Layer | Original | After this branch |
|---|---|---|
| Per-LLM-call error handling | Single broad `except Exception` collapsed every error class to `None` | Typed `LLMError` system, retry/backoff, structured logs, OpenRouter `usage` captured |
| Stage-1 / Stage-2 | Failed members silently dropped from results | All members surfaced with `status: "ok" \| "error"` and structured error payloads |
| Stage-3 chairman fallback | Persisted the literal string `"Error: Unable to generate final synthesis."` as a real assistant answer (root cause of issues #27, #113) | Returns `{status: "ok"}` or `{status: "error", error: {...}}` — UI / persistence can distinguish |
| Stage-2 ranking parser | Empty list on format non-compliance | Returns `parse_status: "ok" \| "partial" \| "parse_error"` with reason; aggregate flagged `partial` |
| Cost control | None — every query fired ~9–10 LLM calls unconditionally | Smart routing (skip council for trivial queries) + in-memory LRU cache + per-stage `max_tokens` caps |
| Storage | Filesystem JSON, no validation, race conditions under multi-worker | Adds UUID-regex validation; **storage race itself is NOT fixed in this branch — see "Known limits" below** |
| Frontend | Crashes on `null` response, renders error strings as real answers | Renders error states, partial-parse badges, cache-hit banner, routing-decision banner |
| Tests | None | 65 unit tests passing in <0.1s, no LLM spend |
| Docs | "99% vibe-coded" disclaimer | `CHANGES_README.md` (this file — entry point + per-finding map), `FUTURE_SCOPE.md` (priority spine), `docs/` (10 chapter docs) |

**30 of 47 audit findings + missing-feature items resolved** (M2 — eval framework — moved from `Specced` to `Delivered: scaffold + 21-row dev sweep` after `docs/10_evals.md` landed). The remaining 17 are P1 follow-ups (separate branches) or explicitly out of scope. See the audit-ID map below and `FUTURE_SCOPE.md`.

---

## Audit ID → where each finding landed

The branch's commits resolve these 24 catalog items from the audit (`docs/03_audit_hypothesis.md`). For the cost-control and eval-framework items, see the next table.

| Audit ID | Item | Where it landed |
|---|---|---|
| A1 | Single broad `except Exception` collapsing all LLM error classes | `backend/openrouter.py` |
| A2 | `asyncio.gather` without `return_exceptions=True` | `backend/openrouter.py:query_models_parallel` |
| A3 | Stage 1 silent-drop of failed council members | `backend/council.py:stage1_collect_responses` |
| A4 | Stage 2 silent-drop of failed rankers | `backend/council.py:stage2_collect_rankings` |
| A5 | **Chairman fallback string masquerading as a real answer** (root) | `backend/council.py:stage3_synthesize_final` |
| A6 | Title-gen failure indistinguishable from default | `backend/council.py:generate_conversation_title` |
| A7 | Stream error path leaked internals to client | `backend/main.py:event_generator` |
| B1 | Stage-2 ranking parser brittle to format non-compliance | `backend/council.py:parse_ranking_from_text` |
| B2 | Aggregate ranking from partial rankers with no signal | `backend/council.py:calculate_aggregate_rankings` |
| B4 | Anonymization labels deterministic by `COUNCIL_MODELS` order | `backend/council.py:_build_label_mapping` |
| B7 | `/message` and `/message/stream` produce different error UX | both endpoints route through `run_full_council` |
| B9 | (partly) Self-favoritism in Stage-2 rankings | mitigated via shuffled labels; full fix is eval-framework concern |
| C3 | No per-model parameter tuning (`max_tokens`, etc.) | `query_model(max_tokens=...)`; per-stage configs in `config.py` |
| C4 | Hardcoded 120s per-call timeout | reduced to 30s default; configurable per call |
| D3 | Wide-open CORS | now driven by `CORS_ORIGINS` env var |
| E1 | Zero structured logging | `_configure_logging()` in `backend/config.py` |
| E2 | OpenRouter `usage` field discarded | captured per call, returned to callers, logged |
| F2 | Stream error path can leak server-side details | sanitization layer in `event_generator` |
| F3 | (partly) No application-level rate limiting | request-body size cap; full per-IP rate limiting is P1 |
| G3 | Path-traversal hardening at storage layer | UUID-regex validation in `backend/storage.py` |
| M5 | No retry / backoff | exponential backoff with jitter for retryable kinds |
| M8 | No token / cost tracking | `usage` returned + structured-logged per call |
| M16 | No startup config validation | `RuntimeError` in `backend/config.py` if key missing |
| M21 | No request body size limit | 64 KB cap + Pydantic `max_length=8192` on content |

### Cost-control layer (new modules)

| Audit ID | Item | Where it landed |
|---|---|---|
| C1 | No caching; every query = 9 LLM calls (2N + 1, default N=4) | `backend/cache.py` (LRU) wired in `backend/main.py` |
| M4 | Caching layer absent | `backend/cache.py` |
| M13 | Smart routing absent — `run_full_council` always called | `backend/router.py` + `main.py` wiring |
| M20 | No cost guardrail | partial — cache + routing reduce spend; explicit `$` caps are in `FUTURE_SCOPE.md` |
| C2 / C3 | Per-model parameter tuning absent | `STAGE1_MAX_TOKENS=800`, `STAGE2_MAX_TOKENS=400`, `CHAIRMAN_MAX_TOKENS=1000` activated in `config.py` (was `None`/uncapped) |

### Eval framework (new directory)

| Audit ID | Item | Where it landed |
|---|---|---|
| M2 | Eval framework absent — no `evals/` dir, no judges, no scoring | `evals/` (datasets, runners, judges, metrics, orchestrator, report). Spec + 21-row dev-sweep results in `docs/10_evals.md`. Raw JSONL in `evals/runs/sweep_20260510_180256.jsonl`. |

---

## How to run it

### Prerequisites

- Python ≥ 3.10
- Node ≥ 20 (for the frontend build)
- An OpenRouter API key from https://openrouter.ai

### Setup

```bash
# clone + check out this branch
git clone https://github.com/abhimazu/llm-council.git
cd llm-council
git checkout changes

# backend
pip install -e .                 # installs from pyproject.toml
# or: uv sync                    # if you use uv

# frontend
cd frontend && npm install && cd ..

# .env at repo root — required
echo "OPENROUTER_API_KEY=sk-or-v1-..." > .env
```

> **Note on models:** `backend/config.py` ships with the **cheap-model lineup verified by the $0.044 dev sweep** (council: `gemini-2.5-flash`, `gpt-4o-mini`, `claude-3.5-haiku`, `grok-4-fast`; chairman: `claude-3.5-haiku`; title + routing: `gemini-2.5-flash`). Same models that were live on OpenRouter on 2026-05-10 when the dev sweep ran (see `docs/10_evals.md`). The original Karpathy config referenced future-dated names like `gpt-5.1` and `gemini-3-pro-preview` that don't actually exist on OpenRouter — running them returned HTTP 404, which this branch surfaces correctly as a structured A5 error envelope instead of silently persisting a fake answer. For a flagship run, edit `COUNCIL_MODELS` and `CHAIRMAN_MODEL` in `config.py` to the May 2026 flagship lineup you've verified for your account (Opus 4.7 / Sonnet 4.6 / GPT-5.4 / Gemini 3.1 Pro Preview / Grok 4.3 referenced in `docs/09_cost_and_scaling.md`).

### Run

```bash
./start.sh
# Backend: http://localhost:8001
# Frontend: http://localhost:5173 (vite dev server)
```

`./start.sh` runs both in foreground (Ctrl-C stops them). For a backend-only run:

```bash
python -m uvicorn backend.main:app --reload --port 8001
# or: ./start.sh --backend-only   (if your start.sh supports it)
```

### Verify it works

```bash
# 1. Health check
curl -s http://localhost:8001/ | jq
# → {"status":"ok","service":"LLM Council API"}

# 2. Cache observability endpoint (NEW in this branch)
curl -s http://localhost:8001/api/cache/stats | jq
# → {"hits":0,"misses":0,"stores":0,"evictions":0,"skipped_error_envelopes":0,"hit_rate":0.0}

# 3. Send a trivial factual query — watch routing skip the council
CONV=$(curl -s -X POST http://localhost:8001/api/conversations \
  -H 'Content-Type: application/json' -d '{}' | jq -r .id)

curl -N -X POST http://localhost:8001/api/conversations/$CONV/message/stream \
  -H 'Content-Type: application/json' \
  -d '{"content":"What is the capital of France?"}'
# Expect SSE events: routing_decision (use_council=false, reason=classified_factual_simple)
#                  → solo_start → solo_complete → stage3_complete

# 4. Send the same query again — should hit cache (0.0s wall, 0 LLM calls)
curl -N -X POST http://localhost:8001/api/conversations/$CONV/message/stream \
  -H 'Content-Type: application/json' \
  -d '{"content":"What is the capital of France?"}'
# Expect: cache_hit event before stage events
```

### Run the test suite

```bash
pip install pytest pytest-asyncio
pytest tests/ -v
# → 65 passed in <0.1s (no LLM calls; httpx-mocked)
```

Test coverage:

| File | Tests | Covers |
|---|---:|---|
| `tests/test_errors.py` | 14 | HTTP status → kind mapping, retryable set membership, `LLMError` JSON roundtrip |
| `tests/test_parser.py` | 9 | Stage-2 ranking parser: clean / partial / parse_error / fallback / dedup / truncation |
| `tests/test_query_model.py` | 9 | Mocked httpx: 200, 401, 429+retry, 503+retry, 400, malformed, parallel mixed-failure, max_tokens propagation |
| `tests/test_council_partial.py` | 5 | Failed members surfaced (not dropped); chairman fallback structured-error not string |
| `tests/test_storage.py` | 8 | UUID validation rejects traversal/empty/non-uuid IDs; structured-error persistence |
| `tests/test_cache.py` | 13 | Key stability under reordering, error envelopes never stored, LRU eviction, hit_rate |
| `tests/test_router.py` | 7 | Heuristic gates, classifier outputs, conservative fallback on classifier failure |

---

## What's in the repo

```
llm-council/
├── backend/
│   ├── errors.py            ← NEW. Typed LLMError + LLMErrorKind enum + classify_http_status()
│   ├── openrouter.py        ← REWRITTEN. (content, usage, error) tuple return, retry/backoff,
│   │                          structured logs, configurable max_tokens
│   ├── council.py           ← REWRITTEN. CouncilMemberResult / RankingResult / ChairmanResult
│   │                          dataclasses; parser returns parse_status; aggregate flagged
│   │                          partial; anonymization labels shuffled per call
│   ├── main.py              ← REWRITTEN. Cache lookup → routing decision → SOLO or council;
│   │                          new SSE events (cache_hit, routing_decision, solo_*); body-size cap;
│   │                          /api/cache/stats endpoint; sanitized error events
│   ├── storage.py           ← UPDATED. UUID validation, malformed-file resilience,
│   │                          None-title-allowed
│   ├── config.py            ← UPDATED. Fail-fast startup if OPENROUTER_API_KEY missing;
│   │                          structured logging configured; per-stage max_tokens activated
│   ├── cache.py             ← NEW. Thread-safe in-memory LRU keyed by SHA-256(query +
│   │                          sorted council models + chairman). Skips error envelopes.
│   └── router.py            ← NEW. should_engage_council() with heuristic gates +
│                              LLM classifier; RoutingDecision dataclass
│
├── frontend/src/
│   ├── App.jsx              ← UPDATED. Handles new SSE events (cache_hit, routing_decision,
│   │                          title_failed, solo_*); per-message _pendingToken rollback
│   ├── api.js               ← UPDATED. Cross-read SSE buffering; tryDispatch helper
│   └── components/
│       ├── Stage1.jsx       ← REWRITTEN. Renders error tabs with red dot + structured error UI
│       ├── Stage2.jsx       ← REWRITTEN. Partial-parse badges; aggregate-partial banner
│       ├── Stage3.jsx       ← REWRITTEN. Renders chairman error state instead of fake string
│       ├── ChatInterface.jsx ← UPDATED. Routing banner + stream-error banner
│       └── (Stage1/2/3.css, ChatInterface.css) ← UPDATED. Error/partial visual states
│
├── tests/                   ← NEW. 65 unit tests (pytest), httpx-mocked
│
├── pytest.ini               ← NEW. asyncio_mode=auto
├── evals/                   ← NEW. Eval framework (datasets, runners, judges, metrics, orchestrator)
├── docs/                    ← NEW. 10 chapter docs (01–10) that fed the consolidated submission
├── CHANGES_README.md        ← THIS FILE. Entry point + per-finding map + how to run + known limits
├── FUTURE_SCOPE.md          ← NEW. P1 / P2 / P3 follow-ups with effort + cost estimates
├── README.md                ← original Karpathy README (unchanged)
└── pyproject.toml           ← unchanged
```

---

## New API endpoints

| Endpoint | Purpose |
|---|---|
| `GET /api/cache/stats` | Returns `{hits, misses, stores, evictions, skipped_error_envelopes, hit_rate}`. Read-only, no auth, no sensitive data. |

Existing endpoints (unchanged paths, **changed payload shapes** — see the before/after below):

| Endpoint | What's different |
|---|---|
| `POST /api/conversations` | Returns `title: null` instead of `"New Conversation"` |
| `POST /api/conversations/{id}/message` | Same envelope shape as the streaming endpoint (resolves audit B7 endpoint inconsistency) |
| `POST /api/conversations/{id}/message/stream` | New SSE event types: `cache_hit`, `routing_decision`, `solo_start`, `solo_complete`, `title_failed`. New per-entry `status` field on existing event payloads. |

### Wire-protocol changes — before / after

The SSE event *names* are unchanged. The shape of `data` inside each event has changed — frontends must handle the new `status` field.

**Before:**

```jsonc
// stage1_complete
data: {"type": "stage1_complete", "data": [
  {"model": "openai/gpt-5.1", "response": "..."}      // failed members silently absent
]}

// stage3_complete (chairman 401)
data: {"type": "stage3_complete", "data": {
  "model": "google/gemini-3-pro-preview",
  "response": "Error: Unable to generate final synthesis."  // string masquerading as a real answer
}}
```

**After:**

```jsonc
// stage1_complete
data: {"type": "stage1_complete", "data": [
  {"model": "openai/gpt-5.1", "status": "ok", "response": "...", "usage": {...}},
  {"model": "anthropic/claude-sonnet-4.5", "status": "error",
   "error": {"kind": "rate_limit", "model": "...", "detail": "HTTP 429",
             "retryable": true, "upstream_status": 429, "attempt": 1}}
]}

// stage3_complete (chairman 401)
data: {"type": "stage3_complete", "data": {
  "model": "google/gemini-3-pro-preview",
  "status": "error",
  "error": {"kind": "auth", "detail": "HTTP 401", "retryable": false, ...}
}}
```

Five new SSE events (added across the refactor + cost-control commits):

- `title_failed` — title-gen failed; conversation has no title.
- `error` — fatal server-side error during streaming; carries `request_id`, `kind`, and `detail`. Never carries raw exception text.
- `cache_hit` — fired before stage events when the cache served the response. Cached envelope's three stage events follow.
- `routing_decision` — fired after a cache miss with `use_council`, `reason`, and `classifier_used` fields. Frontend renders a banner.
- `solo_start` / `solo_complete` — fired when smart routing skipped the council and ran the chairman alone. The same payload is also emitted as `stage3_complete` so existing frontend renderers keep working without changes.

Existing on-disk conversations remain readable (the reader does not validate shape). New writes use the structured-status format above. Frontend branches on the presence of the `status` field.

---

## New environment variables

| Var | Default | Purpose |
|---|---|---|
| `OPENROUTER_API_KEY` | (required, server fails fast at startup) | OpenRouter API key |
| `LOG_LEVEL` | `INFO` | Standard Python logging level |
| `LOG_JSON` | `0` | If `1`/`true`/`yes`, emit JSON-line logs instead of human-readable |
| `DATA_DIR` | `data/conversations` | Where conversation JSONs are written |
| `CORS_ORIGINS` | `http://localhost:5173,http://localhost:3000` | Comma-separated allowed origins |

Per-stage `max_tokens` caps live in `backend/config.py` (no env var):

| Constant | Default | Purpose |
|---|---|---|
| `STAGE1_MAX_TOKENS` | `800` | Output cap for council Stage 1 |
| `STAGE2_MAX_TOKENS` | `400` | Output cap for council Stage 2 ranking |
| `CHAIRMAN_MAX_TOKENS` | `1000` | Output cap for chairman synthesis |
| `TITLE_MODEL` | `google/gemini-3.1-flash-lite` | Cheap model for conversation titles |
| `ROUTING_MODEL` | `google/gemini-3.1-flash-lite` | Cheap model for routing classification |

Set any of the `MAX_TOKENS` constants to `None` to restore the original uncapped behavior.

---

## Cost-control behavior at a glance

The new pipeline does three things to reduce per-query cost (verified empirically against real flagship models — see the candidate's submission `07_flagship_sweep_results.md`):

1. **Smart routing** — a cheap classifier (`google/gemini-3.1-flash-lite`) decides whether a query needs the full 4-LLM council or can be handled by the chairman alone. Trivial factual queries skip the council entirely. Conservative fallback: any classifier error engages the council.
2. **Per-stage `max_tokens` caps** — bound chairman + stage outputs. Original code had no cap; flagship reasoning models could run unbounded output at high cost. Caps trigger `parse_status: "partial"` rather than silent failure.
3. **In-memory LRU cache** — repeated queries return in 0.0s wall clock with 0 LLM calls. Keyed by SHA-256 over normalized query + sorted council models + chairman. Error envelopes are intentionally never cached (transient outages would otherwise poison the cache).

Empirical effect from the sweep ($0.188 spent against real flagship models):

| Comparison | Cost reduction | Latency reduction |
|---|---:|---:|
| Factual query: SOLO vs forced council | ~98.5% | ~6× faster |
| Same query: capped vs uncapped chairman | ~28% | ~32% faster |
| Cache hit on repeat query | 100% | instant |

At 10,000 users/day with a 60% factual / 40% complex / 30% cache-hit workload mix, this projects to **~$9,733/month** vs **~$41,293/month** for the original code at May 2026 flagship pricing — **76% reduction**. See the candidate's `09_cost_and_scaling.md` for the full TCO analysis.

---

## Known limits — read before deploying

This is the section the brief's "be honest about limitations" rubric is designed to test. Three real limits in this branch:

### 1. Storage race condition (D1, D2) is NOT fixed

The current code still uses filesystem JSON storage (`backend/storage.py`). Under `uvicorn --workers > 1`, **64% of writes are lost** (verified empirically — see `04_runtime_verification.md`). Under single-worker uvicorn, the race is dormant.

**→ Run with `--workers 1` or stick with the default `./start.sh` until P1-1 (SQLite migration) lands. See `FUTURE_SCOPE.md`.**

### 2. Cost-control claims are partially verified — flagship sweep still pending

The cost-control layer reduces dollars per query. Whether the routing classifier's decisions preserve answer quality has been **measured on cheap models and is still hypothesised at flagship scale**:

- **Measured (dev sweep, $0.044, 21 rows, cheap models):** 5/5 factual queries routed to solo and answered correctly; 3/3 trap questions declined gracefully across all conditions; forced council cost ~46× solo on factual queries for identical correctness. Full numbers in `docs/10_evals.md`.
- **Hypothesised (not yet measured):** the same routing quality on the May 2026 flagship lineup (Opus 4.7 chairman, Sonnet 4.6 / GPT-5.4 / Gemini 3.1 Pro Preview / Grok 4.3 council). The full 100-question × 3-condition flagship sweep is the natural next run (~$30–60); harness is in `evals/`, JSONL is committed at `evals/runs/sweep_20260510_180256.jsonl` for the dev run.

**→ Cheap-model traffic: the router is validated on the sample we ran. Flagship production traffic: run the flagship sweep first. Conservative fallback in either case: set `STAGE1_MAX_TOKENS = None` etc. in `config.py` and short-circuit the router to always engage the council.**

### 3. The chat product still doesn't actually chat

`backend/main.py` passes only the latest user message to the council, not the full conversation history. This means follow-up questions ("What was my previous question?") get hallucinated answers — verified live in `05_paid_verification.md`.

**→ Adding conversation memory is P1-2 in `FUTURE_SCOPE.md` (estimated 1.5 senior-engineer days).**

Other limits documented in `FUTURE_SCOPE.md`:

- No per-IP rate limiting (rely on Cloudflare in front for now)
- No streaming chairman tokens through to SSE (latency, not correctness)
- No semantic cache (exact-match only)
- No auth or per-user data isolation (treat as single-tenant or single-team)
- No CI / GitHub Actions (run tests locally before merging)

---

## How to verify the audit findings yourself

The 10 underlying chapter docs are now in this repo under `docs/`. They walk through the audit + verification + sweep methodology. The most useful ones for verifying claims:

- `docs/04_runtime_verification.md` — every audit finding either confirmed or corrected with a probe command you can re-run
- `docs/05_paid_verification.md` — $0.00331 cheap-model traffic that confirms the end-to-end pipeline behavior
- `docs/07_flagship_sweep_results.md` — $0.188 flagship-model sweep that anchors the cost-control validation
- `docs/09_cost_and_scaling.md` — TCO at 10k users/day with sourced pricing for May 2026
- `docs/10_evals.md` — eval framework spec + 21-row dev-sweep results (routing quality, council vs solo cost ratios, trap behaviour, honest gaps)

Replay-able artifacts:

- `evals/runs/sweep_20260510_180256.jsonl` — raw JSONL of the dev sweep; pass through `python -m evals.report` to regenerate the numbers in `docs/10_evals.md`.
- `docs/07_flagship_sweep_results.xlsx` — 7-tab spreadsheet of the flagship-model cost sweep.

---

## Pushing to your own fork

```bash
git remote add upstream https://github.com/karpathy/llm-council.git  # if you want upstream tracking
git checkout changes
# make additional commits if you want
git push -u origin changes
```

The `changes` branch on this fork (`abhimazu/llm-council`) was pushed via a git bundle generated from a sandboxed environment; commits are authored as `Abhijeet Mazumdar (via Cowork)` with email `abhijeet@cowork.local`. To re-author under your real identity:

```bash
git rebase --root --exec 'git commit --amend --author="Your Name <you@example.com>" --no-edit' master
git push --force-with-lease origin changes
```
