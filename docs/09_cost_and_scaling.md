# Cost and Scaling Considerations — `llm-council` at 10k Users/Day

**Audience:** product owner / founding engineer making a deploy decision.
**Numbers source:** API costs from OpenRouter (May 2026 published rates), infra from AWS public pricing pages, observability from Datadog public pricing. **All sources cited at the bottom.** Real measurements from the flagship sweep in `07_flagship_sweep_results.md` ($0.188 spent) anchor the per-query token counts; the dollar amounts in this doc are then **recomputed at the latest May 2026 flagship lineup** so the projections reflect today's costs, not what the sweep happened to use.

---

## 1. Model lineup — latest May 2026 flagships

The original Karpathy `config.py` references a future-dated mix of models (`openai/gpt-5.1`, `google/gemini-3-pro-preview`, `anthropic/claude-sonnet-4.5`, `x-ai/grok-4`). As of May 2026, three of those four have either been superseded or scheduled for end-of-life. The table below shows the **latest-available** flagship-tier roster on OpenRouter, with their public passthrough prices.

| Role | Model | Input ($/1M tok) | Output ($/1M tok) | Notes |
|---|---|---:|---:|---|
| **Chairman** | `anthropic/claude-opus-4.7` | $5.00 | $25.00 | New tokenizer uses ~35% more tokens for the same text → effective cost ~35% higher than headline |
| Council | `anthropic/claude-sonnet-4.6` | $3.00 | $15.00 | Latest Sonnet; same headline as 4.5, same family as chairman (B5 audit concern) |
| Council | `openai/gpt-5.4` | $2.50 | $15.00 | Current GPT-5 flagship value tier (GPT-5.5 launched at 2× price; GPT-5.5 Pro at 6×) |
| Council | `google/gemini-3.1-pro-preview` | $2.00 | $12.00 | Replaces gemini-3-pro-preview; flagship reasoning |
| Council | `x-ai/grok-4.3` | $1.25 | $2.50 | **Output is dramatically cheaper than peers**; Grok 4 is being deprecated May 15, 2026 |
| Routing classifier | `google/gemini-3.1-flash-lite` | $0.25 | $1.50 | Cheapest fast tier; right model for one-token classification |
| Title-gen | `google/gemini-3.1-flash-lite` | $0.25 | $1.50 | Same — best-effort, never blocks main flow |

**Key cost-shape observations vs the older models the sweep used:**

- **Grok 4.3 dropped output pricing from ~$15/M to $2.50/M** — a ~6× reduction. Grok 4.3 is now the cheapest output tokens of any flagship-tier model in the council.
- **Claude Opus 4.7's new tokenizer is the only meaningful price increase.** Headline rates didn't change, but the tokenizer counts ~35% more tokens for the same input/output text — so in practice, every Opus 4.7 call costs ~35% more than the Opus 4.5 call would have.
- **GPT-5.4 increased output pricing from $10/M (GPT-4o) to $15/M** — about 1.5× more expensive on output.
- **Gemini 3.1 Pro Preview increased input from $1.25 to $2/M** and output from $10 to $12/M — modest 1.2-1.6× increase.

Net effect across the full pipeline: **roughly 5-30% per-query cost increase from model upgrades**, partially absorbed by Grok 4.3's output savings.

---

## 2. Workload model — what 10k users/day actually means

| Assumption | Value | Why |
|---|---|---|
| Daily active users | 10,000 | Per the brief |
| Queries per user per day | 3 | Typical chat product engagement |
| **Total queries per day** | **30,000** | |
| Peak-to-average ratio | 4× | Peak hour: ~5,000 queries/hour |
| Peak QPS | ~1.4 | (5,000 / 3,600) — modest, single-instance can handle |
| First-message ratio | 30% | Triggers title-gen call on top of council/SOLO |
| Workload mix | 60% factual / 40% complex | Per audit `06_proposed_changes.md` §4 |
| Cache hit rate | 30% | Conservative for FAQ-shaped traffic |
| Avg conversation length | 5 turns | Affects DB write volume |

This is a load profile a single well-tuned host handles, not a horizontally-scaled fleet. The cost story is dominated by LLM API spend, not infrastructure.

---

