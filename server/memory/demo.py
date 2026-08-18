"""
Self-check for the memory layer. Run with:

    python -m server.memory.demo

Three groups, ordered by what they need, so a partial environment still tells
you something useful:

1. **Pure** — description building, score fusion, and the text the
   search_memory tool hands back to the model. No model, no database.
2. **Encoder** — needs the Harrier weights (downloaded on first run, a few
   hundred MB). Skipped with a message if they can't be loaded.
3. **Store** — needs a running MongoDB. Contains the tenant-isolation
   assertion, which is the one check that must pass before this ships to a
   second user: a retrieval bug that leaks across accounts raises nothing,
   logs nothing, and passes every test written with one user.

Everything it writes is scoped to throwaway owner ids and deleted afterwards.
"""

import asyncio
import os
from datetime import datetime
from typing import Any

from bson import ObjectId

from server.memory import context, entities, extract, graph, profile, retrieve, store, tool
from server.memory.encoder import EMBED_DIM, EncoderUnavailable, encode, to_array
from server.memory.types import MemoryUnit, Scope, SearchHit


def _check_pure() -> None:
    """Input: none. Output: none — asserts the parts that need no model and no database."""
    chart = {
        "title": "SJC gold sell price",
        "option": {
            "xAxis": {"data": ["1 Aug", "2 Aug", "3 Aug"]},
            "yAxis": {"name": "VND millions"},
            "series": [{"name": "Sell", "type": "line", "data": [1, 2, 3]}],
        },
    }
    described = extract._describe_chart(chart)
    assert "SJC gold sell price" in described
    assert "1 Aug" in described and "VND millions" in described
    assert "Sell" in described and "line chart" in described

    # A pie chart keeps its labels in the data points, not on an axis — the one
    # shape that would silently contribute no searchable text if missed.
    pie = {"title": "Share", "option": {"series": [{"type": "pie", "data": [{"name": "Hanoi", "value": 4}]}]}}
    assert "Hanoi" in extract._describe_chart(pie)

    diagram = {
        "title": "Order flow",
        "mermaid": 'flowchart TD\n  a["Place order"] --> b["Ship it"]\n  classDef process fill:#fff',
    }
    described = extract._describe_diagram(diagram)
    assert "Place order" in described and "Ship it" in described
    assert "classDef" not in described, "styling lines are not searchable content"

    scope = Scope(owner_id=ObjectId())
    assert "conversationId" not in scope.as_filter()
    assert "conversationId" in Scope(owner_id=ObjectId(), conversation_id=ObjectId()).as_filter()

    # Fusion: a unit both views agree on beats one that only the single, more
    # heavily weighted view found, even though that one ranked top there.
    both, dense_top, weak = ObjectId(), ObjectId(), ObjectId()
    hits = retrieve._fuse(
        [
            (
                "dense",
                [
                    ({"_id": dense_top, "text": "d"}, 1.0),
                    ({"_id": both, "text": "b"}, 0.9),
                    ({"_id": weak, "text": "w"}, 0.5),
                ],
            ),
            ("lexical", [({"_id": both, "text": "b"}, 5.0), ({"_id": weak, "text": "w"}, 1.0)]),
        ],
        {"dense": 0.6, "lexical": 0.4},
        limit=5,
    )
    assert hits[0].unit_id == both, "agreement across views should win"
    assert set(hits[0].view_scores) == {"dense", "lexical"}

    # Min-max scaling puts each view's *worst* candidate at exactly 0, so being
    # last in a view contributes nothing rather than a small amount. That is
    # what Zero-Mem's Eq. 12 specifies and it's fine at the real
    # CANDIDATES_PER_VIEW of 40 — but it's surprising enough when reading a
    # ranking that it's worth pinning down here.
    assert hits[-1].unit_id == weak and hits[-1].score == 0.0

    # A view where everything ties still carries its full opinion forward
    # rather than collapsing to zero.
    tied = retrieve._normalize([({"_id": 1}, 3.0), ({"_id": 2}, 3.0)])
    assert all(score == 1.0 for _, score in tied.values())
    assert retrieve._normalize([]) == {}

    # Evidence closure appends units that matched nothing. They must never be
    # counted or presented as results — a model told these matched will cite
    # them as answers.
    match = SearchHit(unit_id=ObjectId(), kind="turn", text="real match", score=0.8)
    adjacent = SearchHit(unit_id=ObjectId(), kind="turn", text="the turn next to it", score=0.0, is_context=True)
    rendered = tool.format_hits([match, adjacent])
    assert "1 result" in rendered and "plus 1 adjacent for context" in rendered, rendered
    assert "context, adjacent to a match" in rendered
    assert tool.retrieval_record("q", [match, adjacent])["label"] == "Memory: 1 result"
    assert tool.retrieval_record("q", [match, adjacent])["list_result"][1]["isContext"] is True

    # The two entry points are distinguishable in the log, which is the only way
    # to tell "the turn retrieved and the model searched anyway" from either one
    # alone.
    assert tool.retrieval_record("q", [])["source"] == "tool"
    assert tool.retrieval_record("q", [], source="auto")["source"] == "auto"

    _check_tool_format()

    # The pure halves of the new modules own their own assertions; running them
    # from here keeps `python -m server.memory.demo` the single merge gate
    # rather than one of five commands somebody has to remember.
    entities._demo()
    graph._demo()
    profile._demo()
    context._demo()
    print("  pure          ok")


