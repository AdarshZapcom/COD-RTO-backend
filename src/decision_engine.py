"""
Quessathon 2026 - Retail Challenge 01: Find the Signal

Deterministic decision engine.

This module turns the evidence collected by analytics.py into an
operational call: RELEASE / HOLD_FOR_VERIFICATION / ESCALATE.

Design principle (see project notes):
    Deterministic code computes rates, trends and sample sizes.
    This engine reconciles that evidence into a decision using
    fixed, explainable rules.
    An LLM is NOT used here. This engine is the fast, cheap,
    always-available first pass every order goes through; a future
    LLM investigation/reconciliation layer sits on top of it for
    the genuinely ambiguous cases and must fall back to this
    engine's verdict if it fails or is itself low-confidence.
"""

from __future__ import annotations

import logging
from typing import List, Literal

import pandas as pd
from pydantic import BaseModel

from analytics import (
    load_data,
    get_order_investigation,
    is_true,
    normalize_id,
    normalize_pincode,
    humanize_label,
)

logger = logging.getLogger(__name__)

Decision = Literal["RELEASE", "HOLD_FOR_VERIFICATION", "ESCALATE"]
RiskLevel = Literal["LOW", "MEDIUM", "HIGH"]

# Named so the risk-level rule is defensible in one place rather than
# bare literals inline in compute_risk_level().
HIGH_RISK_SUPPORTING_COUNT = 3   # this many supporting-evidence items alone -> HIGH
MEDIUM_RISK_SUPPORTING_COUNT = 1

MIN_COURIER_PINCODE_SAMPLE = 5   # below this, the lane's own rate isn't trustworthy
MIN_CUSTOMER_ORDER_HISTORY = 3   # below this, the customer has no real track record


class DecisionResult(BaseModel):
    order_id: str
    decision: Decision
    risk_level: RiskLevel
    confidence: float
    # Short, concrete reasons behind `confidence` (sample sizes,
    # verification, flag counts) - see compute_confidence(). Lets a UI
    # show why the number is what it is, not just the number itself.
    confidence_factors: List[str]
    reason: str
    supporting_evidence: List[str]
    counter_evidence: List[str]
    uncertainty_flags: List[str]


# ============================================================
# HISTORICAL PRECEDENT
# ============================================================

def has_historical_precedent(data, pincode, courier_id) -> bool:
    """
    Has this exact courier x pincode lane ever been formally
    investigated before?

    This is deliberately an exact-combination match, not a fuzzy
    "seen this courier or this pincode somewhere" match - a lane
    with zero track record is a genuinely different situation from
    one with a thin-but-present history, and the two should not be
    conflated. Case-level retrieval (which past case is *relevant*
    to explain today's order) is the job of the future ChromaDB
    historical case memory; this is only a coarse "do we have any
    track record at all" gate.
    """

    hist = data["historical_cases"]

    matches = hist[
        (hist["pincode"].astype(str) == normalize_pincode(pincode))
        & (hist["courier"] == normalize_id(courier_id))
    ]

    return len(matches) > 0


# ============================================================
# RISK LEVEL
# ============================================================

def compute_risk_level(investigation) -> RiskLevel:

    cp = investigation["courier_pincode"]
    comparison = investigation["signal_comparison"]

    supporting = comparison["supporting_count"]

    severe = (
        cp.get("found")
        and cp.get("trend") == "SEVERE_DETERIORATION"
    )

    if severe or supporting >= HIGH_RISK_SUPPORTING_COUNT:
        return "HIGH"

    if supporting >= MEDIUM_RISK_SUPPORTING_COUNT:
        return "MEDIUM"

    return "LOW"


# ============================================================
# EVIDENCE SUFFICIENCY
# ============================================================

def compute_uncertainty_flags(data, investigation) -> List[str]:
    """
    Reasons we do not have enough of a track record to trust an
    automated call, independent of whether that call would lean
    RELEASE or HOLD. These are what should push a case to a human,
    not the presence of risk itself.

    Flags prefixed "INFO: " are surfaced and still count against
    confidence, but do NOT by themselves force ESCALATE - see
    blocking_flags() below. Right now the only such flag is "customer
    is new": a brand-new customer is common, not a rare gap, so it
    shouldn't alone route every first-time buyer to a human reviewer
    the same way a missing pincode/courier/lane does (see
    analytics.check_data_quality's matching special case).
    """

    flags: List[str] = []

    quality = investigation["data_quality"]
    flags.extend(
        f"CRITICAL: {issue}" for issue in quality["issues"]
    )

    cp = investigation["courier_pincode"]
    if (
        cp.get("found")
        and pd.notna(cp.get("sample_size"))
        and cp["sample_size"] < MIN_COURIER_PINCODE_SAMPLE
    ):
        n = int(cp["sample_size"])
        flags.append(
            f"Courier x pincode sample size is very small "
            f"(based on only {n} past order{'s' if n != 1 else ''})"
        )

    customer = investigation["customer"]
    if not customer.get("found"):
        flags.append(
            "INFO: customer is new (no order history) - weighed via "
            "pincode/courier/lane signals instead of blocking outright"
        )
    elif (
        pd.notna(customer.get("previous_orders"))
        and customer["previous_orders"] < MIN_CUSTOMER_ORDER_HISTORY
    ):
        flags.append(
            "Customer has very limited order history (fewer than 3 orders)"
        )

    order = investigation["order"]
    if not has_historical_precedent(data, order["pincode"], order["courier_id"]):
        flags.append(
            "No comparable historical cases found for this exact "
            "courier/pincode combination"
        )

    return flags


