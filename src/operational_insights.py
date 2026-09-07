"""
Quessathon 2026 - Retail Challenge 01: Find the Signal

Operational Insight layer.

This is the one explicitly required minimum capability that had no code
behind it yet: "produce a courier or pincode-level case a human ops team
could act on, not just a per-order score."

Design principle (same authority model as the rest of the system):
    Deterministic code scans every courier x pincode lane and ranks the
    ones showing a real recent-vs-baseline deterioration - the same
    "recent trend vs. lifetime average" logic decision_engine.py already
    applies per order, just run across all lanes at once instead of one
    at a time. recommended_action is a fixed template string, not an LLM
    call. LLM narration is an optional enhancement that could sit on top
    of this later, never a requirement for it to be useful.
"""

from __future__ import annotations

import logging
from typing import List, Literal, Optional

import pandas as pd
from pydantic import BaseModel, ValidationError

from analytics import humanize_label

logger = logging.getLogger(__name__)

Severity = Literal["HIGH", "MEDIUM", "LOW_SAMPLE"]


class OperationalInsight(BaseModel):
    courier_id: str
    pincode: str
    severity: Severity
    rto_rate_7d: float
    rto_rate_30d: float
    delta: float
    sample_size: int
    possible_cause: Optional[str] = None  # from events table, if any
    recommended_action: str               # template-generated, not an LLM call
    estimated_impact: Optional[str] = None  # None for LOW_SAMPLE - see _estimate_impact()


# Per-failed-COD-order cost the challenge brief itself cites (ShipPrime
# research, 2026: wasted shipping + reverse logistics + blocked
# inventory) - reused here rather than inventing a new figure, so the
# impact estimate stays traceable to the same source the brief already
# grounds the business case in.
COST_PER_FAILED_ORDER_LOW = 180
COST_PER_FAILED_ORDER_HIGH = 240


def _estimate_impact(delta: float, sample_size: int) -> Optional[str]:
    """
    Rough rupee-impact estimate for a HIGH/MEDIUM finding: the excess
    RTO rate (delta = 7d rate - 30d baseline) applied to this lane's own
    recorded order volume (sample_size) - the best available proxy for
    "how many orders like this has the lane actually seen", not a
    monthly-volume projection (no such figure exists in this dataset).

    Deliberately NOT called for LOW_SAMPLE findings (see
    get_operational_insights()) - putting a rupee figure on a rate that
    isn't statistically trustworthy would be exactly the overclaiming
    this project's whole design is built to avoid.
    """
    excess_orders = delta * sample_size
    if excess_orders <= 0:
        return None

    low = round(excess_orders * COST_PER_FAILED_ORDER_LOW)
    high = round(excess_orders * COST_PER_FAILED_ORDER_HIGH)

    return (
        f"Est. impact: Rs {low:,}-{high:,} "
        f"(~{excess_orders:.1f} excess RTO orders x Rs {COST_PER_FAILED_ORDER_LOW}"
        f"-{COST_PER_FAILED_ORDER_HIGH}/order, per the challenge brief's cited "
        "industry figures)"
    )


# ============================================================
# POSSIBLE CAUSE (events table)
# ============================================================

def _possible_cause(events: pd.DataFrame, pincode: str, courier_id: str) -> Optional[str]:
    """
    Any documented operational event (weather, courier capacity, local
    disruption) recorded against this *exact* (pincode, courier_id)
    lane. This is what lets a finding say "may be explained by a
    documented disruption" instead of reading as unexplained
    deterioration - so this is an exact-lane match, not the broader
    pincode-OR-courier match analytics.get_context_events() uses for a
    single order's wider context.
    """

    if events.empty:
        return None

    matches = events[
        (events["pincode"].astype(str) == str(pincode))
        & (events["courier_id"] == courier_id)
    ]

    if matches.empty:
        return None

    descriptions = [
        f"{humanize_label(row.get('event_type'))} on {row.get('date')} "
        f"({row.get('severity')} severity): {row.get('description')}"
        for _, row in matches.iterrows()
    ]

    return " | ".join(descriptions)


# ============================================================
# RECOMMENDED ACTION (fixed template, not an LLM call)
# ============================================================

def _recommended_action(
    severity: Severity,
    courier_id: str,
    pincode: str,
    rto_rate_7d: float,
    rto_rate_30d: float,
    sample_size: int,
) -> str:

    order_word = "order" if sample_size == 1 else "orders"

    if severity == "LOW_SAMPLE":
        return (
            f"Courier {courier_id} shows a {rto_rate_7d:.0%} recent RTO rate "
            f"in pincode {pincode} vs {rto_rate_30d:.0%} baseline, but based on "
            f"only {sample_size} past {order_word} - too small a sample to confirm a "
            "real pattern. Monitor before taking action."
        )

    return (
        f"Courier {courier_id} is deteriorating specifically in pincode "
        f"{pincode} ({rto_rate_7d:.0%} vs {rto_rate_30d:.0%} baseline, "
        f"based on {sample_size} past {order_word}). Consider rerouting new orders "
        "in this pincode to an alternate courier."
    )


# ============================================================
# OPERATIONAL INSIGHT SCAN
# ============================================================

