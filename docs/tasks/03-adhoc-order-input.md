# Ad-hoc Order Input

Handles the jury's live curveball. Right now `get_order_investigation()`
only works for an `order_id` that already exists in the `orders` table —
there's no way to investigate an order the jury describes verbally on
stage without it already being a pre-generated row.

## What it does

Lets the pipeline run on an arbitrary `(customer_id, pincode, courier_id,
order_value)` combination that may not correspond to any existing order —
including combinations where the customer, pincode, or courier themselves
don't exist in the dataset at all (a genuinely new entity).

## Why this is a small change, not a new system

`analytics.get_order_investigation(data, order_id)` already does the real
work in two steps:

1. Look up the order row to get `customer_id`, `pincode`, `courier_id`
2. Call `get_customer_signal` / `get_pincode_signal` / `get_courier_signal`
   / `get_courier_pincode_signal` with those three ids, then assemble

Step 2 is already dimension-id-driven, not order-id-driven. Splitting it
out means step 1 becomes optional.

## Change

In `analytics.py`, refactor `get_order_investigation` into:

```python
def build_investigation(data, order, customer_id, pincode, courier_id) -> dict
    # exactly the current body of get_order_investigation, unchanged,
    # just taking the three ids + an order-shaped dict directly

def get_order_investigation(data, order_id) -> dict
    # looks up the row, then calls build_investigation(...) - unchanged behavior

def get_adhoc_investigation(data, customer_id, pincode, courier_id, order_value, order_id=None, **order_fields) -> dict
    # builds a minimal order dict (order_id defaults to "ADHOC-<timestamp>"),
    # then calls build_investigation(...)
```

No changes needed to `decision_engine.py`, `case_memory.py`, or
`investigation_agent.py` — they all already operate on the `investigation`
dict, not on the existence of a real order row. An unknown
`customer_id`/`pincode`/`courier_id` already flows correctly into
`ESCALATE` via the existing `check_data_quality()` "data not available"
path — this is the same mechanism that already handles the
`MISSING_COURIER_DATA` scenario, just triggered by a nonexistent id
instead of a nulled-out field.

## Where it's exposed

`investigation_agent.py` gets a matching top-level entry point:

```python
def investigate_adhoc(data, customer_id, pincode, courier_id, order_value, **kwargs) -> InvestigationReport
```

mirroring `investigate_order()`, so the FastAPI layer (task 05) can expose
both a `GET /orders/{id}/investigate` and a `POST /investigate` with a
body, without duplicating any pipeline logic.

## Acceptance check

Call `get_adhoc_investigation(data, customer_id="CUST-999", pincode="560099",
courier_id="C99", order_value=1999)` — all three ids nonexistent — and
confirm it returns `found: True` for the investigation itself (it's a
valid, if unknown, order to investigate) with `data_quality.issues`
populated for all three missing dimensions, and that
`decision_engine.decide()` on the result returns `ESCALATE`.
