"""
Quessathon 2026 - Retail Challenge 01: Find the Signal

Robustness / stress-test harness for the deterministic decision engine.

See docs/tasks/04-robustness-testing.md.

This is a *validation* pass, not new pipeline logic: the 10 named
scenarios in decision_engine.EXPECTED prove the engine handles the
situations we deliberately built. This module instead runs decide()
against a much wider, partly-random battery of orders and checks a
fixed list of invariants that should hold regardless of the specific
combination of evidence - not "does it match an expected label", but
"does it ever do something nonsensical".

Test population:
    1. All 500 generated orders (orders.csv) via get_order_investigation.
    2. ~20 synthetic boundary-value investigations, built directly (not
       via data_generator.py) by calling analytics.compare_signals() /
       analytics.check_data_quality() on hand-crafted raw signal dicts
       that sit exactly at decide()'s underlying thresholds - this
       reuses the real production evidence-reconciliation code, only
       the raw customer/pincode/courier/courier-pincode signal inputs
       are synthetic.
    3. ~8 fully-unknown (customer_id, pincode, courier_id) triples that
       do not exist anywhere in the dataset, run through
       get_adhoc_investigation() (task 03).

Invariants checked (see the task spec for the authoritative list):
    1. confidence is always in [0, 1]
    2. every ESCALATE has a non-empty uncertainty_flags list
    3. a lane with trend == SEVERE_DETERIORATION never results in RELEASE
    4. an unknown customer, pincode, or courier id always results in
       ESCALATE (never RELEASE, never HOLD_FOR_VERIFICATION)
    5. courier_pincode.data_quality == "MISSING" always results in ESCALATE
    6. supporting_count >= 2 and counter_count >= 2 always results in
       HOLD_FOR_VERIFICATION, never RELEASE or ESCALATE - except when
       data_quality["issues"] is non-empty, in which case invariant 5
       applies instead: decide() deliberately checks critical-data-
       missing (branch 1) before conflicting-evidence (branch 2), so
       ESCALATE is the correct outcome on that overlap, not a bug
    7. confidence for RELEASE is always higher than confidence for
       ESCALATE on the same order shape (sanity check on the fixed
       confidence table in decide())

Not a pytest suite - matches the project's existing style of runnable
main() scripts (see analytics.py / decision_engine.py) rather than a
test framework: a loop that runs each invariant check against every
generated investigation and prints a pass/fail summary.
"""

from __future__ import annotations

import copy
import sys
from typing import Any, Dict, List, Optional, Tuple

from analytics import (
    load_data,
    get_order_investigation,
    get_adhoc_investigation,
    compare_signals,
    check_data_quality,
)
from decision_engine import decide, DecisionResult


# ============================================================
# INVARIANT CHECKS
#
# Each takes (investigation, decision) and returns a violation
# message, or None if the invariant holds for this case.
# ============================================================

def inv_confidence_in_range(investigation, decision: DecisionResult) -> Optional[str]:
    if not (0.0 <= decision.confidence <= 1.0):
        return f"confidence {decision.confidence!r} is not in [0, 1]"
    return None


def inv_escalate_has_flags(investigation, decision: DecisionResult) -> Optional[str]:
    if decision.decision == "ESCALATE" and not decision.uncertainty_flags:
        return "ESCALATE decision has an empty uncertainty_flags list"
    return None


def inv_severe_never_release(investigation, decision: DecisionResult) -> Optional[str]:
    cp = investigation.get("courier_pincode", {}) or {}
    if cp.get("trend") == "SEVERE_DETERIORATION" and decision.decision == "RELEASE":
        return "courier_pincode.trend == SEVERE_DETERIORATION but decision == RELEASE"
    return None


def inv_unknown_dimension_escalates(investigation, decision: DecisionResult) -> Optional[str]:
    customer = investigation.get("customer", {}) or {}
    pincode = investigation.get("pincode", {}) or {}
    courier = investigation.get("courier", {}) or {}

    unknown_dims = [
        name
        for name, sig in (("customer", customer), ("pincode", pincode), ("courier", courier))
        if not sig.get("found", False)
    ]

    if unknown_dims and decision.decision != "ESCALATE":
        return (
            f"unknown dimension(s) {unknown_dims} present but "
            f"decision == {decision.decision} (expected ESCALATE)"
        )
    return None