## 3. LLM API costs — measured tokens, latest pricing

### 3.1 Methodology

The flagship sweep (`07_flagship_sweep_results.md`) measured **actual input and output token counts** for 52 LLM calls across 8 query attempts ($0.188 spent against the older models that were live at sweep time). For this doc, I take **the same measured token counts** and reprice each call against the latest May 2026 flagship lineup from §1, applying Opus 4.7's tokenizer inflation factor. This is the fairest possible projection — same workload, current prices.

### 3.2 Per-query cost — measured at sweep time vs reprojected at latest pricing

| Phase / query | LLM calls | Sweep cost (older models) | At latest models (§1) | Δ% |
|---|---:|---:|---:|---:|
| Phase 1 (new pipeline) — fact_arith (SOLO) | 2 | $0.00051 | **$0.00066** | +29% |
| Phase 1 (new pipeline) — fact_capital (SOLO) | 2 | $0.00040 | **$0.00051** | +28% |
| Phase 1 (new pipeline) — fact_author (SOLO) | 2 | $0.00043 | **$0.00055** | +28% |
| Phase 1 (new pipeline) — complex_db (council) | 10 | $0.05738 | **$0.05884** | +2.6% |
| Phase 2 baseline — fact_arith forced council, capped | 9 | $0.02980 | **$0.03198** | +7.3% |
| Phase 2 baseline — fact_capital forced council, capped | 9 | $0.02713 | **$0.02823** | +4.0% |
| Phase 2 baseline — fact_author forced council, capped | 9 | $0.03053 | **$0.03199** | +4.8% |
| Phase 3 baseline — fact_arith forced council, **uncapped** | 9 | $0.04161 | **$0.04588** | +10.3% |
| **Sweep total** | 52 | **$0.18781** | **$0.19865** | +5.8% |

The latest pricing is ~6% more expensive end-to-end on the same workload. SOLO queries are most affected (~28% increase) because the chairman call alone is a big share of their cost and Opus 4.7's tokenizer inflation hits hardest there. Council queries are barely affected (~2-7%) because Grok 4.3's output savings offset the Opus increase.

### 3.3 Category averages (latest pricing)

| Path | Avg cost per query | LLM calls | Notes |
|---|---:|---:|---|
| **SOLO** (factual, routed away from council) | **$0.00057** | 2 | Routing classifier + chairman alone |
| **Council** (complex/baseline, capped pipeline) | **$0.03776** | 9-10 | Routing/title + 4 stage-1 + 4 stage-2 + chairman |
| **Council UNCAPPED** (original Karpathy behavior) | **$0.04588** | 9 | What you'd pay without this branch's caps |
| **Cache hit** | **$0.00000** | 0 | Verified empirically: 0.0s wall, 0 LLM calls |

### 3.4 Monthly LLM API spend at 10k users/day (30k queries/day)

**Without this branch (original config: uncapped, no routing, no cache):**

| Component | Math | Per day | Per month (30d) |
|---|---|---:|---:|
| All 30k queries through uncapped council | 30k × $0.04588 | **$1,376** | **$41,293** |

**With this branch (caps + routing + cache):**

| Component | Math | Per day | Per month |
|---|---|---:|---:|
| 60% factual queries → SOLO | 0.6 × 30k × $0.00057 | $10.26 | $308 |
| 40% complex queries → council (capped) | 0.4 × 30k × $0.03776 | $453.12 | $13,594 |
| Cache hits absorb 30% of total | × (1 − 0.30) | $324.43 | **$9,733** |

**Headline at the latest pricing:** the cost-control layer in this branch saves **~$31,560/month at 10k users/day** vs the original codebase, a **76% reduction**.

### 3.5 Sensitivity analysis (latest pricing)

The biggest single lever is the **factual/complex mix**:

| Workload mix | Per-query (new pipeline) | Per-day | Per-month |
|---|---:|---:|---:|
| 80% factual / 20% complex | $0.00561 | $168 | **$5,055** |
| 60% / 40% (default) | $0.01081 | $324 | **$9,733** |
| 30% / 70% (advisor-style) | $0.01861 | $558 | **$16,750** |

Cache hit rate sensitivity:

