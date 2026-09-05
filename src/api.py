"""
Quessathon 2026 - Retail Challenge 01: Find the Signal

FastAPI backend.

Thin REST wrapper around the pipeline that already exists and already
works: analytics.py (evidence) -> decision_engine.py (deterministic
decision, authoritative) -> case_memory.py (ChromaDB precedent) ->
investigation_agent.py (ties together, LLM narrates only, never
decides) -> ticketing.py (escalation tickets) -> operational_insights.py
(courier x pincode lane findings). No new business logic lives here -
every endpoint below only validates input, calls one of those existing
functions, and shapes the response.

Exactly 6 endpoints (see docs/tasks/05-fastapi-backend.md):
    GET  /orders?scenario=&limit=&offset=
    GET  /orders/{order_id}/investigate
    POST /investigate
    GET  /insights?min_sample=&delta_threshold=
    GET  /tickets?status=OPEN
    POST /tickets/{ticket_id}/resolve

Data is loaded once into memory at startup (500 orders + supporting
tables is small) via analytics.load_data() - the same CSVs that mirror
the Supabase tables 1:1 (see supabase_setup.py) - rather than re-read
per request.

Run:
    uvicorn src.api:app --reload --port 8000
"""

from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from analytics import load_data
from case_memory import warm_embedding_model
from investigation_agent import InvestigationReport, investigate_adhoc, investigate_order
from operational_insights import OperationalInsight, get_operational_insights
from ticketing import list_open_tickets, resolve_ticket


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Warm the case-memory embedding model now (costs a few seconds) so
    # the first live investigation isn't the one that pays for it - see
    # case_memory.warm_embedding_model(). Non-fatal: if this fails for
    # any reason, the first real request just pays the warmup cost
    # itself instead, exactly like before this existed.
    try:
        warm_embedding_model()
    except Exception:
        pass
    yield


app = FastAPI(title="Find the Signal", lifespan=lifespan)


# ============================================================
# DATA (loaded once at startup, matches analytics.load_data() pattern)
# ============================================================

_data = None


def get_data():
    global _data
    if _data is None:
        _data = load_data()
    return _data


# ============================================================
# HELPERS
# ============================================================

def _clean(value: Any) -> Any:
    """NaN/NaT -> None so pandas values serialize as valid JSON."""
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _clean_str(value: Any) -> Optional[str]:
    """Same as _clean, but coerces to str - orders.pincode loads as an
    int64 column (unlike pincodes.pincode, read elsewhere with
    .astype(str)), so it needs an explicit cast here too."""
    value = _clean(value)
    return None if value is None else str(value)


# ============================================================
# REQUEST / RESPONSE MODELS
# ============================================================

class OrderSummary(BaseModel):
    order_id: str
    customer_id: Optional[str] = None
    pincode: Optional[str] = None
    courier_id: Optional[str] = None
    order_value: Optional[float] = None
    scenario: Optional[str] = None
    order_status: Optional[str] = None


class AdhocOrderRequest(BaseModel):
    """Body for POST /investigate - an order that may not exist in the
    dataset at all (the live curveball). Only the four fields every
    investigation needs are required; the rest mirror the optional
    order attributes analytics.get_adhoc_investigation() accepts."""

    customer_id: str = Field(..., min_length=1)
    pincode: str = Field(..., min_length=1)
    courier_id: str = Field(..., min_length=1)
    order_value: float = Field(..., gt=0)

    order_id: Optional[str] = None
    seller_id: Optional[str] = None
    product_category: Optional[str] = None
    cod_amount: Optional[float] = None
    payment_type: Optional[str] = None
    is_first_order: Optional[bool] = None
    address_verified: Optional[bool] = None

    @field_validator("customer_id", "pincode", "courier_id", mode="before")
    @classmethod
    def _coerce_to_str(cls, value):
        """Accept a JSON number as well as a string. Every other layer
        of the system (analytics.py, stress_test.py) already treats
        these ids/pincode as str-or-int interchangeably, and this
        endpoint's whole purpose is the jury's free-form live curveball
        input - a numeric pincode is a very plausible thing to receive."""
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return str(value)
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return value