def blocking_flags(flags: List[str]) -> List[str]:
    """
    The subset of compute_uncertainty_flags() that should actually force
    ESCALATE - everything except "INFO: " flags, which are surfaced and
    still cost some confidence but are not, on their own, a reason to
    refuse a decision (currently: a brand-new customer alone).
    """
    return [f for f in flags if not f.startswith("INFO: ")]


def compute_confidence(
    investigation, decision: Decision, flags: List[str]
) -> "tuple[float, List[str]]":
    """
    Confidence in the call actually made, derived from the evidence
    behind it - not a fixed constant per decision branch. Bands per
    decision are kept non-overlapping (ESCALATE strictly below RELEASE)
    so a hedged call never reads as more confident than a clean,
    verified one - see stress_test.py invariant 7, which checks this
    globally across every investigation run.

    Returns (confidence, factors): factors is a short list of the
    concrete reasons behind the number (sample sizes, verification,
    flag counts), so a UI showing "75%" can also show *why* 75% rather
    than asking a viewer to trust a bare percentage - see
    InvestigationReport.confidence_factors.
    """

    quality = investigation["data_quality"]
    warnings = quality.get("warnings", [])
    warning_penalty = min(0.15, 0.03 * len(warnings))

    cp = investigation["courier_pincode"]
    customer = investigation["customer"]

    # Courier x pincode/courier/pincode signals count their track record
    # as "sample_size"; the customer signal counts it as "previous_orders"
    # instead (see analytics.get_customer_signal) - sample_score/
    # sample_label take the count explicitly rather than assuming a
    # single shared key name, so customer history is never silently
    # scored as zero regardless of how established the customer is.
    def sample_score(found: bool, count) -> float:
        if not found or count is None or pd.isna(count):
            return 0.0
        if count >= 50:
            return 1.0
        if count >= 20:
            return 0.7
        if count >= 5:
            return 0.35
        return 0.1

    def sample_label(name: str, found: bool, count) -> str:
        if not found or count is None or pd.isna(count):
            return f"{name}: no track record"
        count = int(count)
        if count >= 50:
            return f"{name}: strong sample (n={count})"
        if count >= 20:
            return f"{name}: moderate sample (n={count})"
        if count >= 5:
            return f"{name}: thin sample (n={count})"
        return f"{name}: very thin sample (n={count})"

    cp_found, cp_count = cp.get("found"), cp.get("sample_size")
    customer_found, customer_count = customer.get("found"), customer.get("previous_orders")

    sample_avg = (
        sample_score(cp_found, cp_count) + sample_score(customer_found, customer_count)
    ) / 2

    if decision == "ESCALATE":
        low, high = 0.15, 0.45
        # More/stronger *blocking* flags make ESCALATE itself a
        # clearer-cut call, not a wilder guess - confidence rises with
        # the flag count within this band, capped at 4 flags. Non-
        # blocking "INFO: " flags (e.g. a new customer) don't count here
        # - they didn't cause the escalation, so they shouldn't inflate
        # confidence in it either.
        blocking = blocking_flags(flags)
        strength = min(1.0, len(blocking) / 4)
        confidence = round(low + (high - low) * strength, 2)
        factors = [
            f"{len(blocking)} of 4 blocking uncertainty flags present"
            if blocking
            else "No blocking flags, but critical data was missing"
        ]
        return confidence, factors

    if decision == "HOLD_FOR_VERIFICATION":
        low, high = 0.45, 0.80
        conf = low + (high - low) * sample_avg - warning_penalty
        confidence = round(max(low, min(high, conf)), 2)
        factors = [
            sample_label("Courier x pincode", cp_found, cp_count),
            sample_label("Customer", customer_found, customer_count),
        ]
        if warnings:
            factors.append(
                f"{len(warnings)} data-quality warning{'s' if len(warnings) != 1 else ''} reduced confidence"
            )
        return confidence, factors

    # RELEASE
    low, high = 0.70, 0.95
    verified_bonus = 0.0
    bonus_notes = []
    if is_true(customer.get("phone_verified")):
        verified_bonus += 0.05
        bonus_notes.append("phone verified")
    if is_true(customer.get("address_verified")):
        verified_bonus += 0.05
        bonus_notes.append("address verified")
    conf = low + (high - low) * sample_avg + verified_bonus - warning_penalty
    confidence = round(max(low, min(high, conf)), 2)
    factors = [
        sample_label("Courier x pincode", cp_found, cp_count),
        sample_label("Customer", customer_found, customer_count),
    ]
    if bonus_notes:
        factors.append("Verified: " + ", ".join(bonus_notes))
    if warnings:
        factors.append(
            f"{len(warnings)} data-quality warning{'s' if len(warnings) != 1 else ''} reduced confidence"
        )
    return confidence, factors