| Cache hit rate | Per-day cost | Per-month |
|---|---:|---:|
| 0% | $463 | $13,904 |
| 30% (default) | $324 | **$9,733** |
| 50% | $232 | $6,952 |
| 70% | $139 | $4,171 |

**Translation:** a customer-support-style workload (high repeat rate, mostly factual) drops monthly LLM spend below $4k. A product-strategy-advisor workload (subjective + low repeat) lands closer to $17k.

---

## 4. Infrastructure costs

The original Karpathy code is local-first. To deploy at 10k users/day you need: web tier, database, cache, load balancer, and observability.

### 4.1 Reference architecture (single AWS region)

```
                   Cloudflare / Route53
                          │
                          ▼
                 ALB (single, multi-AZ)
                          │
              ┌───────────┼───────────┐
              ▼           ▼           ▼
         FastAPI app  FastAPI app  FastAPI app    ← 2-3 EC2 t3.medium or Fargate tasks
              │           │           │
              └───────────┼───────────┘
                          │
              ┌───────────┴───────────┐
              ▼                       ▼
       RDS PostgreSQL          ElastiCache Redis
       (db.t3.medium)          (cache.t3.micro)
       — replaces filesys      — replaces in-process
         JSON storage            CouncilCache
                          │
                          ▼
                 Datadog (APM + logs)
```

**Why these choices:**
- **2-3 EC2 instances behind ALB:** the audit's storage race (D1/D2) requires single-worker uvicorn until SQLite migration lands. Multi-instance is OK as long as each instance is single-worker. 2 instances for HA, 3 for headroom.
- **RDS PostgreSQL:** replaces the filesystem JSON storage that loses 64% of writes under multi-worker load. `db.t3.medium` is overkill for the data volume but the right starting point.
- **ElastiCache Redis:** replaces the in-process `CouncilCache` so cache hits work across all instances. Without this, each instance has its own cache and the hit rate degrades as you scale horizontally.
- **Cloudflare in front:** rate limiting at the edge is cheaper and more effective than per-IP middleware in FastAPI. $5/month catches abuse before it reaches your origin.

### 4.2 Itemized monthly costs (us-east-1, on-demand)

| Component | Spec | Unit cost | Quantity | Monthly |
|---|---|---:|---:|---:|
| EC2 (web tier) | `t3.medium` (2 vCPU, 4 GB) | $30.37 | 3 | **$91** |
| EBS storage | gp3, 100 GB per instance | $0.08/GB | 300 GB | **$24** |
| ALB | base + ~1 LCU avg | $0.0225/hr + LCU | 1 | **~$24** |
| RDS PostgreSQL | `db.t3.medium` | $80.30 | 1 | **$80** |
| RDS storage | gp3, 50 GB allocated | $0.115/GB | 50 GB | **$6** |
| ElastiCache Redis | `cache.t3.micro` | $12.41 | 1 | **$12** |
| Data transfer out | $0.09/GB after 100 GB free | $0.09/GB | ~100 GB | **$0** (within free tier) |
| Cloudflare Workers | $5 base, 0 overage at this scale | $5 | 1 | **$5** |
| Datadog APM | $31/host | $31 | 3 | **$93** |
| Datadog logs (ingest + index) | ~10 GB/mo structured logs | $0.10 + $1.70/M events | — | **~$10** |
| **Infrastructure total** | | | | **~$345/month** |

**The infra is dwarfed by LLM API cost** ($9,733 vs $345 in the default workload — a 28× ratio). For an LLM-heavy product, $345/month on hosting to operate $9,733/month of model traffic is a healthy ratio.

### 4.3 What changes at 100k users/day (10× scale)

| Component | At 10k users | At 100k users | Why |
|---|---|---|---|
| EC2 instances | 3 × t3.medium | 6-8 × m5.large | Higher peak QPS (~14 instead of 1.4) |
| RDS | db.t3.medium | db.m5.large + read replica | Conversation reads dominate |
| Redis | cache.t3.micro | cache.r6g.large | Cache size grows with unique-query distribution |
| **LLM API spend** | **~$9.7k/month** | **~$97k/month** | Linear with query volume |
| Infrastructure spend | ~$345 | ~$1,800 | Far less linear than LLM spend |

