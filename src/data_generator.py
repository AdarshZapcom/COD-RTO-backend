"""
Quessathon 2026 - Retail Challenge 01: Find the Signal

Synthetic interconnected COD/RTO data generator.

Generates:
    data/generated/customers.csv
    data/generated/orders.csv
    data/generated/pincodes.csv
    data/generated/couriers.csv
    data/generated/courier_pincode.csv
    data/generated/delivery_attempts.csv
    data/generated/events.csv
    data/generated/historical_cases.csv

IMPORTANT:
- This is synthetic competition data.
- It is designed to create believable statistical relationships.
- It is NOT intended to be a production RTO model.
- Ground truth (RTO outcome) is generated for evaluation only.
"""

from __future__ import annotations

import os
import random
import math
from datetime import datetime, timedelta

import numpy as np
import pandas as pd


# ============================================================
# CONFIGURATION
# ============================================================

SEED = 42
random.seed(SEED)
np.random.seed(SEED)

OUTPUT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "generated",
)

NUM_CUSTOMERS = 100
NUM_ORDERS = 3000
NUM_PINCODES = 20
NUM_COURIERS = 5
NUM_SELLERS = 10

END_DATE = datetime(2026, 9, 3)
START_DATE = END_DATE - timedelta(days=90)

# Indian-style synthetic pincodes.
PINCODES = [
    "560001",
    "560002",
    "560003",
    "560004",
    "560005",
    "560006",
    "560007",
    "560008",
    "560009",
    "560010",
    "560011",
    "560012",
    "560013",
    "560014",
    "560015",
    "560016",
    "560017",
    "560068",
    "560076",
    "560100",
]

COURIERS = [
    "C01",
    "C02",
    "C03",
    "C04",
    "C07",
]

PRODUCT_CATEGORIES = [
    "Electronics",
    "Fashion",
    "Beauty",
    "Home",
    "Kitchen",
    "Mobile_Accessories",
    "Footwear",
    "Grocery",
    "Sports",
    "Personal_Care",
]

FAILURE_REASONS = [
    "CUSTOMER_UNAVAILABLE",
    "CUSTOMER_REFUSED",
    "ADDRESS_NOT_FOUND",
    "PHONE_UNREACHABLE",
    "ADDRESS_INCORRECT",
    "COD_AMOUNT_NOT_READY",
    "DELIVERY_DELAY",
    "PINCODE_NOT_SERVICEABLE",
    "CUSTOMER_RESCHEDULED",
]


# ============================================================
# HELPERS
# ============================================================

def ensure_output_dir():
    os.makedirs(OUTPUT_DIR, exist_ok=True)


def sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def weighted_choice(items, weights):
    return random.choices(items, weights=weights, k=1)[0]


def random_date(start: datetime, end: datetime) -> datetime:
    delta = end - start
    return start + timedelta(
        seconds=random.randint(0, int(delta.total_seconds()))
    )


def recent_date(days_ago_min: int, days_ago_max: int) -> datetime:
    days = random.randint(days_ago_min, days_ago_max)
    return END_DATE - timedelta(days=days)


# ============================================================
# 1. PINCODE PROFILES
# ============================================================

def create_pincode_profiles():
    """
    Create the underlying operational characteristics of each pincode.

    Important:
    560068 is deliberately configured as a deteriorating pincode.
    """

    rows = []

    for pincode in PINCODES:
        # Baseline pincode RTO.
        baseline_rto = np.random.uniform(0.035, 0.10)

        # Most pincodes remain stable.
        trend = "STABLE"

        rto_90d = baseline_rto
        rto_30d = baseline_rto + np.random.normal(0, 0.01)
        rto_7d = baseline_rto + np.random.normal(0, 0.015)

        avg_delivery_days = np.random.uniform(2.0, 5.0)
        ndr_rate = np.random.uniform(0.05, 0.15)

        # ----------------------------------------------------
        # Deliberate curveball: deteriorating pincode
        # ----------------------------------------------------
        if pincode == "560068":
            rto_90d = 0.07
            rto_30d = 0.11
            rto_7d = 0.24
            avg_delivery_days = 4.8
            ndr_rate = 0.21
            trend = "DETERIORATING"

        # Stable good pincode.
        elif pincode in ["560001", "560004", "560012"]:
            rto_90d = 0.045
            rto_30d = 0.047
            rto_7d = 0.05
            avg_delivery_days = 2.4
            ndr_rate = 0.07
            trend = "STABLE"

        # Another moderately risky pincode.
        elif pincode == "560076":
            rto_90d = 0.09
            rto_30d = 0.105
            rto_7d = 0.13
            avg_delivery_days = 4.5
            ndr_rate = 0.17
            trend = "SLIGHTLY_DETERIORATING"

        rows.append(
            {
                "pincode": pincode,
                "baseline_rto_rate": round(rto_90d, 4),
                "rto_rate_90d": round(rto_90d, 4),
                "rto_rate_30d": round(rto_30d, 4),
                "rto_rate_7d": round(rto_7d, 4),
                "avg_delivery_days": round(avg_delivery_days, 2),
                "ndr_rate": round(ndr_rate, 4),
                "trend": trend,
            }
        )

    return pd.DataFrame(rows)


# ============================================================
# 2. COURIER PROFILES
# ============================================================

def create_courier_profiles():
    """
    Create courier-level performance.

    C07 is deliberately deteriorating recently, but its problem
    is especially strong on pincode 560068.
    """

    rows = []

    for courier in COURIERS:

        rto_90d = np.random.uniform(0.04, 0.09)
        rto_30d = rto_90d + np.random.normal(0, 0.01)
        rto_7d = rto_30d + np.random.normal(0, 0.015)

        avg_delivery_days = np.random.uniform(2.5, 4.5)
        ndr_rate = np.random.uniform(0.06, 0.15)

        trend = "STABLE"

        if courier == "C07":
            rto_90d = 0.06
            rto_30d = 0.10
            rto_7d = 0.17
            avg_delivery_days = 4.6
            ndr_rate = 0.18
            trend = "DETERIORATING"

        rows.append(
            {
                "courier_id": courier,
                "baseline_rto_rate": round(rto_90d, 4),
                "rto_rate_90d": round(rto_90d, 4),
                "rto_rate_30d": round(rto_30d, 4),
                "rto_rate_7d": round(rto_7d, 4),
                "avg_delivery_days": round(avg_delivery_days, 2),
                "ndr_rate": round(ndr_rate, 4),
                "trend": trend,
            }
        )

    return pd.DataFrame(rows)


