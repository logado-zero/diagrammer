"""
Every read and write against the `memory_units` collection and the GridFS
attachment bucket. Nothing else in the codebase touches them directly.
(One read reaches into `conversations` as well — `conversation_titles()` — for
the reason given at that function.)

The rule this module exists to enforce: **a `Scope` is the only way to filter.**
There is no function here that accepts a raw Mongo filter, and `Scope` cannot
be built without an owner id (see types.py). In a retrieval system a forgotten
tenant filter is the worst bug available — it raises nothing, logs nothing,
passes every test written with a single user, and quietly makes one person's
private conversation retrievable by another. Structural prevention is cheaper
than remembering.

Dense scoring is a brute-force dot product over one owner's vectors, in this
process. That is a deliberate ceiling, not an oversight: MongoDB Community has
no `$vectorSearch` (it is Atlas-only), and because every query is owner-scoped,
cost tracks a single user's history depth rather than the size of the user
base — a thousand users do not make any one query slower. The ceiling is
roughly 10k units per user (~25MB of float32 at 640 dimensions); past that,
the replacement is a real vector index behind these same two function
signatures.
"""

from itertools import combinations
from typing import Any

import numpy as np
from bson import ObjectId
from pymongo import UpdateOne

from server.db import attachments, conversations, memory_edges, memory_units
from server.memory.encoder import EMBED_MODEL_NAME
from server.memory.types import MemoryUnit, Scope

# Fields every scoring function projects. Deliberately excludes `vec`: a
# candidate list carries text and payload, and re-reading the vectors would
# multiply the response size for no reason.
_UNIT_FIELDS = {"kind": 1, "role": 1, "text": 1, "payload": 1, "conversationId": 1, "seq": 1, "at": 1}

# How many of a unit's entities get wired into the graph. Lower than
# entities.MAX_ENTITIES_PER_UNIT on purpose: edges are pairwise, so the write
# cost is quadratic in this number while the lookup cost of the full list
# (units_by_entities below) is linear. The list is priority-ordered — most
# specific first — so the top slice is exactly the part worth linking.
MAX_EDGE_ENTITIES = 12
# Tighter still across a unit boundary: adjacency is a weaker signal than
# co-occurrence and there are more pairs of it.
MAX_ADJACENT_ENTITIES = 6

# Ceilings on what one graph query is allowed to pull into the process. Both
# are generous relative to real queries and exist so a pathological entity —
# one that ended up on thousands of units — degrades the ranking rather than
# the event loop.
MAX_EDGES_LOADED = 2000
MAX_GRAPH_CANDIDATES = 400


async def put(units: list[MemoryUnit]) -> list[ObjectId]:
    """
    Input: the units extracted from one turn. Output: their new ids.
    Written in a single `insert_many` so a turn's units either all land or
    none do, rather than leaving a half-indexed turn behind.
    """
    if not units:
        return []
    result = await memory_units.insert_many([u.to_document() for u in units])
    return list(result.inserted_ids)


def _edge_pairs(units: list[tuple[Any, int, list[str]]]) -> list[tuple[str, str, str]]:
    """
    Input: (conversation id, seq, entities) for a run of units, in order.
    Output: (src, dst, kind) triples — every edge those units imply, in both
    directions.

    Takes tuples rather than `MemoryUnit`s so the same function serves all three
    callers: `put_edges()` adds these weights, `delete_conversation()` subtracts
    them, and `backfill.py` adds them for history. Deleting has to regenerate
    the *identical* multiset of pairs that was written, which is only guaranteed
    if one function produces all of them.

    `seq` is in the input for one reason: adjacency requires units that really
    are neighbours. Two units of the same conversation that merely arrived next
    to each other in a list — which is what a partial backfill produces — are
    not adjacent, and linking them would invent a relationship from a gap in the
    data.

    Two kinds, both purely observational (see db.py's note on the collection):
    `cooccur` links entities seen inside the same unit, `adjacent` links across
    consecutive units of the same conversation, which is what carries "the reply
    that answered this question" into the graph.

    Stored as two directed edges per undirected link. That doubles the rows and
    buys the thing that matters at query time: neighbour lookup is one indexed
    equality match on `src` instead of an `$or` over both fields.
    """
    pairs: list[tuple[str, str, str]] = []

    for _conversation_id, _seq, entities in units:
        for a, b in combinations(entities[:MAX_EDGE_ENTITIES], 2):
            pairs.append((a, b, "cooccur"))
            pairs.append((b, a, "cooccur"))

    for (earlier_id, earlier_seq, earlier), (later_id, later_seq, later) in zip(units, units[1:], strict=False):
        if earlier_id != later_id or later_seq != earlier_seq + 1:
            continue
        for a in earlier[:MAX_ADJACENT_ENTITIES]:
            for b in later[:MAX_ADJACENT_ENTITIES]:
                if a == b:
                    continue  # a self-loop carries no information and skews PPR
                pairs.append((a, b, "adjacent"))
                pairs.append((b, a, "adjacent"))

    return pairs