class TicketResolveRequest(BaseModel):
    resolved_by: str = Field(..., min_length=1)
    resolution_note: str = Field(..., min_length=1)


class TicketResolveResponse(BaseModel):
    ticket_id: str
    status: str = "RESOLVED"
    resolved_by: str
    resolution_note: str


# ============================================================
# GET /orders
# ============================================================

@app.get("/orders", response_model=List[OrderSummary])
def get_orders(
    scenario: Optional[str] = Query(None, description="Filter to this exact scenario value"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    orders = get_data()["orders"]

    if scenario is not None:
        orders = orders[orders["scenario"] == scenario]

    page = orders.iloc[offset : offset + limit]

    return [
        OrderSummary(
            order_id=row["order_id"],
            customer_id=_clean(row.get("customer_id")),
            pincode=_clean_str(row.get("pincode")),
            courier_id=_clean(row.get("courier_id")),
            order_value=_clean(row.get("order_value")),
            scenario=_clean(row.get("scenario")),
            order_status=_clean(row.get("order_status")),
        )
        for _, row in page.iterrows()
    ]


# ============================================================
# GET /orders/{order_id}/investigate
# ============================================================

@app.get("/orders/{order_id}/investigate", response_model=InvestigationReport)
def get_order_investigate(order_id: str):
    try:
        return investigate_order(get_data(), order_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Order not found: {order_id}")
    except Exception:
        raise HTTPException(status_code=500, detail="Investigation failed unexpectedly.")


# ============================================================
# POST /investigate
# ============================================================

@app.post("/investigate", response_model=InvestigationReport)
def post_investigate(body: AdhocOrderRequest):
    kwargs = body.model_dump(
        exclude={"customer_id", "pincode", "courier_id", "order_value"},
        exclude_none=True,
    )
    try:
        return investigate_adhoc(
            get_data(),
            body.customer_id,
            body.pincode,
            body.courier_id,
            body.order_value,
            **kwargs,
        )
    except Exception:
        raise HTTPException(status_code=500, detail="Investigation failed unexpectedly.")


# ============================================================
# GET /insights
# ============================================================

@app.get("/insights", response_model=List[OperationalInsight])
def get_insights(
    min_sample: int = Query(5, ge=1),
    delta_threshold: float = Query(0.05, ge=0.0, le=1.0),
):
    try:
        return get_operational_insights(
            get_data(), min_sample=min_sample, delta_threshold=delta_threshold
        )
    except Exception:
        raise HTTPException(status_code=500, detail="Insight scan failed unexpectedly.")


# ============================================================
# GET /tickets
# ============================================================

@app.get("/tickets", response_model=List[Dict[str, Any]])
def get_tickets(status: str = Query("OPEN")):
    if status.upper() != "OPEN":
        # list_open_tickets() is the only query ticketing.py exposes today.
        raise HTTPException(
            status_code=400,
            detail="Only status=OPEN is supported.",
        )
    try:
        return list_open_tickets()
    except Exception:
        raise HTTPException(status_code=503, detail="Ticketing store unavailable.")


# ============================================================
# POST /tickets/{ticket_id}/resolve
# ============================================================

@app.post("/tickets/{ticket_id}/resolve", response_model=TicketResolveResponse)
def post_ticket_resolve(ticket_id: str, body: TicketResolveRequest):
    try:
        found = resolve_ticket(ticket_id, body.resolved_by, body.resolution_note)
    except Exception:
        raise HTTPException(status_code=503, detail="Ticketing store unavailable.")

    if not found:
        raise HTTPException(status_code=404, detail=f"Ticket not found: {ticket_id}")

    return TicketResolveResponse(
        ticket_id=ticket_id,
        resolved_by=body.resolved_by,
        resolution_note=body.resolution_note,
    )