def context_disruption_notes(investigation) -> List[str]:
    """
    Documented operational events (weather, courier capacity, local
    disruption) that could plausibly explain a recent dip without it
    being a persistent problem. Surfaced as context, not used to
    silently downgrade a decision.
    """

    notes = []
    for event in investigation.get("context_events", []):
        if event.get("severity") in ("HIGH", "MEDIUM"):
            notes.append(
                f"Documented {humanize_label(event.get('event_type'))} on "
                f"{event.get('date')} ({event.get('severity')} severity) "
                "may explain part of the recent dip"
            )
    return notes


# ============================================================
# DECISION
# ============================================================

def decide(data, investigation) -> DecisionResult:

    order = investigation["order"]
    comparison = investigation["signal_comparison"]
    quality = investigation["data_quality"]

    supporting = comparison["supporting_evidence"]
    signal_counter = comparison["counter_evidence"]
    disruption_notes = context_disruption_notes(investigation)
    counter = list(signal_counter)
    counter.extend(disruption_notes)

    risk = compute_risk_level(investigation)
    flags = compute_uncertainty_flags(data, investigation)
    # A brand-new customer alone ("INFO: " flag) doesn't count against
    # sufficiency - see blocking_flags(). Everything else still does.
    evidence_sufficient = len(blocking_flags(flags)) == 0

    def result(decision, reason) -> DecisionResult:
        confidence, confidence_factors = compute_confidence(investigation, decision, flags)
        logger.info(
            "order=%s decision=%s risk=%s confidence=%.2f flags=%d",
            order["order_id"], decision, risk, confidence, len(flags),
        )
        return DecisionResult(
            order_id=order["order_id"],
            decision=decision,
            risk_level=risk,
            confidence=confidence,
            confidence_factors=confidence_factors,
            reason=reason,
            supporting_evidence=supporting,
            counter_evidence=counter,
            uncertainty_flags=flags,
        )

    # ------------------------------------------------------
    # 1. Critical data missing -> never guess.
    # ------------------------------------------------------
    if quality["issues"]:
        return result(
            "ESCALATE",
            "Critical signal data is missing for this order - an "
            "automated decision would be guessing rather than reasoning.",
        )

    # ------------------------------------------------------
    # 2. Evidence genuinely conflicts (strong signal on both sides) ->
    #    always worth a human look, regardless of sample sufficiency.
    # ------------------------------------------------------
    if comparison["supporting_count"] >= 2 and comparison["counter_count"] >= 2:
        return result(
            "HOLD_FOR_VERIFICATION",
            "Supporting and counter-evidence are both substantial and "
            "point in different directions - plausibly legitimate, but "
            "the risk signal is real enough to verify before releasing.",
        )

    # ------------------------------------------------------
    # 3. Severe, high-confidence operational risk -> always hold.
    #    (Never auto-reject; a human confirms before this becomes a
    #    lost order.)
    # ------------------------------------------------------
    if risk == "HIGH":
        cp = investigation["courier_pincode"]
        lane_severe = cp.get("found") and cp.get("trend") == "SEVERE_DETERIORATION"

        if lane_severe:
            reason = (
                "The courier x pincode lane itself shows severe, "
                "consistent deterioration."
            )
        else:
            reason = (
                f"Risk level is HIGH on accumulated supporting evidence "
                f"({comparison['supporting_count']} independent signals) "
                f"even though the courier x pincode lane is not itself "
                f"severely deteriorating."
            )

        if len(counter) >= 2:
            if signal_counter:
                reason += (
                    " Meaningful counter-evidence exists, so hold for "
                    "verification rather than reject."
                )
            else:
                reason += (
                    " The counter-evidence here is documented operational "
                    "context (e.g. weather/courier disruption), not "
                    "customer-side reassurance, so hold for verification "
                    "rather than reject."
                )

        return result(
            "HOLD_FOR_VERIFICATION",
            reason,
        )

    # ------------------------------------------------------
    # 4. Not enough of a track record to trust either way.
    # ------------------------------------------------------
    if not evidence_sufficient:
        return result(
            "ESCALATE",
            "Not enough reliable track record to make a confident "
            "automated call (" + "; ".join(blocking_flags(flags)) + ") - "
            "this needs a human, independent of whether the order itself "
            "looks risky.",
        )

    # ------------------------------------------------------
    # 5. Moderate risk, but the evidence behind it is trustworthy.
    # ------------------------------------------------------
    if risk == "MEDIUM":
        return result(
            "HOLD_FOR_VERIFICATION",
            "Some risk signal present, backed by a sufficient track "
            "record, without strong counter-evidence - verify before "
            "releasing.",
        )

    # ------------------------------------------------------
    # 6. Low risk, sufficient and reliable evidence -> release.
    # ------------------------------------------------------
    return result(
        "RELEASE",
        "Signals consistently indicate low risk, and the evidence "
        "behind that read is sufficient and reliable.",
    )


