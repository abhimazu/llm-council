# Candidate Repo Scouting — Methodology, Pool, and Downselect

**Purpose of this doc.** Show the full reasoning chain behind the three finalist repos. The user's preference is "challenge me as a co-founder, do not say yes to everything" — so this doc is meant to be falsifiable. If the search was too narrow, or a cut was wrong, the audit trail should make that visible.

**Status.** Pre-decision. The final pick (one of the three finalists) has not been locked in yet.

---

## 1. Search methodology

### 1.1 Filters derived from the brief (PS 2)

The brief sets these constraints:

- "Public AI project on GitHub that is **clearly a prototype** (minimal error handling, no eval)."
- "Treat it like it is yours and it is going live."
- Deliverables: production audit, refactor of single most critical failure, proposed eval framework, cost/latency at 10k users/day.

Translated into scouting filters:

| Filter | Value |
|---|---|
| Code size | ~500–3000 LOC (small enough to grok in half a day, large enough to have real surface area) |
| AI core-ness | LLM call must be in the hot path, not a garnish |
| Error handling | Minimal or absent (the brief explicitly lists this as a marker) |
| Eval | Absent or vibe-check level |
| Self-description | "Demo," "experimental," "hack," "POC," or equivalent |
| Recency | 2024–2026 (modern LLM patterns, modern API surface) |
| Activity | Recent enough that the repo is real, not abandoned-since-2023 |

### 1.2 Filters derived from the user's profile

User confirmed in the prior turn:

- Deepest production experience is **refactoring / code review / prod hardening** (not RAG, not agents, not eval).
- **No domain-rich corpus** on hand.
- Project instructions say: senior AI engineer, focus on **code explainability, latest API usage, tools usage, rapid prototyping**.

Translated into additional filters:

| Filter | Why |
|---|---|
| Avoid Anthropic / OpenAI / LangChain official repos | Already production-grade. Audit is a strawman. |
| Avoid pure-RAG demo repos with generic data | No corpus differentiation = demo graveyard. Picking PS 2 here just inherits the same problem PS 1 would. |
| Prefer repos where AI is the obvious feature but the systems-level problems are deeper | Plays to refactoring depth. Senior signal: "see past the AI hype to the actual systems problem." |
| Prefer repos with a recognizable author or recent virality | Brand on slide 1 buys 3 minutes of attention before defending tradeoffs. |

### 1.3 Filter for the winner pick

The user explicitly stated: pick the winner where suggested changes will create **larger impact**. Impact axes considered:

- **Cost impact** at 10k users/day (dollar reduction is dramatic and provable).
- **Latency impact** (user-visible UX delta).
- **Reliability impact** (fewer silent failures).
- **Security impact** (closing dangerous behavior).
- **Eval impact** (turning a vibe-check system into a measurable one).

### 1.4 Searches actually run — exact queries with rationale

Two phases: **discovery** (queries 1–5, surface candidates) and **verification** (queries 6–11, confirm or kill specific repos).

The wording matters. Each query was constructed to surface a specific *kind* of repo and to filter out a specific kind of noise. The rationale column is the hypothesis behind each query — what I expected to find, what I was deliberately trying to *not* find, and how the results actually came back.

#### Phase 1 — Discovery

**Query 1** — `github trending AI prototype repos 2026 minimal error handling no eval LLM demo`

- *Hypothesis:* current-year trending repos that explicitly carry prototype markers ("minimal error handling," "no eval," "demo") will surface candidates that fit the brief's wording verbatim.
- *Trying to find:* recent (2026) AI repos self-described as prototypes.
- *Trying to avoid:* mature frameworks, libraries, infra tools.
- *Result quality:* mixed. Surfaced trending lists (OpenClaw, Langflow, Dify, Flowise, Ollama) — most of which are *too big* to be prototypes. The query inadvertently optimized for "popular," which conflicts with "prototype." Useful for cataloging the noise to cut, less useful for finding signal.

**Query 2** — `github "experimental" OR "prototype" AI agent repo small open source 2025 2026`