async def _apply_edges(owner_id: ObjectId, pairs: list[tuple[str, str, str]], delta: int) -> int:
    """
    Input: the owner, the edges to adjust, and +1 to record them or -1 to
    withdraw them. Output: how many upserts were issued.

    `$inc` upserts against the unique index, never plain inserts: an entity pair
    seen in three turns is one edge of weight 3, and running the backfill twice
    must strengthen the graph rather than duplicate it. Getting this wrong turns
    `weight` into a count of how many times the indexer ran.

    Unordered so one failing upsert doesn't abandon the rest — a missing edge
    costs a little recall, and both callers run somewhere that must not be able
    to break the thing it was enriching.
    """
    if not pairs:
        return 0
    operations = [
        UpdateOne(
            {"ownerId": owner_id, "src": src, "dst": dst, "kind": kind},
            {"$inc": {"weight": delta}},
            upsert=True,
        )
        for src, dst, kind in pairs
    ]
    await memory_edges.bulk_write(operations, ordered=False)
    return len(operations)


async def put_edges(owner_id: ObjectId, units: list[MemoryUnit]) -> int:
    """
    Input: the owner and the units of one turn (already carrying entities).
    Output: how many edge upserts were issued.
    """
    return await put_edges_for(owner_id, [(u.conversation_id, u.seq, u.entities) for u in units])


async def put_edges_for(owner_id: ObjectId, run: list[tuple[Any, int, list[str]]]) -> int:
    """
    Input: the owner and (conversation id, seq, entities) for a run of units in
    seq order. Output: how many edge upserts were issued.

    The same write as `put_edges()` for callers that hold documents rather than
    `MemoryUnit`s — backfill.py, which reads history straight out of Mongo. It
    exists so that path doesn't have to reach for a private helper and drift
    away from how live ingest builds the identical graph.
    """
    return await _apply_edges(owner_id, _edge_pairs(run), +1)


async def neighbours(scope: Scope, entities: list[str]) -> dict[str, dict[str, float]]:
    """
    Input: a scope and the entities a query resolved to.
    Output: {entity: {neighbour: weight}} — one hop of this owner's graph.

    **The owner filter here is the whole tenant boundary of the graph view.**
    Entity strings are global by nature: "SJC" is a legitimate node in every
    account's graph, so nothing about the *string* distinguishes one user's
    subgraph from another's. Only `ownerId` does, and it has to hold at every
    hop rather than only at the seed — which is why graph.py walks over the
    adjacency this returns instead of querying the collection itself.
    """
    if not entities:
        return {}

    adjacency: dict[str, dict[str, float]] = {}
    cursor = memory_edges.find(
        {"ownerId": scope.owner_id, "src": {"$in": entities}},
        {"src": 1, "dst": 1, "weight": 1},
    ).limit(MAX_EDGES_LOADED)
    async for edge in cursor:
        adjacency.setdefault(edge["src"], {})[edge["dst"]] = float(edge.get("weight", 1))
    return adjacency


