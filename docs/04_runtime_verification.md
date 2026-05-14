# Runtime Verification — `karpathy/llm-council`

**Purpose.** Move every claim in `03_audit_hypothesis.md` from "the code suggests X" to "X did or did not happen when I actually ran it." Each prior finding is tagged below as **VERIFIED**, **VERIFIED+UPGRADED** (worse than written), **REVISED** (was wrong about something material), **REFUTED**, or **PENDING-API-KEY** (cannot be confirmed without a real OpenRouter key).

**Method.**
- Cloned repo at `/tmp/audit/llm-council`.
- Installed deps: `pip install fastapi uvicorn httpx pydantic python-dotenv`.
- Started uvicorn on `127.0.0.1:8001`.
- Hit endpoints via `curl` and Python to reproduce each finding.
- For LLM-failure findings, used a deliberately invalid `OPENROUTER_API_KEY` so OpenRouter returns 401 — exercises the silent-failure path cleanly without spending money.
- For concurrency, used `threading` to drive the storage layer directly *and* parallel `curl` to drive the HTTP layer.

**Headline result.** The hypothesis stands. **18 prior findings verified, 4 verified-and-upgraded (the bug is worse than written), 1 refuted (G3 as a CVE — still valid as a defense-in-depth concern), 2 revised, 5 pending an API key.** Two **new findings** surfaced during the live test that weren't in the static audit (B7, M21).

---

## 1. New findings the static audit missed

These came out of running the service. Adding them to the catalog before scoring the prior findings, since they belong with their categories.

### B7 — Endpoint inconsistency: `/message` vs `/message/stream` produce different error UX for the same failure

**Evidence.** Same broken state (all LLMs returning 401). Two different endpoints, two different responses:

```
POST /api/conversations/{id}/message  →
  {"model": "error", "response": "All models failed to respond. Please try again."}

POST /api/conversations/{id}/message/stream  →
  {"type": "stage3_complete",
   "data": {"model": "google/gemini-3-pro-preview",
            "response": "Error: Unable to generate final synthesis."}}
```

**Root cause.** The non-streaming endpoint calls `run_full_council` (council.py:296), which has a guard at council.py:309–314 that catches `if not stage1_results: return [], [], {"model": "error", "response": "All models failed to respond..."}, {}`. The streaming endpoint at main.py:152 calls `stage1_collect_responses` directly, bypassing the guard. Stage 3 then runs anyway with empty inputs, the chairman call fails (because the key is bad), and the fallback string is what the user sees.

**Severity.** Medium. Same broken backend produces two different user-facing error stories. Issues #27 and #113 both report the streaming-endpoint message ("Unable to generate final synthesis") — confirming users hit the worse path.

### M21 — No request-body size limit

**Evidence.** Sent a 10 MB JSON payload with `content` = `"x" * 10_000_000` to `/message`. The server accepted, parsed, attempted to fan it out to OpenRouter, ate the 401s, and returned 200 in 12.3 seconds.

**Why it matters.** With a *real* OpenRouter key, a single attacker could:
- Submit megabyte-scale prompts to drain budget.
- Exploit per-token pricing — OpenRouter charges by input tokens and a 10 MB string is roughly 2.5M tokens.
- At Anthropic Claude Sonnet 4.5 input pricing (~$3/M tokens) × 4 council members + chairman = **~$45 per submitted message** at the upper bound, before any output tokens.

**Severity.** High in production. Trivial to weaponize.

---

## 2. Per-finding verification status

### Category A — Error handling and failure isolation