LLM spend stays the cost driver at every scale. Infrastructure is cheap by comparison.

---

## 5. Latency — measured + budget

### 5.1 Measured (from the flagship sweep)

| Path | p50 wall clock | Driver |
|---|---:|---|
| **SOLO** (factual, routed away from council) | **3.5 s** | 1 cheap routing call (~1 s) + 1 chairman call (~2.5 s) |
| **Council** (complex, full pipeline, capped) | **18-36 s** | 4 parallel Stage-1 + 4 parallel Stage-2 + 1 chairman, sequenced |
| **Cache hit** | **0.0 s** | No LLM calls |

Stage 3 (chairman) is the slowest single call — ~50% of council-path wall clock. With reasoning models, this is unavoidable without streaming the chairman tokens to SSE (P2 in `FUTURE_SCOPE.md`). Note: Opus 4.7 with the new tokenizer may run somewhat slower per-equivalent-text than Opus 4.5 due to processing more tokens.

### 5.2 SLO targets at 10k users/day

| Metric | Target | Achievable today? |
|---|---|---|
| Cache hit p99 | < 100 ms | ✅ trivially |
| SOLO query p50 | < 5 s | ✅ measured 3.5 s |
| SOLO query p95 | < 8 s | ✅ likely |
| Council query p50 | < 25 s | ⚠️ measured 18-36 s; depends on slowest model |
| Council query p95 | < 60 s | ⚠️ requires per-stage timeout tuning + streaming chairman |
| Streaming chairman first token | < 3 s | ❌ not built; in `FUTURE_SCOPE.md` P2 |

The council p95 SLO is the hardest. Three levers: (a) shorter per-stage timeouts (already configurable), (b) drop slowest council member after a budget exceeds (not built), (c) stream the chairman tokens for perceived latency improvement (not built).

---

## 6. Scaling considerations — where this architecture breaks

### 6.1 Hard limit: storage race condition (D1/D2)

The current branch still uses filesystem JSON storage. **Under `uvicorn --workers > 1`, 64% of writes are lost** (verified live, doc 04). Deploy story today: **single-worker uvicorn per instance**. With 3 instances behind an ALB, you have 3 workers total — fine for ~5 RPS each, tight at peak QPS of ~1.4.

**Fix: P1-1 in `FUTURE_SCOPE.md`** (SQLite migration, 1 senior-eng day). Until this lands, do not run multi-worker uvicorn.

### 6.2 Cache invalidation across instances

The in-process `CouncilCache` is per-instance. With 3 instances, the cache hit rate drops to roughly 1/3 of single-instance (each query hits a random instance). **Move to ElastiCache Redis behind the same `CouncilCache` interface** — the module's docstring calls out this swap point. ~0.5 senior-eng day.

### 6.3 OpenRouter rate limits

OpenRouter rate-limits per-key. At 30k queries/day with the 60/40 mix:
- **Total LLM calls/day:** ~210k
- **Peak hourly:** ~35k (4× peak factor)
- **Per-second:** ~10 calls/second peak

OpenRouter's documented per-key limits vary by tier; pre-paid credits unlock higher RPM. For this scale: (a) negotiate a higher-tier key, or (b) round-robin multiple keys.

### 6.4 No conversation memory means context loss

The chat product currently treats each user message as context-free (audit G2/M3, demonstrated live in doc 05). Real users will notice this on follow-up questions. **Fix: P1-2 in `FUTURE_SCOPE.md`** (1.5 days). Without it, perceived product quality at 10k users is capped by user frustration with amnesia.

### 6.5 No per-IP rate limiting at the application layer

A determined caller can submit valid 64 KB queries faster than the cache can absorb them, running up the OpenRouter bill. **Fix: `slowapi` middleware (P1-3, 0.5 day).** Until this lands, **rely on Cloudflare in front** — the $5/month plan covers this.

### 6.6 Opus 4.7 tokenizer is a soft trap

Effective cost is ~35% higher than the headline rate suggests. If you upgrade from Opus 4.5 → 4.7 expecting "same price, better model," your bill grows even at constant usage. Monitor token counts before/after the upgrade and adjust caps accordingly.

---

## 7. Cost-optimization levers

