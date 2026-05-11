# Future scope

Tracking everything from the production-readiness audit that did **not**
land in this branch, with clear conviction about what should ship next
and why. Each item carries a brief, a `Why now / Why later` axis so the
priority is defensible, and an effort estimate so it can be slotted into
a sprint.

This file is the canonical "what's next" doc for `karpathy/llm-council`
on this fork. As items land, **delete the corresponding section** rather
than marking it done — the file should always reflect future work only.

---

## Conviction summary

The critical refactor (§2 of the audit), the cost-control layer (§4),
and the eval framework (§3) are in this branch. The harness was
exercised by a $0.04 cheap-model dev sweep — full numbers in
`docs/10_evals.md`. The next three things to build, in this order:

1. **Flagship eval sweep (§3 follow-on).** The harness exists and runs;
   the dev sweep validated it on cheap models. The remaining work is
   running the full 100-question × 3-condition sweep on the May 2026
   flagship lineup (~$30–60). Until that run completes, the routing
   thresholds are *measured on cheap models and hypothesised at
   flagship scale.* If you fund only one of the items below, fund
   this one — every quality claim in Part V of the submission depends
   on it.
2. **P1-1 storage migration (SQLite).** The 64 % data-loss race
   condition is dormant under single-worker uvicorn but active in any
   multi-worker production deploy. Until this lands, deploying this
   branch to production means "single worker only."
3. **P1-2 conversation memory across turns.** The chat product currently
   doesn't actually chat — every turn is treated as context-free. Doc 05
   §C7 demonstrated a real hallucination in production-shaped traffic
   from this gap.

Everything below is ordered against that priority spine.

---

## P1 — flagship eval sweep (the framework already exists)

**The eval framework now lives in the branch under `evals/`** with full
spec in `docs/10_evals.md`. It was exercised by a 21-row cheap-model
dev sweep (total spend $0.044) that validated the routing layer on a
small sample: 5/5 factual queries routed to solo and correct; 3/3
traps declined gracefully across all conditions; forced council cost
~46× solo on factual queries for identical correctness. **What is
still pending is the full sweep on flagship models.**

### What to run (not build)

```bash
# 1. Restore flagship models in backend/config.py if you've swapped them
#    (the committed config already targets flagship — only swap back if
#    you ran the cheap-model sweep locally).
# 2. Run the full sweep with a budget guard:
python -m evals.run_sweep \
    --conditions C0,C2,R \
    --budget 80 \
    --time-budget 3600 \
    --output evals/runs/$(date +%Y%m%d_%H%M%S)_flagship.jsonl

# 3. Roll up:
python -m evals.report evals/runs/<file>.jsonl
```

The orchestrator is resume-aware (`--resume <path>`); a sweep that
trips the budget cap or the time budget exits cleanly with work-to-date
checkpointed to JSONL.

### Cost

- Full 100-question × 3-condition sweep on flagship models: **~$30–60**
  (estimate from the cheap-model cost ratio × the May 2026 flagship
  per-token prices; the C2 forced-council condition dominates).

### Effort

0 engineering days. This is "spend, not engineering." The harness, the
dataset, the judges, the runners, and the report generator are all
committed code on the branch.

### Two small harness follow-ups (each <1 hour)

1. **Populate `raw_envelope.ranks_per_member` for C2 rows** in
   `evals/runners/council_full.py` so the `self_favoritism` metric can
   be re-measured against the flagship lineup. The cheap-model dev
   sweep didn't capture this field; the flagship sweep should.
2. **Make the rubric judge JSON parser tolerant** of preamble before
   the JSON block (`evals/judges/rubric.py:_extract_json`). The one
   open-ended question in the dev sweep returned `avg_score=0` due to
   a JSON parse failure; a regex grab for the first `{...}` block fixes
   it.

### Why this is P1, not P0

It's gated only behind LLM spend. With the harness committed and dev-sweep
validated, this is a 1-hour budget approval + 1-hour sweep wall-clock +
re-render of the report. **Do not deploy the routing layer to flagship
production traffic without this evidence**, but ramp-up on cheap models
is already validated.

---

## P1 — storage migration to SQLite (audit §P1-1, addresses D1, D2, D5)

**The 64 % data-loss race is dormant under `uvicorn --workers 1` and
active under any multi-worker deploy.** Doc 04 demonstrated 32 of 50
parallel writes lost, plus `JSONDecodeError` corruption that crashes
unrelated readers.

### What to build