- *Hypothesis:* agent-specific scope + the keywords "experimental" or "prototype" in quotes would force matches against repos that self-describe that way.
- *Trying to find:* agent prototypes with explicit prototype self-labeling.
- *Trying to avoid:* the same trending-framework noise as Query 1.
- *Result quality:* surfaced ShortGPT (experimental video framework) and Anima-i (autonomous agent continuity experiment) — both prototype-coded. Also surfaced curated lists (`500-AI-Agents-Projects`, `awesome-ai-agents-2026`) which are useful index entries. Still over-indexed on popular agents (OpenClaw, Dify, Agno, RAGFlow).

**Query 3** — `github popular AI demo project no production readiness LLM single call`

- *Hypothesis:* "single call" + "demo" + "no production readiness" would surface repos with one LLM call in the hot path that were never hardened.
- *Trying to find:* the smallest possible prototypes — one model call, one entry point, one happy path.
- *Trying to avoid:* multi-agent frameworks, libraries.
- *Result quality:* **highest-signal query of the discovery phase.** Surfaced `karpathy/llm-council` (the single best match) and `mozilla-ai/any-llm` (cut as a library, not application). The Karpathy result alone justified this query.

**Query 4** — `github voice AI agent prototype repo realtime streaming LLM`

- *Hypothesis:* voice/audio is a category where the AI part is just the LLM call but the *systems* problems are deeper (streaming, buffering, turn-taking, latency). That's a senior-coded audit narrative — "see past the AI hype to the actual systems problem."
- *Trying to find:* a voice prototype where the refactor target is plumbing, not the model.
- *Trying to avoid:* generic chat-with-LLM repos.
- *Result quality:* surfaced `KoljaB/RealtimeVoiceChat` and `vndee/local-talking-llm`. Also surfaced production frameworks (LiveKit, Vocode, Ultravox) cleanly identifiable as cuts. This query did exactly what it was designed to do.

**Query 5** — `github "GPT researcher" OR "MetaGPT" OR "babyagi" prototype known issues no eval`

- *Hypothesis:* the famous-prototype-era agent repos (BabyAGI, AutoGPT, MetaGPT, GPT-Researcher) all have public production-readiness criticism. Naming them directly in the query would surface that critical context.
- *Trying to find:* the original prototype-era agents and their honest assessments.
- *Trying to avoid:* sanitized marketing pages; wanted issues, blog criticism, post-mortems.
- *Result quality:* surfaced `yoheinakajima/babyagi-2o` (the strongest candidate from this query), `assafelovic/gpt-researcher` (later cut as too production-ready), and confirmed that the original BabyAGI was archived in Sept 2024 (frozen → not a fair audit target). MetaGPT surfaced repeatedly but cut on size.

#### Phase 2 — Verification

These queries were not trying to discover new repos. They were trying to confirm or kill specific candidates by pulling out architecture, file count, LOC, and current production state.

**Query 6** — `karpathy llm-council github repo size python files description`

- *Hypothesis check:* is `llm-council` actually small enough to fully audit in 2 days?
- *What I learned:* ~400 LOC main.py, FastAPI + httpx + OpenRouter. Confirmed prototype shape. **Locked in as a finalist.**

**Query 7** — `KoljaB RealtimeVoiceChat github repo size description architecture issues`

- *Hypothesis check:* is `RealtimeVoiceChat` a real-users-real-issues prototype, or a pet project no one runs?
- *What I learned:* 3.4k stars, active issue tracker, Docker recipe, real users. WebSocket streaming pipeline. Larger than `llm-council` but still prototype-coded. **Locked in as a finalist.**

**Query 8** — `vndee local-talking-llm github repo size description issues architecture`

- *Hypothesis check:* is `local-talking-llm` differentiated from `RealtimeVoiceChat`, or a thematic duplicate?
- *What I learned:* nearly identical thematic positioning (voice in, LLM, voice out). Smaller, less recognized. **Cut as a duplicate slot.**

**Query 9** — `assafelovic gpt-researcher github size complexity issues production readiness`

- *Hypothesis check:* is `gpt-researcher` still a prototype, or has it crossed into production-grade?
- *What I learned:* 25k stars, production-ready NextJS frontend variant exists, recent backend cleanup specifically for production deployments. **Cut on brief-fit** — picking it would force me to defend "I picked a production repo for the production-readiness audit."

**Query 10** — `github yoheinakajima babyagi-2o size description "self-building"`

