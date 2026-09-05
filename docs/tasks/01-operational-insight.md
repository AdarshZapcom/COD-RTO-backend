# Operational Insight

Lane-level pattern findings for the ops team — the one explicitly required
minimum capability with no code behind it yet: *"produce a courier or
pincode-level case a human ops team could act on, not just a per-order
score."*

## What it does

Scans every `courier_pincode` lane (not individual orders) and surfaces the
ones showing a real recent-vs-baseline deterioration, ranked by how much it
matters operationally — the same "recent trend vs. lifetime average" logic
`decision_engine.py` already applies per order, just run across all 100
lanes at once instead of one at a time.

## Logic

For each row in `courier_pincode`:

1. `delta = rto_rate_7d - rto_rate_30d`
2. Skip the lane if `total_orders < 5` (too small a sample to call it a
   pattern — flag separately as `LOW_SAMPLE`, don't rank it as a real trend)
3. Skip if `data_quality` is `MISSING` (nothing to compute)
4. Flag as a finding if `delta > 0.05` **and** `rto_rate_7d > 0.15`
5. Severity: `HIGH` if `delta > 0.10`, else `MEDIUM`
6. Check `events` table for any row matching this `(pincode, courier_id)` —
   if found, attach it as `possible_cause` (this is what lets the finding
   say "may be explained by a documented disruption" instead of reading as
   unexplained deterioration)
7. Sort findings by `delta` descending

## Output shape

```python
class OperationalInsight(BaseModel):
    courier_id: str
    pincode: str
    severity: Literal["HIGH", "MEDIUM", "LOW_SAMPLE"]
    rto_rate_7d: float
    rto_rate_30d: float
    delta: float
    sample_size: int
    possible_cause: Optional[str]  # from events table, if any
    recommended_action: str        # template-generated, see below
```

`recommended_action` is a plain string built from a fixed template, not an
LLM call — e.g. `"Courier {courier_id} is deteriorating specifically in
pincode {pincode} ({rto_rate_7d:.0%} vs {rto_rate_30d:.0%} baseline, n={sample_size}).
Consider rerouting new orders in this pincode to an alternate courier."`
Same authority model as the rest of the system: deterministic first,
LLM narration is an optional enhancement on top later if wanted, never a
requirement.

## Where it lives

New file: `src/operational_insights.py`

```python
def get_operational_insights(data, min_sample=5, delta_threshold=0.05) -> List[OperationalInsight]
```

Reuses `data["courier_pincode"]` and `data["events"]` from
`analytics.load_data()` — no new data source needed.

## Acceptance check

Run against the existing dataset and confirm `C07 × 560068` comes back as
the top `HIGH` finding (its actual numbers: `rto_rate_7d=0.29`,
`rto_rate_30d=0.15`, `delta=0.14`), with `possible_cause` populated from
`EVT-001`/`EVT-002` (the heavy-rain / courier-capacity events already
seeded on that exact lane).