- `backend/storage_sqlite.py` implementing the same public interface as
  `backend/storage.py`. Schema:

  ```sql
  CREATE TABLE conversations (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    title TEXT,
    raw_json TEXT NOT NULL  -- the messages array
  );
  CREATE INDEX idx_created_at ON conversations(created_at DESC);
  ```

- A migration script `scripts/migrate_storage.py` that walks the JSON
  data dir and bulk-inserts existing conversations.
- A boolean config `USE_SQLITE_STORAGE` so the switch is reversible.
- Use `aiosqlite` for async access; the FastAPI app is async, the
  storage layer should be too.

### Why SQLite, not Postgres

Single-process write coordination is enough for the foreseeable scale.
SQLite + WAL mode handles concurrent writers correctly without any
extra infrastructure. Postgres is correct for >1 host; SQLite is
correct for >1 worker on the same host. Start with SQLite; document
the swap point for Postgres in the module's docstring.

### Effort

1 senior-engineer day for the migration + data backfill script.

### Until this lands

**Stick to single-worker uvicorn.** Document this in `start.sh` or the
README so nobody accidentally deploys at scale and loses data silently.

---

## P1 — conversation memory across turns (audit §P1-2, addresses G2, M3)

**The chat product currently doesn't actually chat.** `main.py:104`
passes only `request.content` to `run_full_council`, not the full
conversation. Each user turn is treated as context-free. Doc 05 §C7
demonstrated a real hallucination from this gap (one council member
fabricated a "previous question" because it had no memory).

### What to build

- `run_full_council(messages: List[Message])` accepting the full
  prior-turn history rather than just the latest user message.
- Stage 1 prompt becomes a multi-turn `messages` array fed directly
  to OpenRouter.
- Stage 2 ranker prompt becomes "rank these responses to the latest
  turn given the conversation context."
- New config: `MAX_HISTORY_TOKENS` (default ~4000) to truncate old
  turns when the prompt would exceed reasonable cost.
- Truncation strategy: keep the user/assistant pair from the most
  recent N turns, summarize older turns via a cheap call (off by
  default).

### Cost interaction

This adds input tokens to every Stage-1 and Stage-2 call, in
proportion to history length. Combined with the cost-control layer
in this branch, the chairman input trim already mitigates it for
Stage 3. Net effect at typical chat depths is +20-40% prompt tokens
per turn — non-trivial but acceptable for a chat product.

### Effort

1.5 senior-engineer days. Most of the work is prompt engineering for
the multi-turn Stage-2 ranking case (the rankers must consider both
the latest turn AND the relevance to prior context).

---

## P1 — per-IP rate limiting (audit §P1-3, partly addresses F3, M21)

**Body-size cap (M21) is in this branch.** Per-IP rate limiting is not.
A determined caller can still exhaust the OpenRouter budget faster than
the cache can absorb.

### What to build

- `slowapi` middleware. 60 requests / minute per IP for the message
  endpoints; 10 streaming sessions per IP concurrent.
- New config: `RATE_LIMIT_ENABLED` (default True), `RATE_LIMIT_RPM`
  (default 60), `RATE_LIMIT_CONCURRENT_STREAMS` (default 10).
- Rate-limit responses are typed errors and surface through the
  structured-error contract from this branch (`kind="rate_limited"`).

### Effort

0.5 senior-engineer day.

---

## P2 — streaming chairman + per-call timeout tuning (audit §C-C)

**Cost-control C-C in the proposed-changes doc.** The chairman call is
the slowest part of the pipeline (4–5 s with cheap models, 10–60 s
with reasoning-mode flagship models). Currently the full chairman
output is buffered before any SSE event fires (`backend/main.py`
emits `stage3_complete` only after `await stage3_synthesize_final`
completes).

### What to build

- New `query_model_streaming` in `openrouter.py` that uses
  `httpx.AsyncClient.stream("POST", ...)` and yields tokens via a
  callback.
- New SSE event: `stage3_delta` carrying incremental content for the
  in-progress chairman synthesis. Frontend appends as deltas arrive
  and renders with the existing markdown component.
- Per-call timeouts driven by per-stage config: `TIMEOUT_TITLE`,
  `TIMEOUT_STAGE1`, `TIMEOUT_STAGE2`, `TIMEOUT_CHAIRMAN`. Reduce p99
  latency on stuck calls.

### Why P2, not P1

It's a UX / latency win, not a correctness or cost win. The current
pipeline already works correctly; this just makes it *feel* faster.
Useful, but not gating production.

### Effort

1 senior-engineer day (back-end + small frontend update for delta
rendering).

---

## P2 — semantic caching (audit §M4 follow-on)