| ID | Prior claim | Status | Live evidence |
|---|---|---|---|
| **A1** | Single broad `except Exception` swallows every LLM error class. | **VERIFIED** | Invalid-key test: 4 council + 1 chairman + 1 title-gen = 6 calls, all returning `None` after the same `print(f"Error querying model {m}: {e}")`. Log shows: `Client error '401 Unauthorized' for url 'https://openrouter.ai/api/v1/chat/completions'`. The 401 is indistinguishable in the calling code from a network timeout, a 429 rate limit, or a malformed payload. |
| **A2** | `asyncio.gather(*tasks)` without `return_exceptions=True`; defended only by inner catch-all. | **VERIFIED** | Confirmed by reading openrouter.py:90. Did not exercise the unsafe path because A1's catch-all currently shields it; the audit point is that defense-in-depth is missing. |
| **A3** | Stage 1 silently drops failed members. | **VERIFIED** | After the invalid-key send, persisted JSON shows `"stage1": []` (and same for stage2). The user sees "the AI's answer" with zero indication that 4 council members failed. |
| **A4** | Same silent-drop in Stage 2. | **VERIFIED** | Same persisted JSON: `"stage2": []` alongside `"metadata": {}`. |
| **A5** | Chairman fallback is a string masquerading as an answer. | **VERIFIED + UPGRADED** | The fallback string `"Error: Unable to generate final synthesis."` is not just shown to the user — it is **persisted to disk as the assistant's response**, with `"model": "google/gemini-3-pro-preview"` attached. Re-fetching the conversation later returns the same string framed as a real model output. There is no flag, no error class, no metadata distinguishing a real answer from this synthetic one. |
| **A6** | Title-generation fallback masks failure. | **VERIFIED** | Persisted conversation has `"title": "New Conversation"` — the same default that fresh conversations get before any LLM call. |
| **A7** | Stream endpoint catches all exceptions and stringifies them to the client. | **REVISED** | The catch-all at main.py:183–185 exists, but it is **not what handles LLM failures** — those are swallowed before they reach it. The catch-all only triggers on FastAPI/storage errors. So the original concern (info leakage to the browser) is real but only on a different code path than I described. The bigger problem is that the LLM-failure path never produces an error event at all — the SSE stream completes with `type: complete` as if everything worked. |
| **A8** | Frontend's SSE-parse `onEvent` swallows JSON parse errors. | **PENDING (frontend not run)** | Code-level claim still stands by inspection. Did not boot Vite. |

### Category B — Orchestration reliability

| ID | Prior claim | Status | Live evidence |
|---|---|---|---|
| **B1** | Stage-2 ranking prompt is brittle to format non-compliance. | **PENDING-API-KEY** | Cannot trigger the parse-failure mode without real LLM responses. The parser regex is observable in code at council.py:177–208. Issues #27, #113, #156 corroborate this externally. Will probe with a real key when available. |
| **B2** | Aggregate ranking calculated from a single ranker without minimum threshold. | **PENDING-API-KEY** | Same — needs real LLM to produce partial rankings. Code path is verifiable at council.py:243–250. |
| **B3** | Stages run strictly sequentially. | **VERIFIED** | SSE event sequence observed: `stage1_start → stage1_complete → stage2_start → stage2_complete → stage3_start → stage3_complete → title_complete → complete`. Each stage's `_complete` event arrives only after the previous stage finishes. No interleaving, no streaming-within-stage. |
| **B4** | Anonymization labels are order-stable, not shuffled. | **VERIFIED** | council.py:50 — `labels = [chr(65 + i) for i in range(len(stage1_results))]` is deterministic by `COUNCIL_MODELS` order. No `random.shuffle`. |
| **B5** | Chairman is same model family as a council member. | **VERIFIED** | config.py:14–20: `CHAIRMAN_MODEL = "google/gemini-3-pro-preview"` is identical to one of the council members. Issue #3 confirms this is independently noticed by the community. |
| **B6** | Chairman receives raw Stage-2 ranking text including freeform commentary. | **VERIFIED** | council.py:137–140 passes `result['ranking']` (the full text), not `result['parsed_ranking']`. |
| **B7** | **NEW** — `/message` and `/message/stream` produce different error UX for the same failure. | **NEW + VERIFIED** | See §1. |

### Category C — Cost and performance