async def units_by_entities(scope: Scope, activation: dict[str, float], limit: int) -> list[tuple[dict[str, Any], float]]:
    """
    Input: a scope, {entity: activation} from the graph walk, and how many
    candidates to return.
    Output: (unit document, score) pairs, best first — the same shape dense()
    and lexical() return, so retrieve.py's fusion takes it unchanged.

    A unit's score is the sum of the activations of the entities it carries: a
    unit sitting on three activated entities outranks one sitting on a single
    stronger entity, which is the behaviour that makes the graph a *view* rather
    than a second keyword index.

    Sorted by recency before the cap rather than taken in Mongo's arbitrary
    order — if a popular entity has more units than the ceiling, the recent ones
    are the defensible ones to keep in a chat application.
    """
    if not activation:
        return []

    scored: list[tuple[dict[str, Any], float]] = []
    cursor = (
        memory_units.find(
            {**scope.as_filter(), "entities": {"$in": list(activation)}},
            {**_UNIT_FIELDS, "entities": 1},
        )
        .sort([("at", -1)])
        .limit(MAX_GRAPH_CANDIDATES)
    )
    async for doc in cursor:
        score = sum(activation.get(entity, 0.0) for entity in doc.get("entities") or [])
        if score > 0:
            scored.append((doc, score))

    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:limit]


async def local_neighbours(scope: Scope, keys: list[tuple[ObjectId, int]]) -> list[dict[str, Any]]:
    """
    Input: a scope and the (conversation id, seq) of units that already ranked.
    Output: the unit documents immediately before and after each of them.

    Zero-Mem's evidence closure, and the reason it exists is the shortest
    example in DISCUSS_RAG.md: "change the colour to blue" retrieves nothing
    useful on its own, because the turn *before* it is what names the thing
    being recoloured. A hit without its neighbours is a sentence without its
    subject.

    One `$or` query rather than two per hit, over the existing
    {ownerId, conversationId, seq} index.
    """
    if not keys:
        return []
    clauses = [
        {"conversationId": conversation_id, "seq": {"$in": [seq - 1, seq + 1]}}
        for conversation_id, seq in keys
        if conversation_id is not None
    ]
    if not clauses:
        return []
    cursor = memory_units.find({**scope.as_filter(), "$or": clauses}, _UNIT_FIELDS)
    return [doc async for doc in cursor]


async def save_blob(filename: str, data: bytes, media_type: str, owner_id: ObjectId) -> ObjectId:
    """
    Input: the original bytes of an attachment plus who owns them.
    Output: the GridFS file id, stored on the unit as `blobId`.
    The owner is recorded in the file metadata as well as on the unit, so an
    orphaned blob can still be traced back to an account.
    """
    return await attachments.upload_from_stream(
        filename,
        data,
        metadata={"mediaType": media_type, "ownerId": owner_id},
    )


async def count(scope: Scope) -> int:
    """Input: a scope. Output: how many units it contains. Used by the self-check and for capacity monitoring."""
    return await memory_units.count_documents(scope.as_filter())


async def lexical(scope: Scope, query: str, limit: int) -> list[tuple[dict[str, Any], float]]:
    """
    Input: a scope, the raw query text, and how many candidates to return.
    Output: (unit document, score) pairs, best first.

    Mongo's `$text` index rather than a BM25 library: the database already has
    the index, and adding a dependency to re-rank the same terms in Python
    would buy a marginal scoring difference on rare terms and nothing else.
    Exact names, dates, numbers, and quoted phrases are what this view is for —
    the dense view below is what covers paraphrase.
    """
    cursor = (
        memory_units.find(
            {**scope.as_filter(), "$text": {"$search": query}},
            {**_UNIT_FIELDS, "score": {"$meta": "textScore"}},
        )
        .sort([("score", {"$meta": "textScore"})])
        .limit(limit)
    )
    return [(doc, float(doc.get("score", 0.0))) async for doc in cursor]


