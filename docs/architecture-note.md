# Find the Signal — Architecture Note

One page: system flow, dependencies, human hand-off points, as the system stands today.

## System Flow

```
data_generator.py --> data/generated/*.csv (3000 synthetic orders)
                             |
                     supabase_setup.py imports + processes the CSVs
                     into Supabase (Postgres) - schema creation +
                     bulk load, run to (re)publish a dataset
                             |
                     Supabase is the system of record from here on:
                     customers / pincodes / couriers / courier_pincode /
                     orders / delivery_attempts / events /
                     historical_cases, plus the live escalation_tickets
                     table (ticketing.py reads/writes this directly,
                     not via a CSV import)
                             |
        +--------------------+--------------------+
        |                    |                     |
 GET /orders/{id}      POST /investigate      GET /insights, /tickets
 (real order lookup)   (ad-hoc: customer_id,  (operational monitoring,
        |              pincode, courier_id,    ticket queue)
        |              order_value only)
        +--------------------+
                             |
                analytics.build_investigation()
                (customer/pincode/courier/lane signals,
                 data-quality check, evidence comparison)
                             |
              decision_engine.decide()  <- AUTHORITATIVE
              (RELEASE / HOLD_FOR_VERIFICATION / ESCALATE
               + risk_level + confidence, all deterministic)
                             |
              case_memory.retrieve_similar_cases()
              (ChromaDB, local embeddings, same-lane-first)
                             |
              investigation_agent narrative step
              (LLM explains the decision already made;
               never allowed to change it)
                             |
              ticketing.create_ticket()  <- only when decision != RELEASE
                             |
                    InvestigationReport (JSON)
                             |
                    React/Vite frontend renders it
```

Two entry points converge on one pipeline: a real order (a row Supabase
already holds) or an ad-hoc order described live. The ad-hoc form was
simplified this session to its 4 required fields (customer_id, pincode,
courier_id, order_value) — `is_first_order`/`address_verified` per-order
overrides are a real, working backend capability
(`analytics.apply_order_overrides`) but are currently only reachable via a
direct `POST /investigate` call, not the UI.

## Dependencies

| Dependency | Used by | If unavailable |
|---|---|---|
| Supabase/Postgres (pooler connection) | The evidence layer's source of record for customers/pincodes/couriers/courier_pincode/orders/delivery_attempts/events/historical_cases (imported from the CSVs via `supabase_setup.py`, republished by re-running it after a fresh `data_generator.py` pass); `ticketing.py`'s escalation_tickets table is read/written live, not imported | Ticket creation fails silently (caught, `ticket_id: null`, investigation still returns complete); `GET /tickets` / `POST /tickets/{id}/resolve` return `503` |
| OpenAI (`OPENAI_API_KEY`) | `investigation_agent.build_llm_narrative()` | Falls back to a deterministic template narrative (`narrative_source: "template_fallback"`) — `decision`/`risk_level`/`confidence` are byte-identical either way |
| ChromaDB (`data/chroma/`, local sentence-transformers) | `case_memory.py` | Rebuilds automatically from `historical_cases.csv` on first run if the index is missing; no network call at query time |
| Backend API (`VITE_API_BASE_URL`) | Frontend (`api/client.js`) | Surfaced as a plain network-error message in the UI; no silent failure |

## Human Hand-off Points

- **Every non-RELEASE decision writes/refreshes an escalation ticket** (`ticketing.create_ticket`, upserts on `TCK-{order_id}`, reopening it if a prior resolution exists) — this is the actual hand-off artifact an ops reviewer works from (`TicketsView`), not just a decision printed on screen.
- **ESCALATE** — critical data genuinely missing (pincode/courier/lane not found, or a "blocking" uncertainty flag: thin courier×pincode sample, no historical precedent for this lane) with risk not otherwise HIGH. A brand-new customer *alone* no longer forces this (recent change, merged this session) — it's now a non-blocking `INFO:` flag weighed into confidence instead, since a first-time buyer is the common case, not a rare gap.
- **HOLD_FOR_VERIFICATION** — risk is HIGH (a severely deteriorating lane, or enough accumulated supporting evidence even on an otherwise-stable lane), or supporting and counter-evidence are both substantial (≥2 each) and conflict, or risk is MEDIUM with a trustworthy evidence base.
- **Resolution** — a human closes a ticket via `POST /tickets/{ticket_id}/resolve` (`resolved_by`, `resolution_note` recorded); a later re-investigation producing another non-RELEASE call reopens it rather than leaving a stale RESOLVED row hiding a live decision.
- **The LLM is never a hand-off decision-maker** — it only narrates the deterministic call and may add a separate `advisory_flag` for something a human should double-check; it cannot change RELEASE/HOLD/ESCALATE.
