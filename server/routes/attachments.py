"""
Serving a stored attachment's raw bytes back to the client.

This is what a loaded conversation's `attachment.url` (see
routes/conversations.py's public_conversation) points at.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from gridfs.errors import NoFile

from server.auth import current_user
from server.db import attachments
from server.shared import object_id

router = APIRouter()


@router.get("/api/attachments/{blob_id}")
async def get_attachment(blob_id: str, user: dict[str, Any] = Depends(current_user)):
    """Input: a GridFS blob id + the session cookie. Output: the raw attachment bytes with their original media type, or 404 if missing or not this user's."""
    try:
        stream = await attachments.open_download_stream(object_id(blob_id))
    except NoFile:
        raise HTTPException(status_code=404, detail="Attachment not found.") from None
    # GridFS ownership lives in the file's metadata rather than a query term,
    # so this is the one owner check that can't go through owner_filter().
    if stream.metadata is None or stream.metadata.get("ownerId") != user["_id"]:
        raise HTTPException(status_code=404, detail="Attachment not found.")
    data = await stream.read()
    return Response(content=data, media_type=stream.metadata.get("mediaType", "application/octet-stream"))