def _check_tool_format() -> None:
    """
    Input: none. Output: none — asserts what the model actually receives from
    search_memory.

    Worth pinning down separately because this is the layer that fails
    *plausibly*: a hit missing its title or turn number still renders fine, the
    model just quietly stops being able to cite anything, and nothing anywhere
    reports an error.
    """
    rendered = tool.format_hits(
        [
            SearchHit(
                unit_id=ObjectId(),
                kind="chart",
                text='Bar chart "Revenue by region".',
                score=0.9,
                payload={"title": "Revenue by region", "chartType": "bar"},
                conversation_id=ObjectId(),
                seq=14,
                role="assistant",
                at=datetime(2026, 8, 3),
                conversation_title="Q3 revenue review",
            )
        ]
    )
    assert "Q3 revenue review" in rendered, "the model can't cite a source it isn't told"
    assert "turn 14" in rendered
    assert "2026-08-03" in rendered and "assistant" in rendered
    assert "chart" in rendered
    # The raw payload has to survive: it's the exact numbers a follow-up needs,
    # and the whole reason extract.py stores it verbatim next to the prose.
    assert '"chartType": "bar"' in rendered or '"chartType":"bar"' in rendered

    # A deleted-since-indexing conversation still renders rather than crashing
    # on a missing title.
    untitled = tool.format_hits([SearchHit(unit_id=ObjectId(), kind="turn", text="hello", score=0.1)])
    assert "untitled" in untitled and "turn 0" in untitled

    # A sheet window is a 100-row markdown table; eight of them unbudgeted
    # would cost more context than the truncated history this tool exists to
    # make up for.
    long_hit = SearchHit(unit_id=ObjectId(), kind="sheet", text="x" * 5000, score=0.5, conversation_title="Big file")
    capped = tool.format_hits([long_hit])
    assert len(capped) < 2000, f"a 5000-char hit must be truncated, got {len(capped)}"
    assert "truncated" in capped and "5000 chars total" in capped

    # Never an empty string: a blank tool result reads as a malfunction and the
    # model's usual response is to call the tool again with different wording.
    assert tool.format_hits([]).strip(), "an empty result must still say something"
    assert "No matches" in tool.format_hits([])

    _check_retrieval_record()
    _check_limit()


def _no_object_ids(value: Any, path: str = "record") -> None:
    """
    Input: any part of a retrieval record. Output: none — raises if an ObjectId
    survives anywhere inside it.

    GET /api/conversations/{id}/logs hands `steps` straight to FastAPI's JSON
    encoder, which copes with datetime but raises on ObjectId. A raw id here
    wouldn't fail retrieval, or the chat turn, or this file's other assertions —
    it would 500 the one route that exists to read these records, and only once
    someone tried to read one. Cheaper to assert than to discover.
    """
    assert not isinstance(value, ObjectId), f"ObjectId left at {path} — GET .../logs would 500"
    if isinstance(value, dict):
        for key, item in value.items():
            _no_object_ids(item, f"{path}.{key}")
    elif isinstance(value, list):
        for i, item in enumerate(value):
            _no_object_ids(item, f"{path}[{i}]")