async def dense(scope: Scope, query_vec: bytes, limit: int) -> list[tuple[dict[str, Any], float]]:
    """
    Input: a scope, a packed query vector, and how many candidates to return.
    Output: (unit document, cosine similarity) pairs, best first.

    Two passes on purpose. The first reads only `_id` and `vec` to score, so
    the working set stays the vectors themselves rather than every unit's full
    text; the second fetches the documents for the handful that won. One pass
    would pull megabytes of text through Mongo to throw almost all of it away.

    The `embedModel`/`embedSpace` filter is a correctness guard, not an
    optimization: vectors produced by different models occupy unrelated spaces,
    and scoring across them returns confident nonsense.
    """
    scoring_filter = {
        **scope.as_filter(),
        "embedSpace": "text",
        "embedModel": EMBED_MODEL_NAME,
        "vec": {"$ne": None},
    }

    ids: list[ObjectId] = []
    vectors: list[np.ndarray] = []
    async for doc in memory_units.find(scoring_filter, {"vec": 1}):
        ids.append(doc["_id"])
        vectors.append(np.frombuffer(doc["vec"], dtype=np.float32))
    if not ids:
        return []

    # Both sides are already L2-normalized by the encoder, so a dot product is
    # the cosine similarity — no division needed.
    matrix = np.vstack(vectors)
    scores = matrix @ np.frombuffer(query_vec, dtype=np.float32)

    top = np.argsort(scores)[::-1][:limit]
    winners = {ids[i]: float(scores[i]) for i in top}

    documents = {
        doc["_id"]: doc
        async for doc in memory_units.find({**scope.as_filter(), "_id": {"$in": list(winners)}}, _UNIT_FIELDS)
    }
    # Re-sorted rather than trusting Mongo's return order, which is unspecified.
    return sorted(
        ((documents[i], s) for i, s in winners.items() if i in documents),
        key=lambda pair: pair[1],
        reverse=True,
    )


async def conversation_titles(scope: Scope, ids: list[ObjectId]) -> dict[ObjectId, str]:
    """
    Input: a scope and the conversation ids a result set referred to.
    Output: {conversation id: title} for the ones this owner actually owns.

    The only read in this module that leaves `memory_units`. It lives here
    rather than in retrieve.py so the owner filter stays in the one file that
    enforces it — a title is a small leak, but it is still a leak, and it would
    be one written in a file that has no other reason to think about tenancy.

    One query for the whole result set, not one per hit: eight hits from eight
    conversations would otherwise be eight round trips on the response path.
    Ids missing from the result are simply absent, which callers render as an
    untitled hit — a conversation deleted between indexing and searching is
    normal, not an error.
    """
    if not ids:
        return {}
    cursor = conversations.find(
        {"_id": {"$in": list(set(ids))}, "ownerId": scope.owner_id},
        {"title": 1},
    )
    return {doc["_id"]: doc.get("title", "") async for doc in cursor}


async def delete_conversation(scope: Scope) -> int:
    """
    Input: a scope narrowed to one conversation. Output: how many units were removed.

    Called when a conversation is deleted. Without this, deleted content stays
    retrievable — a correctness bug and a privacy one, since the user was told
    it was gone. GridFS blobs go with the units; a failure to delete one is
    logged rather than raised, because a leaked blob is much less bad than a
    delete route that 500s and leaves everything in place.

    **Edges are withdrawn, not deleted.** They are owner-scoped rather than
    conversation-scoped, and the same entity pair may well have been observed in
    conversations the user is keeping — so this subtracts exactly the weight
    this conversation contributed (regenerated from the units' own entities by
    the same `_edge_pairs()` that added it) and then drops whatever falls to
    zero. Deleting outright would tear holes in the graph of surviving
    conversations; leaving the weight would keep a residue of deleted content.
    """
    if scope.conversation_id is None:
        raise ValueError("delete_conversation needs a Scope narrowed to one conversation")

    # Read what the units imply before they are gone: entities for the edge
    # withdrawal, blob ids for GridFS. Ordered by seq so the adjacency pairs
    # regenerate in the same order they were written in.
    doomed: list[tuple[Any, int, list[str]]] = []
    blob_ids: list[ObjectId] = []
    async for doc in memory_units.find(scope.as_filter(), {"entities": 1, "blobId": 1, "conversationId": 1, "seq": 1}).sort(
        [("seq", 1)]
    ):
        doomed.append((doc.get("conversationId"), doc.get("seq", 0), doc.get("entities") or []))
        if doc.get("blobId") is not None:
            blob_ids.append(doc["blobId"])

    for blob_id in blob_ids:
        try:
            await attachments.delete(blob_id)
        except Exception as err:  # noqa: BLE001 - an orphaned blob must not fail the user's delete
            print(f"memory: could not delete blob {blob_id}: {err}")

    await _apply_edges(scope.owner_id, _edge_pairs(doomed), -1)
    await memory_edges.delete_many({"ownerId": scope.owner_id, "weight": {"$lte": 0}})

    result = await memory_units.delete_many(scope.as_filter())
    return result.deleted_count
