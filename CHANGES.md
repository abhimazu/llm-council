# Changes — `changes` branch

This branch contains the §2 critical refactor from the production-readiness
audit (see `audit/06_proposed_changes.md` in the parent project workspace
for the full audit). It does **not** include the eval framework (§3), the
cost-control layer (§4), or the P1 follow-ups (§5). Those are deliberately
left for separate branches once §2 is merged.

## What this branch fixes

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

**24 of 47 audit findings + missing-features resolved by this single
branch.** The remaining 23 are P1 (separate branches) or out of scope
(see `audit/06_proposed_changes.md` §6).

## Wire-protocol changes (frontend will need updates)

The SSE event *names* are unchanged. The shape of `data` inside each
event has changed — frontends must handle the new `status` field.

### Before

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

### After

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

Two new SSE events:

- `title_failed` — title-gen failed; conversation has no title.
- `error` — fatal server-side error during streaming; carries `request_id`,
  `kind`, and `detail`. Never carries raw exception text.

### Persistence shape

Existing on-disk conversations remain readable (the reader does not
validate shape). New writes use the structured-status format above.
A migration script for old conversations is not included — frontend
should branch on the presence of the `status` field.

## Configuration

New environment variables (all optional with sensible defaults):

| Env var | Default | Purpose |
|---|---|---|
| `OPENROUTER_API_KEY` | (required, server fails fast at startup) | OpenRouter API key |
| `LOG_LEVEL` | `INFO` | Standard Python logging level |
| `LOG_JSON` | `0` | If `1`/`true`/`yes`, emit JSON-line logs |
| `DATA_DIR` | `data/conversations` | Where conversation JSONs are written |
| `CORS_ORIGINS` | `http://localhost:5173,http://localhost:3000` | Comma-separated allowed origins |

`backend/config.py` knobs (no env var, edit and redeploy):

| Constant | Default | Purpose |
|---|---|---|
| `STAGE1_MAX_TOKENS` | `None` (uncapped) | Output cap for council stage 1 |
| `STAGE2_MAX_TOKENS` | `None` (uncapped) | Output cap for council stage 2 ranking |
| `CHAIRMAN_MAX_TOKENS` | `None` (uncapped) | Output cap for chairman synthesis |
| `TITLE_MODEL` | `google/gemini-2.5-flash` | Cheap model for conversation titles |

## Running

```bash
# install
uv sync          # or: pip install -e .

# .env at repo root
echo "OPENROUTER_API_KEY=sk-or-v1-..." > .env

# run
./start.sh       # or: python -m uvicorn backend.main:app --reload --port 8001
```

## Testing

```bash
pip install pytest pytest-asyncio
pytest tests/ -v
```

45 backend unit tests pass in <0.1s. Frontend `npm run build` passes.
No LLM calls in the test loop; all httpx-mocked. Coverage:
- error classification matrix
- ranking parser (clean / partial / parse_error)
- query_model (success, 401, 429+retry, 503+retry, 400, malformed, parallel)
- council per-stage failure surfacing
- storage UUID validation, structured-error persistence, malformed-file
  resilience.

## What this branch does NOT do

In line with "scope ruthlessly" from the work-trial brief:

- **No SQLite migration.** The 64% data-loss race condition (D1/D2)
  under multi-worker uvicorn is real but the fix is a separate branch
  (P1-1) so the migration is reviewable in isolation.
- **No conversation memory across turns** (G2/M3). The chat product
  still treats each user message as context-free. Fix lives in P1-2.
- **No eval framework** (M2). The framework spec is in
  `06_proposed_changes.md` §3; implementation is a separate branch.
- **No cost-control layer** (smart routing, caching, budget caps).
  Spec in §4; needs the eval framework to land first to inform the
  routing boundary.
- ~~No frontend changes.~~ **Frontend is now updated** in commit
  `06647a8`. `Stage1`/`Stage2`/`Stage3` components render error
  states and partial parses; `App.jsx` handles `title_failed` and
  the new structured `error` event; `Stage3` detects the legacy
  `Error: Unable to generate final synthesis.` string in old
  persisted conversations and renders an error state for them.
  Vite build verified.
- **No CI / GitHub Actions.** Tests run locally; CI is a follow-up.

## How to push and review

```bash
# from this branch
git push -u origin changes

# open PR against master
gh pr create --base master --head changes \
  --title "Replace silent-failure cluster with structured errors" \
  --body "See CHANGES.md"
```
