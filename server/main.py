"""
FastAPI app: the entire HTTP surface of this backend — the three original
routes, the conversation routes below, and the auth router from
server/auth.py.

An /api/chat request is still self-contained: it carries its own full
message history, and nothing about generating the answer reads from the
database. What the database adds is *durability*, not state — the turn is
written to MongoDB as it streams (see event_stream below) so a signed-in
user can reopen the conversation later, and a session cookie identifies
whose conversation it is.

Concurrency (multiple users at once): every route below is `async def`,
so FastAPI/uvicorn services many concurrent requests on one event loop
without blocking each other while awaiting network I/O (calls to
Anthropic/OpenAI, and now MongoDB) or subprocess I/O (the agent-sdk
provider's local `claude` CLI). There is still no shared mutable state
per request — the Mongo client is a connection pool, not request state —
so this process can also be scaled across CPU cores by running multiple
uvicorn worker processes (`uvicorn server.main:app --workers 4`) with
zero code changes; see README.md for the exact command.
"""

from dotenv import load_dotenv

# Must run before importing anything below that reads an env var at import
# time (e.g. providers/openai_provider.py's OPENAI_MODEL default,
# providers/agent_sdk.py's AGENT_SDK_MODEL default) — same ordering
# constraint as the Node build's `import 'dotenv/config'` being the first line.
load_dotenv()

import asyncio  # noqa: E402
import base64  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
from typing import Any  # noqa: E402

from bson import ObjectId  # noqa: E402
from fastapi import Depends, FastAPI, HTTPException, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse, Response, StreamingResponse  # noqa: E402
from gridfs.errors import NoFile  # noqa: E402
from pymongo.errors import PyMongoError  # noqa: E402

from server import auth  # noqa: E402
from server import memory  # noqa: E402
from server.agent import resolve_claude_transport, run_agent  # noqa: E402
from server.auth import current_user  # noqa: E402
from server.db import agent_logs, attachments, conversations, ensure_indexes  # noqa: E402
from server.memory import store as blob_store  # noqa: E402
from server.models import DEFAULT_MODEL_ID, MODEL_CATALOG, find_model_option, is_model_available  # noqa: E402
from server.shared import now, object_id, owned_conversation, owner_filter  # noqa: E402
from server.subagents import run_title_subagent  # noqa: E402
from server.types import ChatRequestBody  # noqa: E402

PORT = int(os.environ.get("PORT", "8787"))

# Matches the original Express body limit (headroom for base64 images);
# Starlette has no default cap, so this is an explicit ceiling, not a
# behavior port of something Starlette already did.
MAX_BODY_BYTES = 20 * 1024 * 1024

# Placeholder title for a brand-new conversation, until the title subagent
# names it (see _maybe_generate_title). Mirrors the client's old deriveTitle().
TITLE_PLACEHOLDER_CHARS = 48
# Turns after which an unnamed conversation gets titled: 3 questions + their
# 3 answers, per the product rule. A first diagram/chart titles it sooner.
TITLE_AFTER_MESSAGES = 6

# Background memory-indexing tasks, held only so the garbage collector can't
# cancel them mid-flight: asyncio keeps just a weak reference to a running
# task, so a fire-and-forget create_task() with no strong reference anywhere
# can vanish partway through. Each task removes itself on completion.
_ingest_tasks: set[asyncio.Task[Any]] = set()

app = FastAPI()
# allow_credentials is required for the session cookie to be sent on
# cross-origin requests — and CORS forbids pairing it with allow_origins=["*"],
# so the wildcard becomes an explicit dev-origin list. In normal use the app is
# same-origin anyway (Vite proxies /api to this server), so this only matters
# if you point a differently-served frontend at it.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth.router)


@app.exception_handler(PyMongoError)
async def _mongo_unavailable(request: Request, err: PyMongoError):
    """
    Turns any unhandled MongoDB failure into a 503 the UI can show, instead of
    a bare 500. Registered app-wide rather than caught per route because every
    authenticated route touches Mongo — one handler covers all of them, and
    covers routes added later for free. (POST /api/chat never reaches here:
    its errors are already relayed as SSE `error` events mid-stream.)
    """
    print(f"MongoDB error on {request.url.path}: {err.__class__.__name__}: {err}")
    return JSONResponse(
        status_code=503,
        content={"detail": "Can't reach the database. Is MongoDB running? See README.md."},
    )


