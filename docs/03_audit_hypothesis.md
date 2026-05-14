# Audit Hypothesis — `karpathy/llm-council`

**Purpose.** Diagnostic-only doc. Captures the audit hypothesis, the full failure-mode catalog observed by reading the code, the missing-features inventory, the verbatim open-issues list, and the hypothesis-vs-evidence matrix that maps each audit finding to corroborating real-user reports.

**Scope.** This doc focuses on `karpathy/llm-council` as the implicit winner from `02_finalist_verification.md`. If the user wants to flip to `RealtimeVoiceChat` or `babyagi-2o`, the same structure applies — only the contents change.

**Out of scope.** No proposed changes, no fixes, no recommendations. Every finding here is a *what* and a *why-it-breaks*, not a *how-to-fix*. Fixes go in the next doc.

**Method.** Code read end-to-end (`backend/config.py`, `backend/openrouter.py`, `backend/main.py`, `backend/council.py`, `backend/storage.py`, `frontend/src/api.js`, `frontend/src/App.jsx`). Every finding cites exact file and line numbers from the cloned repo at HEAD as of the verification pass. Open issues fetched directly from the GitHub issues page.

---

## 1. The audit hypothesis

> **`karpathy/llm-council` is a Saturday-hack prototype that exposes — but does not survive — three classes of failure that emerge at any non-trivial usage level: (1) unhandled per-LLM call failures cascade into degraded or empty council outputs, (2) cost and latency at modest concurrency are unbounded by design, and (3) the system has no way to know whether the council format actually adds value over a single LLM. The author's own README acknowledges the project is unmaintained ("I don't intend to improve it"), and the open-issue tracker contains real-user reports of the exact failure modes the code structure predicts.**

This hypothesis is testable in three ways:
1. *Code-level evidence* — does the source structure actually contain the claimed gaps? (§2, §3)
2. *Feature-gap evidence* — what isn't there at all? (§4)
3. *User-evidence* — do real users report the failures the hypothesis predicts? (§5, §6)

If all three hold, the hypothesis is supported and the audit deliverables for the brief have empirical backing. If any of the three falls apart on closer inspection, the hypothesis needs revision before we propose changes.

---

## 2. Failure-mode catalog

Categorized, with file:line citations. Every entry is something I read, not something I'm inferring.

### Category A — Error handling and failure isolation

**A1. Single broad `except Exception` in the per-model HTTP call swallows every error class.**
- *Where:* `backend/openrouter.py:43–46`
- *What:* `try/except Exception as e: print(f"Error querying model {model}: {e}"); return None`
- *Why it breaks:* the caller cannot distinguish 401 (bad key) from 429 (rate limit) from 408 (timeout) from 500 (provider) from `KeyError` on a malformed payload. All collapse to `None`.
- *Visible consequence:* a council member that fails for any reason is silently dropped from Stage 1.

**A2. `query_models_parallel` uses `asyncio.gather` without `return_exceptions=True`.**
- *Where:* `backend/openrouter.py:90` (`responses = await asyncio.gather(*tasks)`)
- *What:* gathers results assuming all tasks succeed-or-return-`None`.
- *Why it breaks:* defended only by the catch-all in A1. If any future change in `query_model` lets an exception escape, the whole council call dies. There's no defense-in-depth.

**A3. Stage 1 silently drops failed members.**
- *Where:* `backend/council.py:25–30` (`if response is not None: stage1_results.append(...)`)
- *What:* a council of N is silently downgraded to a council of however-many-didn't-fail.
- *Why it breaks:* if 2 of 4 council members 429, the user sees a "council" answer derived from 2 LLMs and never knows the council was halved.

**A4. The same silent-drop pattern repeats in Stage 2.**
- *Where:* `backend/council.py:101–110`
- *What:* same `if response is not None` pattern when collecting rankings.
- *Why it breaks:* the aggregate ranking calculation can be derived from a partial set of rankers without surfacing that to the chairman or user.

