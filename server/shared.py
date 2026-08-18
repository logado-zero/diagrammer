"""
Small helpers shared by every route module.

The point of this file is that "which rows belong to this user" and "what a
timestamp looks like" each have exactly one spelling. Both used to be written
out per call site — four owner filters in main.py alone, five copies of the
naive-UTC `datetime.now(timezone.utc).replace(tzinfo=None)` dance — which is
how server/memory/__init__.py ended up writing *local* time into the same
collection everything else wrote UTC into.

Note this is deliberately not the same mechanism as server/memory/'s
`Scope.as_filter()`. That one is a type-level tenant boundary the memory
package enforces on itself (see server/memory/types.py); this one is a plain
helper for the HTTP routes. The redundancy between them is the design.
"""

import asyncio
from collections.abc import Coroutine
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import HTTPException

from server.db import conversations


def now() -> datetime:
    """Output: a naive-UTC timestamp — what BSON stores and what auth.py's session checks compare against."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def owner_filter(owner_id: ObjectId, **extra: Any) -> dict[str, Any]:
    """
    Input: the signed-in user's id, plus any additional query terms.
    Output: a Mongo filter with `ownerId` pinned.

    Every query against a per-user collection goes through this so that
    forgetting the owner term is a missing-import error rather than a silent
    cross-tenant read.
    """
    return {"ownerId": owner_id, **extra}


def object_id(raw: str) -> ObjectId:
    """Input: an id string from the client. Output: an ObjectId, or a 404 — a malformed id is a miss, not a 500."""
    try:
        return ObjectId(raw)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=404, detail="Conversation not found.") from None


async def owned_conversation(conversation_id: str, owner_id: ObjectId) -> dict[str, Any]:
    """
    Loads one conversation, scoped to its owner. Every conversation route
    goes through this rather than looking up by id alone, so someone else's
    id is a 404 instead of a read of their data.
    """
    doc = await conversations.find_one(owner_filter(owner_id, _id=object_id(conversation_id)))
    if not doc:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return doc


# Fire-and-forget tasks, held only so the garbage collector can't cancel them
# mid-flight: asyncio keeps just a weak reference to a running task, so a
# create_task() with no strong reference anywhere can vanish partway through.
# Each task removes itself on completion.
_background: set[asyncio.Task[Any]] = set()


def spawn(coro: Coroutine[Any, Any, Any]) -> None:
    """
    Input: a coroutine. Output: none — schedules it and returns immediately.

    The strong-reference dance above, written once. Both callers (background
    memory ingest after a turn, and the encoder warm-up at startup) had their
    own copy of it, comment included.
    """
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)