- *Hypothesis check:* does `babyagi-2o` actually `exec()` LLM-generated code? (The security narrative depends on this.)
- *What I learned:* yes — dynamically creates and registers Python tools the LLM writes. No sandbox visible. **Locked in as a finalist** — the security refactor narrative is real.

**Query 11** — `github 2026 small AI prototype project stars "no error handling" OR "no eval" RAG agent`

- *Hypothesis:* one final sweep for missed small RAG/agent candidates with explicit prototype markers in their READMEs.
- *Result quality:* zero useful results. The query was over-constrained (too many quoted phrases, too narrow). **This is a gap** — it does not mean no such repos exist; it means web search couldn't find them with this query. A GitHub Search API query (e.g., `language:python stars:50..500 pushed:>2025-06-01 LLM in:readme`) would do this better and is listed as a known gap in §5.

---

## 2. Full candidate pool

Tagged by depth of evaluation:

- **[E]** Evaluated against criteria (read about the architecture, considered tradeoffs).
- **[N]** Surfaced by name but quickly cut on category (e.g., framework, list-of-lists, infra tool, production-grade product).
- **[L]** Appeared in result links but I did not open or evaluate — included for honesty, not as serious candidates.

### 2.1 Strong candidates (evaluated)

| Repo | Self-description | Why it surfaced | Tag |
|---|---|---|---|
| `karpathy/llm-council` | "99% vibe coded as a fun Saturday hack." Multi-LLM + chairman synthesizer. ~400 LOC FastAPI + httpx + OpenRouter. | Search 3, search 6 | **[E]** |
| `KoljaB/RealtimeVoiceChat` | Realtime voice pipeline. STT + LLM + TTS over WebSockets, ~500ms latency target. ~3.4k stars. | Search 4, search 7 | **[E]** |
| `vndee/local-talking-llm` | 3-component local voice assistant: Whisper + Ollama + ChatterBox TTS. | Search 4, search 8 | **[E]** |
| `yoheinakajima/babyagi-2o` | "The simplest self-building autonomous agent." `exec()`s LLM-generated Python tools. | Search 5, search 10 | **[E]** |
| `assafelovic/gpt-researcher` | Autonomous research agent. Planner → executors → publisher. 25k stars. | Search 5, search 9 | **[E]** |

### 2.2 Surfaced and cut on category (named, not opened)

| Repo | Why it surfaced | Cut reason | Tag |
|---|---|---|---|
| `openclaw/openclaw` | Search 1, 2 — viral 2026 breakout, 210k stars. Personal AI assistant + 50 integrations. | Too big and too production-coded to be "clearly a prototype." Refactoring 210k-star projects in 2 days is not credible. | **[N]** |
| `langflow-ai/langflow` | Search 1 — visual agent builder, 146k stars. | Production framework, not a prototype. Scope mismatch. | **[N]** |
| `langgenius/dify` | Search 1, 2 — 136k stars. Self-described "production-ready platform." | Self-describes as production. Audit is a strawman. | **[N]** |
| `FlowiseAI/Flowise` | Search 1, 3 — visual AI builder, 51k stars. | Same as Langflow — too polished, framework not prototype. | **[N]** |
| `ollama/ollama` | Search 1, 2 — LLM runner. | Infrastructure, not an AI app. AI is not in its hot path; it *is* the hot path for others. Wrong shape for PS 2. | **[N]** |
| `Mintplex-Labs/anything-llm` | Search 3 — "all-in-one AI productivity accelerator." | Pitched as production. Too polished. | **[N]** |
| `mozilla-ai/any-llm` | Search 3 — unified LLM provider interface. | Library, not an application. No AI in a hot path to audit. | **[N]** |
| `agno-agi/agno` (formerly Phidata) | Search 2 — fast/composable agent runtime. | Framework, not a prototype application. | **[N]** |
| `infiniflow/ragflow` | Search 2 — open-source RAG engine. | Production-grade engine, large team, not a prototype. | **[N]** |
| `livekit/agents` | Search 4 — voice agent framework. | Framework, not a prototype application. | **[N]** |
| `fixie-ai/ultravox` | Search 4 — multimodal LLM platform. | Pitched as platform; backed by venture-funded startup. Not a prototype. | **[N]** |
| `vocodedev/vocode-core` | Search 4 — voice LLM agent library. | Library, not a prototype application. | **[N]** |
| `agentvoiceresponse` (org) | Search 4 — voice response framework. | Framework. | **[N]** |
| `FoundationAgents/MetaGPT` | Search 5 — multi-agent SWE framework. | Too large. Self-positions as a framework. Years of issue churn, but not a prototype. | **[N]** |
| `Significant-Gravitas/AutoGPT` | Search 5 (mentioned) — autonomous agent project. | Now a polished platform with a UI. The original autonomous-agent prototype is in `babyagi_archive`, not here. | **[N]** |
| `yoheinakajima/babyagi` (current) | Search 5 — function database + executor. | The current `babyagi` repo is a function-database project, not the famous 2023 task-loop prototype. The prototype-coded ancestor is `babyagi_archive` (snapshot, frozen Sept 2024) — frozen repos are not a fair audit target. | **[N]** |
| `ShortGPT` | Search 2 — automated short-video creation. | Mentioned as "experimental AI framework." Did not verify size or current state — left as a candidate-of-record but not evaluated deeply. Cut on shallow data. | **[N]** |
| `Anima-i` | Search 2 — autonomous agent continuity experiment. | Niche / academic framing. Hard to ground production-readiness narrative. Cut on framing. | **[N]** |
| `RamziRebai/a-Realtime-Voice-to-Voice-Agentic-RAG-...` | Search 4 — voice-to-voice + RAG + Redis demo. | Personal-portfolio repo, low stars, hard to defend "this matters at 10k users/day" narrative. The brief's "treat it like it is yours" works better with a repo someone actually uses. | **[N]** |
| HuggingFace minimal agents (~1000 lines) | Search 1 (summary mention) | Reference implementation by HF. Effectively a "spec" repo, not a real product running for users. Audit is a strawman. | **[N]** |

