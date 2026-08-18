"""
One-off maintenance sweep over stored units: `python -m server.memory.backfill`.

Two jobs, both of them "this unit is missing something a later version of the
code would have given it":

**Entities and edges.** Every unit written before the entity-graph view existed
has `entities: []`, which means the graph view cannot see any of the user's
actual history — only what they say from now on. DISCUSS_RAG.md §9 promised this
would never be needed, on the theory that the field would be populated from day
one even while unread. The field shipped; the writer didn't. This is the
migration that promise was meant to avoid, and running it once is the whole cost.

**Vectors.** Units stored while the encoder was unavailable have `vec: null` —
by design, since refusing to store them would have lost the data permanently
(see `index_turn`). They are lexically searchable but invisible to dense
retrieval until something re-encodes them. That sweep was described in
DISCUSS_RAG.md §12 and never written until now.

Deliberately a command, not a startup task. It walks every unit of every user;
that belongs in a terminal where someone is watching it, not in front of a web
server trying to bind a port.

Safe to run repeatedly. Entities are only extracted for units that have none,
so edges are written exactly once per unit, and `store.put_edges` is an `$inc`
upsert either way. Interrupt it and run it again — it picks up where it left off
because the work queue is defined by the data, not by a cursor.
"""

import asyncio
from typing import Any

from server.db import ensure_indexes, memory_units
from server.memory import store
from server.memory.encoder import EMBED_DIM, EMBED_MODEL_NAME, EncoderUnavailable, encode
from server.memory.entities import extract_entities

# Units per round trip. Large enough that the encoder batches usefully, small
# enough that one failure doesn't cost much and progress is visible.
BATCH = 64


async def backfill_entities() -> tuple[int, int]:
    """
    Input: none. Output: (units updated, edges written).

    Grouped by owner and conversation and ordered by `seq`, because adjacency
    edges are only meaningful between units that really are neighbours — see
    `store._edge_pairs`, which drops any pair whose `seq` isn't consecutive. A
    flat scan in Mongo's own order would silently produce a different graph
    than live ingest does.
    """
    pipeline = [
        {"$match": {"entities": {"$in": [None, []]}}},
        {"$group": {"_id": {"ownerId": "$ownerId", "conversationId": "$conversationId"}}},
    ]
    # `aggregate` is awaited to get the cursor, unlike `find` which returns one
    # directly — pymongo's async API differs between the two.
    groups = [doc["_id"] async for doc in await memory_units.aggregate(pipeline)]
    print(f"backfill: {len(groups)} conversation(s) need entities")

    updated = edges = 0
    for index, group in enumerate(groups, start=1):
        owner_id, conversation_id = group["ownerId"], group["conversationId"]
        run: list[tuple[Any, int, list[str]]] = []

        cursor = memory_units.find(
            {"ownerId": owner_id, "conversationId": conversation_id, "entities": {"$in": [None, []]}},
            {"text": 1, "kind": 1, "seq": 1},
        ).sort([("seq", 1)])
        async for doc in cursor:
            entities = extract_entities(doc.get("text", ""), doc.get("kind", "turn"))
            await memory_units.update_one({"_id": doc["_id"]}, {"$set": {"entities": entities}})
            run.append((conversation_id, doc.get("seq", 0), entities))
            updated += 1

        edges += await store.put_edges_for(owner_id, run)
        if index % 10 == 0 or index == len(groups):
            print(f"backfill: entities {index}/{len(groups)} conversations · {updated} units · {edges} edges")

    return updated, edges


async def backfill_vectors() -> int:
    """
    Input: none. Output: how many units were re-encoded.

    Stops at the first `EncoderUnavailable` rather than grinding through every
    remaining batch to fail identically each time. The units it did not reach
    still have `vec: null`, so the next run finds exactly them.
    """
    total = await memory_units.count_documents({"vec": None})
    print(f"backfill: {total} unit(s) missing vectors")
    if not total:
        return 0

    done = 0
    while True:
        batch = [
            doc
            async for doc in memory_units.find({"vec": None}, {"text": 1}).limit(BATCH)
        ]
        if not batch:
            break

        try:
            vectors = await encode([doc.get("text", "") for doc in batch], is_query=False)
        except EncoderUnavailable as err:
            print(f"backfill: encoder unavailable, stopping vector sweep ({err})")
            break

        for doc, vec in zip(batch, vectors, strict=True):
            await memory_units.update_one(
                {"_id": doc["_id"]},
                {"$set": {"vec": vec, "embedModel": EMBED_MODEL_NAME, "embedDim": EMBED_DIM}},
            )
        done += len(batch)
        print(f"backfill: vectors {done}/{total}")

    return done


async def main() -> None:
    """Input: none. Output: none — runs both sweeps and prints what changed."""
    # The entity index and the edge indexes are created here too, so a backfill
    # run against a database the server has never started against still ends
    # with a queryable graph rather than a collection scan.
    await ensure_indexes()

    units, edges = await backfill_entities()
    vectors = await backfill_vectors()

    print(f"\nbackfill: done — {units} unit(s) given entities, {edges} edge upsert(s), {vectors} unit(s) re-encoded")
    if not units and not vectors:
        print("backfill: nothing to do (everything is already indexed)")


if __name__ == "__main__":
    asyncio.run(main())