**A5. Chairman fallback is a string masquerading as a response.**
- *Where:* `backend/council.py:164–169`
- *What:* if the chairman call returns `None`, the function returns `{"model": CHAIRMAN_MODEL, "response": "Error: Unable to generate final synthesis."}`.
- *Why it breaks:* this string is then `json.dump`-ed to the conversation history (`backend/storage.py:130–156`), persisted, re-loaded on next page view, and rendered in the UI as if it were a real assistant response. It's also indistinguishable from the case where the chairman *answered* "Error: Unable to generate final synthesis" verbatim. Confirmed by user issues #27 and #113.

**A6. Title-generation fallback masks failure.**
- *Where:* `backend/council.py:280–282`
- *What:* if the title-LLM call returns `None`, the title becomes the literal string `"New Conversation"` (the same default used when the conversation is freshly created).
- *Why it breaks:* the user can't distinguish "the title generator didn't run" from "the title generator hasn't run yet."

**A7. Stream endpoint catches all exceptions and stringifies them to the client.**
- *Where:* `backend/main.py:183–185`
- *What:* `except Exception as e: yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"`
- *Why it breaks:* internal tracebacks (which can include API keys, file paths, or PII from the conversation) are leaked to the browser console and any logging the client does. No sanitization layer.

**A8. Frontend's `onEvent` handler swallows JSON parse errors silently.**
- *Where:* `frontend/src/api.js:108–110`
- *What:* `try { JSON.parse(data) } catch (e) { console.error(...) }` then continues.
- *Why it breaks:* a malformed SSE chunk is logged to the dev console but the user sees a stalled loading state with no error UI.

### Category B — Orchestration reliability

**B1. The Stage-2 ranking prompt is brittle to format non-compliance.**
- *Where:* `backend/council.py:64–93`
- *What:* the prompt commands `Your final ranking MUST be formatted EXACTLY as follows: FINAL RANKING: ...`, with no fallback parser if the model deviates.
- *Why it breaks:* `parse_ranking_from_text` (council.py:177–208) does have a regex fallback to find any `Response [A-Z]` substring, but if the model omits the section heading entirely or uses different label syntax (e.g., "Answer A", "1) Response A"), the parser returns an empty list. Aggregate-ranking calculation (council.py:236–250) silently produces an empty aggregate.
- *Confirmed:* user issues #27, #113, #156 — multiple users report "Unable to generate final synthesis" / "Error in processing query."

**B2. Aggregate ranking is calculated even when only one ranker returned a parsed ranking.**
- *Where:* `backend/council.py:243–250`
- *What:* `for model, positions in model_positions.items(): if positions: avg_rank = sum(positions) / len(positions)` — no minimum threshold.
- *Why it breaks:* a single ranker gives a deterministic but meaningless "average." User sees a confidence-implying aggregate that is one model's opinion presented as collective.

**B3. Stages run strictly sequentially even though Stage 3 only depends on Stages 1+2 outputs.**
- *Where:* `backend/main.py:140–164` (the SSE `event_generator`)
- *What:* `await stage1_collect_responses → await stage2_collect_rankings → await stage3_synthesize_final` in series.
- *Why it breaks:* end-to-end latency is the sum of three slowest-LLM-in-stage. There is no intermediate partial response to the user during stages 1 and 2 — only stage-completion events with the bulk JSON payload.

**B4. Anonymization of council members in Stage 2 is order-stable, not shuffled.**
- *Where:* `backend/council.py:50–56` (`labels = [chr(65 + i) for i in range(...)]`, `label_to_model = {f"Response {label}": result['model'] for label, result in zip(labels, stage1_results)}`)
- *What:* the mapping from label A→model is deterministic across calls based on `COUNCIL_MODELS` order.
- *Why it breaks:* if any model has positional bias (judges Response A more favorably than Response D, for example), and the council ordering is fixed, this bias compounds rather than cancels. The "anonymization" is presentation-only, not counterbalanced.

