"""
POST /api/chat — the one route that runs the agent.

A request is self-contained: it carries its own full message history, and
nothing about generating the answer reads from the database. What the database
adds is *durability*, not state — the turn is written to MongoDB as it streams
so a signed-in user can reopen the conversation later.

The route itself does no LLM logic. It calls run_agent() and relays what that
yields, persisting the turn around it.
"""

import base64
import json
import os
from typing import Any

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from server import memory
from server.agent import run_agent
from server.auth import current_user
from server.db import agent_logs, conversations
from server.memory import store as blob_store
from server.models import find_model_option, is_model_available
from server.shared import now, owned_conversation, spawn
from server.subagents import run_title_subagent
from server.types import ChatRequestBody

router = APIRouter()

# Placeholder title for a brand-new conversation, until the title subagent
# names it (see _maybe_generate_title). Mirrors the client's old deriveTitle().
TITLE_PLACEHOLDER_CHARS = 48
# Turns after which an unnamed conversation gets titled: 3 questions + their
# 3 answers, per the product rule. A first diagram/chart titles it sooner.
TITLE_AFTER_MESSAGES = 6


async def _stored_user_message(body: ChatRequestBody, owner_id: ObjectId) -> dict[str, Any]:
    """
    Input: the request body + the signed-in user's id. Output: the last (new)
    user turn as a stored message. Text always; an image/file attachment's
    original bytes go to GridFS (the same bucket + save_blob() helper the
    memory layer uses) rather than into the document itself, so a reopened
    conversation can show what was attached without megabytes of base64
    sitting in every message.
    """
    last = body.messages[-1]
    message: dict[str, Any] = {"role": last.role, "text": last.text, "at": now()}

    if last.image is not None:
        blob_id = await blob_store.save_blob(
            "image", base64.b64decode(last.image.data), last.image.media_type, owner_id
        )
        message["attachment"] = {"blobId": blob_id, "mediaType": last.image.media_type, "kind": "image"}
    elif last.file is not None:
        blob_id = await blob_store.save_blob(
            last.file.name, base64.b64decode(last.file.data), last.file.media_type, owner_id
        )
        message["attachment"] = {
            "blobId": blob_id,
            "mediaType": last.file.media_type,
            "kind": "file",
            "name": last.file.name,
        }

    return message


async def _begin_turn(body: ChatRequestBody, owner_id: ObjectId) -> tuple[ObjectId, dict[str, Any] | None]:
    """
    Resolves (or creates) the conversation this turn belongs to and writes
    the user's message into it.
    Input: the request body + the signed-in user's id. Output: (conversation
    id, the "conversation" SSE event if one was just created else None).

    The stored messages are truncated to the client's history length before
    the new message is appended, rather than blindly pushed. That is what
    makes retry correct: the client's handleRetry re-sends a *shorter*
    history, and this makes the stored array follow it instead of accumulating
    a duplicate copy of the turn being retried.
    """
    user_message = await _stored_user_message(body, owner_id)
    keep = len(body.messages) - 1

    if body.conversation_id:
        doc = await owned_conversation(body.conversation_id, owner_id)
        kept = doc.get("messages", [])[:keep]
        await conversations.update_one(
            {"_id": doc["_id"]}, {"$set": {"messages": [*kept, user_message], "updatedAt": now()}}
        )
        return doc["_id"], None

    text = body.messages[-1].text.strip()
    title = text[:TITLE_PLACEHOLDER_CHARS] or "New diagram"
    created_at = now()
    result = await conversations.insert_one(
        {
            "ownerId": owner_id,
            "title": title,
            "titleLocked": False,
            "createdAt": created_at,
            "updatedAt": created_at,
            "messages": [user_message],
        }
    )
    return result.inserted_id, {"type": "conversation", "id": str(result.inserted_id), "title": title}


