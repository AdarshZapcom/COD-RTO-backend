"""
Quessathon 2026 - Retail Challenge 01: Find the Signal

ChromaDB-backed historical case memory.

This is the "have we seen something like this before" layer that
sits alongside (not instead of) the deterministic decision engine.

Design choices, and why:

- Embeddings are local (sentence-transformers), not an API call.
  Retrieval must never be a point of failure or added latency during
  a live stage demo, so it has zero network dependency at query time.
- Retrieval always tries an exact courier x pincode filter first
  (the lane this order is actually in), and only broadens to
  semantic-only search across all cases if that comes back empty.
  A semantically-similar case from a different lane is weaker
  evidence than a same-lane case and should not be presented as
  equally strong.
- Reranking here is a deterministic score (embedding similarity +
  exact-lane bonus), not an LLM call - fast, free, and does not
  hallucinate a ranking. If an LLM reranker is added later (see
  investigation_agent.py), this deterministic ranking is what it
  must fall back to if that call fails or is unavailable.
"""

from __future__ import annotations

import logging
import os

# Must be set before sentence-transformers / huggingface_hub import to
# keep the live-demo terminal free of download progress bars and
# rate-limit warnings triggered by loading the local embedding model.
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

# The model is already cached locally after the first successful run -
# without this, every process start still makes a round of live HTTP
# HEAD/GET calls to huggingface.co just to revalidate the cache, adding
# a couple of minutes to startup and turning a flaky venue wifi
# connection into a live-demo failure mode for something that is
# supposed to have "zero network dependency at query time". Offline
# mode skips all of that and reads the cache directly.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from typing import Any, Dict, List, Optional

import chromadb
import pandas as pd
from chromadb.utils import embedding_functions

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "generated",
)

CHROMA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "chroma",
)

COLLECTION_NAME = "rto_historical_cases"
EMBEDDING_MODEL = "all-MiniLM-L6-v2"


# ============================================================
# CASE -> TEXT
# ============================================================

def case_to_text(case: Dict[str, Any]) -> str:
    """
    Build the text representation a historical case is embedded from.

    Deliberately includes the signals, counter-evidence and decision
    reasoning - not just the raw stats - so semantic similarity picks
    up on situations that were argued about the same way, not only
    ones with matching numbers.
    """

    return (
        f"Pincode {case['pincode']}, courier {case['courier']}. "
        f"Customer type: {case['customer_type']}. "
        f"Customer profile: {case['customer_profile']}. "
        f"Order value: Rs {case['order_value']}. "
        f"Risk signals observed: {case['signals']}. "
        f"Counter-evidence observed: {case['counter_evidence']}. "
        f"Root cause: {case['root_cause']}. "
        f"Decision made: {case['decision']}. "
        f"Actual outcome: {case['actual_outcome']}. "
        f"Investigation reasoning: {case['reason']}"
    )


def investigation_to_query(investigation: Dict[str, Any]) -> str:
    """
    Build a grounded, self-contained retrieval query from a live
    order investigation - mirrors the case text shape so embedding
    similarity is comparing like with like.
    """

    order = investigation["order"]
    customer = investigation["customer"]
    comparison = investigation["signal_comparison"]

    customer_profile = (
        f"{customer.get('previous_orders')} previous orders; "
        f"{customer.get('previous_rto')} RTO; "
        f"{customer.get('successful_cod_orders')} successful COD"
    ) if customer.get("found") else "no customer history available"

    signals = comparison["supporting_evidence"] or ["NO_STRONG_RISK_SIGNAL"]
    counter = comparison["counter_evidence"] or ["LIMITED_COUNTER_EVIDENCE"]

    return (
        f"Pincode {order['pincode']}, courier {order['courier_id']}. "
        f"Customer type: {customer.get('customer_type', 'UNKNOWN')}. "
        f"Customer profile: {customer_profile}. "
        f"Order value: Rs {order['order_value']}. "
        f"Risk signals observed: {' | '.join(signals)}. "
        f"Counter-evidence observed: {' | '.join(counter)}."
    )


# ============================================================
# COLLECTION
# ============================================================

def get_embedding_function():
    return embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name=EMBEDDING_MODEL
    )


def warm_embedding_model() -> None:
    """
    Force the sentence-transformers model to load now rather than on the
    first live retrieval call.

    Loading the model the first time it's used costs several seconds;
    every call after that is fast (the library caches the loaded model
    process-wide). Called once at FastAPI startup so that ~seconds-long
    cost happens before the demo starts, not during the first live
    investigation on stage.
    """
    get_embedding_function()(["warmup"])


def get_client():
    os.makedirs(CHROMA_DIR, exist_ok=True)
    return chromadb.PersistentClient(path=CHROMA_DIR)