# ============================================================
# 3. COURIER × PINCODE PROFILES
# ============================================================

def create_courier_pincode_profiles(pincode_df, courier_df):
    """
    Create courier × pincode interaction statistics.

    THIS IS ONE OF THE MOST IMPORTANT TABLES IN THE PROJECT.

    Special pattern:

        C07 + 560068 = very high RTO

    while:

        C07 + other pincodes = mostly normal.
    """

    rows = []

    for courier in COURIERS:
        courier_row = courier_df[
            courier_df["courier_id"] == courier
        ].iloc[0]

        for pincode in PINCODES:

            pincode_row = pincode_df[
                pincode_df["pincode"] == pincode
            ].iloc[0]

            base = (
                courier_row["rto_rate_90d"]
                + pincode_row["rto_rate_90d"]
            ) / 2

            rto_90d = base + np.random.normal(0, 0.015)

            # ------------------------------------------------
            # KILLER PATTERN
            # ------------------------------------------------
            if courier == "C07" and pincode == "560068":
                rto_90d = 0.08
                rto_30d = 0.15
                rto_7d = 0.29
                avg_delivery_days = 5.6
                ndr_rate = 0.27
                trend = "SEVERE_DETERIORATION"

            else:
                rto_30d = rto_90d + np.random.normal(0, 0.012)
                rto_7d = rto_30d + np.random.normal(0, 0.018)

                avg_delivery_days = (
                    courier_row["avg_delivery_days"]
                    + pincode_row["avg_delivery_days"]
                ) / 2

                ndr_rate = (
                    courier_row["ndr_rate"]
                    + pincode_row["ndr_rate"]
                ) / 2

                trend = "STABLE"

                if rto_7d > rto_30d + 0.04:
                    trend = "DETERIORATING"

            rows.append(
                {
                    "courier_id": courier,
                    "pincode": pincode,
                    "rto_rate_90d": round(clamp(rto_90d, 0.01, 0.45), 4),
                    "rto_rate_30d": round(clamp(rto_30d, 0.01, 0.45), 4),
                    "rto_rate_7d": round(clamp(rto_7d, 0.01, 0.50), 4),
                    "avg_delivery_days": round(avg_delivery_days, 2),
                    "ndr_rate": round(clamp(ndr_rate, 0.01, 0.50), 4),
                    "trend": trend,
                }
            )

    return pd.DataFrame(rows)


# ============================================================
# 4. CUSTOMERS
# ============================================================

