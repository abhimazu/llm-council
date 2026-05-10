# Production-Grade Enhancements — Brief

**Repo:** `karpathy/llm-council` → fork at https://github.com/abhimazu/llm-council
**Branch:** `changes` (https://github.com/abhimazu/llm-council/tree/changes)
**Commits on top of master:** 11
**Tests:** 65 unit tests passing in <0.1s, no LLM spend
**Frontend build:** `npm run build` clean, 202 modules transformed
**Sweep validation:** $0.188 of real flagship-model traffic (see `07_flagship_sweep_results.md`)

---

## 1. Why this branch exists

The original `karpathy/llm-council` is a self-described "99% vibe-coded Saturday hack" with three structural failure modes that produced visible user pain (issues #27, #113, #156): (1) per-LLM-call failures collapsed silently to `None`, (2) the Stage-3 chairman fallback string `"Error: Unable to generate final synthesis."` was persisted as a real assistant answer, and (3) every query unconditionally fanned out to a 4-LLM council with no caps, caching, or routing — projected at $135k–270k/month at 10k users/day.

This branch addresses both: a structured-error refactor that ends silent failures, and a cost-control layer that empirically reduces per-query cost ~98% on factual queries and ~28% on council queries (verified at flagship rates).

---

## 2. Enhancement map — audit ID → commit → effect

| Catalog | Item | Commit | Before → After |
|---|---|---|---|
| **A1** | Single broad `except Exception` collapsed every LLM error class | `56b5903` (errors.py) + `fc55384` (openrouter.py) | 401 / 429 / timeout / payload / parse now distinguishable; retryable kinds get 1 retry with exponential backoff |
| **A2** | `asyncio.gather` without `return_exceptions=True` | `fc55384` | Defense-in-depth: future contract bugs can no longer kill the whole council |
| **A3, A4** | Stage 1/2 silently dropped failed members | `fc55384` | Failed members now appear in results with `status="error"` and structured error payload |
| **A5** | Chairman fallback string masquerading as real answer (root cause of #27/#113) | `fc55384` + `358e216` | Chairman returns `status="ok"` or `status="error"` with structured `LLMError`; UI/persistence can distinguish |
| **A6** | Title-gen failure indistinguishable from default | `fc55384` + `358e216` | Title is `null` on failure with explicit `title_failed` SSE event |
| **A7** | Stream error path leaked internals to client | `358e216` | Sanitized SSE error events: `{kind, detail, request_id}`. Raw exceptions never reach the browser |
| **B1** | Stage-2 ranking parser brittle to format non-compliance | `fc55384` | Parser returns `parse_status: "ok" \| "partial" \| "parse_error"`; tolerant of truncation and missing heading |
| **B2** | Aggregate ranking from partial rankers with no signal | `fc55384` | Aggregate flagged `partial: true` when any contributing ranker had non-ok parse status |
| **B4, B9** | Anonymization labels deterministic + self-favoritism | `fc55384` | Labels shuffled per call to reduce position-bias contribution |
| **B7** | `/message` and `/message/stream` produced different error UX | `358e216` | Both endpoints route through `run_full_council`; identical envelope shape |
| **C1, M4** | No caching | `b1f309d` + `23ced09` | In-memory LRU keyed by SHA-256(query + sorted council + chairman); skips error envelopes; empirically verified 0.0s wall + 0 LLM calls on cache hit |
| **C2/C3** | No per-model parameter tuning | `23ced09` | Per-stage caps: `STAGE1=800`, `STAGE2=400`, `CHAIRMAN=1000`. Activated by default; set to `None` in `config.py` to restore uncapped |
| **C4** | Hardcoded 120s per-call timeout | `fc55384` | Reduced to 30s default; configurable per-call |
| **D3** | Wide-open CORS | `358e216` | Driven by `CORS_ORIGINS` env var |
| **E1, E2** | Zero structured logging; OpenRouter `usage` field discarded | `358e216` (config.py logging) + `fc55384` (usage capture) | JSON-line structured logs on every LLM call with `model, kind, elapsed_ms, prompt_tokens, completion_tokens, cost_usd, finish_reason`. New `GET /api/cache/stats` endpoint |
| **F2** | Stream error path leaked details | `358e216` | Sanitization layer; only typed errors leave the server |
| **F3** (partial) | No application-level rate limiting | `358e216` | 64 KB body-size cap. Per-IP rate limiting deferred to `FUTURE_SCOPE.md` |
| **G3** | Path-traversal hardening at storage layer | `358e216` | UUID-regex validation rejects traversal/empty/non-UUID IDs before any `os.path.join` |
| **M5** | No retry / backoff | `fc55384` | Exponential backoff with jitter for `RETRYABLE_KINDS` (rate_limit, timeout, upstream, network) |
| **M8** | No token / cost tracking | `fc55384` | OpenRouter `usage` (incl. `cost`) returned to callers and structured-logged |
| **M13** | Smart routing absent | `b1f309d` + `23ced09` | New `backend/router.py`: heuristic gates + LLM classifier returning `RoutingDecision(use_council, reason, classifier_used)` |
| **M16** | No startup config validation | `358e216` | Server fails fast at import if `OPENROUTER_API_KEY` missing |
| **M21** | No request body size limit | `358e216` | Pydantic `max_length=8192` + 64 KB middleware cap |

**29 of 47 audit findings + missing-feature items resolved by this single branch.**

---

## 3. Architecture additions (new modules in `backend/`)

| Module | Lines | Purpose |
|---|---:|---|
| `backend/errors.py` | 134 | `LLMErrorKind` enum (9 kinds), `LLMError` dataclass with JSON roundtrip, `RETRYABLE_KINDS` set, `classify_http_status()` |
| `backend/cache.py` | 203 | `CouncilCache` (thread-safe LRU), `CacheStats` (hit/miss/store/eviction counters with `hit_rate`), keyed by SHA-256 over normalized query + sorted council + chairman |
| `backend/router.py` | 178 | `should_engage_council()` with heuristic gates (`<30 chars` skip council, `>1000 chars` engage) + LLM classifier; `RoutingDecision` dataclass |

Each is independently testable, documented inline, and has explicit caveats about what was deliberately out of scope (semantic cache, cross-process cache backend, etc. — see `FUTURE_SCOPE.md`).

---

## 4. Wire-protocol changes (frontend was updated to consume them)

SSE event names unchanged, but `data` shapes now carry structured status:

| Event | New shape | Frontend rendering |
|---|---|---|
| `stage1_complete` | Each entry has `status: "ok" \| "error"`; errors carry structured `LLMError` | Failed council members render with red dot + error UI instead of being silently dropped |
| `stage2_complete` | Each entry has `parse_status: "ok" \| "partial" \| "parse_error"` | Partial-parse badge; aggregate `partial: true` flag rendered as banner |
| `stage3_complete` | `status: "ok" \| "error"` at top level | Error state UI replaces the misleading legacy fake-error string |
| `title_failed` | New event | Sidebar handles null titles gracefully |
| `error` | Sanitized: `{kind, detail, request_id}` | Stream-error banner; raw exceptions never shown |
| `cache_hit` | New event, fires before stage events | Pill-style `⚡ cached` banner above message |
| `routing_decision` | New event with `use_council, reason, classifier_used` | Pill-style `⚖ full council` or `↷ chairman only` banner |
| `solo_start` / `solo_complete` | New events for the SOLO chairman path | Skip Stage 1/2 rendering; show stage3 directly |

**Backward-compat:** old persisted conversations (without `status` field) still load and render via legacy code paths. The reader does not validate shape; the renderer falls through `typeof response === "string"` → "ok" for legacy data.

---

## 5. Verification — what was tested and how

### Unit tests (65 passing in <0.1s)

| File | Tests | Covers |
|---|---:|---|
| `tests/test_errors.py` | 14 | HTTP status → kind mapping, retryable set membership, `to_dict`/`from_dict` roundtrip incl. unknown-kind tolerance |
| `tests/test_parser.py` | 9 | Clean / partial / parse_error / tolerant-fallback / dedup / truncation cases for the Stage-2 ranking parser |
| `tests/test_query_model.py` | 9 | `httpx.MockTransport`-driven: 200, 401, 429+retry, 503+retry, 400, malformed response, parallel mixed-failure, max_tokens propagation |
| `tests/test_council_partial.py` | 5 | Failed members surfaced (not dropped), chairman fallback is structured-error not string, stage3 short-circuits on empty stage1, aggregate `partial` flag propagates |
| `tests/test_storage.py` | 8 | UUID validation rejects traversal, structured-error persistence, malformed-file resilience, null-title placeholder |
| `tests/test_cache.py` | 13 | Key stability under reordering/normalization, error envelopes never stored, LRU eviction at max_entries, get refreshes LRU position, hit_rate math |
| `tests/test_router.py` | 7 | Short-query gate (skip classifier), long-query gate (skip classifier), classifier outputs (factual_simple / complex / unparseable), classifier failure → conservative fallback to council |

### Live runtime verification

- `04_runtime_verification.md`: server booted with a fake key, every prior audit finding either confirmed or corrected with probe evidence (the path-traversal claim was downgraded honestly).
- `05_paid_verification.md`: $0.00331 of cheap-model traffic confirmed end-to-end pipeline behavior + revealed real-world hallucination on follow-up turn (now in `FUTURE_SCOPE.md`).
- `07_flagship_sweep_results.md`: $0.188 of flagship traffic against the cost-control layer confirmed routing/cap/cache mechanics empirically and produced the cost projection in `09_cost_and_scaling.md`.

---

## 6. What was deliberately NOT built (scope discipline)

The brief's "scope ruthlessly" requirement, applied honestly:

| Item | Why not in this branch | Where it lives |
|---|---|---|
| **Eval framework (PS 2 deliverable #3)** | Specced fully but not implemented. Needs ~$65 LLM budget for the full sweep + ~4 senior-eng days. The cost-control layer's "no quality loss" claim depends on this measurement; **we should not deploy the routing layer to production traffic without it.** | Spec in `06_proposed_changes.md` §3; tracked in `FUTURE_SCOPE.md` as **P1**. |
| SQLite migration (D1, D2) | The 64% data-loss race condition under multi-worker uvicorn is real. Until this lands, **stick to single-worker uvicorn.** Separate branch for reviewability. | Spec in `06_proposed_changes.md` §5 P1-1; `FUTURE_SCOPE.md` P1. |
| Conversation memory across turns (G2, M3) | The chat product still doesn't actually chat. Live-confirmed hallucination in `05_paid_verification.md`. | `06_proposed_changes.md` §5 P1-2; `FUTURE_SCOPE.md` P1. |
| Per-IP rate limiting (F3) | Body-size cap landed; per-IP rate limit didn't. | `FUTURE_SCOPE.md` P1. |
| Streaming chairman tokens through to SSE (C-C) | Latency win, not a correctness or cost win. | `FUTURE_SCOPE.md` P2. |
| Semantic cache (M4 follow-on) | Exact-match cache catches FAQ-shape traffic; semantic cache is a tail-of-distribution improvement. | `FUTURE_SCOPE.md` P2. |
| Auth + per-user data isolation (D4, M11) | Different problem class; pulling forward would balloon scope. | `FUTURE_SCOPE.md` P2. |
| Frontend test suite, CI, observability surface, multi-host cache backend | Each is its own ~1-day workstream. | `FUTURE_SCOPE.md` P2/P3. |

---

## 7. The eval-framework gap, owned

The brief is explicit: **"Eval is not optional. However you define quality, measure it. A system with no eval is a system you cannot improve."**

I did not build the eval framework. I specced it (100-question dataset, 4 conditions, 6 metrics, calibrated judge), tied it to the routing-quality measurement, and put it as P1 in `FUTURE_SCOPE.md`. **This is the single largest gap between brief and delivery.**

The cost-control layer in this branch is therefore best framed as: *"the mechanics work and the measured savings are real, but whether the routing decisions preserve quality is a hypothesis until the eval framework runs."* The router's classifier output is logged and persisted in `metadata.routing` for every query, so the eval framework — when it lands — has a free A/B dataset to grade against.

---

## 8. How to verify everything in this brief

```bash
# 1. Branch + commits
git clone https://github.com/abhimazu/llm-council.git
cd llm-council
git checkout changes
git log --oneline master..changes        # → 11 commits

# 2. Backend tests (no LLM spend)
pip install -e .
pip install pytest pytest-asyncio
pytest tests/ -v                          # → 65 passed

# 3. Frontend build
cd frontend && npm install && npm run build
                                          # → 202 modules transformed

# 4. End-to-end smoke test (with a real OpenRouter key)
echo "OPENROUTER_API_KEY=sk-or-v1-..." > .env
./start.sh                                # backend on :8001, frontend on :5173
curl -s http://localhost:8001/api/cache/stats | jq
                                          # → {hits, misses, stores, evictions, hit_rate}
```

For the cost-control validation specifically, the sweep artifacts (`raw_calls.jsonl`, `queries.jsonl`, `run_phase.py`) live in `llm-council-changes/sweep_artifacts/` and can be replayed in ~5 minutes.

---

## 9. Documents in this submission package

| File | Purpose |
|---|---|
| `01_repo_scouting_methodology.md` | How we picked the audit target |
| `02_finalist_verification.md` | Why `karpathy/llm-council` over the other two finalists |
| `03_audit_hypothesis.md` | The 47 catalog items + 22 missing features |
| `04_runtime_verification.md` | Live verification of every audit finding |
| `05_paid_verification.md` | $0.00331 cheap-model verification |
| `06_proposed_changes.md` | The full proposal (eval + cost + P1 + P2 + out-of-scope) |
| `07_flagship_sweep_results.md` | $0.188 flagship-model cost-control validation |
| **`08_production_enhancements.md`** | **This doc** |
| `09_cost_and_scaling.md` | TCO at 10k users/day with real cloud + API pricing |
| `CHANGES.md` (in branch) | Per-finding map of audit IDs to commit locations |
| `FUTURE_SCOPE.md` (in branch) | What's deliberately not in the branch |
| `flagship_sweep_results.xlsx` | Sweep telemetry in 7 spreadsheet tabs |
