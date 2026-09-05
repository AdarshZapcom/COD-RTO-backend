"""
Quessathon 2026 - Retail Challenge 01: Find the Signal

Creates the Supabase (Postgres) schema and loads it from the generated
CSVs in data/generated/. This is the hosted-deployment counterpart to
build_db.py's local SQLite mirror - same tables, same relationships,
now backed by a real managed Postgres instance instead of a local file.

Requires SUPABASE_DB_URL in .env (a direct Postgres connection string -
the Data API keys cannot run DDL; see docs/research.md).

Usage:
    python src/supabase_setup.py
"""

from __future__ import annotations

import os

import pandas as pd
import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import execute_values

load_dotenv()

DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "generated",
)

DB_URL = os.environ.get("SUPABASE_DB_URL")

SCHEMA_SQL = """
DROP TABLE IF EXISTS delivery_attempts CASCADE;
DROP TABLE IF EXISTS historical_cases CASCADE;
DROP TABLE IF EXISTS events CASCADE;
DROP TABLE IF EXISTS orders CASCADE;
DROP TABLE IF EXISTS courier_pincode CASCADE;
DROP TABLE IF EXISTS couriers CASCADE;
DROP TABLE IF EXISTS pincodes CASCADE;
DROP TABLE IF EXISTS customers CASCADE;

CREATE TABLE customers (
    customer_id             text PRIMARY KEY,
    account_age_days        integer,
    previous_orders         integer,
    successful_orders       integer,
    previous_rto            integer,
    previous_cod_orders     integer,
    successful_cod_orders   integer,
    failed_cod_orders       integer,
    previous_cancellations  integer,
    phone_verified          boolean,
    address_verified        boolean,
    customer_type           text
);

CREATE TABLE pincodes (
    pincode                 text PRIMARY KEY,
    baseline_rto_rate       double precision,
    rto_rate_90d            double precision,
    rto_rate_30d            double precision,
    rto_rate_7d             double precision,
    avg_delivery_days       double precision,
    ndr_rate                double precision,
    trend                   text,
    total_orders            double precision,
    total_cod_orders        double precision,
    successful_deliveries   double precision,
    rto_orders              double precision,
    rto_rate                double precision,
    recent_change           double precision
);

CREATE TABLE couriers (
    courier_id              text PRIMARY KEY,
    baseline_rto_rate       double precision,
    rto_rate_90d            double precision,
    rto_rate_30d            double precision,
    rto_rate_7d             double precision,
    avg_delivery_days       double precision,
    ndr_rate                double precision,
    trend                   text,
    total_shipments         double precision,
    cod_shipments           double precision,
    rto_count               double precision,
    rto_rate                double precision,
    recent_change           double precision
);

CREATE TABLE courier_pincode (
    courier_id              text REFERENCES couriers(courier_id),
    pincode                 text REFERENCES pincodes(pincode),
    rto_rate_90d            double precision,
    rto_rate_30d            double precision,
    rto_rate_7d             double precision,
    avg_delivery_days       double precision,
    ndr_rate                double precision,
    trend                   text,
    total_orders            double precision,
    successful_orders       double precision,
    rto_orders               double precision,
    data_quality            text,
    data_age_days           double precision,
    rto_rate                double precision,
    recent_change           double precision,
    PRIMARY KEY (courier_id, pincode)
);

CREATE TABLE orders (
    order_id                    text PRIMARY KEY,
    customer_id                 text REFERENCES customers(customer_id),
    order_date                  date,
    pincode                     text REFERENCES pincodes(pincode),
    courier_id                  text REFERENCES couriers(courier_id),
    seller_id                   text,
    product_category             text,
    order_value                 integer,
    cod_amount                  integer,
    payment_type                 text,
    is_first_order               boolean,
    address_verified             boolean,
    order_status                 text,
    rto                          integer,
    synthetic_rto_probability    double precision,
    scenario                     text
);

CREATE TABLE delivery_attempts (
    attempt_id               text PRIMARY KEY,
    order_id                 text REFERENCES orders(order_id),
    attempt_number            integer,
    attempt_date              date,
    attempt_status            text,
    failure_reason            text,
    customer_contacted        boolean,
    rescheduled               boolean,
    delivery_agent_note       text
);

CREATE TABLE events (
    event_id                 text PRIMARY KEY,
    date                     date,
    pincode                  text,
    courier_id               text,
    event_type               text,
    severity                 text,
    description              text
);

CREATE TABLE historical_cases (
    case_id                  text PRIMARY KEY,
    order_id                 text,
    customer_type            text,
    customer_profile         text,
    pincode                  text,
    courier                  text,
    order_value              integer,
    signals                  text,
    counter_evidence         text,
    decision                 text,
    actual_outcome           text,
    reason                   text,
    root_cause               text
);

CREATE INDEX idx_orders_customer ON orders(customer_id);
CREATE INDEX idx_orders_pincode ON orders(pincode);
CREATE INDEX idx_orders_courier ON orders(courier_id);
CREATE INDEX idx_cp_lookup ON courier_pincode(courier_id, pincode);
CREATE INDEX idx_delivery_order ON delivery_attempts(order_id);
CREATE INDEX idx_hist_lookup ON historical_cases(courier, pincode);
"""

TABLES = [
    "customers",
    "pincodes",
    "couriers",
    "courier_pincode",
    "orders",
    "delivery_attempts",
    "events",
    "historical_cases",
]


def load_csv(conn, table: str) -> int:
    path = os.path.join(DATA_DIR, f"{table}.csv")
    df = pd.read_csv(path)
    df = df.where(pd.notna(df), None)

    columns = list(df.columns)
    values = [tuple(row) for row in df.itertuples(index=False, name=None)]

    with conn.cursor() as cur:
        query = (
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES %s"
        )
        execute_values(cur, query, values, page_size=500)

    return len(values)


def main():
    if not DB_URL:
        raise SystemExit(
            "SUPABASE_DB_URL not set in .env - cannot connect."
        )

    print("=" * 70)
    print("QUESSATHON - FIND THE SIGNAL")
    print("Supabase (Postgres) schema + load")
    print("=" * 70)

    conn = psycopg2.connect(DB_URL, connect_timeout=10)
    conn.autocommit = False

    try:
        print("\n[1/2] Creating schema (customers, pincodes, couriers, "
              "courier_pincode, orders, delivery_attempts, events, "
              "historical_cases)...")
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
        conn.commit()
        print("      Schema created.")

        print("\n[2/2] Loading CSVs from data/generated/ ...")
        for table in TABLES:
            n = load_csv(conn, table)
            conn.commit()
            print(f"      {table:20} : {n} rows loaded")

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    print("\nDone. Supabase now mirrors data/generated/*.csv.")


if __name__ == "__main__":
    main()
