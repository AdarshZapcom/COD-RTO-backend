# Find the Signal — Research & Design Notes

Quessathon 2026, Retail Challenge 01. This document captures what we've established
so far: the problem, the use cases the agent must handle, what a completed
investigation is expected to produce, and the architecture (as built, and as planned
for hosting).

## 1. Problem Statement

**Business problem.** Cash-on-Delivery (COD) orders in India carry a Return-to-Origin
(RTO) rate of **20–40%**, versus under **2%** for prepaid orders (TrackVid / IBEF
industry data, 2026). Each failed COD order costs a retailer an estimated
**Rs 180–240** in wasted shipping, reverse logistics, and blocked inventory
(ShipPrime research, 2026).

**What we're building.** Not an RTO prediction model — an **investigation agent**.
Given an incoming COD order, it must reconcile signals from four sources that can
disagree with each other:

- the **customer** (order/RTO history, verification status)
- the **pincode** (delivery-area track record)
- the **courier** (logistics-partner track record)
- the **courier × pincode lane** — the specific combination, which can behave very
  differently from either dimension alone (a courier can be fine everywhere except
  one pincode; a pincode can be fine with one courier and bad with another)

and decide, order by order:

- **RELEASE** — ship it
- **HOLD FOR VERIFICATION** — meaningful risk, but plausibly legitimate; verify before
  dispatch rather than reject outright
- **ESCALATE** — evidence is missing, conflicting, or too thin to decide confidently;
  route to a human ops reviewer

