# Finalist Verification — Code-Level Ground Truth

**Purpose.** Replace the second-hand search-summary data in `01_repo_scouting_methodology.md` with first-hand evidence: cloned repos, line counts, file structure, error-handling discipline, and real-user issue patterns.

**What changed vs. the methodology doc.** Several numbers and one judgment. The previously-stated `llm-council` LOC ("~400") was a rough quote from a search summary; actual is 818 total / 562 code in 7 Python files. `RealtimeVoiceChat` is materially larger and more defensively coded than expected, which **weakens its fit to the brief's "minimal error handling, no eval" framing**. `babyagi-2o` is even smaller than expected (1 file, 174 lines) and has zero real-user issues, which **weakens the production-relevance narrative**. Net result: the recommendation toward `karpathy/llm-council` is reinforced, not changed.

**Method.** All three repos cloned with `git clone --depth=1`, scanned with a simple Python LOC counter (`/tmp/loc.py`), and inspected with targeted `grep` and `find`. GitHub issues fetched directly from the issues page. No external services or paid APIs used.

---

## 1. Repository ground truth

### 1.1 Total scale

| Repo | Total files (counted langs) | Total lines | Code lines | Python files | Python LOC |
|---|---:|---:|---:|---:|---:|
| `karpathy/llm-council` | 27 | 5,682 | 5,178 | 7 | 818 (562 code) |
| `KoljaB/RealtimeVoiceChat` | 23 | 7,502 | 5,590 | 12 | 6,191 (4,610 code) |
| `yoheinakajima/babyagi-2o` | 3 | 359 | 267 | 1 | 174 (153 code) |

> Note: for `llm-council`, 3,788 of the "code" lines are JSON (mostly `package-lock.json` from the React frontend) and 597 are CSS. The actual auditable Python surface is the smallest of the three.

### 1.2 File structure

**`karpathy/llm-council`** — clean monorepo layout with separate backend (Python) and frontend (Vite + React). The audit lives entirely in `backend/`.

```
llm-council/
├── backend/
│   ├── __init__.py        (1 LOC)
│   ├── config.py          (26 LOC)   — env loading, model lists
│   ├── openrouter.py      (79 LOC)   — single + parallel LLM calls
│   ├── council.py         (335 LOC)  — 3-stage orchestration (HOT PATH)
│   ├── main.py            (199 LOC)  — FastAPI app, endpoints
│   └── storage.py         (172 LOC)  — JSON-file conversation persistence
├── frontend/              (React + Vite, 3 stage components)
├── main.py                (6 LOC, launcher)
├── pyproject.toml
├── start.sh
├── README.md              ("Vibe Code Alert" section)
└── CLAUDE.md
```

**`KoljaB/RealtimeVoiceChat`** — single `code/` dir, no package structure, but real Docker support.

```
RealtimeVoiceChat/
├── code/
│   ├── server.py                    (972 LOC)   — FastAPI/WebSocket entry
│   ├── speech_pipeline_manager.py   (1,103 LOC) — pipeline orchestration
│   ├── llm_module.py                (1,277 LOC) — Ollama / OpenAI client
│   ├── transcribe.py                (839 LOC)   — STT
│   ├── audio_module.py              (584 LOC)   — TTS
│   ├── turndetect.py                (541 LOC)   — turn-taking detector
│   ├── audio_in.py                  (245 LOC)
│   ├── text_similarity.py           (258 LOC)
│   ├── upsample_overlap.py          (109 LOC)
│   ├── colors.py, logsetup.py, text_context.py
│   └── static/                      (HTML/JS frontend, AudioWorklets)
├── Dockerfile, docker-compose.yml, entrypoint.sh
├── requirements.txt
└── wheels/                          (pre-bundled deepspeed wheel)
```

**`yoheinakajima/babyagi-2o`** — one file. That's it.

