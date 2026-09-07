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

`order_id` intentionally carries no foreign key to `orders(order_id)`.
Ad hoc investigations (`investigate_adhoc()`, synthetic
"ADHOC-<timestamp>" ids - see task 03/05) are a first-class input that
is never a row in `orders`, so a ticket must still be insertable for
one. `ensure_schema()` also drops that FK if an earlier deployment
already created it, so this is safe to rerun against any existing
table.

Usage:
    python src/ticketing.py     # creates escalation_tickets if missing
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

if TYPE_CHECKING:
    from investigation_agent import InvestigationReport

load_dotenv()

logger = logging.getLogger(__name__)

DB_URL = os.environ.get("SUPABASE_DB_URL")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS escalation_tickets (
    ticket_id            text PRIMARY KEY,
    order_id             text NOT NULL,
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
    resolution_outcome   text,             -- VERIFIED_RELEASE | RISKY_BLOCK_COD | ESCALATE_MANAGER
    resolved_at          timestamptz
);

-- Added after escalation_tickets already existed in deployed
-- environments - ADD COLUMN IF NOT EXISTS instead of relying on the
-- CREATE TABLE above (which only ever runs for a brand-new table).
ALTER TABLE escalation_tickets ADD COLUMN IF NOT EXISTS resolution_outcome text;

-- No FK from order_id to orders(order_id): an ad hoc investigation's
-- order_id (synthetic "ADHOC-<timestamp>") never exists in `orders`,
-- and it is still a first-class case that must be able to escalate.
-- Drop the FK in case an earlier deployment created this table before
-- ad hoc orders existed - IF EXISTS keeps this safe to rerun against
-- a table that never had the constraint.
ALTER TABLE escalation_tickets DROP CONSTRAINT IF EXISTS escalation_tickets_order_id_fkey;

-- list_tickets()/count_tickets() always filter WHERE status = %s, then
-- (for list_tickets) sort by created_at or resolved_at - without these,
-- a status with many rows (e.g. RESOLVED, which only grows) forces a
-- full-table sequential scan + sort on every call. Measured live: a
-- RESOLVED page took ~2.5s against a table with a few thousand rows
-- before these existed, most of it exactly this scan/sort.
CREATE INDEX IF NOT EXISTS idx_escalation_tickets_status_created
    ON escalation_tickets (status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_escalation_tickets_status_resolved
    ON escalation_tickets (status, resolved_at DESC);
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
    key error - the decision/evidence fields are refreshed AND the
    ticket is reopened (status reset to 'OPEN', resolved_by/
    resolution_note/resolved_at cleared). A fresh non-RELEASE decision
    is a new escalation event that needs sign-off, even if a prior
    escalation for this same order was already resolved - otherwise the
    row would silently stay RESOLVED and vanish from
    list_open_tickets()/GET /tickets?status=OPEN despite the report
    handed back to the caller carrying a live ticket_id.
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
                    narrative           = EXCLUDED.narrative,
                    status              = 'OPEN',
                    resolved_by         = NULL,
                    resolution_note     = NULL,
                    resolved_at         = NULL
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

    logger.info("ticket %s upserted for order %s (decision=%s)", ticket_id, report.order_id, report.decision)
    return ticket_id


def list_tickets(status: str = "OPEN", limit: int = 50, offset: int = 0) -> Tuple[List[Dict[str, Any]], int]:
    """
    Tickets in the given status, paginated, plus the total count for
    that status (for real numbered pagination in the UI, not just an
    infinite "load more" that never tells the operator how much is
    actually left) - OPEN ones most recently created first, RESOLVED
    ones most recently resolved first (an operator reviewing history
    cares about "what did I just close", not insertion order).

    One connection for both queries, not two: this used to be
    list_open_tickets()/count_open_tickets() as two separate functions,
    each opening its own connection - against the remote Supabase
    Postgres this app talks to, connection setup (not query execution;
    both queries are index-backed and fast) turned out to be the
    dominant cost, measured live at ~2.3s total for what should be a
    sub-second fetch. Halving the connection count roughly halves it.

    `status` is validated by the caller (see api.get_tickets) against a
    fixed allowlist, never interpolated directly into SQL.

    Added because a live event/stress-test run can accumulate hundreds
    of ad hoc tickets - rendering all of them unpaginated in the ops
    view is a real live-demo scroll/perf risk, not just a cosmetic
    concern. Defaults (limit=50) match GET /orders' own default so the
    paginated lists behave consistently.
    """
    order_column = "resolved_at" if status == "RESOLVED" else "created_at"
    conn = _get_conn()
    try:
        with conn.cursor() as count_cur:
            count_cur.execute("SELECT COUNT(*) FROM escalation_tickets WHERE status = %s", (status,))
            (total,) = count_cur.fetchone()

        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM escalation_tickets "
                "WHERE status = %s "
                f"ORDER BY {order_column} DESC "
                "LIMIT %s OFFSET %s",
                (status, limit, offset),
            )
            rows = cur.fetchall()
        return [dict(row) for row in rows], total
    finally:
        conn.close()


def resolve_ticket(ticket_id: str, resolved_by: str, resolution_note: str, resolution_outcome: str) -> bool:
    """
    Mark a ticket RESOLVED with who closed it, why, and what actually
    happened to the order (resolution_outcome - VERIFIED_RELEASE |
    RISKY_BLOCK_COD | ESCALATE_MANAGER).

    resolution_outcome is validated by the caller (see
    api.post_ticket_resolve) against a fixed allowlist, same convention
    as list_tickets' `status` - this function trusts it.

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
                    resolution_outcome = %s,
                    resolved_at = now()
                WHERE ticket_id = %s
                """,
                (resolved_by, resolution_note, resolution_outcome, ticket_id),
            )
            updated = cur.rowcount > 0
        conn.commit()
        if updated:
            logger.info("ticket %s resolved by %s (%s)", ticket_id, resolved_by, resolution_outcome)
        else:
            logger.warning("resolve attempted for unknown ticket %s", ticket_id)
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