def inv_missing_courier_pincode_escalates(investigation, decision: DecisionResult) -> Optional[str]:
    cp = investigation.get("courier_pincode", {}) or {}
    if cp.get("data_quality") == "MISSING" and decision.decision != "ESCALATE":
        return (
            f"courier_pincode.data_quality == MISSING but "
            f"decision == {decision.decision} (expected ESCALATE)"
        )
    return None


def inv_conflicting_evidence_holds(investigation, decision: DecisionResult) -> Optional[str]:
    # decide()'s branch order is intentional (see its own inline
    # comments): branch 1 (critical data missing -> ESCALATE) is
    # checked *before* branch 2 (conflicting evidence -> HOLD), so a
    # case with data_quality["issues"] takes the ESCALATE path even
    # if supporting/counter evidence also happens to conflict there.
    # Invariant 5 (MISSING critical data -> ESCALATE) therefore wins
    # over invariant 6 on that overlap - mirror that precedence here
    # rather than treating it as a violation of this invariant.
    quality = investigation.get("data_quality", {}) or {}
    if quality.get("issues"):
        return None

    comparison = investigation.get("signal_comparison", {}) or {}
    supporting = comparison.get("supporting_count", 0)
    counter = comparison.get("counter_count", 0)

    if supporting >= 2 and counter >= 2 and decision.decision != "HOLD_FOR_VERIFICATION":
        return (
            f"supporting_count={supporting} and counter_count={counter} "
            f"(both >= 2) but decision == {decision.decision} "
            f"(expected HOLD_FOR_VERIFICATION)"
        )
    return None


# Invariants 1-6 are checked per-investigation. Invariant 7 (RELEASE
# confidence always higher than ESCALATE confidence) is a global
# sanity check on the fixed confidence table, evaluated once across
# the whole run, not per-case - see check_invariant_7_confidence_ordering().
PER_CASE_INVARIANTS = [
    ("1_confidence_range", inv_confidence_in_range),
    ("2_escalate_has_flags", inv_escalate_has_flags),
    ("3_severe_never_release", inv_severe_never_release),
    ("4_unknown_dimension_escalates", inv_unknown_dimension_escalates),
    ("5_missing_cp_data_escalates", inv_missing_courier_pincode_escalates),
    ("6_conflicting_evidence_holds", inv_conflicting_evidence_holds),
]


def run_invariants(investigation, decision: DecisionResult) -> List[Tuple[str, str]]:
    """Run all per-case invariants; return a list of (name, message) violations."""
    violations = []
    for name, fn in PER_CASE_INVARIANTS:
        msg = fn(investigation, decision)
        if msg:
            violations.append((name, msg))
    return violations


def check_invariant_7_confidence_ordering(
    confidences_by_decision: Dict[str, List[float]]
) -> Optional[str]:
    releases = confidences_by_decision.get("RELEASE", [])
    escalates = confidences_by_decision.get("ESCALATE", [])

    if not releases or not escalates:
        return None

    min_release = min(releases)
    max_escalate = max(escalates)

    if not (min_release > max_escalate):
        return (
            f"lowest RELEASE confidence ({min_release}) is not higher than "
            f"highest ESCALATE confidence ({max_escalate})"
        )
    return None


# ============================================================
# SYNTHETIC BOUNDARY-VALUE INVESTIGATIONS
#
# Built directly (not via data_generator.py): hand-crafted raw
# customer/pincode/courier/courier-pincode signal dicts, run through
# the real analytics.compare_signals() / analytics.check_data_quality()
# so the actual production thresholds are exercised, then assembled
# into an investigation dict shaped exactly like
# analytics.build_investigation()'s output and handed to the real
# decision_engine.decide().
# ============================================================

def sig(base: Dict[str, Any], **overrides) -> Dict[str, Any]:
    d = dict(base)
    d.update(overrides)
    return d


# "Neutral" baseline signals that contribute zero supporting/counter
# evidence and no data-quality issues/warnings, so a single boundary
# case can vary exactly one field without other conditions firing
# incidentally.
CUST_NEUTRAL = dict(
    found=True,
    previous_orders=4,      # >= 3 -> avoids the "limited order history" flag
    previous_rto=0,         # < 2  -> avoids the previous-RTO supporting rule
    phone_verified=False,
    address_verified=False,
    customer_type="NEW",
)  # previous_orders=4 is also < 5, so the "established history" counter
   # rule (previous_orders >= 5 and previous_rto <= 1) does not fire either.

PINCODE_NEUTRAL = dict(
    found=True,
    rto_rate_7d=0.05,
    rto_rate_30d=0.05,
    trend="STABLE",
    sample_size=50,
    data_quality="OK",
)