def _check_retrieval_record() -> None:
    """
    Input: none. Output: none — asserts the shape of the log entry a search
    writes to agent_logs.

    One record per search with the hits nested inside it, rather than one entry
    per hit: the flat version turned a two-step turn into a twelve-step one and
    lost which hits belonged to which search.
    """
    hits = [
        SearchHit(
            unit_id=ObjectId(),
            kind="chart",
            text="Bar chart.",
            score=0.82,
            view_scores={"dense": 0.91, "lexical": 0.4},
            conversation_id=ObjectId(),
            seq=14,
            role="assistant",
            at=datetime(2026, 8, 3),
            conversation_title="Q3 revenue review",
        ),
        SearchHit(unit_id=ObjectId(), kind="turn", text="hello", score=0.4, seq=2, conversation_title="Other chat"),
    ]
    record = tool.retrieval_record("revenue by region", hits)
    assert record["label"] == "Memory: 2 results", record["label"]
    assert record["query"] == "revenue by region" and record["status"] == "ok"

    results = record["list_result"]
    assert len(results) == len(hits), results
    assert [r["rank"] for r in results] == [1, 2], "ranks are 1-based and in ranking order"
    first = results[0]
    assert first["conversation"] == "Q3 revenue review" and first["seq"] == 14
    assert first["role"] == "assistant" and first["kind"] == "chart" and first["score"] == 0.82
    # The field that says *why* something ranked where it did — the whole reason
    # this is a structured record rather than a formatted line.
    assert first["viewScores"] == {"dense": 0.91, "lexical": 0.4}, first["viewScores"]
    assert isinstance(first["unitId"], str) and isinstance(first["conversationId"], str)
    assert results[1]["conversationId"] is None, "a hit with no conversation must not crash str()"
    _no_object_ids(record)

    # Singular/plural, and both not-found paths still produce a record — "the
    # model searched and retrieval was down" is exactly what a log must not omit.
    assert tool.retrieval_record("q", hits[:1])["label"] == "Memory: 1 result"
    for status in ("empty-query", "unavailable: ServerSelectionTimeoutError"):
        empty = tool.retrieval_record("q", [], status=status)
        assert empty["list_result"] == [] and empty["status"] == status, empty

    # Per-hit text is capped (the log judges relevance, it doesn't reproduce the
    # unit) — but the query is stored whole, since it's the field you'd search by.
    long_query = "doanh thu " * 40
    wordy = tool.retrieval_record(long_query, [SearchHit(unit_id=ObjectId(), kind="turn", text="x" * 5000, score=0.1)])
    assert wordy["query"] == long_query, "the query must not be truncated in the record"
    assert len(wordy["list_result"][0]["text"]) < tool.MAX_LOG_TEXT_CHARS + 60
    assert "truncated" in wordy["list_result"][0]["text"]


def _check_limit() -> None:
    """Input: none. Output: none — asserts MEMORY_SEARCH_LIMIT is honoured, and that a bad value can't break a live turn."""
    previous = os.environ.pop("MEMORY_SEARCH_LIMIT", None)
    try:
        assert tool._limit() == 10, "the documented default is 10"
        for value, expected in (("3", 3), ("0", 1), ("9999", tool.MAX_LIMIT), ("abc", 10), ("", 10)):
            os.environ["MEMORY_SEARCH_LIMIT"] = value
            assert tool._limit() == expected, f"MEMORY_SEARCH_LIMIT={value!r} -> {tool._limit()}, want {expected}"
    finally:
        os.environ.pop("MEMORY_SEARCH_LIMIT", None)
        if previous is not None:
            os.environ["MEMORY_SEARCH_LIMIT"] = previous


async def _check_encoder() -> bool:
    """Input: none. Output: whether the encoder was available (the rest is asserted)."""
    # Vietnamese on purpose: this app already handles Vietnamese spreadsheets,
    # and an English-only encoder would pass an English-only check.
    text = "doanh thu quý 3"
    try:
        as_document = await encode([text], is_query=False)
        as_query = await encode([text], is_query=True)
    except EncoderUnavailable as err:
        print(f"  encoder       SKIPPED — {err}")
        return False

    vec = to_array(as_document[0])
    assert vec.shape == (EMBED_DIM,), f"expected {EMBED_DIM} dims, got {vec.shape}"
    assert abs(float((vec * vec).sum()) - 1.0) < 1e-3, "vectors must be L2-normalized"

    # The same text encoded as a query and as a document must differ, or the
    # Harrier instruction prefix isn't actually being applied — a failure mode
    # that otherwise costs retrieval quality silently.
    assert to_array(as_query[0]).tolist() != vec.tolist(), "is_query must change the encoding"

    print("  encoder       ok")
    return True