```
babyagi-2o/
├── main.py                (174 LOC, the whole agent)
├── README.md
├── LICENSE
└── .devcontainer.json
```

### 1.3 Dependencies (production surface)

| Repo | LLM client | Server | Auxiliary |
|---|---|---|---|
| `llm-council` | `httpx` (raw OpenRouter HTTP) | `fastapi` + `uvicorn` | `pydantic`, `python-dotenv` |
| `RealtimeVoiceChat` | `ollama`, `openai` | `fastapi` + `uvicorn` (WebSockets) | `realtimestt`, `realtimetts[kokoro,coqui,orpheus]` |
| `babyagi-2o` | `litellm` (provider-agnostic, supports tool calls) | none — CLI only | `subprocess` (for `pip install`) |

### 1.4 Brand-recognition signal

| Repo | Stars | Forks | Open issues | Open PRs |
|---|---:|---:|---:|---:|
| `karpathy/llm-council` | 15,700 | 3,200 | 50 | 64 |
| `KoljaB/RealtimeVoiceChat` | 3,600 | 421 | 39 | 1 |
| `yoheinakajima/babyagi-2o` | 338 | 69 | 0 | — |

> **Important correction to `01_repo_scouting_methodology.md`:** earlier search summaries blurred `llm-council` with `OpenClaw` (which is the actual 200k+ star viral 2026 repo). `llm-council` is at **15.7k stars** — still very high for a Saturday hack, but not the runaway narrative the methodology doc implied. Updated.

> Also note `llm-council`'s **64 open PRs vs 50 open issues** — community is actively trying to harden the prototype while the author has explicitly said "I don't intend to improve it." That's an unusual signal: the hardening backlog is publicly visible.

---

## 2. Production-readiness signals (quantitative)

Counts from `grep` across `*.py` files only:

| Signal | `llm-council` | `RealtimeVoiceChat` | `babyagi-2o` |
|---|---:|---:|---:|
| `try` blocks | 2 | 63 | 5 |
| `except` blocks | 2 | 90 | 5 |
| Bare `except:` | 0 | 0 | 0 |
| Broad `except Exception` | 2 | 45 | 4 |
| `logger.*` calls | **0** | 457 | **0** |
| `print(...)` calls | 2 | 43 | 10 |
| Test files (`test_*.py`) | **0** | **0** | **0** |
| `eval`/`metric`/`score` mentions | 1 (false positive) | 3 (false positives) | 0 |
| `asyncio.gather` | 1 | 2 | 0 |
| `retry`/`backoff`/`tenacity` | 0 | 5 | 0 |

### 2.1 What this means

**`llm-council`** is **textbook "minimal error handling, no eval."** Two try/except in the entire backend, zero structured logging, zero tests, zero retries, zero eval framework. Of the three, this matches the brief's wording most cleanly. **The audit narrative is supported by the grep data verbatim** — you can show a slide that says "the brief asks for a prototype with minimal error handling and no eval; here are the literal counts."

**`RealtimeVoiceChat`** has **substantially more defensive infrastructure** than the brief's "minimal error handling" language implies. 63 try-blocks, 457 structured log calls, 5 retry-related code paths. It's not a "vibe-coded weekend" prototype — it's a community-maintained-but-imperfect production-targeting project. Auditing it is still meaningful, but the framing has to change from *"this prototype is not production-ready"* to *"this is community-driven and the author has stepped away — here's the gap to true production."* That's a more nuanced and harder defense.

**`babyagi-2o`** matches the brief's criteria, but at 153 LOC of code there's not much to audit. The audit deliverable would be padding to fill 2 days. Plus, **zero open issues** (vs. `llm-council`'s 50 + 64 PRs and `RealtimeVoiceChat`'s 39) means there's no real-user failure-mode evidence — you'd have to invent the production-relevance argument rather than ground it in observed user pain.

---

## 3. Real-user issue patterns

### 3.1 `karpathy/llm-council` — top open issues