**B5. The chairman is given full Stage-2 ranking text *and* is the same family of model as one of the council members.**
- *Where:* `backend/config.py:14–20` — `CHAIRMAN_MODEL = "google/gemini-3-pro-preview"` is identical to one council member.
- *What:* Gemini 3 Pro is both a council member voting in Stage 2 *and* the synthesizer in Stage 3.
- *Why it breaks:* the chairman sees its own Stage-1 response and its own Stage-2 ranking labelled with the model name (council.py:131–140). It can recognize itself and weight accordingly. *Confirmed by issue #3 — "Chairman over-influence in council system."*

**B6. The chairman receives raw Stage-2 ranking text including the freeform commentary.**
- *Where:* `backend/council.py:137–140` (passes `result['ranking']`, the full text, not `parsed_ranking`).
- *Why it breaks:* the chairman synthesis is influenced by whatever prose each ranker wrote — including any model's tendency to advocate strongly for its own answer once it sees the full set. Combined with B4 (deterministic labels), self-advocacy compounds.

### Category C — Cost and performance

**C1. Multi-pass LLM fan-out has no caching.**
- *Where:* `backend/council.py` overall — `run_full_council` always issues `(N + N + 1) = 2N + 1` LLM calls for any user query, where N is the number of council members.
- *Default:* N = 4 (config.py:12–17), so every query = 9 LLM calls. 5 council members would be 11. Plus a title-gen call for the first message of a conversation.
- *Why it breaks:* identical or near-identical queries pay the full multi-pass cost every time. No semantic cache, no hash cache, no prefix cache.

**C2. Council and chairman models are top-tier flagship models.**
- *Where:* `backend/config.py:13–17, 20`
- *What:* GPT-5.1, Gemini 3 Pro Preview, Claude Sonnet 4.5, Grok 4 — all premium-priced. Chairman is also Gemini 3 Pro.
- *Why it breaks:* per-query cost is dominated by the 2N+1 calls hitting flagship pricing tiers with no smaller-model fallback path for trivial queries.

**C3. No per-model parameter tuning.**
- *Where:* `backend/openrouter.py:31–37` — payload is just `{"model": model, "messages": messages}`.
- *What:* no `max_tokens`, no `temperature`, no `top_p`, no provider-specific reasoning toggles.
- *Why it breaks:* (a) every model is allowed to ramble to its default cap, inflating output-token cost and latency; (b) reasoning models (Gemini 3 Pro Preview, Grok 4 thinking variants) may run extended reasoning by default at significantly higher cost. The user has no surfaced way to cap.

**C4. Hardcoded 120-second per-call timeout.**
- *Where:* `backend/openrouter.py:11` (`timeout: float = 120.0`)
- *What:* a slow LLM blocks for up to 2 minutes.
- *Why it breaks:* with `asyncio.gather`, the slowest of 4–5 LLMs sets the floor latency for stages 1 and 2 — the council is paced by its slowest member, twice.

**C5. No concurrency cap on outbound LLM calls.**
- *Where:* `backend/openrouter.py:84–93` — `asyncio.gather(*tasks)` over `len(COUNCIL_MODELS)` tasks.
- *What:* if the council grows or if multiple users hit the server simultaneously, OpenRouter rate limits will be hit unevenly and unpredictably.
- *Why it breaks:* combined with A1 (silent failure), rate-limit storms become invisible to the user.

**C6. Stage-3 chairman call is single-shot and synchronous within the SSE stream.**
- *Where:* `backend/main.py:163` — `stage3_result = await stage3_synthesize_final(...)`
- *What:* the chairman's full response is awaited before being yielded.
- *Why it breaks:* even though OpenRouter and the underlying providers support streaming responses, this implementation buffers the entire chairman output before sending one SSE event. The user waits for the entire synthesis to complete before seeing any of it.

