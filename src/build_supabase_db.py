"""
Quessathon 2026 - Retail Challenge 01: Find the Signal

Loads the generated CSVs into a shared Supabase (hosted Postgres)
database, so the whole team queries the same data instead of each
person maintaining their own local DB.

Same relationship to the CSVs as build_db.py (SQLite) and
build_mysql_db.py: the CSVs in data/generated/ are the source of
truth, this just loads them into the database of choice - here, one
the whole team can reach.

Not runnable yet without a connection string. Once the team's
Supabase project exists, set SUPABASE_DB_URL to its connection
string (Supabase dashboard -> Project Settings -> Database ->
Connection string -> URI, "Transaction pooler" mode recommended for
short-lived scripts like this one) and run:

    set SUPABASE_DB_URL=postgresql://postgres.xxxx:PASSWORD@aws-...pooler.supabase.com:6543/postgres
    python src/build_supabase_db.py

Never commit that connection string (it contains the DB password) -
set it as an environment variable or in a local .env file, both of
which are already gitignored.
"""

from __future__ import annotations

import os
import sys

import pandas as pd
from sqlalchemy import create_engine, text

DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "generated",
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

# Postgres, unlike MySQL, indexes TEXT columns without needing a
# prefix length, so this list is simpler than build_mysql_db.py's.
INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders(customer_id)",
    "CREATE INDEX IF NOT EXISTS idx_orders_pincode ON orders(pincode)",
    "CREATE INDEX IF NOT EXISTS idx_orders_courier ON orders(courier_id)",
    "CREATE INDEX IF NOT EXISTS idx_cp_lookup ON courier_pincode(courier_id, pincode)",
    "CREATE INDEX IF NOT EXISTS idx_delivery_order ON delivery_attempts(order_id)",
    "CREATE INDEX IF NOT EXISTS idx_hist_lookup ON historical_cases(courier, pincode)",
]


def get_engine():
    url = os.environ.get("SUPABASE_DB_URL")
    if not url:
        raise RuntimeError(
            "SUPABASE_DB_URL is not set. Get the connection string from "
            "the Supabase dashboard (Project Settings -> Database -> "
            "Connection string -> URI) and set it as an environment "
            "variable before running this script."
        )
    # SQLAlchemy wants the postgresql:// scheme (Supabase's own URIs
    # already use it, but normalize postgres:// just in case).
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return create_engine(url)


def build_db(rebuild: bool = True) -> None:
    engine = get_engine()
    with engine.connect() as conn:
        for table, filename in TABLES.items():
            path = os.path.join(DATA_DIR, filename)
            df = pd.read_csv(path)
            if_exists = "replace" if rebuild else "append"
            df.to_sql(table, conn, if_exists=if_exists, index=False)

        for stmt in INDEXES:
            conn.execute(text(stmt))

        conn.commit()
    engine.dispose()


def main():
    print("=" * 70)
    print("QUESSATHON - FIND THE SIGNAL")
    print("Loading generated CSVs into shared Supabase database")
    print("=" * 70)

    try:
        build_db(rebuild=True)
    except RuntimeError as e:
        print(f"\n{e}")
        sys.exit(1)

    engine = get_engine()
    with engine.connect() as conn:
        print("\nTable row counts:")
        for table in TABLES:
            count = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
            print(f"  {table:20} : {count}")

        print(
            "\nSmoke test - join query (order + customer + courier x pincode "
            "lane for the flagship anomaly order):"
        )
        row = conn.execute(
            text(
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
            )
        ).fetchone()
        print(f"  {row}")
    engine.dispose()


if __name__ == "__main__":
    main()
