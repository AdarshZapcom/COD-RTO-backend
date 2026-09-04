# Find the Signal

Quessathon 2026 — Retail Challenge 01.

An agent that investigates incoming cash-on-delivery orders for
return-to-origin (RTO) risk, reconciles signals from the customer, the
pincode, the courier, and the specific courier x pincode lane (which can
disagree with each other), and decides — order by order — whether to
**release**, **hold for verification**, or **escalate to a human ops
reviewer**.

The core question this is built to answer: not "what's the risk score",
but **does the system know when it doesn't have enough signal to decide
confidently?**

## Pipeline

```
data_generator.py   -> synthetic, interconnected COD/RTO dataset
analytics.py         -> evidence layer (rates, trends, supporting vs
                         counter-evidence, data-quality checks)
decision_engine.py   -> deterministic RELEASE / HOLD / ESCALATE call
case_memory.py        -> ChromaDB historical-case memory (local
                         embeddings, no API key required)
investigation_agent.py -> ties it together: evidence -> decision ->
                         historical precedent -> human-readable narrative
```

The deterministic decision engine is authoritative for the operational
call. An LLM (optional — see below) only explains that call in plain
language for a human reviewer; it can never override it.

## Setup

```
pip install -r requirements.txt
python src/data_generator.py
python src/decision_engine.py
python src/investigation_agent.py
```

`investigation_agent.py` runs fully offline by default (deterministic
template narrative). To enable LLM-generated narratives, set an API key
before running it:

```
set OPENAI_API_KEY=sk-...
```

## Local database (optional)

The generated CSVs are the source of truth; both of these are optional,
rebuildable views over the same data - neither is committed to git.

SQLite (zero setup, single file):

```
python src/build_db.py
```

MySQL (needs a local MySQL server already running):

```
python src/build_mysql_db.py
```

Connects to `127.0.0.1:3306` as `root` with no password by default;
override with `MYSQL_HOST` / `MYSQL_PORT` / `MYSQL_USER` /
`MYSQL_PASSWORD` / `MYSQL_DB` env vars if your setup differs.

## Shared team database (Supabase)

For the whole team to query the same data instead of everyone keeping
a separate local DB, load it into the team's Supabase project:

```
set SUPABASE_DB_URL=postgresql://postgres.xxxx:PASSWORD@aws-...pooler.supabase.com:6543/postgres
python src/build_supabase_db.py
```

Get the connection string from the Supabase dashboard: Project
Settings -> Database -> Connection string -> URI ("Transaction
pooler" mode). Never commit that string - it contains the DB
password.

## Data

Synthetic (no public order-level COD/RTO dataset exists at this
granularity — see the challenge brief). Pincodes are real Bengaluru PIN
codes (India Post PIN directory, data.gov.in). Aggregate rate ranges are
grounded against the challenge brief's cited industry figures
(TrackVid/IBEF 2026: COD RTO 20-40% vs <2% prepaid; ShipPrime 2026: ~Rs
180-240 loss per failed COD order) — per-lane rates in this dataset sit
under that national ceiling by design, since the brief's figures are a
national aggregate blending many regions and categories.

Ten scenario orders (`ORD-10000`-`ORD-10009`) are deliberately
constructed to exercise specific investigative situations: a clear-safe
order, a clearly risky customer, a good customer on a bad pincode, a
courier x pincode-specific anomaly, an order with no track record on any
dimension, a small-sample rate spike, missing courier data, genuinely
conflicting evidence, a documented temporary disruption, and a lane with
no historical precedent.
