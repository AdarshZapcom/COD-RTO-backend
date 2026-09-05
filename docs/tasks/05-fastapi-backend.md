# FastAPI Backend

Turns the pipeline from "scripts you run in a terminal" into "a live
runnable prototype" — the deliverable the brief actually requires. No new
logic; this is a thin REST wrapper around functions that already exist
and already work.

## Endpoints

```
GET  /orders?scenario=&limit=&offset=
    -> list of orders for the order picker (id, customer_id, pincode,
       courier_id, order_value, scenario, order_status)
    -> reads from Supabase `orders` table (already loaded)

GET  /orders/{order_id}/investigate
    -> runs investigation_agent.investigate_order(data, order_id)
    -> returns the full InvestigationReport as JSON

POST /investigate
    body: { customer_id, pincode, courier_id, order_value, ... }
    -> runs investigation_agent.investigate_adhoc(...) (task 03)
    -> for the live curveball: an order that may not exist in the dataset

GET  /insights?min_sample=&delta_threshold=
    -> operational_insights.get_operational_insights(data) (task 01)

GET  /tickets?status=OPEN
    -> ticketing.list_open_tickets() (task 02)

POST /tickets/{ticket_id}/resolve
    body: { resolved_by, resolution_note }
    -> ticketing.resolve_ticket(...) (task 02)
```

## Structure

Single `src/api.py` (matches the project's existing flat-module style —
no need for a routers/ package at this scale):

```python
from fastapi import FastAPI
app = FastAPI(title="Find the Signal")

_data = None
def get_data():
    global _data
    if _data is None:
        _data = load_data()  # from analytics.py, loaded once at startup
    return _data
```

Data is loaded once into memory at startup (500 orders + supporting
tables is small) rather than re-reading Supabase per request — matches
the existing `load_data()` pattern, just called once instead of once per
script run. A `POST /admin/reload` endpoint (optional) can force a reload
if the dataset changes during rehearsal.

## What NOT to build here

No auth, no rate limiting, no pagination beyond a simple `limit`/`offset`
on `/orders` — this is a demo backend for one live audience, not a
production service. Keep it to exactly the 6 endpoints above.

## Run

```
pip install fastapi uvicorn
uvicorn src.api:app --reload --port 8000
```

Add `fastapi`, `uvicorn` to `requirements.txt`.

## Acceptance check

`GET /orders/ORD-10007/investigate` returns the same JSON shape currently
printed by `investigation_agent.print_report()` for that order — decision,
risk_level, confidence, evidence, narrative, ticket_id (once task 02 is
wired in), all matching what the CLI script already produces.