async def _check_store(with_vectors: bool) -> None:
    """
    Input: whether encoding is available. Output: none.
    Writes units for two owners, reaches for them every way this system can, and
    asserts the other's are unreachable through all of them — the tenant-
    isolation gate, and the one check that must pass before this ships to a
    second user.

    Both owners are seeded with the *same* entity strings on purpose. Entity
    strings are global by nature — "warehouse" is a legitimate node in every
    account's graph — so the graph view is the first thing here that could join
    two people's memories through a value they genuinely share. Each owner also
    gets one entity nobody else has ("aliceonly" / "bobonly"), which is what
    makes a leak assertable rather than merely unlikely.
    """
    alice, bob = ObjectId(), ObjectId()
    conversation = ObjectId()
    bob_conversation = ObjectId()
    scope_alice = Scope(owner_id=alice)
    scope_bob = Scope(owner_id=bob)

    def unit(owner: ObjectId, text: str, seq: int, conversation_id: ObjectId) -> MemoryUnit:
        return MemoryUnit(
            owner_id=owner,
            conversation_id=conversation_id,
            kind="turn",
            role="user",
            text=text,
            seq=seq,
            at=datetime.now(),
            entities=entities.extract_entities(text),
        )

    # Near-identical text across owners: if the filter is missing anywhere,
    # Bob's row is exactly what a search as Alice would surface.
    shared = "quarterly revenue for the Hanoi warehouse"
    alice_units = [
        unit(alice, shared, 0, conversation),
        unit(alice, "the ALICEONLY code covers that warehouse", 1, conversation),
    ]
    bob_units = [
        unit(bob, shared, 0, bob_conversation),
        unit(bob, "the BOBONLY code covers that warehouse", 1, bob_conversation),
    ]
    units = alice_units + bob_units

    # The shared entity has to actually be shared, or every assertion below
    # passes for the wrong reason.
    assert "warehouse" in alice_units[0].entities and "warehouse" in bob_units[0].entities
    assert "aliceonly" in alice_units[1].entities and "bobonly" in bob_units[1].entities

    if with_vectors:
        vectors = await encode([u.text for u in units], is_query=False)
        from server.memory.encoder import EMBED_MODEL_NAME

        for u, v in zip(units, vectors, strict=True):
            u.vec, u.embed_model, u.embed_dim = v, EMBED_MODEL_NAME, EMBED_DIM

    # The lexical view needs the text index, which the app creates at startup.
    # Creating it here too keeps the self-check runnable without booting the
    # server, and it's idempotent.
    from server.db import conversations, ensure_indexes, memory_edges, memory_units

    await ensure_indexes()

    # Real conversation documents, because a hit's title is resolved from this
    # collection rather than copied onto the unit. Titles are deliberately
    # distinctive: a leaked *title* is a smaller leak than leaked text, but it
    # is still one, and it would go unnoticed without something to grep for.
    await conversations.insert_many(
        [
            {"_id": conversation, "ownerId": alice, "title": "Alice warehouse planning"},
            {"_id": bob_conversation, "ownerId": bob, "title": "Bob private budget"},
        ]
    )

    try:
        ids = await store.put(units)
        alice_ids, bob_ids = set(ids[:2]), set(ids[2:])
        await store.put_edges(alice, alice_units)
        await store.put_edges(bob, bob_units)
        assert await store.count(scope_alice) == 2
        assert await store.count(scope_bob) == 2

        hits = await retrieve.search(scope_alice, "quarterly revenue Hanoi", k=10)
        assert hits, "Alice must find her own units"
        assert not ({h.unit_id for h in hits} & bob_ids), "TENANT LEAK: search as Alice returned Bob's unit"
        assert all(h.conversation_title == "Alice warehouse planning" for h in hits), "TENANT LEAK: title"
        # The provenance fields, asserted against real documents rather than a
        # hand-built SearchHit — this is what proves the projection and the
        # batched title lookup are actually wired together.
        assert any(h.seq == 0 and h.role == "user" and h.at is not None for h in hits), hits

        # All three views reach the ranking. Without this, a graph view that
        # silently returned nothing — a lost owner filter, a missing index, an
        # extractor that stopped agreeing with itself across the query/document
        # boundary — would look exactly like one that had nothing to add.
        assert any("graph" in h.view_scores for h in hits), [h.view_scores for h in hits]
        assert any("lexical" in h.view_scores for h in hits), [h.view_scores for h in hits]

        await _check_graph_isolation(scope_alice, alice_ids, bob_ids)
        await _check_closure(scope_alice, conversation)
        await _check_push_path(scope_alice, bob_ids)

        # …and the pull path, with the log sink attached — agent_logs is a second
        # surface the same titles and text reach, so it needs its own assertion.
        alice_log: list[dict[str, Any]] = []
        rendered = await tool.run_search_memory(scope_alice, {"query": "quarterly revenue Hanoi"}, alice_log)
        assert "Alice warehouse planning" in rendered, rendered
        assert "Bob private budget" not in rendered, "TENANT LEAK: Bob's conversation title reached Alice"
        assert "turn 0" in rendered

        assert len(alice_log) == 1, f"one search must log exactly one record, got {len(alice_log)}"
        assert alice_log[0]["source"] == "tool", alice_log[0]["source"]
        results = alice_log[0]["list_result"]
        assert {r["conversation"] for r in results} == {"Alice warehouse planning"}, results
        # By id, not by text: the seeded units say the same thing on purpose, so
        # only the id and the title tell them apart.
        assert not any(str(b) in str(results) for b in bob_ids), "TENANT LEAK: Bob's unit reached Alice's log record"
        assert "Bob private budget" not in str(results), "TENANT LEAK: Bob's title reached Alice's log record"
        assert "bobonly" not in str(results).casefold(), "TENANT LEAK: Bob's private entity reached Alice"
        _no_object_ids(alice_log[0])

        # There is no relevance floor, and that is worth pinning rather than
        # discovering later: cosine similarity always has a nearest neighbour,
        # so an unrelated query still returns this owner's best-ranked units.
        # The model is warned about it in MEMORY_TOOL_PROMPT instead of being
        # protected by a threshold, because picking a threshold without an
        # evaluation set is guessing (see DISCUSS_RAG.md).
        unrelated = await tool.run_search_memory(scope_alice, {"query": "zzzz nonexistent xyzzy"})
        assert "Alice warehouse planning" in unrelated, "dense retrieval has no cutoff — expected the top unit back"

        # The genuinely-empty path: an owner with nothing stored. Must still
        # come back with something the model can act on, never "".
        stranger = await tool.run_search_memory(Scope(owner_id=ObjectId()), {"query": "anything at all"})
        assert stranger.strip() and "No matches" in stranger, stranger
        assert (await tool.run_search_memory(scope_alice, {"query": "   "})).strip(), "a blank query must not return ''"

        # Asserted from both sides, so a filter that happens to be right for one
        # owner but wrong for the other still fails.
        bob_hits = await retrieve.search(scope_bob, "quarterly revenue Hanoi", k=10)
        assert not ({h.unit_id for h in bob_hits} & alice_ids), "TENANT LEAK: search as Bob returned Alice's unit"
        assert all(h.conversation_title != "Alice warehouse planning" for h in bob_hits), "TENANT LEAK: title"

        # Deletion cascades to the graph. Edges are withdrawn by weight rather
        # than deleted outright (they are owner-scoped, and other conversations
        # may share a pair), so what must be gone is Alice's private entity —
        # while Bob's identical-looking graph is untouched.
        removed = await store.delete_conversation(Scope(owner_id=alice, conversation_id=conversation))
        assert removed == 2, f"delete must be owner-scoped too, removed {removed}"
        assert await store.count(scope_bob) == 2, "deleting Alice's conversation must not touch Bob's"
        assert not await store.neighbours(scope_alice, ["aliceonly"]), "deleted content left edges behind"
        assert await store.neighbours(scope_bob, ["bobonly"]), "Bob's edges must survive Alice's delete"
    finally:
        await memory_units.delete_many({"ownerId": {"$in": [alice, bob]}})
        await memory_edges.delete_many({"ownerId": {"$in": [alice, bob]}})
        await conversations.delete_many({"_id": {"$in": [conversation, bob_conversation]}})

    print("  store         ok (incl. tenant isolation across the graph)")