**The hard part isn't the obviously bad order.** It's telling apart "looks risky by
pattern but is legitimate" from "looks clean but isn't," using *recent* trend (last
7d/30d) rather than only lifetime averages, and — the core test — **knowing when the
agent doesn't have enough signal to decide, and saying so instead of guessing.**
That last property (confidence grounded in evidence completeness, sample size,
freshness, and agreement — never an arbitrary LLM-generated number) is the single
largest scored dimension in the Quessathon rubric (Judgement & robustness under
uncertainty, 30% weight) and is tested live via an unseen jury scenario ("the Reality
Test") on event day.

## 2. Use Cases to Cover

These are the situations the agent is required to handle correctly, per the
challenge brief's minimum capability list and test-scenario table. Each is
deliberately constructed into the synthetic dataset as one of ten scenario orders
(`ORD-10000`–`ORD-10009`) so behaviour against each can be checked directly.

| Scenario | Situation | Expected decision |
|---|---|---|
| `CLEAR_SAFE` | Loyal customer, healthy pincode, healthy courier, established lane | RELEASE |
| `CLEAR_RISKY` | Customer with a real RTO history; lane itself is clean | HOLD_FOR_VERIFICATION |
| `GOOD_CUSTOMER_BAD_PINCODE` | Strong customer, but pincode/lane carries real risk | HOLD_FOR_VERIFICATION (never auto-reject a good customer) |
| `COURIER_PINCODE_ANOMALY` | Otherwise unremarkable customer; the *specific* courier×pincode lane is severely deteriorating | HOLD_FOR_VERIFICATION (must be detected as lane-specific, not customer-driven) |
| `NEW_EVERYTHING` | No customer history, thin data on every other dimension | ESCALATE (cold start — confidence must fall, not default to release) |
| `LOW_SAMPLE_SPIKE` | A scary-looking recent RTO rate built on a tiny sample size | ESCALATE (must not overreact to a rate that isn't statistically meaningful) |
| `MISSING_COURIER_DATA` | Courier performance data is genuinely missing for this lane | ESCALATE (must recognize and state missing data, not silently fill a default) |
| `CONFLICTING_SIGNALS` | Strong, verified customer history directly contradicted by a risky pincode/lane | HOLD_FOR_VERIFICATION (both sides substantial — a human call, not a forced resolution) |
| `TEMPORARY_DISRUPTION` | Recent dip explained by a documented event (e.g. heavy rain, temporary courier capacity issue) | HOLD_FOR_VERIFICATION, but disruption context must be surfaced so it isn't read as persistent deterioration |
| `NO_HISTORICAL_PRECEDENT` | Decent volume, but this exact courier×pincode lane has never been formally investigated before | ESCALATE (no track record ≠ no risk, and must be distinguished from a lane with a thin-but-present history) |

Beyond the per-order call, the brief also asks for an **operational insight**: a
courier- or pincode-level pattern a human ops team could act on directly (e.g. "this
courier is deteriorating specifically in this pincode cluster"), not just a per-order
score. **This is not yet built** — see Section 4, Status.

## 3. Expected Output

Per order, the system produces a structured **investigation report**, not a bare
score:

```
order_id
decision              RELEASE | HOLD_FOR_VERIFICATION | ESCALATE
risk_level            LOW | MEDIUM | HIGH
confidence            0-1, derived from evidence completeness/sample size/
                       agreement — never invented by an LLM
reason                short deterministic reason for the decision
supporting_evidence   [ ...specific signals that argue for risk... ]
counter_evidence      [ ...specific signals that argue against risk,
                         including documented disruption events... ]
uncertainty_flags     [ ...specific reasons confidence is limited: missing
                         data, small sample, no historical precedent... ]
narrative             2-4 sentence plain-language explanation for a human
                       ops reviewer (LLM-written when available, deterministic
                       template otherwise — same evidence either way)
narrative_source       "llm" | "template_fallback"
retrieved_case_ids     historical cases used as precedent
most_relevant_case_id
advisory_flag          optional: something the LLM noticed that the
                        deterministic rules did not already surface
timing_ms               evidence / decision / retrieval / narrative timings
```

Every field is traceable to a specific, named piece of evidence — nothing in the
`decision`, `risk_level`, or `confidence` is ever produced by an LLM guess. This is
what "explainability" (15% of the rubric) and "prevent hallucination" (a rehearsed
jury question) actually mean in this build: deterministic code computes and decides,
the LLM only narrates and may flag something extra for a human to check — it can
never override the operational call.

## 4. Architecture

### 4.1 Pipeline (as built)

```
data_generator.py   -> synthetic, interconnected COD/RTO dataset
                       (customers, orders, pincodes, couriers,
                        courier_pincode lanes, delivery_attempts,
                        events, historical_cases)
        |
analytics.py         -> evidence layer: per-order signal lookups
                        (customer / pincode / courier / courier×pincode),
                        lifetime vs 30d vs 7d rates, trend, data-quality
                        checks (missing/stale/small-sample), supporting-
                        vs-counter-evidence comparison
        |
decision_engine.py   -> DETERMINISTIC, rule-based RELEASE / HOLD /
                        ESCALATE call + risk level + confidence.
                        Authoritative — this is the operational decision.
        |
case_memory.py        -> ChromaDB historical-case memory. Local embeddings
                        (sentence-transformers, no API key/network
                        dependency at query time). Tries an exact
                        courier×pincode lane filter first; falls back to
                        semantic-only search only if that lane has zero
                        precedent. Reranking is a deterministic score
                        (embedding similarity + same-lane bonus), not an
                        LLM call.
        |
investigation_agent.py -> ties it together: evidence -> decision ->
                        historical precedent -> human-readable narrative.
                        LLM (OpenAI, gpt-4.1-nano) only explains the
                        decision already made; if no API key is set, or
                        the call fails/times out, falls back to a
                        deterministic template narrative built from the
                        same evidence. Retrieved case text is treated as
                        untrusted data (never instructions) — an explicit
                        prompt-injection guard.

build_db.py            -> mirrors the generated CSVs into a local SQLite
                        DB (indexed joins across order/customer/lane) for
                        direct SQL querying and demo convenience. Derived,
                        rebuildable, not authoritative.
```

**Core design principle:** deterministic code computes numbers and decides; the LLM
only interprets and narrates, and is never allowed to change the operational call.
If the LLM is unavailable, slow, or fails, nothing about the decision changes — only
the prose gets simpler (template fallback). This is what makes the confidence score
and decision reproducible and jury-defensible, and is the direct answer to two of the
playbook's rehearsed jury questions ("How do you prevent hallucination?" and "What if
your LLM is wrong?").

### 4.2 Stack decisions

- **LLM**: OpenAI API (`gpt-4.1-nano` by default, via `OPENAI_API_KEY`)
- **Orchestration**: plain Python, no agent framework — each stage is a function with
  a typed (pydantic) input/output; full control, no extra dependency risk for a
  6-day build
- **Vector memory**: ChromaDB with local sentence-transformer embeddings (no network
  dependency at query time — avoids adding LLM-API latency/failure risk to retrieval
  during a live demo)
- **Local data**: CSV (source of truth for the synthetic dataset) + SQLite (derived,
  queryable mirror)

### 4.3 Deployment (planned)

- **Hosting**: AWS EC2 — both the backend (FastAPI, wrapping the investigation
  pipeline above) and the frontend will run on the same instance (or a small EC2
  setup) for the live demo.
- **Database**: Supabase (managed Postgres) replaces the local CSV/SQLite files as
  the system of record once hosted — `orders`, `customers`, `pincodes`, `couriers`,
  `courier_pincode`, `delivery_attempts`, `events`, `historical_cases` become Postgres
  tables. ChromaDB (or a Postgres-native vector extension, TBD) remains the
  historical-case retrieval layer.
- **Frontend**: not yet started. Plan is React, talking to the FastAPI backend.

### 4.4 Status

**Built:** synthetic data generator with 10 deliberate scenario orders; deterministic
evidence layer; deterministic decision engine (validated against all 10 scenarios'
expected decisions); ChromaDB historical-case retrieval with same-lane-first
reranking; LLM narrative layer with deterministic fallback; local SQLite mirror.

**Not yet built:** operational insight output (courier/pincode-level pattern
recommendation for ops teams); FastAPI backend wrapping the pipeline; frontend;
EC2 hosting; Supabase migration; ticket/handoff record for ESCALATE cases; live
"unseen curveball" hardening pass.
