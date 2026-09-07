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
    GET  /tickets?status=OPEN|RESOLVED
    POST /tickets/{ticket_id}/resolve

Data is loaded once into memory at startup (500 orders + supporting
tables is small) via analytics.load_data() - the same CSVs that mirror
the Supabase tables 1:1 (see supabase_setup.py) - rather than re-read
per request.

Run:
    uvicorn src.api:app --reload --port 8000
"""

from __future__ import annotations

import logging
import math
import os
import re
import sys
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from analytics import load_data
from case_memory import warm_embedding_model
from investigation_agent import InvestigationReport, investigate_adhoc, investigate_order
from operational_insights import OperationalInsight, get_operational_insights
from ticketing import list_tickets, resolve_ticket

# Configures the root logger once, at the actual process entry point -
# every other module's `logging.getLogger(__name__)` propagates here,
# so this one call is what makes every log line in the pipeline
# (decision_engine, investigation_agent, ticketing, case_memory)
# actually show up in the uvicorn console instead of going nowhere.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


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

# Demo backend for one local frontend during rehearsal/event day - allow
# any origin rather than hardcode a port that may change (5173 vs 4173
# preview, or an EC2 host later).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    # allow_headers governs REQUEST headers the browser may send; a
    # custom RESPONSE header (X-Total-Count, for GET /tickets'
    # pagination) is invisible to client-side JS unless explicitly
    # exposed here - allow_headers="*" does not cover this direction.
    expose_headers=["X-Total-Count"],
)


def _json_safe(value: Any) -> Any:
    """Recursively replace a non-finite float (NaN/Infinity/-Infinity)
    with its string form so a dict/list is always safe to hand to
    Starlette's JSONResponse, which renders with allow_nan=False."""
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """
    A rejected NaN/Infinity/-Infinity request value (order_value,
    cod_amount, ...) is echoed back verbatim by Pydantic in each
    error's "input" field. FastAPI's own default handler for this
    exception hands that straight to JSONResponse - which, on this
    Starlette version, renders with allow_nan=False and raises
    ValueError deep inside the framework's error-handling machinery
    itself. That secondary crash happens outside every try/except in
    this file (it IS the error handler) and outside
    unhandled_exception_handler above (a handler that itself raises
    isn't retried by another handler), so it fell all the way through
    to Starlette's bare text/plain 500 - the exact failure mode
    reported for a NaN/-Infinity order_value, independent of whichever
    validator actually rejects the value. Sanitizing the error content
    here closes that regardless of which field or validator triggers it.
    """
    return JSONResponse(
        status_code=422,
        content={"detail": _json_safe(jsonable_encoder(exc.errors()))},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Last-resort safety net: FastAPI/Starlette's own default for an
    exception with no more specific handler (HTTPException and
    RequestValidationError already have their own, and still take
    precedence over this) is a bare text/plain "Internal Server Error"
    - inconsistent with every other error response this API returns
    ({"detail": ...} JSON). This guarantees the same structured shape
    even for a crash nothing here anticipated.
    """
    logger.exception("Unhandled exception for %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error."})


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


_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")


def _reject_control_chars(value: str, label: str) -> None:
    """
    A null byte (or other control character) in a path parameter isn't
    a valid id under any circumstance - it's malformed/malicious input,
    not a real lookup key. Reject it here with a 4xx before it reaches
    psycopg2, which raises on a NUL byte in a text parameter; left
    uncaught, that surfaced as a 503 "Ticketing store unavailable",
    indistinguishable from a genuine outage.
    """
    if _CONTROL_CHAR_RE.search(value):
        raise HTTPException(
            status_code=422,
            detail=f"Invalid {label}: control characters are not allowed.",
        )


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

    @field_validator("order_value", "cod_amount", mode="after")
    @classmethod
    def _must_be_finite(cls, value: Optional[float]) -> Optional[float]:
        """
        Reject NaN/Infinity/-Infinity - these are valid JSON-parseable
        float literals (Python's json module accepts them by default)
        but not legitimate order values. Pydantic's own gt=0 constraint
        does not reliably reject them (NaN/-Infinity comparisons are
        not guaranteed to fail the same way a normal negative number
        does), and letting one through crashes deep in the pipeline
        with a raw, unhandled 500 instead of a clean 422.
        """
        if value is not None and not math.isfinite(value):
            raise ValueError("must be a finite number")
        return value


# The three real ops outcomes for a HOLD/ESCALATE ticket, tied to the
# actual question being answered (ship this order COD or not) rather
# than a generic status list - see get_tickets' `status` allowlist for
# the same validate-in-the-API-layer convention.
RESOLUTION_OUTCOMES = ("VERIFIED_RELEASE", "RISKY_BLOCK_COD", "ESCALATE_MANAGER")


class TicketResolveRequest(BaseModel):
    resolved_by: str = Field(..., min_length=1)
    resolution_note: str = Field(..., min_length=1)
    resolution_outcome: str = Field(..., min_length=1)


class TicketResolveResponse(BaseModel):
    ticket_id: str
    status: str = "RESOLVED"
    resolved_by: str
    resolution_note: str
    resolution_outcome: str


# ============================================================
# GET /orders
# ============================================================

@app.get("/orders", response_model=List[OrderSummary])
def get_orders(
    response: Response,
    scenario: Optional[str] = Query(None, description="Filter to this exact scenario value"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    orders = get_data()["orders"]

    if scenario is not None:
        orders = orders[orders["scenario"] == scenario]

    # X-Total-Count reflects the filtered set (post-scenario, pre-page),
    # matching GET /tickets' pattern - lets the frontend render real
    # numbered pagination instead of an infinite "load more".
    response.headers["X-Total-Count"] = str(len(orders))

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
    logger.info("investigate order_id=%s", order_id)
    try:
        return investigate_order(get_data(), order_id)
    except ValueError:
        logger.warning("order not found: %s", order_id)
        raise HTTPException(status_code=404, detail=f"Order not found: {order_id}")
    except Exception:
        logger.exception("GET /orders/%s/investigate failed", order_id)
        raise HTTPException(status_code=500, detail="Investigation failed unexpectedly.")


# ============================================================
# POST /investigate
# ============================================================

@app.post("/investigate", response_model=InvestigationReport)
def post_investigate(body: AdhocOrderRequest):
    logger.info(
        "investigate ad-hoc customer_id=%s pincode=%s courier_id=%s",
        body.customer_id, body.pincode, body.courier_id,
    )
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
        logger.exception("POST /investigate failed")
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
        logger.exception("GET /insights failed")
        raise HTTPException(status_code=500, detail="Insight scan failed unexpectedly.")


# ============================================================
# GET /tickets
# ============================================================

@app.get("/tickets", response_model=List[Dict[str, Any]])
def get_tickets(
    response: Response,
    status: str = Query("OPEN"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    normalized_status = status.upper()
    if normalized_status not in ("OPEN", "RESOLVED"):
        raise HTTPException(
            status_code=400,
            detail="Only status=OPEN or status=RESOLVED is supported.",
        )
    try:
        # X-Total-Count (not a body-shape change) lets the frontend render
        # real numbered pagination (page 1/2/3...) instead of an infinite
        # "load more" that never tells an operator how much is left -
        # the response body stays a plain array, matching every other
        # list endpoint here. One list_tickets() call gets both the page
        # and the total over a single DB connection (see its docstring).
        rows, total = list_tickets(status=normalized_status, limit=limit, offset=offset)
        response.headers["X-Total-Count"] = str(total)
        return rows
    except Exception:
        logger.exception("GET /tickets failed")
        raise HTTPException(status_code=503, detail="Ticketing store unavailable.")


# ============================================================
# POST /tickets/{ticket_id}/resolve
# ============================================================

@app.post("/tickets/{ticket_id}/resolve", response_model=TicketResolveResponse)
def post_ticket_resolve(ticket_id: str, body: TicketResolveRequest):
    _reject_control_chars(ticket_id, "ticket_id")
    if body.resolution_outcome not in RESOLUTION_OUTCOMES:
        raise HTTPException(
            status_code=400,
            detail=f"resolution_outcome must be one of: {', '.join(RESOLUTION_OUTCOMES)}",
        )
    try:
        found = resolve_ticket(ticket_id, body.resolved_by, body.resolution_note, body.resolution_outcome)
    except Exception:
        logger.exception("POST /tickets/%s/resolve failed", ticket_id)
        raise HTTPException(status_code=503, detail="Ticketing store unavailable.")

    if not found:
        raise HTTPException(status_code=404, detail=f"Ticket not found: {ticket_id}")

    return TicketResolveResponse(
        ticket_id=ticket_id,
        resolved_by=body.resolved_by,
        resolution_note=body.resolution_note,
        resolution_outcome=body.resolution_outcome,
    )