@app.middleware("http")
async def limit_body_size(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_BODY_BYTES:
        return JSONResponse(status_code=413, content={"error": "Request body too large."})
    return await call_next(request)


@app.get("/api/health")
def health():
    """Input: none. Output: which Claude transport is active and whether either API key is configured."""
    return {
        "ok": True,
        "claudeTransport": resolve_claude_transport(),
        "hasApiKey": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "hasOpenAiApiKey": bool(os.environ.get("OPENAI_API_KEY")),
        # agent-sdk mode has no way to check credentials up front — it relies on
        # an existing local `claude` CLI login and only fails on the first request.
    }


@app.get("/api/models")
def models():
    """Input: none. Output: the model catalog (models.py) plus per-entry availability, so the client never hardcodes model options."""
    return {
        "defaultModelId": DEFAULT_MODEL_ID,
        "models": [
            {
                "id": option.id,
                "label": option.label,
                "shortLabel": option.short_label,
                "provider": option.provider,
                "description": option.description,
                "available": is_model_available(option),
            }
            for option in MODEL_CATALOG
        ],
    }


def _public_conversation(doc: dict[str, Any]) -> dict[str, Any]:
    """Input: a conversation document. Output: the JSON the client consumes (ObjectIds stringified)."""
    return {
        "id": str(doc["_id"]),
        "title": doc.get("title", ""),
        "updatedAt": doc.get("updatedAt", doc.get("createdAt")),
        "messages": [
            {
                "role": m.get("role"),
                "text": m.get("text", ""),
                "diagrams": m.get("diagrams") or [],
                "charts": m.get("charts") or [],
                "trace": m.get("trace") or [],
                "attachment": (
                    {
                        "url": f"/api/attachments/{a['blobId']}",
                        "mediaType": a.get("mediaType"),
                        "kind": a.get("kind"),
                        "name": a.get("name"),
                    }
                    if (a := m.get("attachment"))
                    else None
                ),
            }
            for m in doc.get("messages", [])
        ],
    }


@app.get("/api/conversations")
async def list_conversations(user: dict[str, Any] = Depends(current_user)):
    """Input: the session cookie. Output: this user's conversations, newest first, without their message bodies (the sidebar only needs titles)."""
    cursor = (
        conversations.find(owner_filter(user["_id"]), {"title": 1, "updatedAt": 1})
        .sort("updatedAt", -1)
        .limit(200)
    )
    return [
        {"id": str(doc["_id"]), "title": doc.get("title", ""), "updatedAt": doc.get("updatedAt")}
        async for doc in cursor
    ]


@app.get("/api/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, user: dict[str, Any] = Depends(current_user)):
    """Input: a conversation id + the session cookie. Output: the full conversation including every stored diagram/chart payload, ready to re-render with no LLM call."""
    return _public_conversation(await owned_conversation(conversation_id, user["_id"]))


@app.get("/api/conversations/{conversation_id}/logs")
async def get_conversation_logs(conversation_id: str, user: dict[str, Any] = Depends(current_user)):
    """Input: a conversation id + the session cookie. Output: that conversation's agent_logs turns (see server/db.py), newest first — what the agent actually did on each turn, durable past the ephemeral ProcessTrace UI."""
    doc = await owned_conversation(conversation_id, user["_id"])
    cursor = agent_logs.find(owner_filter(user["_id"], conversationId=doc["_id"])).sort("startedAt", -1)
    return [
        {
            "id": str(log["_id"]),
            "startedAt": log.get("startedAt"),
            "endedAt": log.get("endedAt"),
            "status": log.get("status"),
            "steps": log.get("steps", []),
        }
        async for log in cursor
    ]


@app.get("/api/attachments/{blob_id}")
async def get_attachment(blob_id: str, user: dict[str, Any] = Depends(current_user)):
    """Input: a GridFS blob id + the session cookie. Output: the raw attachment bytes with their original media type, or 404 if missing or not this user's. This is what a loaded conversation's `attachment.url` (see _public_conversation) points at."""
    blob_oid = object_id(blob_id)
    try:
        stream = await attachments.open_download_stream(blob_oid)
    except NoFile:
        raise HTTPException(status_code=404, detail="Attachment not found.") from None
    if stream.metadata is None or stream.metadata.get("ownerId") != user["_id"]:
        raise HTTPException(status_code=404, detail="Attachment not found.")
    data = await stream.read()
    return Response(content=data, media_type=stream.metadata.get("mediaType", "application/octet-stream"))


@app.delete("/api/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str, user: dict[str, Any] = Depends(current_user)):
    """
    Input: a conversation id + the session cookie. Output: {ok: true}, or 404 if it isn't this user's.

    Deletes the memory units, stored attachments, and agent_logs too. Without
    that cascade a deleted conversation would stay retrievable through
    server/memory/ or GET .../logs — the user asked for it to be gone, so
    leaving a searchable copy behind would be both a correctness and a
    privacy bug.
    """
    conversation_oid = object_id(conversation_id)
    doc = await conversations.find_one_and_delete(owner_filter(user["_id"], _id=conversation_oid))
    if doc is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    for m in doc.get("messages", []):
        attachment = m.get("attachment")
        if attachment and attachment.get("blobId"):
            try:
                await attachments.delete(attachment["blobId"])
            except NoFile:
                pass
    await memory.forget_conversation(user["_id"], conversation_oid)
    await agent_logs.delete_many(owner_filter(user["_id"], conversationId=conversation_oid))
    return {"ok": True}


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
    makes retry correct: App.tsx's handleRetry re-sends a *shorter* history,
    and this makes the stored array follow it instead of accumulating a
    duplicate copy of the turn being retried.
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


async def _maybe_generate_title(conversation_id: ObjectId) -> dict[str, Any] | None:
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

    title = await run_title_subagent(messages)
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

    task = asyncio.create_task(run())
    _ingest_tasks.add(task)
    task.add_done_callback(_ingest_tasks.discard)


@app.post("/api/chat")
async def chat(body: ChatRequestBody, user: dict[str, Any] = Depends(current_user)):
    """
    Input: { messages, model, mode, conversationId } (ChatRequestBody) as
    JSON, plus a session cookie.
    Output: a Server-Sent Events stream of AgentEvent JSON lines
    (text/diagram/chart/trace/error/done, plus conversation/title from here).
    Does no LLM logic itself — calls run_agent() and relays what it yields,
    persisting the turn to MongoDB around it.
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
        # Buffers the assistant turn as it streams so it can be stored as one
        # message on "done". Nothing is written on "error" — a failed turn
        # leaves the user's question stored and no answer, which is exactly
        # what a retry expects to find.
        reply: dict[str, Any] = {"role": "assistant", "text": "", "diagrams": [], "charts": [], "trace": []}
        # Durable, queryable twin of reply["trace"] (see server/db.py's
        # agent_logs comment) — written once the turn ends, not embedded on
        # the message, so it survives independent of conversation history.
        steps: list[dict[str, Any]] = []
        started_at = now()
        try:
            conversation_id, created = await _begin_turn(body, user["_id"])
            if created:
                yield f"data: {json.dumps(created)}\n\n"

            async for event in run_agent(body.messages, body.model, body.mode, _memory_scope(user)):
                if event["type"] == "text":
                    reply["text"] += event["text"]
                elif event["type"] == "diagram":
                    reply["diagrams"].append(event["payload"])
                elif event["type"] == "chart":
                    reply["charts"].append(event["payload"])
                elif event["type"] == "trace":
                    reply["trace"].append(event["label"])
                    steps.append({"type": "trace", "label": event["label"], "at": now()})
                elif event["type"] == "retrieval":
                    # The one provider event that is never relayed. It's a
                    # diagnostic record of what a memory search returned (see
                    # memory/tool.py's retrieval_record) — for the agent_logs
                    # document only, not for the chat UI's Process Trace. The
                    # `continue` past the yield below is what enforces that, and
                    # it also keeps json.dumps away from the datetimes inside.
                    steps.append({**event, "at": now()})
                    continue
                elif event["type"] == "done":
                    reply["at"] = now()
                    await conversations.update_one(
                        {"_id": conversation_id},
                        {"$push": {"messages": reply}, "$set": {"updatedAt": now()}},
                    )
                    titled = await _maybe_generate_title(conversation_id)
                    if titled:
                        yield f"data: {json.dumps(titled)}\n\n"
                    # Scheduled, not awaited — see _spawn_memory_ingest. This
                    # is what makes the turn searchable *next* time; the read
                    # side runs inside run_agent above, via the search_memory
                    # tool (see DISCUSS_RAG.md).
                    _spawn_memory_ingest(body, conversation_id, user["_id"], reply)
                elif event["type"] == "error":
                    steps.append({"type": "error", "label": event["message"], "at": now()})

                if event["type"] in ("done", "error"):
                    if event["type"] == "done":
                        steps.append({"type": "done", "label": "Reply ready", "at": now()})
                    await agent_logs.insert_one(
                        {
                            "ownerId": user["_id"],
                            "conversationId": conversation_id,
                            "startedAt": started_at,
                            "endedAt": now(),
                            "status": event["type"],
                            "steps": steps,
                        }
                    )

                yield f"data: {json.dumps(event)}\n\n"
                if event["type"] in ("done", "error"):
                    break
        except HTTPException as err:  # e.g. a conversationId that isn't this user's
            yield f"data: {json.dumps({'type': 'error', 'message': err.detail})}\n\n"
        except Exception as err:  # noqa: BLE001 - surfaced to the client as an SSE error event
            message = str(err) or "Unexpected server error."
            yield f"data: {json.dumps({'type': 'error', 'message': message})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.on_event("startup")
async def _log_startup():
    transport = resolve_claude_transport()
    print(f"Diagrammer API listening on http://localhost:{PORT}")
    try:
        await ensure_indexes()
    except Exception as err:  # noqa: BLE001 - a dead Mongo must not stop the server from booting
        # Deliberately non-fatal: the server still starts and says why login
        # and history are broken, instead of dying with a connection trace
        # that looks like a code bug.
        print(f"MongoDB unreachable at startup ({err.__class__.__name__}) — login and saved history will fail.")
        print("Start MongoDB and restart, or check MONGODB_URI in .env.")
    print(f"Claude transport: {transport} (auto-detected from ANTHROPIC_API_KEY)")
    if transport == "agent-sdk":
        print(
            "No ANTHROPIC_API_KEY set — Claude requests fall back to agent-sdk mode, which relies on a local "
            "`claude` CLI login on this machine and does not support image input. Personal/dev use only, see "
            "CLAUDE.md for why this is not something to deploy for other users."
        )
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is not set — OpenAI models will be marked unavailable until it is.")
    _spawn_encoder_warmup()


def _spawn_encoder_warmup() -> None:
    """
    Input: none. Output: none — schedules one throwaway encode and returns.

    The encoder loads lazily on first use, and that load is ~4s of reading
    weights off disk and building an onnxruntime session, against ~145ms for
    every encode afterwards. That was fine while the only caller was background
    indexing, which nobody is waiting on. It stopped being fine when retrieval
    moved onto the request path: without this, the first chat turn after every
    boot — and after every `--reload` — pays the load in front of the user, and
    reliably enough that it trips `context.TIMEOUT_SECONDS` and silently answers
    that turn with no memory at all.

    Scheduled rather than awaited, for the same reason ingest is: startup must
    not block on it, and a machine that can't load the encoder must still serve
    a working chat app. Failure is swallowed — the load will simply be retried
    on first real use, which is exactly the behaviour this replaces.
    """
    if not memory.memory_enabled():
        return

    async def warm() -> None:
        try:
            from server.memory.encoder import encode

            await encode(["warm up"], is_query=True)
            print("Memory encoder warmed.")
        except Exception as err:  # noqa: BLE001 - a cold encoder is slow, not broken
            print(f"memory: encoder warm-up skipped ({err.__class__.__name__}: {err})")

    task = asyncio.create_task(warm())
    # Same strong reference as the ingest tasks: asyncio only holds a weak one,
    # so a fire-and-forget task can be garbage collected mid-flight.
    _ingest_tasks.add(task)
    task.add_done_callback(_ingest_tasks.discard)