### 2.3 Curated lists / awesome-repos (not candidates themselves)

| Repo | Tag |
|---|---|
| `caramaschiHG/awesome-ai-agents-2026` | **[N]** (list) |
| `ashishpatel26/500-AI-Agents-Projects` | **[N]** (list) |
| `Jenqyang/Awesome-AI-Agents` | **[N]** (list) |
| `Shubhamsaboo/awesome-llm-apps` | **[N]** (list) |
| `InfiniteAICreations/awesome-llm-projects` | **[N]** (list) |
| `alvinreal/awesome-opensource-ai` | **[N]** (list) |

### 2.4 Result-link noise (not opened)

These appeared in search-result link lists but I did not evaluate:

`GURPREETKAURJETHRA/END-TO-END-GENERATIVE-AI-PROJECTS` **[L]**, `ShaikhWarsi/free-ai-tools` **[L]**, `Gen-Verse/OpenClaw-RL` **[L]**, `lloydchang/karpathy-llm-council` (fork) **[L]**, `prax-lannister/karpathy-llm-council` (fork) **[L]**, `lucasastorian/llmwiki` **[L]**, `karpathy/llm-wiki` (gist, not repo) **[L]**, `pnkvalavala/repochat` **[L]**, `franztao/MetaGPT` (fork) **[L]**, `areibman/MetaGPT` (fork) **[L]**, `OpenHands/OpenHands` **[L]**, `karpathy/llm.c` **[L]** (training, not application).

---

## 3. Downselect from 5 evaluated candidates → 3 finalists

### 3.1 Cut: `vndee/local-talking-llm`

| Criterion | Assessment |
|---|---|
| Prototype shape | Yes — small local voice assistant. |
| AI core | Yes (Whisper + Ollama + TTS in pipeline). |
| Refactor surface | Modest — small codebase, fewer findings. |
| Brand recognition | Low. |
| Differentiation from `RealtimeVoiceChat` | Thematically overlapping (both voice). Including both burns a finalist slot on near-duplicates. |

**Cut reason:** thematic duplicate of `RealtimeVoiceChat`, with strictly less surface area and less brand recognition. Keep the better voice candidate; don't waste a slot.

### 3.2 Cut: `assafelovic/gpt-researcher`

| Criterion | Assessment |
|---|---|
| Prototype shape | **Failing** — 25k stars, has a "production-ready" NextJS frontend variant, recent backend cleanup for production deployments. |
| AI core | Yes. |
| Refactor surface | Large but already worked-on. The interesting failures are deeper / harder to find in 2 days. |
| Brand recognition | Medium-high in the agent-research space. |
| Differentiation | Agent-research narrative is rich. |

