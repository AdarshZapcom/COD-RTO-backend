# Escalation Ticket / Handoff Record

The other explicitly required minimum capability with no code behind it:
*"route to a human reviewer instead of guessing."* Right now
`HOLD_FOR_VERIFICATION` and `ESCALATE` decisions just print to the console
via `print_decision()` — there's no actual artifact a human ops reviewer
would open and act on.

## What it does

Whenever `investigate_order()` returns a decision that isn't `RELEASE`,
write one row to a new `escalation_tickets` table in Supabase. This is
plain CRUD — no new logic beyond what `decide()` already produces.

## Schema (Supabase)

```sql
CREATE TABLE escalation_tickets (
    ticket_id          text PRIMARY KEY,
    order_id           text REFERENCES orders(order_id),
    decision           text,              -- HOLD_FOR_VERIFICATION | ESCALATE
    risk_level         text,
    confidence         double precision,
    reason             text,
    supporting_evidence text,             -- joined with " | "
    counter_evidence    text,
    uncertainty_flags   text,
    narrative           text,
    created_at          timestamptz DEFAULT now(),
    status               text DEFAULT 'OPEN',   -- OPEN | RESOLVED
    resolved_by          text,
    resolution_note      text,
    resolved_at           timestamptz
);
```

## Where it lives

New file: `src/ticketing.py`

```python
def create_ticket(report: InvestigationReport) -> str
    # inserts a row, returns ticket_id (e.g. f"TCK-{order_id}")
    # no-op / returns None if report.decision == "RELEASE"

def list_open_tickets() -> list[dict]
    # SELECT * WHERE status = 'OPEN' ORDER BY created_at DESC

def resolve_ticket(ticket_id: str, resolved_by: str, resolution_note: str) -> None
    # UPDATE status='RESOLVED', resolved_by, resolution_note, resolved_at=now()
```

Use `psycopg2` with `SUPABASE_DB_URL` for the insert/update (consistent
with `supabase_setup.py`), or the REST API with `SUPABASE_SECRET_KEY` —
either works once the table exists; `psycopg2` is simpler since the code
already exists as a pattern to copy.

## Integration point

In `investigation_agent.investigate_order()`, after building the
`InvestigationReport`, call `create_ticket(report)` and add the resulting
`ticket_id` as a field on the report (`ticket_id: Optional[str] = None`).

## Acceptance check

Run `investigate_order()` on `ORD-10007` (`CONFLICTING_SIGNALS`, expected
`HOLD_FOR_VERIFICATION`) and confirm a row appears in
`escalation_tickets` with matching `reason`/`confidence`; run it on
`ORD-10000` (`CLEAR_SAFE`, `RELEASE`) and confirm no ticket is created.
