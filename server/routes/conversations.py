"""
Saved conversations: list, read, read the agent log, delete.

Every route here takes `current_user` and reaches the database through
`shared.owner_filter()` / `shared.owned_conversation()`, so someone else's
conversation id is a 404 rather than a read of their data.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from gridfs.errors import NoFile

from server import memory
from server.auth import current_user
from server.db import agent_logs, attachments, conversations
from server.shared import object_id, owned_conversation, owner_filter

router = APIRouter()


def public_conversation(doc: dict[str, Any]) -> dict[str, Any]:
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


@router.get("/api/conversations")
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


@router.get("/api/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, user: dict[str, Any] = Depends(current_user)):
    """Input: a conversation id + the session cookie. Output: the full conversation including every stored diagram/chart payload, ready to re-render with no LLM call."""
    return public_conversation(await owned_conversation(conversation_id, user["_id"]))


@router.get("/api/conversations/{conversation_id}/logs")
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


@router.delete("/api/conversations/{conversation_id}")
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