Issues page shows 50 open. Filtering out spam/test issues (random titles, foreign-language one-word issues, "create a nail brand" type misuse):

| # | Title | Theme |
|---|---|---|
| 3 | "Chairman over-influence in council system" | **Architecture critique — validates audit finding #1** |
| 27 | "Error: Stage 3 final consult answered… unable to generate final synthesis" | Reliability — Stage 3 parsing/format failure |
| 113 | "Error: Unable to generate final synthesis" | Same root cause, multiple users |
| 117 | `"finalResponse.model.split is not a function"` | Frontend defensive-coding bug |
| 133 | OpenRouter 401s in fork (key valid, direct API works) | Auth / error-surfacing |
| 156 | "Error in processing query" | Generic failure mode, undiagnosed |

Pattern: the **"Stage 3 final synthesis fails silently"** failure mode is real, recurring, and affects multiple users. This is exactly the silent-failure-on-LLM-format-non-compliance class I flagged in the recommendation memo. **The audit's most impactful refactor target is now empirically validated by user reports.**

### 3.2 `KoljaB/RealtimeVoiceChat` — top open issues

39 open issues. Top items:

| # | Title | Theme |
|---|---|---|
| 50 | "Hangs when changing the language to japanese" | Reliability — language-specific deadlock |
| 47 | "Whole system stucks and UI not updated when changing transcription model" | Reliability — model-swap deadlock |
| 43 | "TTS Output not working" | Reliability — TTS failure mode |
| 52 | "docker compose up failed" | Deployment / install |
| 46 | "How do I host orpheus-TTS on the cloud?" | Deployment — cloud productionization |
| 49 | "Recommended typescript API client" | Integration / API stability |
| 53 | "Have you tried Mars8 TTS?" | TTS extensibility |
| 45 | "Finally got it work on my Macbook Pro M1!!!" | Install pain |
| 42 | "Is it possible to add a new custom TTS?" | Extensibility |
| 51 | "Why train turn detection on text not audio?" | Architecture question (not a bug) |

Pattern: real users report **state-management lockups when switching models or languages**, plus heavy install/deployment friction. The audit narrative here is "this works for the happy path but state transitions and platform variance destroy the experience." That's a valid story but it's harder to fix in 2 days than `llm-council`'s narrower failure modes.

### 3.3 `yoheinakajima/babyagi-2o` — top open issues

**Zero open issues. Zero closed issues.** No real user engagement. Either the security implications scare people away from filing bugs, or the user base is too small to surface them. Either way, there's no real-user pain to ground a production-readiness audit in.

---

## 4. Production gaps observed in code (per repo)

Brief inventory of gaps I confirmed by reading the code, not by inferring from search summaries. These are the candidate "single most critical failure point" picks for the brief's refactor deliverable.

### 4.1 `karpathy/llm-council` — confirmed gaps

From `backend/openrouter.py` (full file read):

1. **Single broad `except Exception`** swallows all errors and returns `None`. No distinction between rate limit (429), auth (401), timeout, payload error, network error. Caller has no way to act differently.
2. **Hardcoded 120s timeout** with no per-model configuration. A slow LLM blocks the entire async fan-out.
3. **`asyncio.gather(*tasks)` without `return_exceptions=True`.** Defended by the swallow-everything `except` inside `query_model`, but means any future bare-bubble exception kills the whole council.
4. **No retries / no backoff.** Transient 429 or 500 → council member silently dropped.
5. **`print(f"Error querying model {model}: {e}")`** instead of structured logging. Zero observability for production.
6. **No request instrumentation** — no latency, token, or cost tracking.
7. **No concurrency cap.** If `COUNCIL_MODELS` grows to 10–20, the OpenRouter rate limit becomes a foot-gun.

From `backend/council.py` (head read):