### Category D — Concurrency and multi-tenancy

**D1. Conversations are stored as one JSON file per conversation, with no locking.**
- *Where:* `backend/storage.py:42–43, 67–78` (`with open(path, 'w') as f: json.dump(conversation, f, indent=2)`)
- *What:* read-modify-write cycle on every `add_user_message`, `add_assistant_message`, `update_conversation_title`.
- *Why it breaks:* two parallel writes to the same conversation file race; later writer wins, earlier writer's data lost. No `fcntl.flock`, no atomic rename, no transaction.

**D2. Read-then-write race in every storage mutator.**
- *Where:* `backend/storage.py:118–127, 145–156, 167–172`
- *What:* every mutator follows `conversation = get_conversation(...)` (full file read) → modify dict → `save_conversation(...)` (full file overwrite).
- *Why it breaks:* even a single user with two browser tabs open on the same conversation can lose messages. The streaming endpoint's `add_user_message` followed by `add_assistant_message` (main.py:143, 173) compounds this within a single request.

**D3. CORS is wide-open for the configured origins.**
- *Where:* `backend/main.py:17–24` — `allow_origins=["http://localhost:5173", "http://localhost:3000"], allow_methods=["*"], allow_headers=["*"], allow_credentials=True`.
- *What:* permissive CORS for dev, but unchanged.
- *Why it breaks:* not a vulnerability locally, but means the project has no production-deployment story.

**D4. No authentication or session model.**
- *Where:* nowhere — there are no auth headers, no per-user data partitioning.
- *What:* every conversation is visible to every caller of the API.
- *Why it breaks:* "deploy this to a server" = "all conversations are world-readable for anyone with the conversation UUID, and listable for everyone."

**D5. `list_conversations` reads every JSON file in the data dir on every call.**
- *Where:* `backend/storage.py:90–105`
- *What:* `for filename in os.listdir(DATA_DIR): ... json.load(f)` on each.
- *Why it breaks:* O(N) disk reads per `GET /api/conversations`. At 10k conversations, the sidebar render becomes a multi-second I/O storm.

### Category E — Observability

**E1. Zero structured logging across the backend.**
- *Where:* none. Confirmed by `grep -rE 'logger\.(info|warning|error|debug)' backend/` → 0 matches.
- *What:* the only diagnostic is `print(f"Error querying model {model}: {e}")` in openrouter.py:46.
- *Why it breaks:* in production, there is no log to grep, no level to filter, no JSON to ship to a log aggregator.

**E2. No request instrumentation.**
- *Where:* none — confirmed by absence of any timing, token-counting, or cost-tracking code in `query_model` (openrouter.py:7–48).
- *What:* OpenRouter returns token counts in its response payload; the code reads `data['choices'][0]['message']` only and discards the rest.
- *Why it breaks:* there is no record of what each query cost, how long each LLM took, or which models hit rate limits. Cost-at-scale arguments cannot be made from the system itself.

**E3. No health metrics beyond `/`.**
- *Where:* `backend/main.py:53–56` — `/` returns `{"status": "ok"}` always.
- *What:* not a real health check — doesn't verify OpenRouter reachability, doesn't verify storage writability.
- *Why it breaks:* a load balancer in front of the service will never mark it unhealthy even if the OpenRouter API key is invalid.

**E4. Frontend SSE errors only go to `console.error`.**
- *Where:* `frontend/src/api.js:108–110`, `frontend/src/App.jsx:30, 39, 53` — every catch is `console.error(...)` with no UI surfacing.
- *Why it breaks:* a non-developer user sees a stalled spinner with no message.

### Category F — Security and secret handling

**F1. `OPENROUTER_API_KEY` is loaded at module import time and never refreshed.**
- *Where:* `backend/config.py:6, 9` (`load_dotenv(); OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")`)
- *Why it breaks:* key rotation requires a process restart. Not catastrophic, but typical of a prototype.