COURIER_NEUTRAL = dict(
    found=True,
    rto_rate_7d=0.05,
    rto_rate_30d=0.05,
    trend="STABLE",
    sample_size=50,
    data_quality="OK",
)

CP_NEUTRAL = dict(
    found=True,
    rto_rate_7d=0.05,
    rto_rate_30d=0.05,
    trend="STABLE",
    sample_size=50,
    data_quality="OK",
)

# A real (pincode, courier) lane that DOES have historical precedent in
# historical_cases.csv (CASE-1801), used for the handful of boundary
# cases that need evidence_sufficient == True to reach decide()'s
# risk-based branches instead of being pre-empted by the "no historical
# precedent" uncertainty flag.
PRECEDENT_PINCODE = 560076
PRECEDENT_COURIER = "C04"

# A synthetic (pincode, courier) pair guaranteed to have zero historical
# precedent, used for isolated compare_signals()/risk-level boundary
# checks where reaching a specific decide() branch doesn't matter -
# only that no invariant is violated.
NO_PRECEDENT_PINCODE = "999001"
NO_PRECEDENT_COURIER = "ZZ-BOUNDARY"


def build_boundary_investigation(
    case_id: str,
    customer_signal: Dict[str, Any],
    pincode_signal: Dict[str, Any],
    courier_signal: Dict[str, Any],
    courier_pincode_signal: Dict[str, Any],
    pincode=NO_PRECEDENT_PINCODE,
    courier_id=NO_PRECEDENT_COURIER,
) -> Dict[str, Any]:

    data_quality = check_data_quality(
        customer_signal, pincode_signal, courier_signal, courier_pincode_signal
    )
    signal_comparison = compare_signals(
        customer_signal, pincode_signal, courier_signal, courier_pincode_signal
    )

    return {
        "found": True,
        "order": {
            "order_id": case_id,
            "customer_id": "BOUNDARY-CUST",
            "pincode": pincode,
            "courier_id": courier_id,
            "order_value": 1000,
        },
        "customer": customer_signal,
        "pincode": pincode_signal,
        "courier": courier_signal,
        "courier_pincode": courier_pincode_signal,
        "delivery": {"attempt_count": 0, "attempts": [], "failure_reasons": []},
        "context_events": [],
        "data_quality": data_quality,
        "signal_comparison": signal_comparison,
    }


