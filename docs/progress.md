# Progress — What's Been Done So Far

Status snapshot for Quessathon 2026, Retail Challenge 01 ("Find the Signal").
Companion to [`research.md`](research.md) (problem/architecture) and
[`tasks/`](tasks/) (per-feature specs) — this doc is the running record of
what's actually built, tested, and verified, updated as work lands.

## TL;DR

Both repos have real, working, tested code:

- **`COD-RTO-backend`** (this repo, `C:\Users\AdarshGadekar\qwerty`) — full
  investigation pipeline + FastAPI backend, all 5 planned features built,
  7 critical bugs found and fixed via independent review, 4 more issues
  (latency, dependency gaps, missing `.env` loading) found and fixed after
  that.
- **`COD-RTO-frontend`** (`C:\Users\AdarshGadekar\COD-RTO-frontend`) — full
  React + Vite app, all 10 components built, 5 critical race-condition
  bugs found and fixed, 4 accessibility/consistency issues fixed after that.
- **LLM narratives are live** — `OPENAI_API_KEY` is configured and verified
  working end-to-end (`narrative_source: "llm"`, real gpt-4.1-nano prose),
  with the deterministic template fallback still intact if it's ever
  unavailable.
- **Supabase hosts all data** — 8 tables mirroring `data/generated/*.csv`.
- **Not done, deliberately deferred**: EC2 hosting (needs AWS credentials
  not available in this environment — the plan is to run both apps locally
  for now); a real manual browser click-through of the frontend (no
  subagent in this environment has browser access — see Known Gaps).

## How this was built

Work was organized through nine written skill definitions in
`.claude/skills/` (`solution-architect`, `backend-core-engineer`,
`backend-platform-engineer`, `frontend-application-engineer`,
`frontend-ux-engineer`, `llm-ai-engineer`, `qa-engineer`, `devops-engineer`,
`technical-reviewer`), each with a defined role, working process, and
output format. Only the skills a given task actually needed were invoked
(never all nine at once). Each build ran the same collaboration pipeline:

```
solution-architect (locks execution order / catches file conflicts)
        -> implementing engineer(s)
        -> qa-engineer (independent, adversarial verification)
        -> technical-reviewer (Critical / Important / Suggestions)
        -> repair pass (only Critical findings get auto-fixed)
```

Every fix below was independently **re-verified with a real, executed
reproduction** (a live run, a real build, a direct query against the real
Supabase database) — not just re-read and assumed fixed.

## Backend — build and repair history

**Built** (`docs/tasks/01`–`05`):

| Feature | File(s) |
|---|---|
| Operational insight (courier×pincode lane findings) | `src/operational_insights.py` |
| Escalation ticket / handoff record | `src/ticketing.py` + `escalation_tickets` table |
| Ad-hoc order input (the jury-curveball path) | `src/analytics.py`, `src/investigation_agent.py` |
| Robustness / stress-test harness (538 investigations, 7 invariants) | `src/stress_test.py` |
| FastAPI backend (6 endpoints) | `src/api.py` |

**7 Critical issues found by review, all fixed and re-verified live:**
1. `investigate_order()` had no failure guard around ticket creation (unlike its sibling `investigate_adhoc()`) — a transient DB hiccup would have discarded an already-correct decision as an opaque 500.
2. `get_operational_insights()` crashed the entire `/insights` endpoint on one malformed lane row.
3. A caller could pair fabricated ad-hoc data with a **real** `order_id` and silently overwrite that order's genuine ticket — fixed by unconditionally namespacing ad-hoc order IDs under `ADHOC-`.
4. Re-investigating an order after its ticket was resolved never reopened the ticket — fixed so resolution state resets on a fresh escalation.
5. `stress_test.py` shipped in a permanently-failing state (two invariants conflicted on one adversarial case) — fixed to respect `decide()`'s documented precedence.
6. Ad-hoc escalation tickets failed **100% of the time** (FK constraint required a real `orders` row) — fixed by dropping the FK; ad-hoc orders are first-class input, not an anomaly.
7. `investigate_order()`/`investigate_adhoc()` were ~90% duplicated code that had already silently diverged — collapsed into one shared `_run_pipeline()` helper.

**4 more issues found afterward:**
- Cold-start latency: first live investigation paid a ~13s embedding-model load cost — fixed by warming the model at FastAPI startup (`case_memory.warm_embedding_model()`); first call now ~235ms.
- The ad-hoc endpoint rejected a numeric pincode (`560068` vs `"560068"`) — the exact input shape a live curveball is likely to send — fixed with a pydantic coercion validator.
- `requirements.txt` listed `pydantic`, `chromadb`, `sentence-transformers`, and `openai` but none were actually installed in this venv (surfaced repeatedly across different tasks) — fixed by running a full `pip install -r requirements.txt`; regression-tested afterward (`decision_engine.py`, `stress_test.py` both still clean).
- `investigation_agent.py` never called `load_dotenv()`, so `OPENAI_API_KEY` in `.env` was silently never loaded regardless of entry point — fixed; verified via a real OpenAI call and a full end-to-end investigation.