**F2. The streaming error path can leak server-side details to the client.**
- *Where:* `backend/main.py:185` — `'message': str(e)`
- *Why it breaks:* a `KeyError` from a malformed OpenRouter response, a `FileNotFoundError` from storage, or an httpx `ConnectError` with the OpenRouter host name, will all be passed to the browser. Any one of those could leak file paths, API host details, or partial PII from the conversation when it appears in tracebacks.

**F3. There is no rate limiting at the application level.**
- *Where:* nowhere.
- *Why it breaks:* a single misbehaving caller can drain the OpenRouter budget by submitting queries in a tight loop. There is no token bucket, no per-IP cap, no per-user cap.

**F4. The OpenRouter API key is the *only* gate on cost.**
- *What:* combined with F3, anyone who reaches the service costs money. There is no free-tier vs. paid-tier branching.

**F5. Title generation prompt is uninstrumented and ungated.**
- *Where:* `backend/council.py:268–273` — every first message of a conversation triggers a separate `gemini-2.5-flash` call before stages 1–3.
- *Why it breaks:* a user spamming `POST /api/conversations` followed by `POST /message` racks up flash-tier title-gen calls in addition to the council fan-out.

### Category G — Data integrity

**G1. The `messages` array in storage mixes shapes (user vs assistant).**
- *Where:* `backend/storage.py:122–125, 149–154`
- *What:* user messages are `{"role": "user", "content": str}`. Assistant messages are `{"role": "assistant", "stage1": [...], "stage2": [...], "stage3": {...}}` — no `content` key.
- *Why it breaks:* downstream consumers (frontend rendering, future re-prompting that needs prior turns) must branch on shape. The frontend already handles this in `App.jsx:65–70` by constructing a synthetic user message; for assistant messages, the rendering relies on three different stage components reading three different keys. There's no canonical "what did the assistant say?" string for re-feeding into a follow-up turn.

**G2. The conversation history is not actually fed back into Stage 1 for follow-ups.**
- *Where:* `backend/main.py:104–106` calls `run_full_council(request.content)` — `request.content` is just the latest user message, not the full conversation.
- *Why it breaks:* despite being framed as a chat, the system has no memory across turns. The user's second message is treated as an entirely fresh, context-free query. *This is also a missing feature (see §4).*

**G3. Conversation IDs are random UUIDs but not URL-safe-validated.**
- *Where:* `backend/main.py:73, 82` (`@app.get("/api/conversations/{conversation_id}")`) → `backend/storage.py:18` (`os.path.join(DATA_DIR, f"{conversation_id}.json")`)
- *Why it breaks:* path traversal — a request to `/api/conversations/..%2F..%2Fetc%2Fpasswd` would, after URL-decoding by FastAPI, attempt to read `data/conversations/../../etc/passwd.json`. FastAPI does decode path params, and `os.path.join` does not sanitize. Whether this is exploitable depends on FastAPI's default decoding behavior for path params and the storage filesystem layout, but the code does no defensive validation.

### Category H — Frontend contract

**H1. The frontend optimistically appends to `prev.messages` assuming the prior render had a populated `messages` array.**
- *Where:* `frontend/src/App.jsx:67–70`
- *Why it breaks:* if `loadConversation` is mid-flight when `handleSendMessage` is called, `prev.messages` could be undefined. This is the class of bug user issue #117 (`finalResponse.model.split is not a function`) suggests — the frontend is not defensive against partially-loaded state.

**H2. The new-conversation `setConversations` call omits the `title` field entirely.**
- *Where:* `frontend/src/App.jsx:46–48` — `{ id: newConv.id, created_at: newConv.created_at, message_count: 0 }`
- *Why it breaks:* the sidebar metadata for a freshly-created conversation shows undefined for `title` until a re-fetch. Cosmetic, but signals the same lack of defensive frontend coding.