def create_customers():
    """
    Generate customer profiles with different behaviour types.
    """

    rows = []

    customer_types = [
        "LOYAL",
        "NORMAL",
        "RISKY",
        "NEW",
        "ATYPICAL_LEGITIMATE",
    ]

    for i in range(NUM_CUSTOMERS):

        customer_id = f"CUST-{i + 1:03d}"

        # Force some special customers for deterministic test cases.
        if i < 15:
            customer_type = "LOYAL"
        elif i < 25:
            customer_type = "RISKY"
        elif i < 35:
            customer_type = "NEW"
        elif i < 45:
            customer_type = "ATYPICAL_LEGITIMATE"
        else:
            customer_type = weighted_choice(
                customer_types,
                [35, 40, 10, 10, 5],
            )

        if customer_type == "LOYAL":
            previous_orders = random.randint(8, 30)
            previous_rto = random.randint(0, 1)
            previous_cod_orders = random.randint(
                max(1, previous_orders // 3),
                previous_orders
            )
            successful_cod_orders = max(
                0,
                previous_cod_orders - previous_rto
            )
            cancellations = random.randint(0, 1)
            phone_verified = True
            address_verified = True

        elif customer_type == "RISKY":
            previous_orders = random.randint(2, 12)
            previous_rto = random.randint(2, max(2, previous_orders // 2))
            previous_cod_orders = random.randint(
                1,
                max(1, previous_orders)
            )
            successful_cod_orders = max(
                0,
                previous_cod_orders - previous_rto
            )
            cancellations = random.randint(1, 4)
            phone_verified = random.random() < 0.65
            address_verified = random.random() < 0.65

        elif customer_type == "NEW":
            previous_orders = random.randint(0, 2)
            previous_rto = 0
            previous_cod_orders = random.randint(0, previous_orders)
            successful_cod_orders = previous_cod_orders
            cancellations = 0
            phone_verified = random.random() < 0.75
            address_verified = random.random() < 0.75

        elif customer_type == "ATYPICAL_LEGITIMATE":
            previous_orders = random.randint(4, 10)
            previous_rto = random.randint(0, 2)
            previous_cod_orders = random.randint(
                2,
                previous_orders
            )
            successful_cod_orders = max(
                0,
                previous_cod_orders - previous_rto
            )
            cancellations = random.randint(0, 1)
            phone_verified = True
            address_verified = True

        else:
            previous_orders = random.randint(1, 15)
            previous_rto = random.randint(
                0,
                max(1, previous_orders // 4)
            )
            previous_cod_orders = random.randint(
                0,
                previous_orders
            )
            successful_cod_orders = max(
                0,
                previous_cod_orders - previous_rto
            )
            cancellations = random.randint(0, 2)
            phone_verified = random.random() < 0.85
            address_verified = random.random() < 0.85

        account_age_days = (
            random.randint(5, 40)
            if customer_type == "NEW"
            else random.randint(60, 1200)
        )

        rows.append(
            {
                "customer_id": customer_id,
                "account_age_days": account_age_days,
                "previous_orders": previous_orders,
                "successful_orders": max(
                    0,
                    previous_orders - previous_rto - cancellations
                ),
                "previous_rto": previous_rto,
                "previous_cod_orders": previous_cod_orders,
                "successful_cod_orders": successful_cod_orders,
                "failed_cod_orders": previous_rto,
                "previous_cancellations": cancellations,
                "phone_verified": phone_verified,
                "address_verified": address_verified,
                "customer_type": customer_type,
            }
        )

    return pd.DataFrame(rows)


# ============================================================
# 5. ORDERS
# ============================================================

def choose_customer(customers_df):
    """
    Weighted customer selection.

    Customers with more history appear somewhat more frequently.
    """

    weights = (
        customers_df["previous_orders"].astype(float) + 1
    ).tolist()

    return random.choices(
        customers_df["customer_id"].tolist(),
        weights=weights,
        k=1,
    )[0]


def calculate_order_rto_probability(
    customer,
    pincode,
    courier,
    courier_pincode,
    order_value,
    event_multiplier=1.0,
):
    """
    Create a synthetic ground-truth probability.

    This is NOT a trained prediction model.

    It is only used to generate realistic synthetic outcomes.

    The eventual investigation agent must NOT use this formula.
    """

    # Start with a reasonable baseline.
    score = -2.6

    # -------------------------------
    # Customer behaviour
    # -------------------------------

    if customer["customer_type"] == "LOYAL":
        score -= 1.3

    elif customer["customer_type"] == "RISKY":
        score += 1.4

    elif customer["customer_type"] == "NEW":
        score += 0.4

    elif customer["customer_type"] == "ATYPICAL_LEGITIMATE":
        score -= 0.7

    if customer["phone_verified"]:
        score -= 0.25

    else:
        score += 0.3

    if customer["address_verified"]:
        score -= 0.35

    else:
        score += 0.45

    # Prior RTO behaviour.
    if customer["previous_orders"] > 0:
        previous_rto_rate = (
            customer["previous_rto"]
            / customer["previous_orders"]
        )

        score += previous_rto_rate * 2.5

    # -------------------------------
    # Pincode
    # -------------------------------

    score += (
        pincode["rto_rate_30d"] - 0.07
    ) * 4.0

    # -------------------------------
    # Courier
    # -------------------------------

    score += (
        courier["rto_rate_30d"] - 0.07
    ) * 3.0

    # -------------------------------
    # Courier × pincode
    # -------------------------------

    score += (
        courier_pincode["rto_rate_30d"] - 0.07
    ) * 5.0

    # Recent deterioration.
    if courier_pincode["rto_rate_7d"] > (
        courier_pincode["rto_rate_30d"] + 0.05
    ):
        score += 0.45

    # -------------------------------
    # Order value
    # -------------------------------

    if order_value > 3000:
        score += 0.25

    elif order_value < 500:
        score -= 0.10

    # -------------------------------
    # Temporary events
    # -------------------------------

    score *= event_multiplier

    probability = sigmoid(score)

    return clamp(probability, 0.02, 0.85)


def create_orders(
    customers_df,
    pincode_df,
    courier_df,
    courier_pincode_df,
):
    """
    Generate 500 interconnected orders.

    Several orders are deliberately reserved for special scenarios.
    """

    rows = []

    # Special scenario assignments.
    special_scenarios = {
        0: "CLEAR_SAFE",
        1: "CLEAR_RISKY",
        2: "GOOD_CUSTOMER_BAD_PINCODE",
        3: "COURIER_PINCODE_ANOMALY",
        4: "NEW_EVERYTHING",
        5: "LOW_SAMPLE_SPIKE",
        6: "MISSING_COURIER_DATA",
        7: "CONFLICTING_SIGNALS",
        8: "TEMPORARY_DISRUPTION",
        9: "NO_HISTORICAL_PRECEDENT",
    }

    for i in range(NUM_ORDERS):

        order_id = f"ORD-{10000 + i}"

        scenario = special_scenarios.get(i, "NORMAL")

        # ----------------------------------------------------
        # Select customer
        # ----------------------------------------------------

        if scenario == "CLEAR_SAFE":
            customer = customers_df[
                customers_df["customer_type"] == "LOYAL"
            ].iloc[0]

        elif scenario == "GOOD_CUSTOMER_BAD_PINCODE":
            customer = customers_df[
                customers_df["customer_type"] == "LOYAL"
            ].iloc[1]

        elif scenario == "NEW_EVERYTHING":
            customer = customers_df[
                customers_df["customer_type"] == "NEW"
            ].iloc[0]

        elif scenario == "CLEAR_RISKY":
            customer = customers_df[
                customers_df["customer_type"] == "RISKY"
            ].iloc[0]

        elif scenario == "COURIER_PINCODE_ANOMALY":
            # Deliberately an otherwise-unremarkable customer, so the
            # anomaly reads as lane-specific rather than customer-driven.
            customer = customers_df[
                customers_df["customer_type"] == "NORMAL"
            ].iloc[0]

        elif scenario == "MISSING_COURIER_DATA":
            customer = customers_df[
                customers_df["customer_type"] == "LOYAL"
            ].iloc[2]

        elif scenario == "CONFLICTING_SIGNALS":
            # Strong, verified customer history deliberately paired
            # against a risky pincode further down.
            customer = customers_df[
                customers_df["customer_type"] == "LOYAL"
            ].iloc[3]

        elif scenario == "LOW_SAMPLE_SPIKE":
            customer = customers_df[
                customers_df["customer_type"] == "LOYAL"
            ].iloc[4]

        elif scenario == "NO_HISTORICAL_PRECEDENT":
            customer = customers_df[
                customers_df["customer_type"] == "LOYAL"
            ].iloc[5]

        elif scenario == "TEMPORARY_DISRUPTION":
            customer = customers_df[
                customers_df["customer_type"] == "NORMAL"
            ].iloc[1]

        else:
            customer_id = choose_customer(customers_df)
            customer = customers_df[
                customers_df["customer_id"] == customer_id
            ].iloc[0]

        # ----------------------------------------------------
        # Pincode
        # ----------------------------------------------------

        if scenario in [
            "GOOD_CUSTOMER_BAD_PINCODE",
            "COURIER_PINCODE_ANOMALY",
            "TEMPORARY_DISRUPTION",
        ]:
            pincode_id = "560068"

        elif scenario == "CLEAR_SAFE":
            pincode_id = "560001"

        elif scenario == "NEW_EVERYTHING":
            # New-ish operational area.
            pincode_id = "560100"

        elif scenario == "MISSING_COURIER_DATA":
            # Must match the courier x pincode combo whose performance
            # data is deliberately blanked out below.
            pincode_id = "560014"

        elif scenario == "CLEAR_RISKY":
            pincode_id = "560004"

        elif scenario == "LOW_SAMPLE_SPIKE":
            pincode_id = "560009"

        elif scenario == "NO_HISTORICAL_PRECEDENT":
            pincode_id = "560012"

        elif scenario == "CONFLICTING_SIGNALS":
            # Same risky pincode as the anomaly cases, but paired with
            # a clean courier and a strong customer below.
            pincode_id = "560068"

        else:
            pincode_id = random.choice(PINCODES)

        pincode = pincode_df[
            pincode_df["pincode"] == pincode_id
        ].iloc[0]

        # ----------------------------------------------------
        # Courier
        # ----------------------------------------------------

        if scenario in [
            "COURIER_PINCODE_ANOMALY",
            "GOOD_CUSTOMER_BAD_PINCODE",
            "TEMPORARY_DISRUPTION",
        ]:
            courier_id = "C07"

        elif scenario == "CLEAR_SAFE":
            courier_id = "C03"

        elif scenario == "NEW_EVERYTHING":
            courier_id = "C04"

        elif scenario == "MISSING_COURIER_DATA":
            courier_id = "C04"

        elif scenario == "CLEAR_RISKY":
            courier_id = "C01"

        elif scenario == "LOW_SAMPLE_SPIKE":
            courier_id = "C02"

        elif scenario == "NO_HISTORICAL_PRECEDENT":
            courier_id = "C01"

        elif scenario == "CONFLICTING_SIGNALS":
            courier_id = "C01"

        else:
            courier_id = random.choice(COURIERS)

        courier = courier_df[
            courier_df["courier_id"] == courier_id
        ].iloc[0]

        cp = courier_pincode_df[
            (courier_pincode_df["courier_id"] == courier_id)
            & (courier_pincode_df["pincode"] == pincode_id)
        ].iloc[0]

        # ----------------------------------------------------
        # Order attributes
        # ----------------------------------------------------

        order_value = random.choice(
            [
                399,
                599,
                799,
                999,
                1299,
                1499,
                1799,
                1999,
                2499,
                2999,
                3999,
                4999,
            ]
        )

        if scenario in [
            "CLEAR_RISKY",
            "COURIER_PINCODE_ANOMALY",
        ]:
            order_value = random.choice(
                [1299, 1799, 1999, 2499]
            )

        cod_amount = order_value

        category = random.choice(PRODUCT_CATEGORIES)

        is_first_order = customer["previous_orders"] == 0

        seller_id = f"SELLER-{random.randint(1, NUM_SELLERS):02d}"

        order_date = random_date(
            START_DATE,
            END_DATE,
        )

        # ----------------------------------------------------
        # Temporary disruption
        # ----------------------------------------------------

        event_multiplier = 1.0

        if scenario == "TEMPORARY_DISRUPTION":
            event_multiplier = 1.20

        # ----------------------------------------------------
        # Ground truth RTO probability
        # ----------------------------------------------------

        probability = calculate_order_rto_probability(
            customer=customer,
            pincode=pincode,
            courier=courier,
            courier_pincode=cp,
            order_value=order_value,
            event_multiplier=event_multiplier,
        )

        # ----------------------------------------------------
        # Special deterministic cases
        # ----------------------------------------------------

        if scenario == "CLEAR_SAFE":
            probability = 0.04

        elif scenario == "CLEAR_RISKY":
            probability = 0.70

        elif scenario == "GOOD_CUSTOMER_BAD_PINCODE":
            # Important:
            # risk exists but customer is strong.
            probability = 0.28

        elif scenario == "COURIER_PINCODE_ANOMALY":
            probability = 0.42

        elif scenario == "NEW_EVERYTHING":
            # Unknown, not necessarily truly risky.
            probability = 0.32

        elif scenario == "LOW_SAMPLE_SPIKE":
            probability = 0.20

        elif scenario == "CONFLICTING_SIGNALS":
            probability = 0.28

        elif scenario == "NO_HISTORICAL_PRECEDENT":
            probability = 0.30

        rto = int(random.random() < probability)

        order_status = "RTO" if rto else "DELIVERED"

        rows.append(
            {
                "order_id": order_id,
                "customer_id": customer["customer_id"],
                "order_date": order_date.strftime("%Y-%m-%d"),
                "pincode": pincode_id,
                "courier_id": courier_id,
                "seller_id": seller_id,
                "product_category": category,
                "order_value": order_value,
                "cod_amount": cod_amount,
                "payment_type": "COD",
                "is_first_order": is_first_order,
                "address_verified": customer["address_verified"],
                "order_status": order_status,
                "rto": rto,
                "synthetic_rto_probability": round(probability, 4),
                "scenario": scenario,
            }
        )

    return pd.DataFrame(rows)


# ============================================================
# 6. DELIVERY ATTEMPTS
# ============================================================

def create_delivery_attempts(orders_df):
    """
    Create delivery/NDR records.

    RTO orders get more attempts/failure reasons.
    """

    rows = []

    attempt_id = 1

    for _, order in orders_df.iterrows():

        if order["rto"] == 1:

            attempts = random.choice([1, 2, 2, 3])

            for attempt in range(1, attempts + 1):

                failure_reason = weighted_choice(
                    FAILURE_REASONS,
                    [
                        20,  # unavailable
                        20,  # refused
                        8,   # address not found
                        10,  # phone unreachable
                        8,   # address incorrect
                        8,   # cod amount
                        15,  # delay
                        3,   # serviceability
                        8,   # rescheduled
                    ],
                )

                rows.append(
                    {
                        "attempt_id": f"ATT-{attempt_id:05d}",
                        "order_id": order["order_id"],
                        "attempt_number": attempt,
                        "attempt_date": order["order_date"],
                        "attempt_status": "FAILED",
                        "failure_reason": failure_reason,
                        "customer_contacted": random.random() < 0.70,
                        "rescheduled": failure_reason
                        == "CUSTOMER_RESCHEDULED",
                        "delivery_agent_note": (
                            "Delivery attempt unsuccessful"
                        ),
                    }
                )

                attempt_id += 1

        else:

            # Most successful orders have one successful attempt.
            rows.append(
                {
                    "attempt_id": f"ATT-{attempt_id:05d}",
                    "order_id": order["order_id"],
                    "attempt_number": 1,
                    "attempt_date": order["order_date"],
                    "attempt_status": "DELIVERED",
                    "failure_reason": "",
                    "customer_contacted": True,
                    "rescheduled": False,
                    "delivery_agent_note": "Delivered successfully",
                }
            )

            attempt_id += 1

    return pd.DataFrame(rows)


# ============================================================
# 7. EVENTS
# ============================================================

def create_events():
    """
    Generate contextual events.

    These are important for distinguishing:
        persistent deterioration
    from:
        temporary disruption.
    """

    events = [
        {
            "event_id": "EVT-001",
            "date": "2026-08-30",
            "pincode": "560068",
            "courier_id": "C07",
            "event_type": "HEAVY_RAIN",
            "severity": "HIGH",
            "description": (
                "Heavy rainfall caused delivery delays and "
                "temporary route disruption."
            ),
        },
        {
            "event_id": "EVT-002",
            "date": "2026-08-31",
            "pincode": "560068",
            "courier_id": "C07",
            "event_type": "COURIER_DISRUPTION",
            "severity": "MEDIUM",
            "description": (
                "Temporary courier capacity issue affected "
                "delivery attempts."
            ),
        },
        {
            "event_id": "EVT-003",
            "date": "2026-08-20",
            "pincode": "560076",
            "courier_id": "C02",
            "event_type": "LOCAL_EVENT",
            "severity": "LOW",
            "description": (
                "Local event caused temporary congestion."
            ),
        },
        {
            "event_id": "EVT-004",
            "date": "2026-08-15",
            "pincode": "560010",
            "courier_id": "C04",
            "event_type": "WAREHOUSE_DELAY",
            "severity": "MEDIUM",
            "description": (
                "Warehouse processing delay temporarily "
                "increased delivery time."
            ),
        },
        # Additional context events on lanes not tied to any named
        # scenario - more of the documented-disruption "noise" a real
        # ops history would carry, without touching the specific
        # rows the named scenarios (see docs/business-scenarios.md)
        # depend on matching exactly.
        {
            "event_id": "EVT-005",
            "date": "2026-08-25",
            "pincode": "560015",
            "courier_id": "C03",
            "event_type": "FESTIVAL_SURGE",
            "severity": "MEDIUM",
            "description": (
                "Festival-season order surge temporarily "
                "strained delivery capacity."
            ),
        },
        {
            "event_id": "EVT-006",
            "date": "2026-08-18",
            "pincode": "560100",
            "courier_id": "C01",
            "event_type": "ROAD_CLOSURE",
            "severity": "LOW",
            "description": (
                "Planned road maintenance caused minor "
                "delivery delays."
            ),
        },
        {
            "event_id": "EVT-007",
            "date": "2026-08-22",
            "pincode": "560005",
            "courier_id": "C02",
            "event_type": "STRIKE",
            "severity": "HIGH",
            "description": (
                "Regional transport strike disrupted delivery "
                "operations for several days."
            ),
        },
        {
            "event_id": "EVT-008",
            "date": "2026-08-12",
            "pincode": "560017",
            "courier_id": "C04",
            "event_type": "SORTING_FACILITY_ISSUE",
            "severity": "MEDIUM",
            "description": (
                "A sorting-facility outage temporarily delayed "
                "outbound shipments."
            ),
        },
    ]

    return pd.DataFrame(events)


# ============================================================
# 8. HISTORICAL CASES
# ============================================================

def create_historical_cases(
    orders_df,
    customers_df,
    pincode_df,
    courier_df,
    courier_pincode_df,
):
    """
    Create historical investigation cases.

    These later become the ChromaDB/RAG case memory.
    """

    cases = []

    historical_orders = orders_df.sample(
        n=min(100, len(orders_df)),
        random_state=SEED,
    )

    for idx, (_, order) in enumerate(
        historical_orders.iterrows(),
        start=1,
    ):

        customer = customers_df[
            customers_df["customer_id"]
            == order["customer_id"]
        ].iloc[0]

        pincode = pincode_df[
            pincode_df["pincode"]
            == order["pincode"]
        ].iloc[0]

        courier = courier_df[
            courier_df["courier_id"]
            == order["courier_id"]
        ].iloc[0]

        cp = courier_pincode_df[
            (courier_pincode_df["courier_id"]
             == order["courier_id"])
            & (courier_pincode_df["pincode"]
               == order["pincode"])
        ].iloc[0]

        signals = []

        if pincode["rto_rate_30d"] > 0.10:
            signals.append("HIGH_PINCODE_RTO")

        if courier["rto_rate_30d"] > 0.10:
            signals.append("HIGH_COURIER_RTO")

        if cp["rto_rate_30d"] > 0.12:
            signals.append("HIGH_COURIER_PINCODE_RTO")

        if cp["rto_rate_7d"] > cp["rto_rate_30d"] + 0.05:
            signals.append("RECENT_DETERIORATION")

        if customer["previous_rto"] > 1:
            signals.append("CUSTOMER_RTO_HISTORY")

        if not customer["address_verified"]:
            signals.append("ADDRESS_NOT_VERIFIED")

        if not customer["phone_verified"]:
            signals.append("PHONE_NOT_VERIFIED")

        if not signals:
            signals.append("NO_STRONG_RISK_SIGNAL")

        # Counter evidence.
        counter_evidence = []

        if customer["successful_cod_orders"] >= 3:
            counter_evidence.append(
                "SUCCESSFUL_COD_HISTORY"
            )

        if customer["address_verified"]:
            counter_evidence.append(
                "ADDRESS_VERIFIED"
            )

        if customer["phone_verified"]:
            counter_evidence.append(
                "PHONE_VERIFIED"
            )

        if not counter_evidence:
            counter_evidence.append(
                "LIMITED_COUNTER_EVIDENCE"
            )

        # Decision based on synthetic outcome and evidence.
        if order["rto"] == 1:
            decision = "HOLD"
            reason = (
                "Historical investigation identified meaningful "
                "RTO risk before the order was completed."
            )
        else:
            decision = "RELEASE"
            reason = (
                "Historical investigation found sufficient "
                "evidence supporting delivery."
            )

        # Inject some human-review cases.
        if idx % 11 == 0:
            decision = "ESCALATE"
            reason = (
                "Evidence was incomplete or conflicting and "
                "required human judgement."
            )

        cases.append(
            {
                "case_id": f"CASE-{1800 + idx}",
                "order_id": order["order_id"],
                "customer_type": customer["customer_type"],
                "customer_profile": (
                    f"{customer['previous_orders']} previous orders; "
                    f"{customer['previous_rto']} RTO; "
                    f"{customer['successful_cod_orders']} successful COD"
                ),
                "pincode": order["pincode"],
                "courier": order["courier_id"],
                "order_value": order["order_value"],
                "signals": " | ".join(signals),
                "counter_evidence": " | ".join(counter_evidence),
                "decision": decision,
                "actual_outcome": (
                    "RTO"
                    if order["rto"] == 1
                    else "DELIVERED"
                ),
                "reason": reason,
                "root_cause": (
                    "CUSTOMER_BEHAVIOUR"
                    if customer["previous_rto"] > 1
                    else "OPERATIONAL"
                ),
            }
        )

    return pd.DataFrame(cases)


# ============================================================
# 9. AGGREGATE PINCODE / COURIER STATISTICS
# ============================================================

def add_realized_statistics(
    orders_df,
    pincode_df,
    courier_df,
    courier_pincode_df,
):
    """
    Calculate realized statistics from the generated orders.

    These are the statistics your future agent can consume.

    Note:
    Because only 500 orders are generated, these realized rates
    will not exactly equal the underlying profile rates.
    """

    orders = orders_df.copy()
    orders["order_date"] = pd.to_datetime(orders["order_date"])

    # --------------------------------------------------------
    # Pincode realized stats
    # --------------------------------------------------------

    for pincode in PINCODES:

        subset = orders[
            orders["pincode"] == pincode
        ]

        if len(subset) == 0:
            continue

        pincode_df.loc[
            pincode_df["pincode"] == pincode,
            "total_orders"
        ] = len(subset)

        pincode_df.loc[
            pincode_df["pincode"] == pincode,
            "total_cod_orders"
        ] = len(subset)

        pincode_df.loc[
            pincode_df["pincode"] == pincode,
            "successful_deliveries"
        ] = int((subset["rto"] == 0).sum())

        pincode_df.loc[
            pincode_df["pincode"] == pincode,
            "rto_orders"
        ] = int(subset["rto"].sum())

    # --------------------------------------------------------
    # Courier realized stats
    # --------------------------------------------------------

    for courier in COURIERS:

        subset = orders[
            orders["courier_id"] == courier
        ]

        if len(subset) == 0:
            continue

        courier_df.loc[
            courier_df["courier_id"] == courier,
            "total_shipments"
        ] = len(subset)

        courier_df.loc[
            courier_df["courier_id"] == courier,
            "cod_shipments"
        ] = len(subset)

        courier_df.loc[
            courier_df["courier_id"] == courier,
            "rto_count"
        ] = int(subset["rto"].sum())

    # --------------------------------------------------------
    # Courier × Pincode realized stats
    # --------------------------------------------------------

    for idx, row in courier_pincode_df.iterrows():

        subset = orders[
            (orders["courier_id"] == row["courier_id"])
            & (orders["pincode"] == row["pincode"])
        ]

        courier_pincode_df.loc[
            idx,
            "total_orders"
        ] = len(subset)

        courier_pincode_df.loc[
            idx,
            "successful_orders"
        ] = int((subset["rto"] == 0).sum())

        courier_pincode_df.loc[
            idx,
            "rto_orders"
        ] = int(subset["rto"].sum())

    return (
        pincode_df,
        courier_df,
        courier_pincode_df,
    )


# ============================================================
# 10. ADD MISSING / STALE DATA CURVEBALLS
# ============================================================

def inject_data_quality_issues(
    orders_df,
    courier_pincode_df,
):
    """
    Add deliberate data-quality issues.

    These are essential for testing whether the agent knows
    what it does not know.
    """

    # Missing courier performance.
    missing_cp_index = courier_pincode_df[
        (courier_pincode_df["courier_id"] == "C04")
        & (courier_pincode_df["pincode"] == "560014")
    ].index

    if len(missing_cp_index):
        idx = missing_cp_index[0]

        courier_pincode_df.loc[
            idx,
            [
                "rto_rate_7d",
                "rto_rate_30d",
                "rto_rate_90d",
                "ndr_rate",
            ]
        ] = np.nan

        courier_pincode_df.loc[
            idx,
            "data_quality"
        ] = "MISSING"

    # Mark some records as stale.
    stale_index = courier_pincode_df[
        (courier_pincode_df["courier_id"] == "C02")
        & (courier_pincode_df["pincode"] == "560015")
    ].index

    if len(stale_index):
        idx = stale_index[0]

        courier_pincode_df.loc[
            idx,
            "data_quality"
        ] = "STALE"

        courier_pincode_df.loc[
            idx,
            "data_age_days"
        ] = 45

    return orders_df, courier_pincode_df


# ============================================================
# 10b. FORCE SCENARIO SIGNATURES
# ============================================================

def inject_scenario_signatures(
    customers_df,
    courier_pincode_df,
    historical_cases_df,
):
    """
    Force the operational statistics behind each special-case order to
    actually match what its scenario name promises.

    Random assignment alone does not reliably produce "this lane has
    a tiny sample" or "this lane has no historical precedent" - those
    are deliberate constructions, and the jury explicitly expects us
    to be able to defend how synthetic curveballs were built.
    """

    def set_cp(courier_id, pincode, **fields):
        idx = courier_pincode_df[
            (courier_pincode_df["courier_id"] == courier_id)
            & (courier_pincode_df["pincode"] == pincode)
        ].index
        if len(idx):
            for key, value in fields.items():
                courier_pincode_df.loc[idx[0], key] = value

    def has_precedent(pincode, courier_id):
        return bool((
            (historical_cases_df["pincode"] == pincode)
            & (historical_cases_df["courier"] == courier_id)
        ).any())

    def ensure_precedent(pincode, courier_id, decision="RELEASE"):
        if has_precedent(pincode, courier_id):
            return
        historical_cases_df.loc[len(historical_cases_df)] = {
            "case_id": f"CASE-{9000 + len(historical_cases_df)}",
            "order_id": f"SEED-{pincode}-{courier_id}",
            "customer_type": "LOYAL",
            "customer_profile": "Seeded precedent case for this lane",
            "pincode": pincode,
            "courier": courier_id,
            "order_value": 999,
            "signals": "NO_STRONG_RISK_SIGNAL",
            "counter_evidence": (
                "SUCCESSFUL_COD_HISTORY | ADDRESS_VERIFIED | PHONE_VERIFIED"
            ),
            "decision": decision,
            "actual_outcome": "DELIVERED" if decision == "RELEASE" else "RTO",
            "reason": (
                "Historical investigation found sufficient evidence "
                "supporting delivery."
                if decision == "RELEASE"
                else "Historical investigation identified meaningful "
                "RTO risk before the order was completed."
            ),
            "root_cause": "OPERATIONAL",
        }

    def remove_precedent(pincode, courier_id):
        keep = ~(
            (historical_cases_df["pincode"] == pincode)
            & (historical_cases_df["courier"] == courier_id)
        )
        return historical_cases_df[keep].reset_index(drop=True)

    # --- CLEAR_SAFE: 560001 x C03 - healthy, well-established lane. ---
    set_cp(
        "C03", "560001",
        total_orders=15, successful_orders=14, rto_orders=1,
        rto_rate_7d=0.05, rto_rate_30d=0.06, rto_rate_90d=0.06,
        trend="STABLE",
    )
    ensure_precedent("560001", "C03", decision="RELEASE")

    # --- CLEAR_RISKY: 560004 x C01 - clean lane; risk is customer-only. ---
    set_cp(
        "C01", "560004",
        total_orders=12, successful_orders=11, rto_orders=1,
        rto_rate_7d=0.05, rto_rate_30d=0.06, rto_rate_90d=0.06,
        trend="STABLE",
    )
    ensure_precedent("560004", "C01", decision="RELEASE")

    risky_idx = customers_df[
        customers_df["customer_type"] == "RISKY"
    ].index[0]
    customers_df.loc[risky_idx, "previous_orders"] = 8
    customers_df.loc[risky_idx, "previous_rto"] = 3
    customers_df.loc[risky_idx, "previous_cod_orders"] = 6
    customers_df.loc[risky_idx, "successful_cod_orders"] = 3
    customers_df.loc[risky_idx, "failed_cod_orders"] = 3

    # --- NEW_EVERYTHING: 560100 x C04 - thin on every dimension. ---
    set_cp(
        "C04", "560100",
        total_orders=2, successful_orders=2, rto_orders=0,
        rto_rate_7d=0.10, rto_rate_30d=0.10, rto_rate_90d=0.10,
        trend="STABLE",
    )
    historical_cases_df = remove_precedent("560100", "C04")

    # --- LOW_SAMPLE_SPIKE: 560009 x C02 - a scary rate on a tiny base. ---
    set_cp(
        "C02", "560009",
        total_orders=3, successful_orders=1, rto_orders=2,
        rto_rate_7d=0.33, rto_rate_30d=0.08, rto_rate_90d=0.05,
        trend="DETERIORATING",
    )
    ensure_precedent("560009", "C02", decision="RELEASE")

    # --- NO_HISTORICAL_PRECEDENT: 560012 x C01 - decent volume, but this
    # --- exact lane has never been formally investigated before.
    set_cp(
        "C01", "560012",
        total_orders=6, successful_orders=6, rto_orders=0,
        rto_rate_7d=0.06, rto_rate_30d=0.06, rto_rate_90d=0.06,
        trend="STABLE",
    )
    historical_cases_df = remove_precedent("560012", "C01")

    # --- CONFLICTING_SIGNALS: 560068 x C01 - risky pincode, clean courier. ---
    set_cp(
        "C01", "560068",
        total_orders=10, successful_orders=8, rto_orders=2,
        rto_rate_7d=0.22, rto_rate_30d=0.11, rto_rate_90d=0.08,
        trend="DETERIORATING",
    )
    ensure_precedent("560068", "C01", decision="HOLD")

    return customers_df, courier_pincode_df, historical_cases_df


# ============================================================
# 11. FINALISE DATA
# ============================================================

def finalise_data(
    customers_df,
    orders_df,
    pincode_df,
    courier_df,
    courier_pincode_df,
    delivery_attempts_df,
    events_df,
    historical_cases_df,
):
    """
    Clean types, add derived statistics and save CSVs.
    """

    # --------------------------------------------------------
    # Pincode derived fields
    # --------------------------------------------------------

    for df, total_col, rto_col, rate_col in [
        (
            pincode_df,
            "total_orders",
            "rto_orders",
            "rto_rate",
        ),
        (
            courier_df,
            "total_shipments",
            "rto_count",
            "rto_rate",
        ),
    ]:

        if total_col in df.columns:

            df[rate_col] = np.where(
                df[total_col] > 0,
                df[rto_col] / df[total_col],
                np.nan,
            )

    # --------------------------------------------------------
    # Courier × Pincode
    # --------------------------------------------------------

    courier_pincode_df["rto_rate"] = np.where(
        courier_pincode_df["total_orders"] > 0,
        courier_pincode_df["rto_orders"]
        / courier_pincode_df["total_orders"],
        np.nan,
    )

    # --------------------------------------------------------
    # Derived trends
    # --------------------------------------------------------

    pincode_df["recent_change"] = (
        pincode_df["rto_rate_7d"]
        - pincode_df["rto_rate_30d"]
    )

    courier_df["recent_change"] = (
        courier_df["rto_rate_7d"]
        - courier_df["rto_rate_30d"]
    )

    courier_pincode_df["recent_change"] = (
        courier_pincode_df["rto_rate_7d"]
        - courier_pincode_df["rto_rate_30d"]
    )

    # --------------------------------------------------------
    # Data freshness
    # --------------------------------------------------------

    if "data_age_days" not in courier_pincode_df.columns:
        courier_pincode_df["data_age_days"] = 1

    if "data_quality" not in courier_pincode_df.columns:
        courier_pincode_df["data_quality"] = "GOOD"

    # --------------------------------------------------------
    # Save
    # --------------------------------------------------------

    customers_df.to_csv(
        os.path.join(OUTPUT_DIR, "customers.csv"),
        index=False,
    )

    orders_df.to_csv(
        os.path.join(OUTPUT_DIR, "orders.csv"),
        index=False,
    )

    pincode_df.to_csv(
        os.path.join(OUTPUT_DIR, "pincodes.csv"),
        index=False,
    )

    courier_df.to_csv(
        os.path.join(OUTPUT_DIR, "couriers.csv"),
        index=False,
    )

    courier_pincode_df.to_csv(
        os.path.join(OUTPUT_DIR, "courier_pincode.csv"),
        index=False,
    )

    delivery_attempts_df.to_csv(
        os.path.join(OUTPUT_DIR, "delivery_attempts.csv"),
        index=False,
    )

    events_df.to_csv(
        os.path.join(OUTPUT_DIR, "events.csv"),
        index=False,
    )

    historical_cases_df.to_csv(
        os.path.join(OUTPUT_DIR, "historical_cases.csv"),
        index=False,
    )


# ============================================================
# 12. MAIN
# ============================================================

def main():

    print("=" * 70)
    print("QUESSATHON - FIND THE SIGNAL")
    print("Synthetic COD/RTO data generator")
    print("=" * 70)

    ensure_output_dir()

    print("\n[1/8] Creating pincode profiles...")
    pincode_df = create_pincode_profiles()

    print("[2/8] Creating courier profiles...")
    courier_df = create_courier_profiles()

    print("[3/8] Creating courier x pincode profiles...")
    courier_pincode_df = create_courier_pincode_profiles(
        pincode_df,
        courier_df,
    )

    print("[4/8] Creating customers...")
    customers_df = create_customers()

    print("[5/8] Creating interconnected orders...")
    orders_df = create_orders(
        customers_df,
        pincode_df,
        courier_df,
        courier_pincode_df,
    )

    print("[6/8] Creating delivery attempts and events...")
    delivery_attempts_df = create_delivery_attempts(
        orders_df
    )

    events_df = create_events()

    print("[7/8] Creating historical investigation cases...")
    historical_cases_df = create_historical_cases(
        orders_df,
        customers_df,
        pincode_df,
        courier_df,
        courier_pincode_df,
    )

    print("[8/8] Adding realized statistics and data-quality issues...")

    (
        pincode_df,
        courier_df,
        courier_pincode_df,
    ) = add_realized_statistics(
        orders_df,
        pincode_df,
        courier_df,
        courier_pincode_df,
    )

    (
        orders_df,
        courier_pincode_df,
    ) = inject_data_quality_issues(
        orders_df,
        courier_pincode_df,
    )

    (
        customers_df,
        courier_pincode_df,
        historical_cases_df,
    ) = inject_scenario_signatures(
        customers_df,
        courier_pincode_df,
        historical_cases_df,
    )

    finalise_data(
        customers_df,
        orders_df,
        pincode_df,
        courier_df,
        courier_pincode_df,
        delivery_attempts_df,
        events_df,
        historical_cases_df,
    )

    # ========================================================
    # SUMMARY
    # ========================================================

    print("\n" + "=" * 70)
    print("DATASET GENERATED")
    print("=" * 70)

    print(f"\nCustomers       : {len(customers_df)}")
    print(f"Orders          : {len(orders_df)}")
    print(f"Pincodes        : {len(pincode_df)}")
    print(f"Couriers        : {len(courier_df)}")
    print(f"Courier x Pincode : {len(courier_pincode_df)}")
    print(f"Delivery events : {len(delivery_attempts_df)}")
    print(f"Context events  : {len(events_df)}")
    print(f"Historical cases: {len(historical_cases_df)}")

    print("\nOverall RTO:")
    print(
        f"  {orders_df['rto'].mean() * 100:.2f}%"
    )

    print("\nImportant pincode:")
    print(
        pincode_df[
            pincode_df["pincode"] == "560068"
        ][
            [
                "pincode",
                "rto_rate_90d",
                "rto_rate_30d",
                "rto_rate_7d",
                "trend",
            ]
        ].to_string(index=False)
    )

    print("\nImportant courier:")
    print(
        courier_df[
            courier_df["courier_id"] == "C07"
        ][
            [
                "courier_id",
                "rto_rate_90d",
                "rto_rate_30d",
                "rto_rate_7d",
                "trend",
            ]
        ].to_string(index=False)
    )

    print("\nImportant courier x pincode:")
    print(
        courier_pincode_df[
            (courier_pincode_df["courier_id"] == "C07")
            & (courier_pincode_df["pincode"] == "560068")
        ][
            [
                "courier_id",
                "pincode",
                "rto_rate_90d",
                "rto_rate_30d",
                "rto_rate_7d",
                "trend",
            ]
        ].to_string(index=False)
    )

    print("\nSpecial scenarios:")
    print(
        orders_df[
            orders_df["scenario"] != "NORMAL"
        ][
            [
                "order_id",
                "scenario",
                "customer_id",
                "pincode",
                "courier_id",
                "order_value",
                "rto",
            ]
        ].to_string(index=False)
    )

    print("\nFiles written to:")
    print(f"  {OUTPUT_DIR}/")

    print("\nNext step:")
    print("  Build analytics.py")
    print("  DO NOT build the LLM agent yet.")


if __name__ == "__main__":
    main()