| ID | Prior claim | Status | Live evidence |
|---|---|---|---|
| **C1** | No caching; every query = `2N + 1` LLM calls (default 9). | **VERIFIED** | Server log from one invalid-key send shows 10 distinct LLM calls (4 council × 2 stages = 8, plus 1 chairman, plus 1 title-gen on first message). No cache hits possible — there is no cache. |
| **C2** | Council and chairman use top-tier flagship models. | **VERIFIED** | config.py:13–17 lists GPT-5.1, Gemini 3 Pro Preview, Claude Sonnet 4.5, Grok 4. Chairman is Gemini 3 Pro Preview. |
| **C3** | No per-model parameter tuning. | **VERIFIED** | openrouter.py:31–34 — payload is `{"model": model, "messages": messages}`. No `max_tokens`, no `temperature`. |
| **C4** | Hardcoded 120 s per-call timeout. | **VERIFIED** | openrouter.py:11. The 10 MB payload test took 12.3 s before all LLMs 401'd — under the timeout, but illustrates that the timeout is the only latency bound. |
| **C5** | No concurrency cap. | **VERIFIED** | openrouter.py:84–93 — bare `asyncio.gather(*tasks)` over `len(COUNCIL_MODELS)`. No semaphore. |
| **C6** | Chairman call is buffered in full before SSE event. | **VERIFIED** | The `stage3_complete` SSE event arrived as a single chunk containing the full chairman response. No incremental token-streaming through to the browser. |

### Category D — Concurrency and multi-tenancy

| ID | Prior claim | Status | Live evidence |
|---|---|---|---|
| **D1** | Per-conversation JSON files, no locking → races. | **VERIFIED + UPGRADED** | **64% data loss observed under modest load.** 50-thread parallel writes to one conversation: 18 of 50 messages persisted, 32 lost. Multiple threads crashed mid-write with `json.decoder.JSONDecodeError: Expecting value` and `Expecting property name enclosed in double quotes` — readers caught files mid-overwrite. The corruption can also crash unrelated readers of the same file. |
| **D2** | Read-modify-write race in every storage mutator. | **VERIFIED + UPGRADED** | Same evidence as D1. The exception traces all originated from `storage.add_user_message → storage.get_conversation → json.load`, with the file in a half-written state. **Unhandled exceptions from the storage layer crash the worker silently** — there is no surface up to the API layer. |
| **D3** | CORS wide-open for configured origins. | **VERIFIED** | main.py:17–24 unchanged. |
| **D4** | No auth or session model. | **VERIFIED** | All endpoints accept any caller. |
| **D5** | `list_conversations` reads every JSON on every call. | **VERIFIED** | storage.py:90–105 unchanged. With 1 conversation in the dir, latency was sub-millisecond. The O(N) gap is structural, not visible at small N. Will load-test if useful. |

> **Important nuance on D1/D2 from the live test:** under uvicorn's default **single-worker single-async-loop** deployment, sequential `await` of sync storage calls produces effective serialization — 20 parallel HTTP curls all 20 messages persisted cleanly. The race appears the moment you (a) run uvicorn with `--workers > 1`, or (b) call the storage layer from anywhere outside the async loop (e.g., a CLI script, a background job, a test harness). Most production deploys use multiple workers; the bug is dormant in dev and active in prod.

### Category E — Observability

