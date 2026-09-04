"""
Quessathon 2026 - Retail Challenge 01: Find the Signal

Loads the generated CSVs into a local MySQL database.

Same relationship to the CSVs as build_db.py (SQLite) - this is a
derived artifact, rebuilt from data/generated/*.csv, not something
committed to git. Use this one instead of build_db.py when a real
client/server database is wanted for the demo (e.g. to show
concurrent access, a proper schema in MySQL Workbench, or because
the team standardized on MySQL) rather than SQLite's single-file
engine.

Connection defaults assume a local MySQL server with no root
password (as set up for this project's dev environment). Override
via environment variables if your setup differs:
    MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DB
"""

from __future__ import annotations

import os

import pandas as pd
from sqlalchemy import create_engine, text

DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "generated",
)

MYSQL_HOST = os.environ.get("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = os.environ.get("MYSQL_PORT", "3306")
MYSQL_USER = os.environ.get("MYSQL_USER", "root")
MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD", "")
MYSQL_DB = os.environ.get("MYSQL_DB", "find_signal")

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
    # pincode is stored as an integer column - no prefix length needed
    # (and not allowed) there; only the free-text id columns need one,
    # since pandas.to_sql creates them as unbounded TEXT/BLOB in MySQL.
    "CREATE INDEX idx_orders_customer ON orders(customer_id(20))",
    "CREATE INDEX idx_orders_pincode ON orders(pincode)",
    "CREATE INDEX idx_orders_courier ON orders(courier_id(10))",
    "CREATE INDEX idx_cp_lookup ON courier_pincode(courier_id(10), pincode)",
    "CREATE INDEX idx_delivery_order ON delivery_attempts(order_id(20))",
    "CREATE INDEX idx_hist_lookup ON historical_cases(courier(10), pincode)",
]


def get_engine(database: str | None = MYSQL_DB):
    url = (
        f"mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}"
        f"@{MYSQL_HOST}:{MYSQL_PORT}"
        + (f"/{database}" if database else "")
    )
    return create_engine(url)


def build_db(rebuild: bool = True) -> str:
    """
    (Re)build the MySQL database from the generated CSVs.
    Returns a connection string (password redacted) for logging.
    """

    server_engine = get_engine(database=None)
    with server_engine.connect() as conn:
        if rebuild:
            conn.execute(text(f"DROP DATABASE IF EXISTS `{MYSQL_DB}`"))
        conn.execute(text(f"CREATE DATABASE IF NOT EXISTS `{MYSQL_DB}`"))
        conn.commit()
    server_engine.dispose()

    engine = get_engine()
    with engine.connect() as conn:
        for table, filename in TABLES.items():
            path = os.path.join(DATA_DIR, filename)
            df = pd.read_csv(path)
            df.to_sql(table, conn, if_exists="replace", index=False)

        for stmt in INDEXES:
            conn.execute(text(stmt))

        conn.commit()
    engine.dispose()

    return f"mysql://{MYSQL_USER}@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DB}"


def main():
    print("=" * 70)
    print("QUESSATHON - FIND THE SIGNAL")
    print("Building local MySQL database from generated CSVs")
    print("=" * 70)

    conn_str = build_db(rebuild=True)
    print(f"\nDatabase ready: {conn_str}")

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