def build_boundary_cases() -> List[Tuple[str, Dict[str, Any]]]:
    cases = []

    def add(case_id, customer=CUST_NEUTRAL, pincode=PINCODE_NEUTRAL,
            courier=COURIER_NEUTRAL, cp=CP_NEUTRAL,
            lane_pincode=NO_PRECEDENT_PINCODE, lane_courier=NO_PRECEDENT_COURIER):
        cases.append((
            case_id,
            build_boundary_investigation(
                case_id, customer, pincode, courier, cp,
                pincode=lane_pincode, courier_id=lane_courier,
            ),
        ))

    # --- pincode rto_rate_7d >= 0.20 threshold ---------------------
    add("BND-pincode-rto7d-at-0.20",
        pincode=sig(PINCODE_NEUTRAL, rto_rate_7d=0.20, rto_rate_30d=0.20))
    add("BND-pincode-rto7d-just-below-0.20",
        pincode=sig(PINCODE_NEUTRAL, rto_rate_7d=0.1999, rto_rate_30d=0.1999))

    # --- pincode deterioration ratio (rto7 > rto30*1.5, rto7>=0.10) --
    add("BND-pincode-deterioration-ratio-at-0.10",
        pincode=sig(PINCODE_NEUTRAL, rto_rate_7d=0.10, rto_rate_30d=0.0666))
    add("BND-pincode-deterioration-ratio-just-below",
        pincode=sig(PINCODE_NEUTRAL, rto_rate_7d=0.0999, rto_rate_30d=0.0666))

    # --- pincode TEMPORARY_DISRUPTION -> counter evidence -----------
    add("BND-pincode-temporary-disruption",
        pincode=sig(PINCODE_NEUTRAL, trend="TEMPORARY_DISRUPTION"))

    # --- courier rto_rate_7d >= 0.15 threshold ----------------------
    add("BND-courier-rto7d-at-0.15",
        courier=sig(COURIER_NEUTRAL, rto_rate_7d=0.15, rto_rate_30d=0.15))
    add("BND-courier-rto7d-just-below-0.15",
        courier=sig(COURIER_NEUTRAL, rto_rate_7d=0.1499, rto_rate_30d=0.1499))

    # --- courier deterioration ratio ---------------------------------
    add("BND-courier-deterioration-ratio-at-0.10",
        courier=sig(COURIER_NEUTRAL, rto_rate_7d=0.10, rto_rate_30d=0.0666))
    add("BND-courier-deterioration-ratio-just-below",
        courier=sig(COURIER_NEUTRAL, rto_rate_7d=0.0999, rto_rate_30d=0.0666))

    # --- courier_pincode rto_rate_7d >= 0.20 threshold --------------
    add("BND-cp-rto7d-at-0.20",
        cp=sig(CP_NEUTRAL, rto_rate_7d=0.20, rto_rate_30d=0.20))
    add("BND-cp-rto7d-just-below-0.20",
        cp=sig(CP_NEUTRAL, rto_rate_7d=0.1999, rto_rate_30d=0.1999))

    # --- courier_pincode deterioration ratio (threshold 0.12) -------
    add("BND-cp-deterioration-ratio-at-0.12",
        cp=sig(CP_NEUTRAL, rto_rate_7d=0.12, rto_rate_30d=0.0799))
    add("BND-cp-deterioration-ratio-just-below",
        cp=sig(CP_NEUTRAL, rto_rate_7d=0.1199, rto_rate_30d=0.0799))

    # --- courier_pincode SEVERE_DETERIORATION trend -----------------
    #     (key case for invariant 3 - must never RELEASE, and hits
    #     decide()'s risk==HIGH branch ahead of the "no precedent" flag)
    add("BND-cp-severe-deterioration-trend",
        cp=sig(CP_NEUTRAL, trend="SEVERE_DETERIORATION"))

    # --- customer previous_rto >= 2 threshold -----------------------
    add("BND-customer-previous-rto-at-2",
        customer=sig(CUST_NEUTRAL, previous_rto=2))
    add("BND-customer-previous-rto-just-below-2",
        customer=sig(CUST_NEUTRAL, previous_rto=1))

    # --- customer "established history" counter (previous_orders>=5
    #     and previous_rto<=1) ------------------------------------
    add("BND-customer-established-history-at-5-orders",
        customer=sig(CUST_NEUTRAL, previous_orders=5, previous_rto=1))
    add("BND-customer-established-history-just-below-5-orders",
        customer=sig(CUST_NEUTRAL, previous_orders=4, previous_rto=1))

    # --- customer phone/address verified counters -------------------
    add("BND-customer-phone-verified-counter",
        customer=sig(CUST_NEUTRAL, phone_verified=True))
    add("BND-customer-address-verified-counter",
        customer=sig(CUST_NEUTRAL, address_verified=True))

    # --- risk_level HIGH via supporting_count == 3 (not via severe
    #     trend) - must never RELEASE ---------------------------------
    add("BND-risk-high-via-supporting-count-3",
        customer=sig(CUST_NEUTRAL, previous_rto=5),
        pincode=sig(PINCODE_NEUTRAL, rto_rate_7d=0.25, rto_rate_30d=0.25),
        courier=sig(COURIER_NEUTRAL, rto_rate_7d=0.20, rto_rate_30d=0.20))

    # --- risk_level MEDIUM via supporting_count == 1 ------------------
    add("BND-risk-medium-via-supporting-count-1",
        courier=sig(COURIER_NEUTRAL, rto_rate_7d=0.15, rto_rate_30d=0.15))

    # --- supporting_count == 2 and counter_count == 2 exactly, no
    #     data-quality issues - the explicit branch invariant 6 targets
    #     directly at its boundary ---------------------------------
    add("BND-supporting2-counter2-exact-clean-quality",
        customer=sig(CUST_NEUTRAL, previous_rto=3, phone_verified=True, address_verified=True),
        pincode=sig(PINCODE_NEUTRAL, rto_rate_7d=0.25, rto_rate_30d=0.25))
    # (supporting: customer previous_rto>=2, pincode rto_7>=0.20 => 2
    #  counter: phone_verified, address_verified => 2)

    # --- THE adversarial case: supporting>=2 and counter>=2 (as above)
    #     but courier_pincode.data_quality == "MISSING", which makes
    #     check_data_quality() report a critical issue - directly pits
    #     invariant 5 (MISSING -> always ESCALATE) against invariant 6
    #     (supporting>=2 & counter>=2 -> always HOLD) in one case -----
    add("BND-CONFLICT-supporting2-counter2-with-missing-cp-quality",
        customer=sig(CUST_NEUTRAL, previous_rto=3, phone_verified=True, address_verified=True),
        pincode=sig(PINCODE_NEUTRAL, rto_rate_7d=0.25, rto_rate_30d=0.25),
        cp=sig(CP_NEUTRAL, data_quality="MISSING"))

    # --- courier_pincode.data_quality == MISSING alone (no conflict) -
    add("BND-cp-data-quality-missing-alone",
        cp=sig(CP_NEUTRAL, data_quality="MISSING"))

    # --- unknown dimensions, isolated one at a time -------------------
    add("BND-unknown-customer-only",
        customer=sig(CUST_NEUTRAL, found=False))
    add("BND-unknown-pincode-only",
        pincode=sig(PINCODE_NEUTRAL, found=False))
    add("BND-unknown-courier-only",
        courier=sig(COURIER_NEUTRAL, found=False))

    # --- clean, low-risk order *with* historical precedent -> should
    #     reach decide()'s RELEASE branch -----------------------------
    add("BND-clean-release-with-precedent",
        lane_pincode=PRECEDENT_PINCODE, lane_courier=PRECEDENT_COURIER)

    # --- moderate risk *with* historical precedent -> should reach
    #     decide()'s MEDIUM-risk HOLD branch ---------------------------
    add("BND-medium-risk-with-precedent",
        courier=sig(COURIER_NEUTRAL, rto_rate_7d=0.15, rto_rate_30d=0.15),
        lane_pincode=PRECEDENT_PINCODE, lane_courier=PRECEDENT_COURIER)

    return cases