async def _maybe_generate_title(conversation_id: ObjectId, provider: str | None = None) -> dict[str, Any] | None:
    """
    Names a conversation once there's enough of it to name, then locks the
    title so it never churns again.
    Input: the conversation id (called right after the assistant turn is
    stored). Output: a "title" SSE event if a title was generated, else None.

    Triggers on whichever comes first: the conversation's first diagram or
    chart, or its 6th message (3 questions and their 3 answers).
    """
    doc = await conversations.find_one({"_id": conversation_id})
    if not doc or doc.get("titleLocked"):
        return None

    messages = doc.get("messages", [])
    has_visual = any(m.get("diagrams") or m.get("charts") for m in messages)
    if not has_visual and len(messages) < TITLE_AFTER_MESSAGES:
        return None

    title = await run_title_subagent(messages, provider)
    if not title:
        # No subagent client, or the call failed. Leave the placeholder and
        # stay unlocked so the next turn can try again.
        return None
    await conversations.update_one({"_id": conversation_id}, {"$set": {"title": title, "titleLocked": True}})
    return {"type": "title", "title": title}


def _memory_scope(user: dict[str, Any]) -> memory.Scope | None:
    """
    Input: the signed-in user. Output: the scope the search_memory tool may
    read, or None to run the turn without that tool at all.

    Owner-wide on purpose — `conversation_id` is left unset. Recall across
    conversations is the point of the feature ("the chart we made last week"
    was almost certainly not made in this chat), and it is why each result
    carries a conversation title: the model has to be able to say which one it
    found something in.

    This is also the security boundary in its entirety. The scope comes from
    the session cookie and is the only owner identity anywhere in the tool
    path; the model supplies a query string and has no schema field through
    which to name a different owner or widen what it can see.
    """
    return memory.Scope(owner_id=user["_id"]) if memory.memory_enabled() else None


def _spawn_memory_ingest(
    body: ChatRequestBody, conversation_id: ObjectId, owner_id: ObjectId, reply: dict[str, Any]
) -> None:
    """
    Input: the request body (its last message still carries the raw
    attachments, which the stored copy deliberately drops), the conversation
    and owner, and the assistant reply that just finished streaming.
    Output: none — schedules indexing and returns immediately.

    Deliberately fire-and-forget. Indexing embeds several units and may call a
    vision model to describe an attached image; none of that belongs in front
    of a response the user is already reading. Awaiting it here would add
    seconds to every turn for a feature nothing currently reads from.

    Failure is swallowed on purpose. A turn that answered correctly and got
    stored correctly must not be reported as broken because a background
    enrichment pass failed — the same judgement main.py already applies to an
    unreachable MongoDB at startup and subagents.py applies to a failed
    styling pass.
    """
    if not memory.memory_enabled():
        return

    async def run() -> None:
        try:
            await memory.index_turn(
                owner_id=owner_id,
                conversation_id=conversation_id,
                user_message=body.messages[-1],
                reply=reply,
            )
        except Exception as err:  # noqa: BLE001 - indexing must never surface to the user
            print(f"memory: indexing turn failed ({err.__class__.__name__}: {err})")

    spawn(run())


def _sse(payload: dict[str, Any]) -> str:
    """Input: an event dict. Output: one SSE frame. The wire format, in one place."""
    return f"data: {json.dumps(payload)}\n\n"