def build_collection(rebuild: bool = False):
    """
    (Re)build the historical case collection from
    data/generated/historical_cases.csv.
    """

    client = get_client()
    ef = get_embedding_function()

    if rebuild:
        try:
            client.delete_collection(COLLECTION_NAME)
        except Exception:
            pass

    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=ef,
    )

    if collection.count() > 0 and not rebuild:
        return collection

    cases = pd.read_csv(os.path.join(DATA_DIR, "historical_cases.csv"))
    cases["pincode"] = cases["pincode"].astype(str)

    ids = cases["case_id"].astype(str).tolist()
    documents = [case_to_text(row) for _, row in cases.iterrows()]
    metadatas = [
        {
            "case_id": row["case_id"],
            "pincode": str(row["pincode"]),
            "courier": row["courier"],
            "decision": row["decision"],
            "actual_outcome": row["actual_outcome"],
            "root_cause": row["root_cause"],
        }
        for _, row in cases.iterrows()
    ]

    collection.upsert(
        ids=ids,
        documents=documents,
        metadatas=metadatas,
    )

    return collection


# ============================================================
# RETRIEVAL + DETERMINISTIC RERANK
# ============================================================

def retrieve_similar_cases(
    investigation: Dict[str, Any],
    top_k: int = 3,
    candidate_pool: int = 10,
) -> List[Dict[str, Any]]:
    """
    Retrieve and rerank historical cases relevant to this order.

    Tries an exact courier x pincode lane filter first; falls back
    to unfiltered semantic search across all cases if the lane has
    no history at all (this is itself a meaningful signal - see
    decision_engine.has_historical_precedent - not just a retrieval
    inconvenience).
    """

    collection = build_collection()
    order = investigation["order"]
    query_text = investigation_to_query(investigation)

    same_lane = collection.query(
        query_texts=[query_text],
        n_results=min(candidate_pool, collection.count()),
        where={
            "$and": [
                {"pincode": {"$eq": str(order["pincode"])}},
                {"courier": {"$eq": order["courier_id"]}},
            ]
        },
    )

    used_fallback = False
    result = same_lane

    if not same_lane["ids"] or not same_lane["ids"][0]:
        used_fallback = True
        logger.info(
            "no same-lane cases for pincode=%s courier=%s, falling back to "
            "semantic-only search across all lanes",
            order["pincode"], order["courier_id"],
        )
        result = collection.query(
            query_texts=[query_text],
            n_results=min(candidate_pool, collection.count()),
        )

    if not result["ids"] or not result["ids"][0]:
        logger.info(
            "no historical cases retrieved at all for pincode=%s courier=%s",
            order["pincode"], order["courier_id"],
        )
        return []

    candidates = []
    for i, case_id in enumerate(result["ids"][0]):
        distance = result["distances"][0][i]
        metadata = result["metadatas"][0][i]
        document = result["documents"][0][i]

        same_lane_bonus = (
            0.0
            if used_fallback
            else 0.15
        )

        # Chroma's default distance is smaller-is-better; convert to a
        # 0-1 "similarity-ish" score and add the deterministic bonus.
        similarity = 1.0 / (1.0 + distance)

        candidates.append({
            "case_id": case_id,
            "text": document,
            "metadata": metadata,
            "similarity": round(similarity, 4),
            "same_lane": not used_fallback,
            "score": round(similarity + same_lane_bonus, 4),
        })

    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates[:top_k]


# ============================================================
# MAIN (smoke test)
# ============================================================

def main():
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from analytics import load_data, get_order_investigation

    print("=" * 70)
    print("QUESSATHON - FIND THE SIGNAL")
    print("Historical Case Memory (ChromaDB)")
    print("=" * 70)

    print(f"\nBuilding/loading collection at {CHROMA_DIR} ...")
    collection = build_collection(rebuild=True)
    print(f"Collection size: {collection.count()} cases")

    data = load_data()
    orders = data["orders"]
    scenarios = orders[
        orders["scenario"].notna() & (orders["scenario"] != "NORMAL")
    ]

    for _, row in scenarios.iterrows():
        investigation = get_order_investigation(data, row["order_id"])
        results = retrieve_similar_cases(investigation, top_k=3)

        print(f"\n{row['order_id']} ({row['scenario']}):")
        if not results:
            print("  No historical cases retrieved at all.")
            continue

        for r in results:
            lane_tag = "SAME LANE" if r["same_lane"] else "cross-lane"
            print(
                f"  [{lane_tag}] {r['case_id']} "
                f"score={r['score']:.3f} "
                f"decision={r['metadata']['decision']} "
                f"outcome={r['metadata']['actual_outcome']}"
            )


if __name__ == "__main__":
    main()