async def _check_graph_isolation(scope_alice: Scope, alice_ids: set, bob_ids: set) -> None:
    """
    Input: Alice's scope and both owners' unit ids. Output: none.

    The graph view is a new way to leave a tenant, and a uniquely quiet one.
    Every other query filters `ownerId` once, at the leaf; a walk hops
    unit -> entity -> unit, and "warehouse" is a real node in both accounts. If
    `store.neighbours()` ever loses its owner filter, this is where a query as
    Alice starts returning Bob's memories — with no error and nothing in a log.
    """
    # One hop from the entity they share must reach Alice's private entity and
    # never Bob's, even though both are exactly one hop from "warehouse".
    adjacency = await store.neighbours(scope_alice, ["warehouse"])
    reachable = {neighbour for edges in adjacency.values() for neighbour in edges}
    assert "aliceonly" in reachable, f"Alice's own graph is not connected: {reachable}"
    assert "bobonly" not in reachable, "TENANT LEAK: Bob's entity is one hop from Alice's query"

    # The walk itself, and then the units it scores — the two halves that make
    # the view, asserted separately so a leak is attributable.
    activation = graph.personalized_pagerank(await graph.load_adjacency(scope_alice, ["warehouse"]), {"warehouse": 1.0})
    assert "bobonly" not in activation, "TENANT LEAK: the PPR walk reached Bob's subgraph"

    scored = await store.units_by_entities(scope_alice, {"warehouse": 1.0, "bobonly": 1.0}, limit=10)
    found = {doc["_id"] for doc, _ in scored}
    assert found & alice_ids, "Alice's units must score on her own entity"
    assert not (found & bob_ids), "TENANT LEAK: units_by_entities crossed owners"

    # And end to end through the view as retrieve.py calls it.
    view = await graph.graph_view(scope_alice, ["warehouse", "bobonly"], limit=10)
    assert not ({doc["_id"] for doc, _ in view} & bob_ids), "TENANT LEAK: graph_view returned Bob's units"