8. **Stage 1 silently drops failed members** (`if response is not None`). The "council" can become "the chairman of the 2 LLMs that didn't time out."
9. **Brittle ranking format** — "MUST be formatted EXACTLY as follows: FINAL RANKING:..." — with no parser fallback. Confirmed by user issues #27 and #113 ("Unable to generate final synthesis").
10. **Three sequential stages** (collect → rank → synthesize). Worst-case latency = 3× slowest LLM call. No streaming back to the user during stages 1 and 2.
11. **No caching.** Same query asked twice = full multi-LLM fan-out twice.

From `backend/storage.py`:

12. **JSON-file-per-conversation with no locking.** Concurrent writes to the same conversation file race. Single-tenant assumption baked in.

**Single most critical failure (audit pick):** issues #27 and #113 — the silent-failure mode where the chairman fails to parse rankings → user gets no answer. Two-line user-visible bug, but the fix touches the parser, retries, and observability — a clean refactor that hits multiple gaps at once.

### 4.2 `KoljaB/RealtimeVoiceChat` — confirmed gaps

From `code/server.py` (head read):

1. **Three `TTS_START_ENGINE = "..."` assignments overwriting each other** at the top of `server.py` (last one wins). Configuration smell — clearly the dev was switching between engines manually.
2. **Hardcoded model paths** (`hf.co/bartowski/...Mistral-Small-24B...Q4_K_M`) in source. Not env-var-configured.
3. **Single global audio queue** with `MAX_AUDIO_QUEUE_SIZE` env var. Single-tenant design — no per-session isolation.
4. **No tests, no CI** despite Docker support.
5. From the issue tracker: model-swap and language-swap cause deadlocks → there's a state-machine bug somewhere in the pipeline manager.