### 7.1 In this branch (verified by sweep)

| Lever | Mechanism | Empirical effect |
|---|---|---|
| Smart routing | Skip 4-LLM council for trivial queries | ~98.5% cost reduction per factual query |
| Per-stage `max_tokens` caps | Bound chairman + stage outputs | ~30% cost reduction on the same query (capped vs uncapped, latest pricing) |
| In-memory LRU cache | Skip LLM calls entirely on repeats | 100% on cache hits (verified) |

### 7.2 Future levers (specced in `FUTURE_SCOPE.md`)

| Lever | Estimated additional savings | Effort |
|---|---|---|
| Semantic cache (P2) | +5-15% on top of exact-match cache | 2 days |
| Streaming chairman + per-stage timeouts (P2) | Latency, not direct cost | 1 day |
| Drop slowest council member after budget | -25% on council queries that hit slow-tail | 1 day |
| **Swap chairman to a cheaper model** (e.g., Sonnet 4.6 instead of Opus 4.7) | **~50% on chairman cost alone**, conditional on eval | Eval framework first |
| **Use Grok 4.3 as chairman** (very cheap output, $2.50/M) | **Up to 80% on chairman cost**, conditional on eval | Eval framework first |
| Provider-direct contracts at scale | -10-20% on flagship rates | Negotiation |

### 7.3 The eval framework dependency

The biggest single unlock is data: knowing **which queries the council was right about and which it could have skipped without quality loss.** Without that data:
- The routing classifier's threshold is a guess.
- "Swap to a cheaper chairman" is a guess.
- "Drop the slowest council member" is a guess.

The eval framework cost (~$65 for the full sweep) is trivial compared to the monthly LLM spend it would optimize. **At 10k users/day saving 10% via eval-informed model swaps = ~$1,000/month, ~15× ROI on the eval cost in the first month.**

---

## 8. Bottom line — TCO summary at latest May 2026 pricing

| Stage | Setup | Monthly LLM | Monthly infra | Monthly total |
|---|---|---:|---:|---:|
| **Original code, deployed as-is** (uncapped, no routing, no cache) | 3 instances, no SQLite, no Redis, no monitoring | $41,293 | $145 | **~$41,438** |
| **This branch, deployed conservatively** (single-worker uvicorn, in-process cache per instance) | 3 instances, no SQLite, no shared cache | ~$10,400 | $345 | **~$10,745** |
| **This branch + P1 fixes** (SQLite, shared Redis cache, conv memory, rate limit) | Full reference architecture above | **~$9,733** | $360 | **~$10,093** |
| **Original code at 100k users/day** (linear extrapolation) | 6-8 instances + DB + Redis | $412,930 | $1,800 | **~$414,730** |
| **This branch + P1 at 100k users/day** | Full architecture | ~$97,300 | $1,800 | **~$99,100** |

**Conclusion at latest pricing:** the cost-control layer in this branch reduces monthly TCO by **~76%** at 10k users/day vs the original codebase. Reduction grows in absolute terms with scale because LLM spend dominates infra spend at every scale we considered.

The single biggest remaining lever is the eval framework — not for additional reduction, but to **defend the routing claims under scrutiny** *and* to unlock the larger model-swap savings (chairman down-tiering, slowest-member dropping) that aren't safe to pursue without eval data.

---

## 9. What I'm uncertain about (own the gaps)

1. **OpenRouter passthrough at 30k queries/day.** Published rates and the 5.5% credit-purchase fee are well-documented; volume discounts at this tier are not. ±10% on the LLM line item.
2. **Opus 4.7 tokenizer inflation factor.** I used 1.35× per the OpenRouter announcement. The real number depends on text characteristics (English prose ≠ code ≠ JSON). Could be 1.20-1.50×.
3. **Cache hit rate of 30%.** Pulled from "typical for FAQ-shaped traffic." Yours might be 10% (novel-query workload) or 70% (repetitive support workload). Re-run §3.5 with your number.
4. **The 60/40 factual/complex mix.** Same caveat as cache hit rate. The sensitivity table gives bounds.
5. **Whether the routing classifier preserves quality.** Open until eval framework lands. If the classifier is wrong 20% of the time on factual queries, the cost numbers above are right but UX is degraded — and we cannot detect this without eval.
6. **Datadog cost at log volume.** $10/month assumes ~10 GB/month. A debug-mode misconfig could 10× this.
7. **Data egress.** Estimated 100 GB/month based on small SSE chunk sizes. If users keep streams open or you serve large bodies, this grows.
8. **Grok 4.3 longevity.** xAI's release cadence is faster than Anthropic/OpenAI. The model lineup may shift within 3-6 months; revisit this doc when it does.