def build_unknown_triple_cases() -> List[Dict[str, Any]]:
    """
    A handful of fully-unknown (customer_id, pincode, courier_id)
    triples that don't exist anywhere in the dataset, plus every
    single/double-unknown-dimension combination, run through
    get_adhoc_investigation() from task 03.
    """
    return [
        dict(case_id="ADH-all-unknown-1", customer_id="CUST-UNKNOWN-001",
             pincode="900001", courier_id="CX-UNKNOWN-01"),
        dict(case_id="ADH-unknown-customer-only", customer_id="CUST-UNKNOWN-002",
             pincode=560001, courier_id="C01"),
        dict(case_id="ADH-unknown-pincode-only", customer_id="CUST-001",
             pincode="900002", courier_id="C01"),
        dict(case_id="ADH-unknown-courier-only", customer_id="CUST-001",
             pincode=560001, courier_id="CX-UNKNOWN-02"),
        dict(case_id="ADH-unknown-customer-and-pincode", customer_id="CUST-UNKNOWN-003",
             pincode="900003", courier_id="C02"),
        dict(case_id="ADH-unknown-customer-and-courier", customer_id="CUST-UNKNOWN-004",
             pincode=560002, courier_id="CX-UNKNOWN-03"),
        dict(case_id="ADH-unknown-pincode-and-courier", customer_id="CUST-002",
             pincode="900004", courier_id="CX-UNKNOWN-04"),
        dict(case_id="ADH-all-unknown-2", customer_id="CUST-UNKNOWN-005",
             pincode="900005", courier_id="CX-UNKNOWN-05"),
    ]


# ============================================================
# RUNNER
# ============================================================

def run_population(data, label, cases_iter):
    """
    cases_iter yields (case_id, investigation) pairs. Runs decide() on
    each, checks all per-case invariants, and returns
    (results, confidences_by_decision, violations) where results is a
    list of (case_id, decision) for reporting.
    """
    violations = []
    confidences_by_decision: Dict[str, List[float]] = {}
    checked = 0

    for case_id, investigation in cases_iter:
        decision = decide(data, investigation)
        checked += 1

        confidences_by_decision.setdefault(decision.decision, []).append(decision.confidence)

        for name, msg in run_invariants(investigation, decision):
            violations.append({
                "population": label,
                "case_id": case_id,
                "invariant": name,
                "message": msg,
                "decision": decision.decision,
                "confidence": decision.confidence,
            })

    return checked, confidences_by_decision, violations


def merge_confidence_maps(*maps: Dict[str, List[float]]) -> Dict[str, List[float]]:
    merged: Dict[str, List[float]] = {}
    for m in maps:
        for k, v in m.items():
            merged.setdefault(k, []).extend(v)
    return merged


