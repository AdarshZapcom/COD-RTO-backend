# Find the Signal — Business Scenario Catalog

Real-world, business-framed situations the investigation agent should handle
correctly. The 10 scenarios baked into the dataset (`ORD-10000`–`ORD-10009`,
see `docs/research.md` §2) prove the core decision logic. This catalog goes
wider: it's the scenario list the `COD-RTO-frontend/tests/e2e/scenarios.spec.js`
Playwright suite drives end-to-end, and the reference for anyone (jury,
teammate, future contributor) asking "what happens if...?"

Each scenario gives concrete input values so it's directly reproducible
through `AdHocOrderForm` (or `GET /orders/{order_id}/investigate` for a
dataset order) — no guessing required.

---

## A. Core decision scenarios (dataset orders)

Already built and validated in `decision_engine.EXPECTED`; listed here only
for completeness of "all possible scenarios."

| # | Order | Business situation | Expected |
|---|---|---|---|
| A1 | `ORD-10000` | Loyal, fully-verified customer; healthy pincode/courier/lane | RELEASE |
| A2 | `ORD-10001` | Customer with a real RTO history; lane itself is clean | HOLD_FOR_VERIFICATION |
| A3 | `ORD-10002` | Strong customer, risky pincode/lane | HOLD_FOR_VERIFICATION |
| A4 | `ORD-10003` | Unremarkable customer; this specific courier×pincode lane is severely deteriorating | HOLD_FOR_VERIFICATION |
| A5 | `ORD-10004` | No customer history, thin data everywhere (cold start) | ESCALATE |
| A6 | `ORD-10005` | A scary 7d rate on a tiny sample, but the customer is established and fully verified | HOLD_FOR_VERIFICATION — see note below |
| A7 | `ORD-10006` | Courier performance data genuinely missing for this lane | ESCALATE |
| A8 | `ORD-10007` | Strong verified customer history directly contradicted by a risky lane | HOLD_FOR_VERIFICATION |
| A9 | `ORD-10008` | Recent dip explained by a documented weather/courier event | HOLD_FOR_VERIFICATION, disruption context surfaced |
| A10 | `ORD-10009` | Decent volume, but this exact lane has never been formally investigated | ESCALATE |