---

## Sources

API pricing (OpenRouter, May 2026):
- [OpenRouter Pricing](https://openrouter.ai/pricing)
- [Claude Opus 4.7 - API Pricing & Benchmarks](https://openrouter.ai/anthropic/claude-opus-4.7)
- [Opus 4.7's New Tokenizer: What It Actually Costs](https://openrouter.ai/announcements/opus-47-tokenizer-analysis)
- [Claude Sonnet 4.6 - API Pricing & Benchmarks](https://openrouter.ai/anthropic/claude-sonnet-4.6)
- [GPT-5.4 - API Pricing & Benchmarks](https://openrouter.ai/openai/gpt-5.4)
- [GPT-5.5 - API Pricing & Benchmarks](https://openrouter.ai/openai/gpt-5.5)
- [GPT-5.5 Price Increase: What It Actually Costs](https://openrouter.ai/announcements/gpt55-cost-analysis)
- [Gemini 3.1 Pro Preview - API Pricing & Benchmarks](https://openrouter.ai/google/gemini-3.1-pro-preview)
- [Gemini 3 Flash Preview - API Pricing & Benchmarks](https://openrouter.ai/google/gemini-3-flash-preview)
- [Gemini 3.1 Flash Lite - API Pricing & Providers](https://openrouter.ai/google/gemini-3.1-flash-lite)
- [Grok 4.3 - API Pricing & Benchmarks](https://openrouter.ai/x-ai/grok-4.3)
- [Grok 4 - API Pricing & Benchmarks (deprecating May 15, 2026)](https://openrouter.ai/x-ai/grok-4)
- [OpenAI API Pricing Guide 2026](https://devtk.ai/en/blog/openai-api-pricing-guide-2026/)
- [OpenRouter Pricing Calculator & Cost Guide (May 2026)](https://costgoat.com/pricing/openrouter)

AWS infrastructure (us-east-1, on-demand, May 2026):
- [Amazon EC2 On-Demand Pricing](https://aws.amazon.com/ec2/pricing/on-demand/)
- [t3.medium pricing: $30.37 monthly](https://www.economize.cloud/resources/aws/pricing/ec2/t3.medium/)
- [AWS RDS for PostgreSQL Pricing](https://aws.amazon.com/rds/postgresql/pricing/)
- [db.t3.medium pricing: $80.30 monthly](https://www.economize.cloud/resources/aws/pricing/rds/db.t3.medium/)
- [Amazon ElastiCache Pricing](https://aws.amazon.com/elasticache/pricing/)
- [cache.t3.micro pricing: $12.41 monthly](https://www.economize.cloud/resources/aws/pricing/elasticache/cache.t3.micro/)
- [Elastic Load Balancing Pricing](https://aws.amazon.com/elasticloadbalancing/pricing/)
- [AWS ALB Pricing Explained: A 2026 Guide](https://www.cloudzero.com/blog/aws-alb-pricing/)

Observability (Datadog, May 2026):
- [Datadog Pricing](https://www.datadoghq.com/pricing/)
- [Datadog Pricing 2026: $15-$27/host + APM $31/host + Logs $0.10/GB](https://costbench.com/software/observability/datadog/)

Edge / rate limiting (Cloudflare, May 2026):
- [Cloudflare Workers Pricing](https://developers.cloudflare.com/workers/platform/pricing/)
- [Cloudflare Workers Pricing Plans (2026)](https://comparetiers.com/tools/cloudflare-workers)

Sweep telemetry (this submission package):
- `07_flagship_sweep_results.md` — measured token counts that anchor every projection above
- `llm-council-changes/sweep_artifacts/raw_calls.jsonl` — raw OpenRouter `usage` data (52 calls)
