"""
Offline retrieval eval: recall@K and MRR against a hand-labelled golden set
(server/memory/golden_set.py), no LLM call — the CI-friendly harness
DISCUSS_RAG.md §16 designs ("runs in CI... seconds"), scaled to 12 queries.

    python -m server.memory.eval

Seeds the golden corpus under one throwaway owner (same pattern as
demo.py::_check_store), runs retrieve.search() per query, scores it, prints a
table, and exits non-zero if either metric drops below its threshold. Cleans
up in a `finally` block regardless of outcome.
"""

import asyncio

from bson import ObjectId

from server.memory import entities, retrieve, store
from server.memory.encoder import EMBED_DIM, EMBED_MODEL_NAME, EncoderUnavailable, encode
from server.memory.golden_set import CORPUS, GOLDEN_QUERIES
from server.memory.types import MemoryUnit, Scope
from server.shared import now

EVAL_K = 5
MIN_RECALL_AT_K = 0.8
MIN_MRR = 0.7


async def _seed() -> tuple[Scope, dict[str, ObjectId]]:
    """Input: none. Output: the throwaway scope and each corpus key's stored unit id."""
    from server.db import conversations, ensure_indexes

    await ensure_indexes()

    owner = ObjectId()
    conversation = ObjectId()
    units = [
        MemoryUnit(
            owner_id=owner,
            conversation_id=conversation,
            kind="turn",
            role="user",
            text=entry["text"],
            seq=i,
            at=now(),
            entities=entities.extract_entities(entry["text"]),
        )
        for i, entry in enumerate(CORPUS)
    ]

    try:
        vectors = await encode([u.text for u in units], is_query=False)
        for unit, vec in zip(units, vectors, strict=True):
            unit.vec, unit.embed_model, unit.embed_dim = vec, EMBED_MODEL_NAME, EMBED_DIM
    except EncoderUnavailable as err:
        print(f"eval: dense view unavailable, scoring lexical/graph only ({err})")

    await conversations.insert_one({"_id": conversation, "ownerId": owner, "title": "Eval fixture conversation"})
    ids = await store.put(units)
    await store.put_edges(owner, units)

    key_to_id = {entry["key"]: unit_id for entry, unit_id in zip(CORPUS, ids, strict=True)}
    return Scope(owner_id=owner), key_to_id


async def _cleanup(scope: Scope) -> None:
    from server.db import conversations, memory_edges, memory_units

    await memory_units.delete_many({"ownerId": scope.owner_id})
    await memory_edges.delete_many({"ownerId": scope.owner_id})
    await conversations.delete_many({"ownerId": scope.owner_id})


def _score(ranked_ids: list[ObjectId], relevant_ids: set[ObjectId]) -> tuple[float, float]:
    """Input: hit ids in ranked order, and the relevant ids for this query. Output: (recall@K, reciprocal rank)."""
    top_k = ranked_ids[:EVAL_K]
    recall = len(set(top_k) & relevant_ids) / len(relevant_ids) if relevant_ids else 0.0
    for rank, unit_id in enumerate(ranked_ids, start=1):
        if unit_id in relevant_ids:
            return recall, 1.0 / rank
    return recall, 0.0


async def main() -> None:
    print("memory retrieval eval")
    scope, key_to_id = await _seed()
    try:
        recalls: list[float] = []
        reciprocal_ranks: list[float] = []
        print(f"{'query':<42} {'recall@' + str(EVAL_K):>10} {'RR':>6}")
        for case in GOLDEN_QUERIES:
            query = str(case["query"])
            relevant_ids = {key_to_id[key] for key in case["relevant"]}
            hits = await retrieve.search(scope, query, k=EVAL_K)
            ranked_ids = [h.unit_id for h in hits if not h.is_context]
            recall, rr = _score(ranked_ids, relevant_ids)
            recalls.append(recall)
            reciprocal_ranks.append(rr)
            print(f"{query:<42} {recall:>10.2f} {rr:>6.2f}")

        mean_recall = sum(recalls) / len(recalls)
        mean_mrr = sum(reciprocal_ranks) / len(reciprocal_ranks)
        print(f"\nmean recall@{EVAL_K} = {mean_recall:.3f} (min {MIN_RECALL_AT_K})")
        print(f"mean MRR       = {mean_mrr:.3f} (min {MIN_MRR})")

        if mean_recall < MIN_RECALL_AT_K or mean_mrr < MIN_MRR:
            raise SystemExit("memory retrieval eval FAILED — below threshold")
        print("\nmemory retrieval eval passed")
    finally:
        await _cleanup(scope)


if __name__ == "__main__":
    asyncio.run(main())