**Single most critical failure (audit pick):** the model-swap deadlock (#47, #50). Real users report it, it's a state-management bug, and fixing it requires understanding the WebSocket + STT + LLM + TTS interaction model — high senior-signal but likely **more than 2 days to land cleanly**.

### 4.3 `yoheinakajima/babyagi-2o` — confirmed gaps

From `main.py` (full file read):

1. **`exec(code, globals())` on LLM-generated Python.** Line ~38. No sandbox, no AST validation, no resource limit. The LLM can write arbitrary code that runs on the host.
2. **`subprocess.check_call([sys.executable, "-m", "pip", "install", package_name])`** — agent installs arbitrary packages. Supply-chain attack surface.
3. **API keys leaked into the system prompt.** The agent scans environment variables for `API_KEY|ACCESS_TOKEN|SECRET_KEY|TOKEN|APISECRET` and lists them in the prompt. A prompt injection that surfaces those names is a clean exfiltration path.
4. **`max_iterations = 50`, `sleep(2)` between** — unbounded LLM cost per task, up to 50 LLM calls.
5. **No persistence** — tools are recreated every session.
6. **`input()` at the bottom** — single-user CLI, no concurrency at all.
7. **All errors caught and stringified back to the LLM** as the next turn's input — the agent "learns" from errors with no formal recovery semantics.
8. **`traceback.print_exc()` to stdout** — no observability.

The author themselves writes in the README: *"Because this installs dependencies and executes code based on an LLMs output, please execute in a safe environment and be mindful of the types of requests you make."* That's a strong audit framing — they know it's unsafe, the audit is "what does 'safe environment' actually mean in production terms?"

**Single most critical failure (audit pick):** sandboxing the `exec()`. Dramatic, defensible, but replacing the unsafe-by-design core of a 174-line repo essentially means rewriting the repo. The 2-day audit becomes a rewrite, not a refactor. **This is a scope mismatch.**

---

## 5. Updated finalist scorecard

| Criterion | `llm-council` | `RealtimeVoiceChat` | `babyagi-2o` |
|---|---|---|---|
| Brief-fit ("clearly a prototype, minimal error handling, no eval") | **Strong** — grep-verified | Weak — 457 log calls, 5 retry paths | **Strong** — but only 174 LOC |
| Auditable scope in 2 days | **Excellent** (~560 LOC Python) | Risky (~4,610 LOC Python) | **Too small** (153 LOC) |
| Brand recognition | **Strong** (15.7k stars, Karpathy) | Medium (3.6k stars) | Weak (338 stars) |
| Real-user pain documented | **Strong** (issues #27/#113 validate audit pick) | **Strong** (model-swap deadlocks) | **None** (0 issues ever) |
| Single critical-failure refactor possible in 2 days | **Yes** (Stage-3 parser + observability) | **No** (state-machine deadlock is deep) | **No** (rewrite, not refactor) |
| Cost-at-10k-users story | **Strong** (multi-LLM × multi-pass × no caching) | Different shape (GPU economics) | Weak (CLI, no concurrency) |
| Eval-framework gap | **Strong** (does council > chairman alone?) | **Strong** (no standard for voice agents) | Weak (no real users to evaluate against) |
| Security narrative | Modest | Modest | **Dramatic** (`exec` on LLM output) |
| Demo risk | Low | **High** (live voice can fail on stage) | Low |

### 5.1 Updated recommendation

**Lock in `karpathy/llm-council`.** Verification reinforced rather than weakened the prior recommendation. New supporting evidence:

1. The brief's "minimal error handling, no eval" filter is **literally true** by grep (2 try/except, 0 logger calls, 0 tests, 0 retries). No interviewer can dispute the framing.
2. Issues #27 and #113 are real-user reports of the **exact failure mode** the audit's refactor would target — silent failure of Stage 3 final-synthesis parsing. The "single most critical failure" deliverable now has user-quote evidence behind it.
3. 64 open PRs vs. 50 issues = the community is actively trying to harden this and the author has bowed out. The "treat it like it's yours and going live" framing has natural standing.
4. Issue #3 ("Chairman over-influence") independently validates an architectural audit point you'd raise.
5. ~560 lines of Python = full audit possible in 2 days, not partial.

### 5.2 What changed in the relative ranking

- **`RealtimeVoiceChat` dropped one notch.** Not because it's worse, but because (a) the codebase is materially larger than methodology-doc estimates, (b) the defensive-code volume (457 log calls, 90 except blocks, 5 retry paths) makes the "clearly a prototype" framing harder to defend, and (c) the highest-impact refactor target (model-swap deadlock) is probably > 2 days.
- **`babyagi-2o` dropped further.** Verified to be just 174 lines of code in a single file, with zero open issues. The dramatic security narrative is real, but the refactor target (sandboxing `exec()`) is essentially "rewrite the repo." Plus, no real users = no production-relevance ground truth.

### 5.3 If you want to override

The verification doesn't change the recommendation logic, just sharpens it. Reasons you'd still pick differently:

- Pick **`RealtimeVoiceChat`** if you want a longer-shot, higher-ceiling story that depends on you having voice/audio/streaming systems experience to defend in Q&A. The repo is a real production-targeting project, the failure modes are real, and the eval framework you'd propose for voice agents is genuinely novel. But it's a 3-day audit packed into 2.
- Pick **`babyagi-2o`** if you want the most dramatic "this is a security horror show" presentation. Memorable, but the rewrite-vs-refactor scope mismatch means the brief's specific deliverables (audit + single refactor + eval + cost math) don't all fit cleanly.
- Pick **none of these and widen** if you want a contrarian "no-name 200-star repo" play — see §5 of `01_repo_scouting_methodology.md` for the search gaps that make this option live.

---

## 6. Decision needed

1. Confirm `karpathy/llm-council` as the audit target.
2. Or override per §5.3.
3. Either way, the next steps after lock-in are: (a) draft the audit framework / production-readiness rubric, (b) build the cost-at-10k-users spreadsheet, (c) define the eval framework dimensions for "council vs. chairman alone," (d) implement the single-critical-failure refactor.
