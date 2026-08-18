"""
MongoDB handles for user accounts and saved conversations — the app's only
persistent state (everything else about a chat turn is still stateless per
request, see server/routes/).

Uses pymongo 4.9+'s built-in AsyncMongoClient rather than motor, which is
deprecated and folded into pymongo itself. The client is constructed at
import time: unlike the OpenAI SDK it does not connect or throw when the
server is unreachable, it just queues operations and fails on first use,
so an unreachable Mongo can't break server startup.
"""

import os

from gridfs import AsyncGridFSBucket
from pymongo import ASCENDING, DESCENDING, AsyncMongoClient

MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
DB_NAME = "chart-chatbot"

# 3s, not pymongo's 30s default. This is a local server behind a login form:
# if it isn't there, the user needs to be told so now, not after half a minute
# of a spinner. main.py turns the resulting error into a 503 with a message.
SERVER_SELECTION_TIMEOUT_MS = 3000

client: AsyncMongoClient = AsyncMongoClient(MONGODB_URI, serverSelectionTimeoutMS=SERVER_SELECTION_TIMEOUT_MS)
db = client[DB_NAME]

# users          {_id, userId, passwordHash, createdAt}
# sessions       {_id, token, ownerId, expiresAt}
# conversations  {_id, ownerId, title, titleLocked, createdAt, updatedAt,
#                 messages: [ {role, text, diagrams, charts, trace, attachment, at} ]}
#   attachment   {blobId, mediaType, kind: "image" | "file", name?} — set by
#                server/routes/chat.py's _stored_user_message() when the turn's last message
#                carries an image/file. A reference, not the bytes: those go to
#                the `attachments` GridFS bucket below (same bucket, same
#                save_blob() helper the memory layer uses), so
#                reopening a conversation can show what was attached without
#                putting megabytes of base64 in the document itself.
#
# `userId` is the login handle the person types (unique, lowercased);
# `ownerId` is the ObjectId foreign key back to users._id. Two names on
# purpose — one string the user chose, one id the database chose.
#
# Messages are embedded in the conversation document rather than living in
# their own collection: one read reopens a whole conversation, and staying
# reference-only for attachments (rather than inlining their bytes) is what
# keeps the documents far below Mongo's 16MB per-document ceiling.
users = db["users"]
sessions = db["sessions"]
conversations = db["conversations"]

# memory_units  {_id, ownerId, conversationId, kind, role, text, seq, at,
#                payload, blobId, entities, vec, embedModel, embedDim, embedSpace}
#
# The retrieval layer (server/memory/). One document per retrievable thing —
# a chat turn, a chart/diagram tool payload, a window of an attached sheet, a
# description of an attached image. Deliberately its own collection rather than
# more fields on `conversations`: units are written asynchronously after a turn
# finishes, are queried by a completely different access pattern (per-owner
# across conversations, not per-conversation), and their vectors would bloat a
# document that is read in full every time a chat is reopened.
memory_units = db["memory_units"]

# memory_edges  {_id, ownerId, src, dst, weight, kind: "cooccur" | "adjacent"}
#
# The entity-graph view's adjacency (server/memory/graph.py). Two entity
# strings are linked because they were observed together — inside one unit
# ("cooccur") or in consecutive units of a conversation ("adjacent") — never
# because a model inferred a relationship between them.
#
# Its own collection rather than an array on the unit, because the graph is
# walked entity-first: a query resolves to entity strings and needs their
# neighbours, which an array nested inside units could only answer by scanning
# every unit. `ownerId` leads every document and every index here for the same
# reason it does on memory_units, and one that is sharper: entity strings are
# inherently global — "SJC" is a legitimate key in every user's graph — so the
# owner is the only thing keeping one person's graph from joining another's.
memory_edges = db["memory_edges"]

# GridFS bucket for the original bytes of an attached image or spreadsheet.
# Attachments used to be dropped entirely to keep conversation documents under
# Mongo's 16MB ceiling; GridFS chunks them instead, which is what makes them
# retrievable later without putting megabytes of base64 in a document.
attachments = AsyncGridFSBucket(db, bucket_name="attachments")

# agent_logs  {_id, ownerId, conversationId, startedAt, endedAt,
#              status: "done" | "error", steps: [{type, label, at}]}
#
# One document per turn, written once the turn finishes (see server/routes/chat.py's
# event_stream()) — a durable, queryable record of what the agent actually
# did (the same trace-level phases ProcessTrace.tsx shows live, plus the
# turn's final outcome), unlike the ephemeral UI trace which vanishes once
# the reply starts.
agent_logs = db["agent_logs"]


async def ensure_indexes() -> None:
    """
    Input: none. Output: none — creates the indexes this app relies on
    (idempotent, so it's safe to call on every startup). The TTL index on
    sessions.expiresAt is what actually expires a login; nothing in the app
    deletes stale sessions itself.
    """
    await users.create_index([("userId", ASCENDING)], unique=True)
    await sessions.create_index([("token", ASCENDING)], unique=True)
    await sessions.create_index([("expiresAt", ASCENDING)], expireAfterSeconds=0)
    await conversations.create_index([("ownerId", ASCENDING), ("updatedAt", -1)])

    # Every memory index leads with ownerId. That isn't only for speed: every
    # query in server/memory/ filters by owner first, and an index that matches
    # that shape is what keeps one user's history depth — rather than the size
    # of the user base — the thing that governs query cost.
    await memory_units.create_index(
        [("ownerId", ASCENDING), ("conversationId", ASCENDING), ("seq", ASCENDING)]
    )
    await memory_units.create_index([("ownerId", ASCENDING), ("at", DESCENDING)])
    # Dense scoring loads vectors through this one. embedModel/embedSpace are in
    # the key because vectors are only comparable within a single
    # (model, space) pair — mixing them silently returns nonsense.
    await memory_units.create_index(
        [("ownerId", ASCENDING), ("embedSpace", ASCENDING), ("embedModel", ASCENDING)]
    )
    # Entry points for the entity-graph view: a query's entities resolve to the
    # units carrying them through this index (a multikey index over the array).
    await memory_units.create_index([("ownerId", ASCENDING), ("entities", ASCENDING)])
    # The lexical half of hybrid retrieval. Mongo allows only one text index per
    # collection, so this is the one.
    await memory_units.create_index([("text", "text")])

    # One hop of the graph walk: given an entity, who are its neighbours.
    await memory_edges.create_index([("ownerId", ASCENDING), ("src", ASCENDING)])
    # Unique so writing an edge is an idempotent `$inc` upsert rather than an
    # insert. Re-indexing a turn, or a backfill run twice, must strengthen an
    # existing edge instead of duplicating it — otherwise weight becomes a
    # count of how many times the job ran.
    await memory_edges.create_index(
        [("ownerId", ASCENDING), ("src", ASCENDING), ("dst", ASCENDING), ("kind", ASCENDING)],
        unique=True,
    )

    await agent_logs.create_index([("ownerId", ASCENDING), ("conversationId", ASCENDING), ("startedAt", DESCENDING)])