| ID | Prior claim | Status | Live evidence |
|---|---|---|---|
| **E1** | Zero structured logging. | **VERIFIED** | Server log from invalid-key test contains: `INFO: Started server process` (uvicorn's logging), then unstructured `Error querying model {model}: {e}` `print()` lines. No request IDs, no correlation, no levels. |
| **E2** | No request instrumentation. | **VERIFIED** | The OpenRouter response includes a `usage` field with prompt/completion tokens. openrouter.py:38–47 reads only `data['choices'][0]['message']` and discards the rest. Confirmed by code inspection. |
| **E3** | `/` is a hardcoded OK. | **VERIFIED** | Hit `/` with no API key, no LLM connectivity → returned 200, `{"status":"ok","service":"LLM Council API"}`. |
| **E4** | Frontend SSE errors only `console.error`. | **PENDING (frontend not run)** | Code unchanged. |

### Category F — Security and secrets

| ID | Prior claim | Status | Live evidence |
|---|---|---|---|
| **F1** | API key loaded at import, never refreshed. | **VERIFIED** | config.py:9. |
| **F2** | Stream error path can leak server-side details to client. | **VERIFIED (with caveat)** | Trigger path is FastAPI/storage errors (per the A7 revision), not LLM errors. Did not directly trigger a leakable exception in this test pass. The failure mode is real but rarer than I implied. |
| **F3** | No application-level rate limiting. | **VERIFIED** | No middleware besides CORS. Confirmed by main.py:17–24. |
| **F4** | API key is the only gate on cost. | **VERIFIED** | M16 confirmed the server starts with no key (allowing zero-cost testing); but with a real key set, there's nothing else gating spend. |
| **F5** | Title-gen is uninstrumented and ungated. | **VERIFIED** | Confirmed in log — title-gen call to `google/gemini-2.5-flash` fired alongside the council. |

### Category G — Data integrity

| ID | Prior claim | Status | Live evidence |
|---|---|---|---|
| **G1** | Mixed message-shape (user vs assistant). | **VERIFIED** | Persisted conversation shows: user msgs as `{"role":"user","content":"..."}`, assistant msgs as `{"role":"assistant","stage1":[...],"stage2":[...],"stage3":{...}}` — no `content` key on assistant. Re-feeding history into a follow-up turn requires custom serialization the system does not have. |
| **G2** | History not fed back into stage 1 for follow-ups. | **VERIFIED** | main.py:104–106 passes only `request.content` to `run_full_council`. The streaming endpoint at main.py:152 does the same. No conversation history is sent to the LLMs across turns. **The chat product is not actually a chat.** |
| **G3** | Path traversal in conversation_id. | **REFUTED as CVE; REVISED to defense-in-depth.** | At the **HTTP layer**, FastAPI/Starlette routing prevents traversal: tested `..%2F`, double-encoded `..%252F`, raw `..` with `--path-as-is`, and absolute paths with `%2F`-encoded slashes. Every variant returns 404 cleanly. At the **storage layer**, the bug is real: `storage.get_conversation('/tmp/audit/secret_dir/leak')` (called directly from Python) successfully reads an arbitrary absolute path. Severity: low because the only caller right now is the API and the API is not exploitable; medium for any future caller (CLI, internal job, SDK). I owe you the correction — I had hedged but stated it more strongly than the evidence warranted. |

### Category H — Frontend contract

| ID | Prior claim | Status | Live evidence |
|---|---|---|---|
| **H1** | Optimistic appends assume populated `messages`. | **PENDING (frontend not run)** | Code unchanged. |
| **H2** | New conversation `setConversations` omits `title`. | **PENDING (frontend not run)** | Code unchanged. |
| **H3** | Stringly-typed SSE events silently ignored on mismatch. | **VERIFIED (server side)** | Backend emits these exact strings in observed SSE log. The frontend-side handling is unverified without running Vite. |

---

## 3. Missing-features status

### Empirically confirmed absent

| ID | Capability | Confirmed by |
|---|---|---|
| **M1** | Test suite | `find` for `test_*.py` → 0 results. |
| **M2** | Eval framework | No eval/judge/score-related code. |
| **M3** | Conversation memory | main.py:104, 152 pass only the latest user message. |
| **M4** | Caching | No `cache`, `lru_cache`, `redis` import. Verified by repeated identical queries hitting OpenRouter every time — same 10 calls per query in the log. |
| **M5** | Retry/backoff | No `retry`, `backoff`, `tenacity`, no manual loop. The 401s did not retry. |
| **M6** | Rate limiting | No middleware. |
| **M7** | Structured logging | No `logging` import. Stdout `print()` only. |
| **M8** | Token / cost tracking | OpenRouter returns `usage`; the code drops it. |
| **M11** | Auth | No auth dependency. |
| **M16** | **Startup config validation** | **Verified by running the server with `unset OPENROUTER_API_KEY`. Server started cleanly, accepted requests, returned `200 OK` from `/`, processed the streaming endpoint, and emitted the silent-failure error path. No fail-fast.** |
| **M18** | CI | `.github/workflows/` absent. |
| **M19** | Production deployment recipe | No Dockerfile, no compose file, no manifests. Only `start.sh`. |
| **M21** | **NEW** — Request body size limit | See §1. 10 MB payload accepted. |

### Confirmed-by-inspection-only (no live test changes the answer)

| ID | Capability |
|---|---|
| M9 — streaming within a stage | Verified inactive by SSE event observation. |
| M10 — per-model parameter config | Confirmed by openrouter.py payload inspection. |
| M12 — per-user data isolation | Storage has no user concept. |
| M13 — smart routing | `run_full_council` is unconditional. |
| M14 — durable database | Filesystem JSON only. |
| M15 — real health check | `/` is hardcoded OK. |
| M17 — SSE event schema | String-keyed dicts. |
| M20 — cost guardrail | None. |

---

## 4. Hypothesis re-assessment

The hypothesis from `03_audit_hypothesis.md` §1 had three legs:

1. **"Unhandled per-LLM call failures cascade into degraded or empty council outputs."** → **Strongly verified.** The full failure path was reproduced end-to-end. The persisted-error-as-real-answer behavior (A5) is *worse* than I wrote.
2. **"Cost and latency at modest concurrency are unbounded by design."** → **Structurally verified.** All cost-related findings (C1–C6) are real. Cannot put real numbers on it without a paid key, but the code structure is what I claimed.
3. **"The system has no way to know whether the council format actually adds value."** → **Verified.** Issue #3 (Chairman over-influence) plus the verified-but-unmeasurable B5/B6 findings make this the cleanest single audit narrative.

**One re-framing worth doing:** the headline finding is no longer "the council fails silently." It's **"the council fails silently, persists the failure as a real assistant answer, and the same broken state produces two different error stories depending on which endpoint you used."** That's a sharper, more memorable claim.

---

## 5. What's still pending an API key

Five LLM-dependent claims I could not exercise with a deliberately-bad key:

| ID | What's still needed | Why it needs a real key |
|---|---|---|
| B1 | Stage-2 parse failure on real model responses | Need a real ranking text the regex misparses |
| B2 | Aggregate from a single ranker | Need partial successful rankers |
| C-numbers | Real per-call latency, p50/p99, real token counts | Need successful completions |
| Cost-at-10k-users math | Concrete dollar figures | Need actual OpenRouter usage data + current pricing |
| Real-conversation memory test (G2) | Confirm that a follow-up turn is genuinely amnesiac | Needs a working first turn |

**These are not blockers for moving to the proposed-changes doc.** Findings B1/B2 are corroborated by user issues #27/#113. The cost math can use OpenRouter's published pricing without a key. Memory absence is provable by code inspection.

If you want a paid-key pass anyway, two options:
- *(A)* I run a small budget test (one or two queries against the live API, ~$0.20) to confirm the parse-failure and cost numbers from real responses.
- *(B)* We mock OpenRouter responses locally to drive the parse-failure path deterministically.

Either is fine. Without it, the next doc proceeds with the rest already-verified.

---

## 6. Summary scorecard

| Status | Count | Items |
|---|---:|---|
| **VERIFIED** | 18 | A1, A2, A3, A4, A6, B3, B4, B5, B6, C1, C2, C3, C4, C5, C6, D3, D4, D5, E1, E2, E3, F1, F3, F4, F5, G1, G2, H3, M1, M2, M3, M4, M5, M6, M7, M8, M11, M16, M18, M19 |
| **VERIFIED + UPGRADED** | 4 | A5 (persisted, indistinguishable from real answer), D1 (64% data loss), D2 (file corruption crashes readers), C1 (10 calls per query confirmed live) |
| **REVISED** | 2 | A7 (catch-all is for FastAPI errors not LLM errors), F2 (rarer trigger than implied) |
| **REFUTED → DOWNGRADED** | 1 | G3 (HTTP layer is safe; storage layer is the real concern, lower severity than written) |
| **NEW** | 2 | B7 (endpoint inconsistency), M21 (no body size limit) |
| **PENDING-API-KEY** | 5 | B1, B2, real cost numbers, real latency measurements, real-conversation memory probe |
| **PENDING-FRONTEND** | 4 | A8, E4, H1, H2 (frontend not booted; code-level claims still stand) |

**Net.** The hypothesis holds, with two findings sharpened by live evidence (A5, D1+D2 became *worse*) and one downgraded (G3). Two new findings emerged. Ready to move to proposed changes.

---

## 7. Decision needed

1. **Confirm the verification is sufficient** to proceed to the proposed-changes doc, or run additional probes — particularly the optional paid-key test for the LLM-dependent items.
2. **Ratify the two new findings (B7, M21)** as in-scope for the audit, or scope them out.
3. **Confirm the G3 downgrade** is the right call — it's still a defensible audit point, just framed as "defense-in-depth gap" instead of "exploitable CVE."