**H3. SSE event types are stringly-typed and silently ignored on mismatch.**
- *Where:* `frontend/src/App.jsx:93–100+` switch on `eventType` strings.
- *Why it breaks:* if the backend ever introduces a new event type (e.g., `'partial_stage1'`), the frontend silently drops it. There's no schema, no type contract, no Pydantic-equivalent on the JS side.

---

## 3. Summary table — failure modes by category

| Category | # findings | Severity profile | Affects |
|---|---:|---|---|
| A — Error handling | 8 | High (silent data loss + leaked tracebacks) | Every query that hits any LLM error |
| B — Orchestration reliability | 6 | Medium-High | Quality of council output |
| C — Cost and performance | 6 | High at any non-trivial usage | Budget + UX |
| D — Concurrency / multi-tenancy | 5 | High at >1 concurrent user | Any deployment beyond local |
| E — Observability | 4 | High for operability | Anyone running this in prod |
| F — Security / secrets | 5 | Medium-High | Any deployment beyond localhost |
| G — Data integrity | 3 | Medium | Long-running conversations, sec-sensitive paths |
| H — Frontend contract | 3 | Low-Medium | UX correctness |
| **Total** | **40** | | |

---

## 4. Missing features inventory

Things absent from the codebase entirely. Distinguished from "failure modes" because there's nothing to *fix* — there's nothing there at all.

| ID | Missing capability | Where you'd expect it | Confirmed absent by |
|---|---|---|---|
| M1 | Test suite | `tests/`, `test_*.py`, or `pytest.ini` | `find -name 'test_*.py' -o -name '*_test.py'` → 0 results |
| M2 | Eval framework | `evals/`, judge prompts, ground-truth dataset, scoring code | No file or directory matches; `grep -ri 'eval\|metric\|score'` returns only false positives |
| M3 | Conversation memory across turns | `run_full_council` should accept full message history, not just `request.content` | `backend/main.py:104–106` passes only the latest user message |
| M4 | Caching layer (any kind) | Hash-based, semantic, prefix, or full-response | No `cache`, `redis`, `lru_cache`, or `functools` usage in backend |
| M5 | Retry / backoff for LLM calls | `tenacity`, `backoff`, manual loops, or even single-retry | `grep -riE 'retry\|backoff\|tenacity'` → 0 matches in backend |
| M6 | Rate limiting | `slowapi`, FastAPI middleware, or token bucket | No middleware beyond CORS in `backend/main.py:17–24` |
| M7 | Structured logging | `logging` module setup | `import logging` → 0 matches in backend |
| M8 | Token / cost tracking | Reading OpenRouter's `usage` field on responses | `backend/openrouter.py:38–47` reads only `choices[0].message`; the rest of the response (including `usage`) is discarded |
| M9 | Streaming within a stage | Provider-level streaming through to SSE | OpenRouter calls are non-streaming; results are awaited in full before any SSE event fires |
| M10 | Per-model parameter configuration | `temperature`, `max_tokens`, `top_p`, reasoning toggles per model | `backend/openrouter.py:31–34` — payload has only `model` and `messages` |
| M11 | Auth / session model | API keys, JWT, OAuth, anything | No auth dependency, no session middleware |
| M12 | Per-user data isolation | User ID in conversation records or path | `storage.py` has no user concept; conversations are addressed by UUID alone |
| M13 | Smart routing (council vs. solo) | A classifier or heuristic that decides whether a query needs full council | `run_full_council` is called unconditionally for every message |
| M14 | Database / durable storage | Postgres, SQLite, anything other than per-file JSON | `storage.py` is filesystem JSON only |
| M15 | Health checks beyond static OK | Verify OpenRouter reachability, storage writability | `main.py:53–56` returns hardcoded OK |
| M16 | Configuration validation at startup | Fail fast if `OPENROUTER_API_KEY` is missing | `config.py:9` does `os.getenv` without raising |
| M17 | Schema / contract for SSE events | Versioned event schema, frontend type generation | events are string-typed dicts; frontend switch in `App.jsx` is hand-written |
| M18 | CI / GitHub Actions | `.github/workflows/` | Directory absent in repo |
| M19 | Production deployment recipe | Dockerfile, docker-compose, k8s manifests, helm chart | Only `start.sh` (a dev launcher) |
| M20 | Cost guardrail / budget cap | Stop processing if monthly OpenRouter spend exceeds X | None |