**This branch ships exact-match caching only.** Two queries that mean
the same thing but differ in a comma or capitalization will both miss.

### What to build

- Embedding model integration (OpenAI `text-embedding-3-small` or
  `nomic-embed-text` via OpenRouter — cheap).
- `CouncilCache.get_semantic(query, threshold=0.95)` — embed query,
  search stored entries by cosine similarity, return the cached
  envelope if any entry is above threshold.
- Storage: in-memory FAISS or a SQLite VSS extension. For < 100k
  cached entries, in-memory is fine.

### Why P2

Exact-match caching captures most of the FAQ-shape traffic. Semantic
caching is a tail-of-the-distribution improvement that's worth
building but not gating.

### Effort

2 senior-engineer days including the embedding model wiring and the
threshold tuning against the eval dataset.

---

## P2 — observability surface (audit §E1, §E3 follow-on)

This branch logs every LLM call as a structured JSON line with
`model`, `kind`, `elapsed_ms`, `prompt_tokens`, `completion_tokens`,
`cost_usd`, `finish_reason`. That's good. What's missing:

- A real readiness check at `/healthz/ready` that verifies OpenRouter
  reachability + storage writability. Distinct from the static `/`
  liveness check.
- A real `/metrics` endpoint exposing Prometheus-format counters for
  cache hits/misses, routing decisions, per-stage latency p50/p95/p99,
  cost by model.
- Per-request `X-Request-Id` header propagation through to LLM call
  logs (currently logged at the SSE layer only).

### Effort

1 senior-engineer day.

---

## P2 — auth and per-user data isolation (audit §D4, M11, M12)

**This branch ships unauthenticated endpoints.** Every conversation is
visible to every caller of the API. Nothing about the original Karpathy
hack changed this; nothing this branch adds changes it.

### What to build

The first iteration is API-key auth via `Authorization: Bearer`
headers + a `users` table mapping keys to user IDs. Conversations get
a `user_id` foreign key. Storage layer scopes every read/write by
the authenticated user.

### Why P2

It's a different problem class (identity / IAM) and pulling it forward
into this PR would balloon scope. A separate branch is correct.

### Effort

2-3 senior-engineer days for a clean v1; multi-day work for a fuller
identity story (OAuth, sessions, rotation).

---

## P3 — frontend test suite

**This branch ships zero frontend tests** — `npm run build` only
verifies that the JSX compiles, not that it behaves correctly.

### What to build

`vitest` + `@testing-library/react`:
- `Stage1.test.jsx` — error tabs render, ok tabs render, mixed states.
- `Stage2.test.jsx` — partial-parse badge appears, aggregate-partial
  banner appears, deAnonymizeText guards null.
- `Stage3.test.jsx` — ok / error / absent paths.
- `App.test.jsx` — SSE event handler routes new events correctly.

### Effort

1 senior-engineer day for the basic suite. The components are small
enough that thorough coverage doesn't take long.

---

## P3 — CI / GitHub Actions

`pytest tests/` and `npm run build` run cleanly locally. They should
run on every PR.

### What to build

- `.github/workflows/ci.yml` running:
  - `pytest tests/ -v` (Python 3.10, 3.11, 3.12 matrix)
  - `npm run build`
  - Both on every PR + push to master.
- A status badge in the README.

### Effort

0.5 senior-engineer day.

---

## Out of scope (explicit "we will not do this" list)

These appeared in the audit but are deliberately not on the roadmap:

- **Multi-tenant SaaS hosting.** The original Karpathy framing is
  local-first; that's the right framing. If you want this hosted,
  fork to a separate product line, don't bend this codebase to do it.
- **Migrating off OpenRouter.** OpenRouter is the unified provider
  layer; replacing it with per-provider integrations would
  multiply the surface area of the LLM-call layer. The structured
  error system in this branch is provider-agnostic on purpose so
  this swap is *possible*, but it's not on the roadmap.
- **Replacing the chairman with majority voting / consensus algorithms.**
  Several issues on the upstream repo propose this. Without the eval
  framework, we have no way to measure whether it improves quality.
  Defer until the eval framework gives us a baseline.

---

## How to use this file

When you start work on one of the items above:

1. Open a branch named after the item: e.g. `eval-framework`,
   `sqlite-migration`, `streaming-chairman`.
2. Branch from `master` (after this `changes` branch is merged) or
   from `changes` if it isn't merged yet.
3. When the work lands, delete the corresponding section from this
   file as part of the merging PR. The file should always reflect
   work that hasn't shipped.
