# Robustness / Stress-Test Harness

The 10 named scenarios prove the decision engine handles the situations we
deliberately built. They don't prove it behaves sensibly on combinations we
didn't think to construct — which is exactly what the jury's live Reality
Test is designed to expose. This is a validation pass, not new pipeline
logic.

## What it does

Runs `decide()` against a wider, partly-random battery of orders and
checks a fixed list of invariants that should hold regardless of the
specific combination — not "does it match an expected label" (that's what
`decision_engine.py`'s existing `EXPECTED` table already checks for the
10 named scenarios), but "does it ever do something nonsensical."

## Invariants to check

1. `confidence` is always in `[0, 1]`
2. Every `ESCALATE` decision has a non-empty `uncertainty_flags` list
   (an escalation with no stated reason is a bug, not a valid escalation)
3. A lane with `trend == "SEVERE_DETERIORATION"` never results in `RELEASE`
4. An unknown customer, pincode, or courier id always results in `ESCALATE`
   (never `RELEASE`, never `HOLD_FOR_VERIFICATION`)
5. `courier_pincode.data_quality == "MISSING"` always results in `ESCALATE`
6. `supporting_count >= 2 and counter_count >= 2` always results in
   `HOLD_FOR_VERIFICATION`, never `RELEASE` or `ESCALATE` (matches the
   explicit branch in `decide()` — this test exists to catch a future
   refactor breaking that branch, not to discover new behavior)
7. Confidence for `RELEASE` is always higher than confidence for
   `ESCALATE` on the same order shape (sanity check on the fixed
   confidence table in `decide()`)

## Test population

- All 500 generated orders (`orders.csv`) run through `decide()` —
  cheap, exercises the full realistic distribution, not just the 10
  named scenarios
- ~30 synthetic edge cases built directly (not via `data_generator.py`):
  boundary values sitting exactly at the `decide()` thresholds (e.g.
  `rto_rate_7d` exactly `0.20`, `supporting_count` exactly `2`), and a
  handful of fully unknown `(customer_id, pincode, courier_id)` triples
  that don't exist anywhere in the dataset (using `get_adhoc_investigation`
  from task 03)

## Where it lives

New file: `src/stress_test.py`, structured like `decision_engine.py`'s
existing `main()` scenario table — a loop that runs each invariant check
against every generated investigation and prints a pass/fail summary,
not a full pytest suite (matches the project's existing style of
runnable `main()` scripts rather than a test framework).

## Acceptance check

Zero invariant violations across all 500 real orders + the ~30 synthetic
edge cases. Any violation found here before the event is a real bug to
fix in `decision_engine.py`, not a test to loosen.
