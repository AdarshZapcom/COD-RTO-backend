"""
Quessathon 2026 - Retail Challenge 01: Find the Signal

Escalation ticketing.

`investigate_order()` already produces an authoritative decision, but
until now a HOLD_FOR_VERIFICATION / ESCALATE call only ever reached a
human via `print_decision()` on stdout - there was no artifact an ops
reviewer could actually open, work, and close out. This module is
that artifact: one row per non-RELEASE investigation in a new
`escalation_tickets` table in Supabase.

Plain CRUD, no new decision logic - everything written here is already
present on the `InvestigationReport` produced by decision_engine.decide()
plus case_memory/narrative. `psycopg2` + `SUPABASE_DB_URL` is used for
consistency with `supabase_setup.py`.

The schema is created with `CREATE TABLE IF NOT EXISTS`, so re-running
this module's `ensure_schema()` (e.g. via `python src/ticketing.py`) is
always safe and never touches existing rows.

Usage:
    python src/ticketing.py     # creates escalation_tickets if missing
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

if TYPE_CHECKING:
    from investigation_agent import InvestigationReport

load_dotenv()

DB_URL = os.environ.get("SUPABASE_DB_URL")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS escalation_tickets (
    ticket_id            text PRIMARY KEY,
    order_id             text REFERENCES orders(order_id),
    decision             text,              -- HOLD_FOR_VERIFICATION | ESCALATE
    risk_level           text,
    confidence           double precision,
    reason               text,
    supporting_evidence  text,             -- joined with " | "
    counter_evidence     text,
    uncertainty_flags    text,
    narrative            text,
    created_at           timestamptz DEFAULT now(),
    status               text DEFAULT 'OPEN',   -- OPEN | RESOLVED
    resolved_by          text,
    resolution_note      text,
    resolved_at          timestamptz
);
"""


def _get_conn():
    if not DB_URL:
        raise RuntimeError("SUPABASE_DB_URL not set in .env - cannot connect.")
    return psycopg2.connect(DB_URL, connect_timeout=10)


def ensure_schema() -> None:
    """Create the escalation_tickets table if it does not already exist."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()


def create_ticket(report: "InvestigationReport") -> Optional[str]:
    """
    Insert one escalation ticket row for a non-RELEASE investigation
    report and return its ticket_id.

    No-op (returns None) when report.decision == "RELEASE" - a released
    order never needs a human reviewer.

    ticket_id is deterministic (f"TCK-{order_id}"), so re-investigating
    the same order upserts the same row instead of raising a duplicate
    key error - the decision/evidence fields are refreshed, the OPEN/
    RESOLVED workflow state (status, resolved_*) is left alone.
    """

    if report.decision == "RELEASE":
        return None

    ticket_id = f"TCK-{report.order_id}"

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO escalation_tickets (
                    ticket_id, order_id, decision, risk_level, confidence,
                    reason, supporting_evidence, counter_evidence,
                    uncertainty_flags, narrative
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (ticket_id) DO UPDATE SET
                    decision            = EXCLUDED.decision,
                    risk_level          = EXCLUDED.risk_level,
                    confidence          = EXCLUDED.confidence,
                    reason              = EXCLUDED.reason,
                    supporting_evidence = EXCLUDED.supporting_evidence,
                    counter_evidence    = EXCLUDED.counter_evidence,
                    uncertainty_flags   = EXCLUDED.uncertainty_flags,
                    narrative           = EXCLUDED.narrative
                """,
                (
                    ticket_id,
                    report.order_id,
                    report.decision,
                    report.risk_level,
                    report.confidence,
                    report.reason,
                    " | ".join(report.supporting_evidence),
                    " | ".join(report.counter_evidence),
                    " | ".join(report.uncertainty_flags),
                    report.narrative,
                ),
            )
        conn.commit()
    finally:
        conn.close()

    return ticket_id


def list_open_tickets() -> List[Dict[str, Any]]:
    """All OPEN tickets, most recently created first."""
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM escalation_tickets "
                "WHERE status = 'OPEN' "
                "ORDER BY created_at DESC"
            )
            rows = cur.fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def resolve_ticket(ticket_id: str, resolved_by: str, resolution_note: str) -> bool:
    """
    Mark a ticket RESOLVED with who closed it and why.

    Returns True if a ticket with this id existed and was updated,
    False if no row matched ticket_id - lets a caller (e.g. the API
    layer) distinguish "no such ticket" (404) from a successful
    resolve without an extra round trip.
    """
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE escalation_tickets
                SET status = 'RESOLVED',
                    resolved_by = %s,
                    resolution_note = %s,
                    resolved_at = now()
                WHERE ticket_id = %s
                """,
                (resolved_by, resolution_note, ticket_id),
            )
            updated = cur.rowcount > 0
        conn.commit()
        return updated
    finally:
        conn.close()


def main():
    print("=" * 70)
    print("QUESSATHON - FIND THE SIGNAL")
    print("Escalation ticketing - schema setup")
    print("=" * 70)

    print("\nCreating escalation_tickets (IF NOT EXISTS) ...")
    ensure_schema()
    print("Done.")


if __name__ == "__main__":
    main()