def main():
    print("=" * 70)
    print("QUESSATHON - FIND THE SIGNAL")
    print("Robustness / Stress-Test Harness")
    print("=" * 70)

    data = load_data()
    orders = data["orders"]

    # ------------------------------------------------------------
    # Population 1: all 500 real generated orders
    # ------------------------------------------------------------
    def real_orders_iter():
        for order_id in orders["order_id"]:
            investigation = get_order_investigation(data, order_id)
            yield order_id, investigation

    print(f"\n[1/3] Running decide() against all {len(orders)} real orders ...")
    n_real, conf_real, viol_real = run_population(data, "real_orders", real_orders_iter())
    print(f"      checked: {n_real}   violations: {len(viol_real)}")

    # ------------------------------------------------------------
    # Population 2: synthetic boundary-value investigations
    # ------------------------------------------------------------
    boundary_cases = build_boundary_cases()
    print(f"\n[2/3] Running decide() against {len(boundary_cases)} synthetic "
          f"boundary-value cases ...")
    n_bnd, conf_bnd, viol_bnd = run_population(data, "boundary", iter(boundary_cases))
    print(f"      checked: {n_bnd}   violations: {len(viol_bnd)}")

    # ------------------------------------------------------------
    # Population 3: fully-unknown (customer, pincode, courier) triples
    # ------------------------------------------------------------
    unknown_specs = build_unknown_triple_cases()

    def unknown_iter():
        for spec in unknown_specs:
            investigation = get_adhoc_investigation(
                data,
                customer_id=spec["customer_id"],
                pincode=spec["pincode"],
                courier_id=spec["courier_id"],
                order_value=1500,
                order_id=spec["case_id"],
            )
            yield spec["case_id"], investigation

    print(f"\n[3/3] Running decide() against {len(unknown_specs)} fully-unknown "
          f"(customer, pincode, courier) triples via get_adhoc_investigation() ...")
    n_adh, conf_adh, viol_adh = run_population(data, "unknown_triples", unknown_iter())
    print(f"      checked: {n_adh}   violations: {len(viol_adh)}")

    total_checked = n_real + n_bnd + n_adh
    all_violations = viol_real + viol_bnd + viol_adh

    # ------------------------------------------------------------
    # Invariant 7: global confidence-ordering sanity check
    # ------------------------------------------------------------
    all_confidences = merge_confidence_maps(conf_real, conf_bnd, conf_adh)
    inv7_msg = check_invariant_7_confidence_ordering(all_confidences)
    if inv7_msg:
        all_violations.append({
            "population": "ALL",
            "case_id": "(global)",
            "invariant": "7_release_conf_gt_escalate_conf",
            "message": inv7_msg,
            "decision": "-",
            "confidence": "-",
        })

    # ------------------------------------------------------------
    # Report
    # ------------------------------------------------------------
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Total investigations checked : {total_checked}")
    print(f"  - real orders (orders.csv) : {n_real}")
    print(f"  - synthetic boundary cases : {n_bnd}")
    print(f"  - unknown-triple cases     : {n_adh}")

    decision_counts: Dict[str, int] = {}
    for k, v in all_confidences.items():
        decision_counts[k] = len(v)
    print(f"\nDecision distribution across all populations: {decision_counts}")

    if all_confidences.get("RELEASE") and all_confidences.get("ESCALATE"):
        print(
            f"RELEASE confidence range   : "
            f"[{min(all_confidences['RELEASE'])}, {max(all_confidences['RELEASE'])}]"
        )
        print(
            f"ESCALATE confidence range  : "
            f"[{min(all_confidences['ESCALATE'])}, {max(all_confidences['ESCALATE'])}]"
        )

    print(f"\nTotal invariant violations : {len(all_violations)}")

    if all_violations:
        print("\n" + "-" * 100)
        print(f"{'POPULATION':16} {'CASE':45} {'INVARIANT':32} DETAIL")
        print("-" * 100)
        for v in all_violations:
            print(
                f"{v['population']:16} {v['case_id']:45} {v['invariant']:32} "
                f"{v['message']}"
            )
        print("-" * 100)

    print("\n" + "=" * 70)
    if all_violations:
        print(f"ACCEPTANCE CHECK: FAIL - {len(all_violations)} invariant "
              f"violation(s) found across {total_checked} investigations.")
    else:
        print(f"ACCEPTANCE CHECK: PASS - zero invariant violations across "
              f"{total_checked} investigations "
              f"({n_real} real orders + {n_bnd + n_adh} synthetic edge cases).")
    print("=" * 70)

    return 1 if all_violations else 0


if __name__ == "__main__":
    sys.exit(main())