**End-to-end QA** (live server, real Supabase data): all three decision
branches (RELEASE/HOLD/ESCALATE) cross-checked against direct DB queries,
the ad-hoc collision defense, the ticket resolve→reopen lifecycle, and
negative/adversarial inputs — all passed, zero unhandled tracebacks.

## Frontend — build and repair history

**Built**: a Vite + React app (`src/`) with all 10 components from
`docs/tasks/06-react-frontend.md` — `OrderPicker`, `AdHocOrderForm`,
`EvidencePanel`, `EvidenceComparison`, `PrecedentPanel`, `NarrativePanel`,
`DecisionBanner`, `TimingFooter`, `InsightsStrip`, `TicketsView` — plus a
shared `src/api/client.js`, `src/lib/format.js`, and `src/types/index.js`.

**Notable architecture finding**: the original task doc described richer
evidence than the real backend contract actually returns (per-dimension
rates/trend/sample-size, and per-case lane/decision/outcome detail for
retrieved precedent). Rather than fabricate that data client-side, the
components were built against the **real** wire contract and the
deviation was documented directly in the code — `EvidencePanel` renders
the real `uncertainty_flags` two-tier critical/warning split instead of a
4-dimension grid that doesn't exist over the wire.

**5 Critical race-condition bugs found by review, all fixed and
re-verified with clean builds + standalone Node reproductions:**
1. Selecting an order, then quickly submitting an ad-hoc investigation before the first resolved, could let the slower request silently overwrite the faster one on screen — fixed with a request-id guard in `App.jsx`.
2. "Load more" pagination could get permanently stuck disabled if the scenario filter changed mid-fetch — fixed with separate request-id refs per fetch type in `OrderPicker.jsx`.
3. Resolving one ticket while a second ticket's resolve form was open could silently erase the second operator's typed note — fixed by scoping the reset to the ticket actually being resolved in `TicketsView.jsx`.
4. (Also caught by the UX-adherence lens) the same `App.jsx` race, plus: any failed re-fetch unconditionally nulled out a perfectly good displayed investigation — fixed to keep showing the last good result alongside the new error.

**4 more issues fixed directly afterward** (well-specified "Important"
findings, fixed without another full review round):
- FastAPI validation-error arrays rendered as raw JSON blobs to the user — now formatted as readable text.
- The "CRITICAL: " flag-parsing convention was reimplemented 3 different ways across 3 files (one didn't even strip the prefix) — consolidated into one shared `splitUncertaintyFlags()` helper in `lib/format.js`.
- Keyboard focus-visible styling existed on only 2 of 10 components — hoisted to one global rule in `index.css`.
- Ticket resolution didn't manage focus or announce completion for screen readers — now returns focus sensibly (to the Resolve button on cancel, to the panel heading after a successful resolve) and announces via `aria-live`.

Both fixed and unfixed states were confirmed with a real `npm run build`
(clean, 53 modules) and a live `vite` dev server smoke test after every
change.

## Known gaps (honest, not glossed over)

- **No real browser testing has happened.** Every frontend verification —
  by the workflow's QA pass and by me directly — was a clean production
  build, live backend contract checks, static code review, and
  standalone Node reproductions of the exact race-condition logic. **Nobody
  has actually clicked through the rendered UI in a browser.** This is the
  single most important thing to do before the event.
- **EC2 hosting is not done and is explicitly out of scope for me** — it
  needs real AWS credentials, which aren't available in this environment.
  Plan is to run both apps locally (`uvicorn` + `npm run dev`/`vite
  preview`) for the demo unless that changes.
- **Concurrency/multi-user testing** hasn't been done (single-operator
  demo scenario assumed throughout).
- Two low-severity, non-blocking items from the backend review were never
  circled back to: generic 500 responses still don't log the original
  exception server-side (harder to root-cause a real production failure
  after the fact), and `ensure_schema()` for the tickets table isn't
  called automatically at API startup (documented as a one-time manual
  step instead).

## Suggested next steps

1. **Manually open the frontend in a real browser** against the live
   backend and click through a few scenarios (a `RELEASE`, a `HOLD`, an
   ad-hoc curveball, resolving a ticket) — this is the one thing that
   still hasn't actually been *seen* working.
2. Rehearse the 3-minute demo flow end to end, including an unrehearsed
   ad-hoc order to simulate the jury's live curveball.
3. Decide on hosting: run locally from a laptop (lowest risk, no new
   moving parts) or revisit EC2 if AWS access becomes available.
4. If time allows: the two low-severity backend items above, and a
   deliberate pass at intentionally-adversarial jury-style curveballs
   beyond what's already in `stress_test.py`.