> M1 + M2 + M5 + M7 are the four absences that match the brief's verbatim language ("minimal error handling, no eval"). M3 is a notable surprise — this is a *chat interface*, but the council does not actually see prior turns.

---

## 5. Open issues — full list

Pulled from the GitHub issues page on 2026-05-09. Filtered to open issues only. Spam / non-issue items are flagged but not removed, because they themselves are a finding (see §5.2).

### 5.1 Issues by class

**Substantive technical issues (the audit-relevant ones):**

| # | Date | Title | Class |
|---|---|---|---|
| 3 | (older) | Chairman over-influence in council system | Architecture critique |
| 27 | (older) | Error: "Stage 3 final consult answered… unable to generate final synthesis" | Reliability — Stage 3 |
| 113 | (older) | Error: Unable to generate final synthesis | Reliability — Stage 3 (same as #27) |
| 117 | 2026-01-01 | `finalResponse.model.split is not a function` | Frontend defensive coding |
| 133 | 2026-01-08 | OpenRouter 401s in "LLM Council Plus" fork despite valid key | Auth / fork-related |
| 156 | 2026-02-10 | Error in processing query | Reliability — generic |

**Non-issues (using the issue tracker as a chat / scratchpad):**

| # | Date | Title |
|---|---|---|
| 9 | (older) | A 10X improvement feature |
| 100 | (older) | Council |
| 104 | (older) | LLM Council Plus |
| 120 | 2026-01-03 | My project |
| 146 | 2026-01-29 | ggg |
| 150 | 2026-01-31 | create a new professional nail brand called nexa |
| 151 | 2026-02-01 | ТЕМА |
| 153 | 2026-02-04 | "No point using this app with tons of setup. Check https://openrouter.ai/chat" |
| 155 | 2026-02-10 | Answe |
| 157 | 2026-02-11 | a |
| 160 | 2026-02-11 | Reset |
| 185 | 2026-03-07 | تقرير العقل الألي |

(There are 50 total open issues; the items above are the ones surfaced via GitHub's first-page render and the prior-search hits. The remaining ~32 are likely a mix of the same two patterns.)

### 5.2 Pattern observation

A meaningful fraction of "open issues" on this repo are users typing into the issues field as if it were the chat input, or filing one-word issues, or filing feature requests with no detail. This itself is a finding: the project has no contributing guidelines, no issue templates, and no triage process — which is consistent with the README's "I don't intend to improve it." It's not a code-level bug, but it's a project-readiness gap.

### 5.3 Pull-request observation (not in §5.1)

64 open PRs vs. 50 open issues — unusual for a small Python repo. Suggests the community is actively trying to harden this codebase, which the maintainer has explicitly disengaged from. PRs are the *real* hardening backlog.

---

## 6. Hypothesis-vs-evidence matrix

Maps each open *substantive* issue to the audit findings it independently corroborates.

| Issue | Title | Corroborates findings | Strength |
|---|---|---|---|
| **#3** | Chairman over-influence in council system | **B5** (chairman is same model family as a council member), **B6** (chairman sees raw Stage-2 text including any model's self-advocacy) | **Strong** — this issue is verbatim about the architecture point |
| **#27** | Stage 3 final consult answered… unable to generate final synthesis | **A5** (chairman fallback is a string masquerading as an answer), **B1** (Stage-2 ranking format is brittle and Stage-3 receives the broken text), **A1** (silent failure of underlying call) | **Strong** — the literal error string in the issue is what `council.py:166–169` emits |
| **#113** | Error: Unable to generate final synthesis | Same as #27 | **Strong** — second user reports the same string, ruling out one-off |
| **#117** | `finalResponse.model.split is not a function` | **H1** (frontend not defensive against partially-populated state), **H2** (new conversations created without all fields), **A8** (frontend's silent SSE-parse fallback) | **Medium** — the specific symptom is frontend, but rooted in the contract gap between backend response shape and frontend assumptions |
| **#133** | OpenRouter 401s in fork despite valid key | **A1** (single broad except → 401 indistinguishable from any other failure), **F1** (key loaded at import time, no rotation feedback) | **Medium** — issue is on a fork, but underlying error-classification gap is upstream |
| **#156** | Error in processing query | **A1**, **A2**, **A3**, **B1**, **B2** — the catch-all "something failed somewhere and the user has no detail" issue | **Weak-but-broad** — corroborates the entire silent-failure category, even if no single finding maps 1:1 |

### 6.1 Hypothesis assessment

The three legs of the hypothesis stated in §1:

1. *"Unhandled per-LLM call failures cascade into degraded or empty council outputs."* — **Supported.** Code findings A1–A6, B1–B2, with verbatim user reports at #27, #113, #156, #133.
2. *"Cost and latency at modest concurrency are unbounded by design."* — **Supported by code**, not yet by user reports. C1–C6 are structural; users haven't filed issues about cost (likely because the cost is paid per-user via personal OpenRouter keys, not by the project operator). This is a gap to flag — the cost story is a *projection* until we put numbers on it (next doc).
3. *"The system has no way to know whether the council format actually adds value."* — **Supported.** M2 (no eval framework), and #3 ("Chairman over-influence") is a community member articulating doubt about the architecture's core premise. This is the most novel audit angle and the one with the cleanest narrative for the brief.

### 6.2 What this means for the next doc

The hypothesis holds. The audit findings are not theoretical — they are empirically grounded by user reports for the high-severity items, and structurally undeniable for the rest. The proposed-changes doc (next) can build on this base without needing to re-litigate any of the diagnostic claims.

---

## 7. Open questions before writing the proposed-changes doc

Calling these out so we don't accidentally drift into proposals:

1. *Are there closed issues that contain root-cause discussion we should mine?* — This doc only covers open issues. Closed issues sometimes have the maintainer's own explanation of a workaround.
2. *Are any of the 64 open PRs already attempting fixes for findings here?* — If so, the proposed-changes doc should reference them rather than duplicate.
3. *What's the actual OpenRouter pricing for the four flagship council models as of May 2026?* — Needed for the cost-at-10k-users math in the next deliverable.
4. *Does Karpathy's `CLAUDE.md` (in repo root) contain any maintainer guidance we haven't read?* — Worth checking.
5. *Is the Issue #3 thread substantive or a one-line complaint?* — Worth pulling the full thread before designing the chairman-bias fix.
6. *Has the project been featured in any independent code review or post-mortem?* — VirtusLab "GitHub All-Stars" mentioned it; worth a deeper read for any architectural criticism we can cite without re-doing.

---

## 8. Decision needed before next step

This doc captures *what is broken* and *what is missing*. Next step is the proposed-changes doc, which will turn this catalog into:
- prioritized fix list (severity × effort × impact),
- the single most critical failure-point pick for the brief's deliverable,
- proposed eval framework dimensions,
- cost-at-10k-users spreadsheet inputs.

Before starting that, confirm whether:
1. The hypothesis as stated in §1 is the right framing, or needs revision.
2. Any of §7's open questions are blocking — if so, address them first.
3. The categorization (A–H) feels right for organizing the fixes that follow, or should be restructured.
