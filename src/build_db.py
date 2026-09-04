"""
Quessathon 2026 - Retail Challenge 01: Find the Signal

Loads the generated CSVs into a local SQLite database.

This is a derived artifact, rebuilt from data/generated/*.csv - same
relationship the Chroma vector index has to historical_cases.csv (see
case_memory.py). It is not committed to git; run this script whenever
you need it. It exists so the tabular data can be queried directly
with SQL (joins across customer/order/pincode/courier) instead of only
through pandas, and so a real database - not just flat files - backs
the "system of record" story for the demo.
"""

from __future__ import annotations

import os
import sqlite3

import pandas as pd

DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "generated",
)

DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "find_signal.db",
)

TABLES = {
    "customers": "customers.csv",
    "orders": "orders.csv",
    "pincodes": "pincodes.csv",
    "couriers": "couriers.csv",
    "courier_pincode": "courier_pincode.csv",
    "delivery_attempts": "delivery_attempts.csv",
    "events": "events.csv",
    "historical_cases": "historical_cases.csv",
}

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders(customer_id)",
    "CREATE INDEX IF NOT EXISTS idx_orders_pincode ON orders(pincode)",
    "CREATE INDEX IF NOT EXISTS idx_orders_courier ON orders(courier_id)",
    "CREATE INDEX IF NOT EXISTS idx_cp_lookup ON courier_pincode(courier_id, pincode)",
    "CREATE INDEX IF NOT EXISTS idx_delivery_order ON delivery_attempts(order_id)",
    "CREATE INDEX IF NOT EXISTS idx_hist_lookup ON historical_cases(courier, pincode)",
]


def build_db(rebuild: bool = True) -> str:
    """
    (Re)build data/find_signal.db from the generated CSVs.
    Returns the DB path.
    """

    if rebuild and os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    conn = sqlite3.connect(DB_PATH)

    try:
        for table, filename in TABLES.items():
            path = os.path.join(DATA_DIR, filename)
            df = pd.read_csv(path)
            df.to_sql(table, conn, if_exists="replace", index=False)

        for stmt in INDEXES:
            conn.execute(stmt)

        conn.commit()
    finally:
        conn.close()

    return DB_PATH


def main():
    print("=" * 70)
    print("QUESSATHON - FIND THE SIGNAL")
    print("Building local SQLite database from generated CSVs")
    print("=" * 70)

    path = build_db(rebuild=True)
    print(f"\nDatabase written to: {path}")

    conn = sqlite3.connect(path)
    try:
        cur = conn.cursor()

        print("\nTable row counts:")
        for table in TABLES:
            count = cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"  {table:20} : {count}")

        print("\nSmoke test - join query (order + customer + courier x pincode "
              "lane for the flagship anomaly order):")
        row = cur.execute(
            """
            SELECT o.order_id, o.pincode, o.courier_id,
                   c.customer_type, c.previous_orders, c.previous_rto,
                   cp.rto_rate_7d, cp.rto_rate_30d, cp.trend
            FROM orders o
            JOIN customers c ON c.customer_id = o.customer_id
            JOIN courier_pincode cp
                 ON cp.courier_id = o.courier_id AND cp.pincode = o.pincode
            WHERE o.order_id = 'ORD-10003'
            """
        ).fetchone()
        print(f"  {row}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