def get_operational_insights(
    data,
    min_sample: int = 5,
    delta_threshold: float = 0.05,
) -> List[OperationalInsight]:
    """
    Scan every courier x pincode lane (not individual orders) and
    surface the ones showing a real recent-vs-baseline deterioration,
    ranked by how much it matters operationally.

    Reuses data["courier_pincode"] and data["events"] from
    analytics.load_data() - no new data source needed.
    """

    lanes = data["courier_pincode"]
    events = data["events"]

    findings: List[OperationalInsight] = []

    for idx, row in lanes.iterrows():

        # Nothing to compute without the underlying numbers.
        if row.get("data_quality") == "MISSING":
            continue

        courier_id = row.get("courier_id")
        pincode = row.get("pincode")

        # A lane row with no identifying courier/pincode can't be reported
        # or acted on either - skip it the same way a MISSING/NaN numeric
        # row is skipped above, rather than let it reach model
        # construction and blow up.
        if pd.isna(courier_id) or pd.isna(pincode):
            logger.warning(
                "Skipping courier_pincode row %s: missing courier_id/pincode.",
                idx,
            )
            continue

        rto_7d = row.get("rto_rate_7d")
        rto_30d = row.get("rto_rate_30d")

        if pd.isna(rto_7d) or pd.isna(rto_30d):
            continue

        # One malformed lane (bad numeric type, unexpected value, etc.)
        # must never abort the scan and discard every other otherwise-
        # valid finding - skip and log just that row.
        try:
            rto_7d = float(rto_7d)
            rto_30d = float(rto_30d)
            delta = round(rto_7d - rto_30d, 4)

            # Only a real, material recent deterioration is worth surfacing
            # as a finding at all.
            if not (delta > delta_threshold and rto_7d > 0.15):
                continue

            total_orders = row.get("total_orders")
            sample_size = int(total_orders) if pd.notna(total_orders) else 0

            courier_id = str(courier_id)
            pincode = str(pincode)

            # Too small a sample to call it a pattern - flag separately as
            # LOW_SAMPLE, don't rank it alongside a trustworthy HIGH/MEDIUM
            # trend built on enough orders to mean something.
            if sample_size < min_sample:
                severity: Severity = "LOW_SAMPLE"
            elif delta > 0.10:
                severity = "HIGH"
            else:
                severity = "MEDIUM"

            cause = _possible_cause(events, pincode, courier_id)

            finding = OperationalInsight(
                courier_id=courier_id,
                pincode=pincode,
                severity=severity,
                rto_rate_7d=round(rto_7d, 4),
                rto_rate_30d=round(rto_30d, 4),
                delta=delta,
                sample_size=sample_size,
                possible_cause=cause,
                recommended_action=_recommended_action(
                    severity,
                    courier_id,
                    pincode,
                    rto_7d,
                    rto_30d,
                    sample_size,
                ),
                estimated_impact=(
                    _estimate_impact(delta, sample_size)
                    if severity != "LOW_SAMPLE"
                    else None
                ),
            )
        except (ValidationError, ValueError, TypeError) as exc:
            logger.warning(
                "Skipping malformed courier_pincode row %s (courier_id=%r, "
                "pincode=%r): %s",
                idx, courier_id, pincode, exc,
            )
            continue

        findings.append(finding)

    findings.sort(key=lambda f: f.delta, reverse=True)

    return findings


# ============================================================
# PRETTY PRINT
# ============================================================

def print_insights(findings: List[OperationalInsight]) -> None:

    print("\n" + "=" * 90)
    print("OPERATIONAL INSIGHTS: courier x pincode lanes with real recent deterioration")
    print("=" * 90)

    if not findings:
        print("No lanes met the deterioration threshold.")
        return

    header = (
        f"{'COURIER':8} {'PINCODE':8} {'SEVERITY':10} "
        f"{'7D':6} {'30D':6} {'DELTA':6} {'N':4} CAUSE"
    )
    print(header)
    print("-" * 90)

    for f in findings:
        cause = f.possible_cause if f.possible_cause else "-"
        print(
            f"{f.courier_id:8} {f.pincode:8} {f.severity:10} "
            f"{f.rto_rate_7d:<6.2f} {f.rto_rate_30d:<6.2f} "
            f"{f.delta:<6.2f} {f.sample_size:<4} {cause}"
        )


# ============================================================
# MAIN (acceptance check)
# ============================================================

def main():
    import os
    import sys

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from analytics import load_data

    print("=" * 90)
    print("QUESSATHON - FIND THE SIGNAL")
    print("Operational Insight (courier x pincode lane scan)")
    print("=" * 90)

    data = load_data()
    findings = get_operational_insights(data)

    print(f"\nTotal lanes scanned : {len(data['courier_pincode'])}")
    print(f"Total findings      : {len(findings)}")

    print_insights(findings)

    high_findings = [f for f in findings if f.severity == "HIGH"]

    print("\n" + "=" * 90)
    print("ACCEPTANCE CHECK: top HIGH finding should be C07 x 560068")
    print("=" * 90)

    if not high_findings:
        print("FAIL: no HIGH findings produced at all.")
        return

    top = high_findings[0]
    print(f"Top HIGH finding: {top.courier_id} x {top.pincode}")
    print(top.model_dump_json(indent=2))

    expected = {
        "courier_id": "C07",
        "pincode": "560068",
        "rto_rate_7d": 0.29,
        "rto_rate_30d": 0.15,
        "delta": 0.14,
    }

    ok = (
        top.courier_id == expected["courier_id"]
        and top.pincode == expected["pincode"]
        and top.rto_rate_7d == expected["rto_rate_7d"]
        and top.rto_rate_30d == expected["rto_rate_30d"]
        and top.delta == expected["delta"]
    )

    cause_ok = top.possible_cause is not None and (
        "HEAVY_RAIN" in top.possible_cause or "COURIER_DISRUPTION" in top.possible_cause
    )

    print(f"\nNumbers match spec   : {ok}")
    print(f"possible_cause populated from EVT-001/EVT-002 events: {cause_ok}")
    print(f"possible_cause value : {top.possible_cause}")

    if ok and cause_ok:
        print("\nPASS: acceptance check satisfied.")
    else:
        print("\nFAIL: acceptance check NOT satisfied.")


if __name__ == "__main__":
    main()