async def _check_closure(scope_alice: Scope, conversation: ObjectId) -> None:
    """
    Input: Alice's scope and her conversation. Output: none.

    Evidence closure is the answer to "change the colour to blue" — a hit whose
    meaning lives in the turn next to it. Asserted here because it is also a
    read that could forget its owner filter.
    """
    neighbours = await store.local_neighbours(scope_alice, [(conversation, 0)])
    assert [doc["seq"] for doc in neighbours] == [1], f"seq 0's neighbour is seq 1, got {neighbours}"

    # A different owner asking about the same conversation id gets nothing —
    # the conversation id alone must never be enough.
    intruder = await store.local_neighbours(Scope(owner_id=ObjectId()), [(conversation, 0)])
    assert intruder == [], "TENANT LEAK: local_neighbours honoured a foreign conversation id"


async def _check_push_path(scope_alice: Scope, bob_ids: set) -> None:
    """
    Input: Alice's scope and Bob's unit ids. Output: none.

    `select_context` is a second entry point into retrieval, so it is a second
    place a leak could surface — and the more dangerous of the two, because its
    output goes into the prompt without the model ever asking for it.
    """
    from server.types import ChatRequestMessage

    def msg(role: str, text: str) -> ChatRequestMessage:
        return ChatRequestMessage(role=role, text=text)  # type: ignore[arg-type]

    evidence, record = await context.select_context(
        scope_alice,
        [msg("assistant", "Here is the warehouse summary."), msg("user", "what was the quarterly revenue there?")],
    )
    assert evidence, "a real question must retrieve something"
    assert record is not None and record["source"] == "auto", record
    assert "Alice warehouse planning" in evidence, evidence
    assert "Bob private budget" not in evidence, "TENANT LEAK: Bob's title reached Alice's prompt"
    assert "bobonly" not in evidence.casefold(), "TENANT LEAK: Bob's entity reached Alice's prompt"
    assert not any(str(b) in str(record) for b in bob_ids), "TENANT LEAK: Bob's unit reached the auto record"
    _no_object_ids(record)

    # The gate: a greeting costs nothing, and is still logged so "it skipped"
    # and "it never ran" are distinguishable after the fact.
    skipped_evidence, skipped_record = await context.select_context(
        scope_alice, [msg("assistant", "Here is a very long reply. " * 50), msg("user", "thanks")]
    )
    assert skipped_evidence is None, "a greeting must not spend an encode"
    assert skipped_record is not None and skipped_record["status"] == "skipped", skipped_record
    assert skipped_record["list_result"] == []


async def main() -> None:
    """Input: none. Output: none — runs every group and raises on the first failure."""
    print("memory self-check")
    _check_pure()
    has_encoder = await _check_encoder()
    try:
        await _check_store(with_vectors=has_encoder)
    except Exception as err:  # noqa: BLE001 - a dead Mongo should say so, not print a driver traceback
        if err.__class__.__name__ in ("ServerSelectionTimeoutError", "AutoReconnect"):
            print(f"  store         SKIPPED — MongoDB unreachable ({err.__class__.__name__})")
            print("\nStart MongoDB and re-run: the tenant-isolation check is the merge gate.")
            return
        raise
    print("\nall checks passed")


if __name__ == "__main__":
    asyncio.run(main())