**Cut reason:** the brief explicitly says "*clearly* a prototype (minimal error handling, no eval)." gpt-researcher has been actively production-hardened. Picking it forces an awkward defense: "I picked a production repo for the production-readiness audit." The framing is fragile. Cut on brief-fit, not on quality.

### 3.3 The three finalists

| Repo | Shape | Strongest narrative | Weakest narrative |
|---|---|---|---|
| `karpathy/llm-council` | ~400 LOC, FastAPI + httpx + OpenRouter | Cost at 10k users/day. Multi-LLM fan-out math. Eval gap (does the council actually beat a single LLM?). | Saturated — recent virality means other candidates may pick it. |
| `KoljaB/RealtimeVoiceChat` | ~3.4k stars, WebSocket realtime audio pipeline | UX latency. Audio pipeline hardening. Eval-as-invention (no standard for voice agent eval). | Live demo is high-variance; voice domain knowledge required on top of code review. |
| `yoheinakajima/babyagi-2o` | Tiny, "simplest self-building autonomous agent" | Security horror story (`exec()` on LLM-generated code). Reliability + sandboxing refactor. | "Self-building agents" is a 2023 narrative that's aged poorly — framing risk. |

---

## 4. Recommended winner (held loosely)

**Recommendation: `karpathy/llm-council`.**

Defended on five axes:

1. **Scope fit.** ~400 LOC = complete audit possible in 2 days, not partial. Maps directly to the brief's "scope ruthlessly" principle.
2. **Brand-front-loading.** Karpathy's name + recent VentureBeat coverage = earned attention on slide 1.
3. **Cost story is the cleanest.** Multi-LLM × multi-pass × no caching has a deterministic, defensible cost model. Order-of-magnitude impact at 10k users/day. Spreadsheet writes itself.
4. **Best eval gap.** Karpathy didn't measure whether the council adds value over the chairman alone. That's PS 2's "proposed eval framework" deliverable handed over for free — and it's the exact question a venture studio would ask before productizing.
5. **Aligns with stated user strengths.** Small enough to fully comprehend → code explainability. Modern stack → latest API usage. Refactor-shaped problem (smart routing layer) → refactoring depth.

Held loosely because:

- If user has voice/audio/streaming systems experience not yet surfaced, `RealtimeVoiceChat` becomes the better pick — that depth is rarer and harder to fake.
- If user wants a contrarian "no-one-has-heard-of-this" play, none of these three are the answer; would require widening the search.

---

## 5. Honest gaps in this search

Calling these out so the user can decide whether to widen.

1. **No GitHub Search API queries.** All searches were Google-mediated. GitHub-native filters (stars, language, last-pushed, license, README content matching) would surface a different population — likely more obscure, less brand-named candidates.
2. **No code-level verification.** Sizes and architecture descriptions are from search summaries, not from cloning and running `tokei` / `cloc`. Confidence in LOC numbers is medium. We should clone the finalists before locking in.
3. **Skewed toward known names.** Searches 1, 2, 5 were biased toward "trending" and "popular," which surfaces well-known repos. A "give me a 200-star prototype with one LLM call in the hot path" search would have produced a different candidate pool. That's the contrarian-play option.
4. **No coverage of vertical AI prototypes.** Searches did not target legal-AI, medical-AI, finance-AI, code-AI, or other verticals where genuinely novel prototypes exist. If the user has a domain bias, this is a gap.
5. **No issue-tracker reading.** A real production-readiness audit would start with reading the open issues on each finalist. We have not yet done that.
6. **`ShortGPT` and `Anima-i` were cut on shallow data.** Surfaced in search 2 but never verified. If user wants a thorough sweep, these are open candidates.

---

## 6. Decision needed from user

Before proceeding to the audit:

1. **Lock the winner**: confirm `karpathy/llm-council`, or override to one of the other two finalists.
2. **Or widen the search** if (a) the contrarian no-name play is preferred, (b) a specific vertical is preferred, or (c) the gaps in §5 feel material.
3. **Or override the entire problem statement** — if after reading this, PS 4 (Eval Framework from Scratch) feels like a stronger fit than PS 2, that's still on the table.
