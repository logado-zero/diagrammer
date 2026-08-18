"""
The shapes every other module in server/memory/ passes around.

Two of these carry design weight beyond "a bag of fields":

`Scope` is the tenant boundary. It is the *only* way to express a memory
query filter — no function in this package accepts a raw Mongo filter dict,
and `Scope` cannot be constructed without an owner id. That is deliberate:
a missing `ownerId` filter in a retrieval system doesn't raise, doesn't log,
and passes every test written with one user, while quietly making another
person's private conversation retrievable. Making the filter unforgettable
in the type system is cheaper than remembering it at every call site.

`MemoryUnit` carries three embedding-provenance fields (`embed_model`,
`embed_dim`, `embed_space`) that nothing reads today. They exist because a
vector is only meaningful against the exact model that produced it: swap the
encoder without recording which one ran, and every stored vector silently
becomes noise compared against new queries — no error, just steadily worse
results nobody attributes to the change. Recording it now makes that swap a
backfill instead of a mystery. `entities` is written empty for the same
reason: the Zero-Mem graph view (see DISCUSS_RAG.md, phase 3) populates it,
and having the field from day one makes that a job rather than a migration.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from bson import ObjectId

# What a stored unit was derived from. "turn" is a chat message; the rest come
# from a tool call or an attachment on one.
UnitKind = Literal["turn", "chart", "diagram", "sheet", "image"]

# Which vector space a unit's embedding lives in. Only "text" exists today —
# an image encoder would add "image", and vectors from different spaces must
# never be scored against each other (see store.dense()).
EmbedSpace = Literal["text", "image"]


@dataclass(frozen=True)
class Scope:
    """
    Who is allowed to see the units a query touches.

    Always pass this as the first argument to anything in store.py or
    search.py. `conversation_id` narrows further to a single conversation;
    leaving it None searches everything this user owns, which is what
    cross-conversation recall needs.
    """

    owner_id: ObjectId
    conversation_id: ObjectId | None = None

    def as_filter(self) -> dict[str, Any]:
        """Input: none. Output: the Mongo filter fragment every memory query starts from."""
        query: dict[str, Any] = {"ownerId": self.owner_id}
        if self.conversation_id is not None:
            query["conversationId"] = self.conversation_id
        return query


@dataclass
class MemoryUnit:
    """
    One retrievable thing. `text` is what actually gets embedded, and for a
    "turn" it is the original message — never a summary. Charts and diagrams
    keep their raw tool payload alongside a descriptive text rendering, so a
    follow-up can be answered from the exact numbers rather than a paraphrase
    of them.
    """

    owner_id: ObjectId
    conversation_id: ObjectId
    kind: UnitKind
    role: Literal["user", "assistant"]
    text: str
    seq: int
    at: datetime
    # Raw render_chart / render_diagram tool input, for kind chart/diagram.
    payload: dict[str, Any] | None = None
    # GridFS id of the original file, for kind sheet/image.
    blob_id: ObjectId | None = None
    # Populated by the graph view in a later phase; written empty until then.
    entities: list[str] = field(default_factory=list)
    # None when encoding failed — the unit is still stored and still correct,
    # it just isn't dense-retrievable until a backfill re-encodes it.
    vec: bytes | None = None
    embed_model: str | None = None
    embed_dim: int | None = None
    embed_space: EmbedSpace = "text"

    def to_document(self) -> dict[str, Any]:
        """Input: none. Output: the BSON document stored in the memory_units collection."""
        return {
            "ownerId": self.owner_id,
            "conversationId": self.conversation_id,
            "kind": self.kind,
            "role": self.role,
            "text": self.text,
            "seq": self.seq,
            "at": self.at,
            "payload": self.payload,
            "blobId": self.blob_id,
            "entities": self.entities,
            "vec": self.vec,
            "embedModel": self.embed_model,
            "embedDim": self.embed_dim,
            "embedSpace": self.embed_space,
        }


@dataclass(frozen=True)
class SearchHit:
    """
    One retrieved unit plus why it surfaced.

    Everything below `score` is provenance: where the hit came from and how it
    ranked. That matters more here than in a typical search result, because the
    consumer is a language model rather than a person clicking a link. A model
    handed bare text has no way to say *where* it learned something, so it
    either omits the source or invents one; handed `conversation_title` and
    `seq` it can write "in 'Q3 review', turn 14" and the user can go check.
    """

    unit_id: ObjectId
    kind: UnitKind
    text: str
    score: float
    # Normalized contribution from each view that matched, e.g.
    # {"dense": 0.91, "lexical": 0.40}. A view missing from this dict returned
    # nothing for the query, which is usually the first clue when a ranking
    # looks wrong.
    view_scores: dict[str, float] = field(default_factory=dict)
    payload: dict[str, Any] | None = None
    conversation_id: ObjectId | None = None
    # Position in its conversation — the "turn order" a citation needs. Note
    # this counts *units*, not messages: a turn that attached a spreadsheet
    # contributes several. It is a stable ordering, not a message number.
    seq: int = 0
    role: Literal["user", "assistant"] = "user"
    at: datetime | None = None
    # Resolved from the `conversations` collection after fusion, not stored on
    # the unit. Titles are renamed (see server/routes/chat.py's _maybe_generate_title, which
    # replaces the placeholder once a conversation is worth naming), and a copy
    # on every unit would be stale the moment that happened.
    conversation_title: str | None = None
    # True for a unit pulled in by evidence closure (retrieve._close_evidence)
    # rather than by matching anything. It sits next to a real hit and supplies
    # the context that hit assumes — the turn before "change the colour to blue"
    # is what names the diagram. Flagged rather than silently mixed in, because
    # a model told these matched would cite them as though they answered the
    # question. Their `score` is 0 and their `view_scores` empty, which is the
    # honest record of how they got here.
    is_context: bool = False
