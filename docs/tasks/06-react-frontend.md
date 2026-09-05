# React Frontend

The demo UX. Lives in the separate `COD-RTO-frontend` repo
(`C:\Users\AdarshGadekar\COD-RTO-frontend`), talking to the FastAPI backend
(task 05) over REST. Scope is deliberately small — this needs to show the
reasoning clearly on stage, not be a polished product.

## Screens / components

**OrderPicker**
- Searchable/filterable list from `GET /orders` (filter by scenario for
  rehearsal)
- Each row: order_id, customer_id, pincode, courier_id, order_value

**AdHocOrderForm**
- The piece that handles the live curveball: free-entry `customer_id`,
  `pincode`, `courier_id`, `order_value` (any of which may not exist in
  the dataset), submits to `POST /investigate`
- Needs to work identically whether the jury describes a brand-new
  customer or reuses an existing one

**EvidencePanel** — 4 cards, one per dimension (Customer / Pincode /
Courier / Lane), each showing:
- `rto_rate_7d` / `30d` / `90d`, `trend`
- sample size, with a visible badge if `data_quality` is `STALE` or
  `MISSING` (this needs to be impossible to miss — it's the "does the
  agent know what it doesn't know" signal made visible)

**EvidenceComparison** — two columns, Supporting vs. Counter-evidence,
straight from `signal_comparison.supporting_evidence` /
`counter_evidence`

**PrecedentPanel** — top-3 retrieved cases from
`retrieved_case_ids`/`most_relevant_case_id`, each tagged SAME LANE vs
cross-lane, showing that case's past decision + actual outcome

**DecisionBanner** — the one thing visible without scrolling:
`decision` (color-coded: RELEASE / HOLD_FOR_VERIFICATION / ESCALATE),
`risk_level`, `confidence`, `reason`

**NarrativePanel** — the `narrative` text, with a visible tag showing
`narrative_source` (`llm` vs `template_fallback`) — don't hide this, it's
a credibility signal that nothing is faked if the LLM call fails live

**InsightsStrip** — from `GET /insights` (task 01): courier/pincode
findings, severity-coded

**TicketsView** — from `GET /tickets` (task 02): open escalations, with a
resolve action

**TimingFooter** — `timing_ms` breakdown from the report (evidence /
decision / retrieval / narrative) — cheap to show, reinforces engineering
quality live

## Stack

Plain React (Vite), fetch/axios against the FastAPI backend's base URL
via an env var (`VITE_API_BASE_URL`) so it can point at `localhost:8000`
during rehearsal and the EC2 URL (or still `localhost`, if task 07 is
skipped) on demo day without a code change.

## What NOT to build

No client-side routing library needed for 9 components on one screen; no
global state library — local component state + one shared "current
investigation" object passed down is enough at this scale.

## Acceptance check

Full flow works end to end against a running `uvicorn` instance: pick
`ORD-10003` (`COURIER_PINCODE_ANOMALY`) from OrderPicker, see all panels
populate, confirm `HOLD_FOR_VERIFICATION` renders in the banner; then use
AdHocOrderForm with a made-up customer/pincode/courier and confirm it
comes back `ESCALATE` with the missing-data flags visible.