class _Turn:
    """
    The bookkeeping that used to be four local variables and four scattered
    `steps.append(...)` calls inside the stream generator.

    `reply` buffers the assistant turn as it streams so it can be stored as one
    message on "done" — nothing is written on "error", so a failed turn leaves
    the user's question stored and no answer, which is exactly what a retry
    expects to find. `steps` is the durable, queryable twin of reply["trace"]
    (see server/db.py's agent_logs comment), written once the turn ends rather
    than embedded on the message, so it survives independent of history.
    """

    def __init__(self) -> None:
        self.reply: dict[str, Any] = {"role": "assistant", "text": "", "diagrams": [], "charts": [], "trace": []}
        self.steps: list[dict[str, Any]] = []
        self.started_at = now()

    def record(self, step: dict[str, Any]) -> None:
        """Appends one agent_logs step, timestamped now."""
        self.steps.append({**step, "at": now()})

    def absorb(self, event: dict[str, Any]) -> None:
        """Folds one provider event into the buffered reply and the step log."""
        kind = event["type"]
        if kind == "text":
            self.reply["text"] += event["text"]
        elif kind == "diagram":
            self.reply["diagrams"].append(event["payload"])
        elif kind == "chart":
            self.reply["charts"].append(event["payload"])
        elif kind == "trace":
            self.reply["trace"].append(event["label"])
            self.record({"type": "trace", "label": event["label"]})
        elif kind == "retrieval":
            self.record(event)
        elif kind == "error":
            self.record({"type": "error", "label": event["message"]})

    async def finish(self, conversation_id: ObjectId, owner_id: ObjectId, status: str) -> None:
        """Writes the agent_logs document for this turn. Called once, on `done` or `error`."""
        if status == "done":
            self.record({"type": "done", "label": "Reply ready"})
        await agent_logs.insert_one(
            {
                "ownerId": owner_id,
                "conversationId": conversation_id,
                "startedAt": self.started_at,
                "endedAt": now(),
                "status": status,
                "steps": self.steps,
            }
        )


@router.post("/api/chat")
async def chat(body: ChatRequestBody, user: dict[str, Any] = Depends(current_user)):
    """
    Input: { messages, model, mode, conversationId } (ChatRequestBody) as
    JSON, plus a session cookie.
    Output: a Server-Sent Events stream of AgentEvent JSON lines
    (text/diagram/chart/trace/error/done, plus conversation/title from here).
    """
    if len(body.messages) == 0:
        return JSONResponse(status_code=400, content={"error": "messages is required."})

    option = find_model_option(body.model)
    # The same gate GET /api/models reports as `available`, applied here too.
    # Greying an option out in the picker is presentation, not enforcement —
    # without this a client could POST any catalog id and be served it.
    if not is_model_available(option):
        if option.provider == "openai" and not os.environ.get("OPENAI_API_KEY"):
            return JSONResponse(
                status_code=500, content={"error": "OPENAI_API_KEY is not configured on the server."}
            )
        return JSONResponse(status_code=400, content={"error": f"{option.label} is not available."})

    async def event_stream():
        turn = _Turn()
        try:
            conversation_id, created = await _begin_turn(body, user["_id"])
            if created:
                yield _sse(created)

            async for event in run_agent(body.messages, body.model, body.mode, _memory_scope(user)):
                turn.absorb(event)

                # The one provider event that is never relayed: a diagnostic
                # record of what a memory search returned (see memory/tool.py's
                # retrieval_record), for the agent_logs document only, not for
                # the chat UI's Process Trace. Skipping the yield is what
                # enforces that, and it keeps json.dumps away from the
                # datetimes inside.
                if event["type"] == "retrieval":
                    continue

                if event["type"] == "done":
                    turn.reply["at"] = now()
                    await conversations.update_one(
                        {"_id": conversation_id},
                        {"$push": {"messages": turn.reply}, "$set": {"updatedAt": now()}},
                    )
                    titled = await _maybe_generate_title(conversation_id, option.provider)
                    if titled:
                        yield _sse(titled)
                    # Scheduled, not awaited — see _spawn_memory_ingest. This
                    # is what makes the turn searchable *next* time; the read
                    # side runs inside run_agent above, via the search_memory
                    # tool (see DISCUSS_RAG.md).
                    _spawn_memory_ingest(body, conversation_id, user["_id"], turn.reply)

                terminal = event["type"] in ("done", "error")
                if terminal:
                    await turn.finish(conversation_id, user["_id"], event["type"])

                yield _sse(event)
                if terminal:
                    break
        except HTTPException as err:  # e.g. a conversationId that isn't this user's
            yield _sse({"type": "error", "message": err.detail})
        except Exception as err:  # noqa: BLE001 - surfaced to the client as an SSE error event
            yield _sse({"type": "error", "message": str(err) or "Unexpected server error."})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