# ============================================================
# PRETTY PRINT
# ============================================================

def print_decision(decision: DecisionResult) -> None:

    print("\n" + "=" * 70)
    print(f"DECISION: {decision.order_id}")
    print("=" * 70)

    print(f"Risk level  : {decision.risk_level}")
    print(f"Confidence  : {decision.confidence:.0%}")
    print(f"Decision    : {decision.decision}")
    print(f"Reason      : {decision.reason}")

    print("\nSupporting evidence:")
    for item in decision.supporting_evidence:
        print(f"  + {item}")
    if not decision.supporting_evidence:
        print("  None")

    print("Counter evidence:")
    for item in decision.counter_evidence:
        print(f"  + {item}")
    if not decision.counter_evidence:
        print("  None")

    print("Uncertainty flags:")
    for item in decision.uncertainty_flags:
        print(f"  ! {item}")
    if not decision.uncertainty_flags:
        print("  None")


# ============================================================
# MAIN
# ============================================================

EXPECTED = {
    "CLEAR_SAFE": "RELEASE",
    "CLEAR_RISKY": "HOLD_FOR_VERIFICATION",
    "GOOD_CUSTOMER_BAD_PINCODE": "HOLD_FOR_VERIFICATION",
    "COURIER_PINCODE_ANOMALY": "HOLD_FOR_VERIFICATION",
    "NEW_EVERYTHING": "ESCALATE",
    # CUST-005 (this scenario's customer) is a well-established, fully
    # verified LOYAL customer - once the phone/address-verified counter
    # evidence bug (numpy.bool_ `is True`) was fixed, that verification
    # legitimately produces counter_count=3 against this lane's
    # supporting_count=2, so decide()'s branch 2 ("evidence genuinely
    # conflicts" - see stress_test.py invariant 6) now fires ahead of
    # branch 4's thin-sample ESCALATE, exactly per its documented
    # precedence. HOLD_FOR_VERIFICATION is the correct call here, not
    # a regression.
    "LOW_SAMPLE_SPIKE": "HOLD_FOR_VERIFICATION",
    "MISSING_COURIER_DATA": "ESCALATE",
    "CONFLICTING_SIGNALS": "HOLD_FOR_VERIFICATION",
    "TEMPORARY_DISRUPTION": "HOLD_FOR_VERIFICATION",
    "NO_HISTORICAL_PRECEDENT": "ESCALATE",
}


def main():

    print("=" * 70)
    print("QUESSATHON - FIND THE SIGNAL")
    print("Deterministic Decision Engine")
    print("=" * 70)

    data = load_data()
    orders = data["orders"]

    scenarios = orders[orders["scenario"].notna() & (orders["scenario"] != "NORMAL")]

    correct = 0
    total = 0

    print(f"\n{'ORDER':10} {'SCENARIO':28} {'EXPECTED':22} {'GOT':22} {'CONF':6} MATCH")
    print("-" * 100)

    for _, row in scenarios.iterrows():

        investigation = get_order_investigation(data, row["order_id"])
        decision = decide(data, investigation)

        expected = EXPECTED.get(row["scenario"], "?")
        match = "OK" if decision.decision == expected else "DIFF"

        total += 1
        if match == "OK":
            correct += 1

        print(
            f"{row['order_id']:10} {row['scenario']:28} "
            f"{expected:22} {decision.decision:22} "
            f"{decision.confidence:5.0%} {match}"
        )

    print("-" * 100)
    print(f"Matched expected behaviour: {correct}/{total}")

    print("\n\nDETAILED VIEW OF EACH SCENARIO")

    for _, row in scenarios.iterrows():
        investigation = get_order_investigation(data, row["order_id"])
        decision = decide(data, investigation)
        print(f"\nScenario: {row['scenario']}")
        print_decision(decision)


if __name__ == "__main__":
    main()
