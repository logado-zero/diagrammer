"""
The retrieval layer: what the app remembers across turns and conversations.

It indexes every finished turn (text, chart and diagram payloads, attached
spreadsheets, attached images) into the `memory_units` collection, and reads it
back through **two entry points into one retrieval core**:

- **push** — `select_context()` runs on every turn that looks like it has a
  subject, before the model sees anything, and appends what it found to the
  user's message. The model never has to know it needed to remember.
- **pull** — `search_memory()` is a tool the model calls when it decides it
  wants something specific.

Both run the same `search()`, and the only difference recorded anywhere is the
`source` field on the log entry. Push exists because pull can't be enough on its
own: to call the tool, the model must first notice something is missing, and a
truncated history leaves no trace of what was truncated.

Three phases of DISCUSS_RAG.md's design are built:

- **Write (phase 1).** `index_turn()` runs as a background task after the SSE
  stream closes, so encoding cost and the image-description call are invisible
  to the user.
- **Read (phase 2).** The `search_memory` tool. This is what makes `agent.py`'s
  `HISTORY_LEN = 3` truncation safe: the last three messages are in context, and
  everything older — other conversations, and the full contents of attachments,
  not just the truncated copy inlined into the prompt — is one call away.
- **Zero-Mem (phase 3).** A third retrieval view over an entity co-occurrence
  graph, walked with Personalized PageRank; evidence closure pulling in the
  units adjacent to a hit; a deterministic query profile that decides whether
  retrieving is worth it at all. What remains unbuilt is `Route(q)` — the
  weights are a fixed constant, and honestly so, until there is an evaluation
  set to tune them against.

The public surface is five functions, and other modules import nothing else
from here — the same discipline `agent.py` follows with `run_agent()`:

    index_turn()          write one finished turn into memory (call and forget)
    search()              three-view retrieval over one owner's units
    select_context()      the push path: what this turn should be told, unasked
    search_memory()       the pull path: the same, as a tool result for the model
    forget_conversation() cascade delete when a conversation is deleted

Everything is scoped by `Scope`, which cannot be constructed without an owner
id. See types.py for why that is a type-level rule rather than a convention —
and graph.py for why the graph view makes it matter more, not less.

Module map:

    types.py    Scope / MemoryUnit / SearchHit
    encoder.py  Harrier-270m via onnxruntime, off the event loop
    extract.py  one turn -> units (incl. the image description pass)
    entities.py what a unit is about, as strings — no NER model
    store.py    MongoDB + GridFS, the scoring primitives, the edge writes
    graph.py    Personalized PageRank over the entity graph
    profile.py  φ(q): the retrieval gate, and the query the push path uses
    retrieve.py view fusion + evidence closure
    context.py  the push path
    tool.py     the pull path
    backfill.py `python -m server.memory.backfill` — entities, edges, vectors
    demo.py     `python -m server.memory.demo`
"""

from typing import Any

from bson import ObjectId

from server.shared import now

from server.memory import extract, store
from server.memory.context import select_context
from server.memory.encoder import EMBED_DIM, EMBED_MODEL_NAME, EncoderUnavailable, encode
from server.memory.retrieve import memory_enabled, search
from server.memory.tool import run_search_memory as search_memory
from server.memory.types import MemoryUnit, Scope, SearchHit

__all__ = [
    "index_turn",
    "search",
    "search_memory",
    "select_context",
    "forget_conversation",
    "Scope",
    "SearchHit",
    "memory_enabled",
]


async def index_turn(
    *,
    owner_id: ObjectId,
    conversation_id: ObjectId,
    user_message: Any,
    reply: dict[str, Any],
) -> int:
    """
    Input: who owns the turn, its conversation, the user's original message
    (attachments still attached), and the assistant reply main.py buffered
    while streaming.
    Output: how many units were stored.

    Call this as a background task, never inline — see main.py's
    `_spawn_memory_ingest`. It runs after the SSE stream has already closed, so
    encoding cost and the image-description call are invisible to the user.

    Encoding failure is not an error here. The units are stored with `vec:
    None`, which keeps the conversation complete and lets a later backfill
    re-encode them; only dense retrieval is degraded in the meantime. The
    alternative — refusing to store anything because the embedder is down —
    would lose the data permanently.
    """
    if not memory_enabled():
        return 0

    scope = Scope(owner_id=owner_id, conversation_id=conversation_id)
    units = await extract.units_from_turn(
        owner_id=owner_id,
        conversation_id=conversation_id,
        user_message=user_message,
        reply=reply,
        # Numbering continues from whatever this conversation already has, so
        # `seq` is a genuine total order across turns. One indexed count is
        # cheap here — this runs in the background, after the user's response
        # has already been sent.
        base_seq=await store.count(scope),
        at=now(),
    )
    if not units:
        return 0

    await _attach_vectors(units)
    await store.put(units)
    # Edges after the units land, not before: a graph node pointing at a unit
    # that failed to insert is worse than a unit with no edges yet, and the
    # backfill can always add missing edges while it can't remove phantom ones.
    await store.put_edges(owner_id, units)
    return len(units)


async def _attach_vectors(units: list[MemoryUnit]) -> None:
    """
    Input: units with `vec` unset. Output: none — they're filled in place.
    Encoded as one batch rather than one call per unit: the model runs far
    better on a batch, and a turn with a chunked spreadsheet can easily be a
    dozen units.
    """
    try:
        vectors = await encode([u.text for u in units], is_query=False)
    except EncoderUnavailable as err:
        print(f"memory: storing {len(units)} units without vectors ({err})")
        return

    for unit, vec in zip(units, vectors, strict=True):
        unit.vec = vec
        unit.embed_model = EMBED_MODEL_NAME
        unit.embed_dim = EMBED_DIM


async def forget_conversation(owner_id: ObjectId, conversation_id: ObjectId) -> int:
    """
    Input: the owner and the conversation being deleted.
    Output: how many units were removed.

    Called from the delete route. Without it, a conversation the user deleted
    would stay retrievable through memory — a correctness bug and a privacy
    one, since deletion was the thing they asked for.
    """
    if not memory_enabled():
        return 0
    return await store.delete_conversation(Scope(owner_id=owner_id, conversation_id=conversation_id))
