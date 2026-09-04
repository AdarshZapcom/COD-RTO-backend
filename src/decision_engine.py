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

from typing import List, Literal

import pandas as pd
from pydantic import BaseModel

from analytics import load_data, get_order_investigation

Decision = Literal["RELEASE", "HOLD_FOR_VERIFICATION", "ESCALATE"]
RiskLevel = Literal["LOW", "MEDIUM", "HIGH"]


class DecisionResult(BaseModel):
    order_id: str
    decision: Decision
    risk_level: RiskLevel
    confidence: float
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
        (hist["pincode"].astype(str) == str(pincode))
        & (hist["courier"] == courier_id)
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

    if severe or supporting >= 3:
        return "HIGH"

    if supporting >= 1:
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
        and cp["sample_size"] < 5
    ):
        flags.append(
            f"Courier x pincode sample size is very small "
            f"(n={int(cp['sample_size'])})"
        )

    customer = investigation["customer"]
    if (
        customer.get("found")
        and pd.notna(customer.get("previous_orders"))
        and customer["previous_orders"] < 3
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
                f"Documented {event.get('event_type')} on "
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
    counter = list(comparison["counter_evidence"])
    counter.extend(context_disruption_notes(investigation))

    risk = compute_risk_level(investigation)
    flags = compute_uncertainty_flags(data, investigation)
    evidence_sufficient = len(flags) == 0

    def result(decision, confidence, reason) -> DecisionResult:
        return DecisionResult(
            order_id=order["order_id"],
            decision=decision,
            risk_level=risk,
            confidence=confidence,
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
            0.30,
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
            0.55,
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
        reason = (
            "Operational signals (courier x pincode lane) show severe, "
            "consistent deterioration."
        )
        if len(counter) >= 2:
            reason += (
                " Meaningful counter-evidence on the customer side "
                "exists, so hold for verification rather than reject."
            )
        return result(
            "HOLD_FOR_VERIFICATION",
            0.75,
            reason,
        )

    # ------------------------------------------------------
    # 4. Not enough of a track record to trust either way.
    # ------------------------------------------------------
    if not evidence_sufficient:
        return result(
            "ESCALATE",
            0.40,
            "Not enough reliable track record to make a confident "
            "automated call (" + "; ".join(flags) + ") - this needs a "
            "human, independent of whether the order itself looks risky.",
        )

    # ------------------------------------------------------
    # 5. Moderate risk, but the evidence behind it is trustworthy.
    # ------------------------------------------------------
    if risk == "MEDIUM":
        return result(
            "HOLD_FOR_VERIFICATION",
            0.65,
            "Some risk signal present, backed by a sufficient track "
            "record, without strong counter-evidence - verify before "
            "releasing.",
        )

    # ------------------------------------------------------
    # 6. Low risk, sufficient and reliable evidence -> release.
    # ------------------------------------------------------
    return result(
        "RELEASE",
        0.85,
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
    "LOW_SAMPLE_SPIKE": "ESCALATE",
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