> **A6 note:** `LOW_SAMPLE_SPIKE`'s expected outcome changed from ESCALATE to
> HOLD_FOR_VERIFICATION when the numpy-bool verified-evidence bug (below, B1)
> was fixed — CUST-005's phone/address verification and established history
> now correctly count as counter-evidence, so the "genuinely conflicting
> evidence" branch legitimately fires ahead of the "thin sample" branch. This
> is the corrected, intended behavior (see `decision_engine.py` EXPECTED
> dict's inline comment), not a regression.

---

## B. Regression scenarios (the 10 bugs just fixed)

Concrete repro for each, framed as the business situation that surfaces it —
this is what the Playwright suite asserts against directly.

### B1 — Verified customer evidence actually counts
**Situation:** A phone- and address-verified, long-standing customer places
an order. An ops reviewer expects "verified" status to visibly reduce risk.
**Input:** `ORD-10000` (`CUST-001`) via OrderPicker.
**Expected:** Counter-evidence panel lists "Phone number is verified" *and*
"Address is verified"; RELEASE confidence ≈ 0.90, not silently stuck low.

### B2 — Malformed order value doesn't crash the API
**Situation:** A buggy integration or adversarial client sends a
non-standard numeric literal as the order value.
**Input (direct API, not reachable via the number input in the UI):**
`POST /investigate` with raw body `{"order_value": NaN}`, then `-Infinity`,
then `Infinity`, then `0`, each with valid `customer_id`/`pincode`/`courier_id`.
**Expected:** Every case returns a clean `422` with a structured
`{"detail": [...]}` body — never a bare-text `500`.

### B3 — Simultaneous curveball submissions don't clobber each other
**Situation:** Two ops reviewers (or one, double-checking) submit different
ad-hoc orders for investigation in the same instant during a live incident.
**Input:** Fire ~20 concurrent `POST /investigate` calls with distinct
customer/pincode/courier combinations and no explicit `order_id`.
**Expected:** Every generated `order_id` is unique; no escalation ticket's
evidence is silently overwritten by an unrelated investigation.
*(Already unit-verified — code-level test only; not re-run against the live
shared Supabase instance to avoid unnecessary load. See §4 for how to re-run
if ever needed.)*

### B4 — Sloppy data entry still resolves the right customer/courier
**Situation:** A call-center agent re-keys a customer ID by hand and fat-
fingers the case or leaves a trailing space; a courier ID gets copy-pasted
with stray whitespace.
**Input:** `customer_id: "cust-001"`, then `" CUST-001 "`; `courier_id: "c01"`.
**Expected:** Resolves the real `CUST-001`/`C01` — full evidence populated,
no "customer: data not available" issue.

### B5 — Pincode entered as a numeric string with a float suffix still matches
**Situation:** A spreadsheet export or a jury member reading a pincode off a
number field states it as "560001.0".
**Input:** `pincode: "560001.0"` via AdHocOrderForm.
**Expected:** Pincode evidence card populates with real rate/trend data
(client-side validation must not block this before it even reaches the API).

### B6 — Order-history depth changes confidence
**Situation:** Two RELEASE-shaped customers, one with a deep track record,
one nearly new — an ops reviewer expects the deeper history to read as more
trustworthy, not identical.
**Input:** `ORD-10000` (`CUST-001`, deep history) vs. an ad-hoc order for a
customer with `previous_orders` near zero.
**Expected:** Confidence values are meaningfully different, not flat.

### B7 — HIGH-risk explanation matches the actual cause
**Situation:** A reviewer reads the reason text to decide what to check
first — it must name the right dimension, not a generic canned line.
**Input:** `ORD-10003` (lane itself severely deteriorating) vs. a
constructed case where HIGH is reached purely via accumulated
supporting-evidence count on an otherwise-stable lane.
**Expected:** Reason text names the lane specifically only when the lane
itself is the cause; otherwise it says so, and correctly attributes
counter-evidence to documented disruption vs. customer-side reassurance.

### B8 — Malformed ticket id is a client error, not a fake outage
**Situation:** A malformed link, a copy-paste error, or a probing/adversarial
request hits the resolve endpoint with a control character embedded in the id.
**Input:** `POST /tickets/TCK-doesnotexist%00/resolve`.
**Expected:** `422` ("invalid ticket_id"), not `503` ("Ticketing store
unavailable") — an ops reviewer must not think the database is down when
it's actually their own malformed request.

### B9 — Per-order verification override actually changes the call
**Situation:** A reviewer personally re-verifies (or flags as unverified) an
address for this one specific order, overriding what's on file for the
customer generally — e.g. the customer moved and this delivery is to a new,
not-yet-verified address.
**Input:** Ad-hoc order for `CUST-005` (on-file `address_verified=True`)
with the form's "Address verified?" set explicitly to **No**.
**Expected:** Counter-evidence panel does *not* list "Address is verified"
for this submission — proving the override, not the stored profile, drove
the evidence.

### B10 — A frantic double-click submits once
**Situation:** The UI feels slow during a live demo/incident and a reviewer
clicks "Investigate" twice in rapid succession.
**Input:** Two `.click()` dispatches on the submit button with zero ticks
between them (a real user's fastest possible double-click, not two separate
`dblclick` events).
**Expected:** Exactly one `POST /investigate` fires.

---

## C. Business patterns not yet modeled (documented gaps, not bugs)

Worth knowing about explicitly rather than discovering live on stage.

### C1 — First-time high-value COD order
**Situation:** A brand-new customer places an unusually large COD order —
the single most common real-world COD fraud pattern (a fraudster's first
and only order is often disproportionately large).
**Current behavior:** `order_value` has zero effect on risk scoring by
design — the engine scopes itself to customer/pincode/courier/lane signals
only (see `README.md`). `NEW_EVERYTHING` (A5) already escalates on thin data
regardless of value, so the *outcome* is defensible, but the *reason* never
mentions order value specifically.
**Note:** `data_generator.py`'s own synthetic ground-truth formula *does*
treat order value as risk-relevant when generating training/eval data — a
real inconsistency between what the data reflects and what the engine
consumes. Flagged as a product enhancement idea, not a defect.

### C2 — Cross-lane fraud ring
**Situation:** One customer orders across many different pincode/courier
combinations in a short window, each individually unremarkable, but the
pattern across orders is the signal.
**Current behavior:** Not modeled — each investigation is evaluated
independently; there is no cross-order/cross-customer velocity check.

### C3 — Seller-driven RTO pattern
**Situation:** A specific seller's listings (regardless of customer/pincode/
courier) run a persistently higher RTO rate — a merchandising/QC problem,
not a delivery-risk one.
**Current behavior:** `seller_id` is captured and displayed but not
consulted by any signal or the decision engine.

---

## D. Resilience / failure-injection scenarios

### D1 — LLM narrative unavailable
**Situation:** OpenAI is down, rate-limited, or `OPENAI_API_KEY` is unset —
common on a demo network.
**Input:** Unset/invalid `OPENAI_API_KEY`, run any investigation.
**Expected:** `narrative_source: "template_fallback"`; `decision`/
`risk_level`/`confidence` are byte-for-byte identical to the LLM-narrated
run — the operational call never depends on the LLM being reachable.

### D2 — Escalation-ticket store unavailable
**Situation:** Supabase/Postgres is briefly unreachable while an ESCALATE/
HOLD decision is being made.
**Expected:** The investigation still returns a complete report with
`ticket_id: null` (logged, not raised) — a DB hiccup must never take down an
otherwise-successful investigation.

### D3 — Ticket reopened by a fresh escalation
**Situation:** An order was escalated, a human resolved it, and a later
re-investigation of the *same* order (or a resubmitted ad-hoc order sharing
an id) again produces a non-RELEASE decision.
**Expected:** The ticket reopens (`status` back to `OPEN`, `resolved_by`/
`resolution_note`/`resolved_at` cleared) rather than staying invisibly
RESOLVED while carrying a live decision a human hasn't actually seen.

---

## E. Input-format robustness (consolidated)

Beyond B4/B5 specifically, the ad-hoc endpoint should treat these as
equivalent, not fragile:

| Field | Equivalent forms that must all resolve the same |
|---|---|
| `pincode` | `560001` (int) · `"560001"` (str) · `"560001.0"` (float-suffixed str) · `" 560001 "` (padded) |
| `customer_id` / `courier_id` | `"CUST-001"` · `"cust-001"` · `" CUST-001 "` |
| `order_value` | any finite number `> 0`; `0`, negative, `NaN`, `±Infinity` all clean-422 |
| `address_verified` / `is_first_order` | omitted (defer to stored profile) vs. explicit `true`/`false` (override) |

---

## Using this catalog

- **Manual/demo rehearsal:** work top to bottom through section A, then spot-
  check a few from B/C/D depending on what the audience asks about.
- **Automated regression:** `COD-RTO-frontend/tests/e2e/scenarios.spec.js`
  encodes sections A/B/E as data-driven Playwright cases against a live
  `localhost:8000` + `localhost:5173`. C is documentation-only (nothing to
  assert against). D3/B3 require multi-request orchestration beyond a single
  browser session and are covered by direct API calls within the same spec
  file rather than UI interaction.
