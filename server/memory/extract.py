"""
Turns one finished chat turn into the list of things worth remembering.

The guiding rule is Zero-Mem's, and it is the opposite of what most memory
systems do: **store the original, never a summary.** A chart's payload already
holds its title, series names and exact numbers; paraphrasing that into prose
would throw away the very data a follow-up question needs. So a chart unit
keeps the raw tool payload verbatim and embeds a *description* of it purely as
a retrieval handle — the description is how it gets found, the payload is what
gets used.

Images are the one exception, and a deliberate one. There is no text in an
image to store, so a vision model writes a description at ingest time and that
description is what gets embedded. It is the only generative call anywhere in
the memory path, it happens after the user's response has already been sent,
and if it fails the image is still stored — just not searchable by content.
"""

import base64
import os
from datetime import datetime
from typing import Any

from bson import ObjectId

from server.attachments import sheet_row_windows
from server.memory import store
from server.memory.entities import extract_entities
from server.memory.types import MemoryUnit
from server.types import ChatRequestMessage

# Vision model used to describe an attached image. Its output is embedded like
# any other text, so it is written for retrieval rather than for a reader.
IMAGE_CAPTION_MODEL = os.environ.get("MEMORY_CAPTION_MODEL", "gpt-5.6-luna")

IMAGE_CAPTION_PROMPT = """You write descriptions of images so they can be found later by text search.

Describe what the image actually contains, in one dense paragraph. Include:
- every piece of text visible in it, transcribed exactly — titles, axis labels,
  legend entries, numbers, units, dates, currency symbols
- what kind of thing it is (photo, screenshot, bar chart, flowchart, table,
  diagram, spreadsheet)
- the subject matter, and any named entities: people, places, companies,
  products, metrics

Write in the same language as any text in the image. Do not interpret, judge, or
speculate about the image's purpose — someone searching for it will remember what
was in it, not what it meant. No preamble, no "This image shows"."""

# Sheets are chunked so a question about a row deep in the file is still
# answerable. See attachments.sheet_row_windows() for why memory doesn't
# inherit the prompt's 300-row cap.
SHEET_WINDOW_ROWS = 100
SHEET_WINDOW_OVERLAP = 10


async def describe_image(media_type: str, data: str) -> str | None:
    """
    Input: an attached image's media type and base64 bytes.
    Output: a description written for retrieval, or None if no OpenAI key is
    configured or the call failed.

    Returning None rather than raising follows the same convention as
    server/subagents.py: a best-effort enrichment pass must never be able to
    fail the thing it was enriching.
    """
    if not os.environ.get("OPENAI_API_KEY"):
        return None

    from server.providers import openai_provider

    try:
        client = await openai_provider._get_client()
        response = await client.responses.create(
            model=IMAGE_CAPTION_MODEL,
            instructions=IMAGE_CAPTION_PROMPT,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "Describe this image for search."},
                        {
                            "type": "input_image",
                            "detail": "auto",
                            "image_url": f"data:{media_type};base64,{data}",
                        },
                    ],
                }
            ],
            store=False,
        )
    except Exception as err:  # noqa: BLE001 - the image is still stored, just not searchable by content
        print(f"memory: image description failed ({err.__class__.__name__}: {err})")
        return None

    text = (getattr(response, "output_text", "") or "").strip()
    return text or None


def _describe_chart(payload: dict[str, Any]) -> str:
    """
    Input: a render_chart tool payload. Output: text that makes it findable.

    Reads the ECharts option the chart subagent produced — its title, axis
    categories and series names are the words someone would actually search
    for ("revenue by region", "SJC gold sell price"), and they are already
    exact, so there is nothing to summarize.
    """
    parts = [str(payload.get("title") or "")]
    option = payload.get("option") or {}

    for axis_key in ("xAxis", "yAxis"):
        axis = option.get(axis_key)
        for entry in axis if isinstance(axis, list) else [axis]:
            if isinstance(entry, dict):
                if entry.get("name"):
                    parts.append(str(entry["name"]))
                parts.extend(str(v) for v in (entry.get("data") or [])[:60])

    series = option.get("series")
    for entry in series if isinstance(series, list) else [series]:
        if isinstance(entry, dict):
            if entry.get("name"):
                parts.append(str(entry["name"]))
            if entry.get("type"):
                parts.append(f"{entry['type']} chart")
            # Pie/funnel/radar carry their labels inside the data points rather
            # than on an axis, so they'd otherwise contribute no searchable text.
            for point in (entry.get("data") or [])[:60]:
                if isinstance(point, dict) and point.get("name"):
                    parts.append(str(point["name"]))

    return " · ".join(p for p in parts if p)


def _describe_diagram(payload: dict[str, Any]) -> str:
    """
    Input: a render_diagram tool payload. Output: text that makes it findable.
    The Mermaid source is kept verbatim in `payload`; what gets embedded is
    the title plus the node and edge labels, which is what a person would
    remember and search for.
    """
    parts = [str(payload.get("title") or "")]
    mermaid = str(payload.get("mermaid") or "")
    # Node and edge labels are the quoted or bracketed text in Mermaid source.
    # A light scrape beats a real parser here: it never throws on syntax this
    # app didn't generate, and a missed label costs recall, not correctness.
    for line in mermaid.splitlines():
        line = line.strip()
        if not line or line.startswith(("classDef", "%%", "---", "flowchart", "graph", "config:", "look:")):
            continue
        parts.append(line)
    return " · ".join(p for p in parts if p)


async def units_from_turn(
    *,
    owner_id: ObjectId,
    conversation_id: ObjectId,
    user_message: ChatRequestMessage,
    reply: dict[str, Any],
    base_seq: int,
    at: datetime,
) -> list[MemoryUnit]:
    """
    Input: who owns the turn, which conversation it belongs to, the user's
    original message (attachments still attached — the chat route has the request body
    before agent.py inlines anything), the assistant reply the chat route buffered
    while streaming, the sequence number to start numbering at, and the turn's
    timestamp.
    Output: every unit worth storing, in order, with `vec` still unset —
    index_turn() encodes them in one batch afterwards.

    Attachment bytes are written to GridFS here, because a unit isn't complete
    without the id of the blob it points at.
    """
    units: list[MemoryUnit] = []
    seq = base_seq

    def add(kind: str, role: str, text: str, **extra: Any) -> None:
        nonlocal seq
        # An empty unit would be an embedding of nothing that can still be
        # returned by a lexical match, so drop it rather than store it.
        if not text.strip():
            return
        units.append(
            MemoryUnit(
                owner_id=owner_id,
                conversation_id=conversation_id,
                kind=kind,  # type: ignore[arg-type]
                role=role,  # type: ignore[arg-type]
                text=text.strip(),
                seq=seq,
                at=at,
                # The entity-graph view's nodes. Extracted at write time from
                # the same text that gets embedded, by the same function that
                # runs over a query at read time — an entity links the two only
                # if identical strings come out of both sides.
                entities=extract_entities(text, kind),  # type: ignore[arg-type]
                **extra,
            )
        )
        seq += 1

    add("turn", "user", user_message.text)

    if user_message.file is not None:
        file = user_message.file
        # GridFS stores the real file, not the base64 envelope it arrived in —
        # decoding here is what makes the stored blob a usable .xlsx/.csv later.
        blob_id = await store.save_blob(
            file.name, base64.b64decode(file.data), file.media_type, owner_id
        )
        windows = sheet_row_windows(
            file.data, file.media_type, window=SHEET_WINDOW_ROWS, overlap=SHEET_WINDOW_OVERLAP
        )
        for i, chunk in enumerate(windows):
            label = f"Attached file `{file.name}`"
            if len(windows) > 1:
                label += f" (part {i + 1} of {len(windows)})"
            add("sheet", "user", f"{label}:\n\n{chunk}", blob_id=blob_id)

    if user_message.image is not None:
        image = user_message.image
        suffix = (image.media_type.rsplit("/", 1) + ["bin"])[1]
        blob_id = await store.save_blob(
            f"image.{suffix}", base64.b64decode(image.data), image.media_type, owner_id
        )
        description = await describe_image(image.media_type, image.data)
        # With no description there is nothing to embed, but the blob is still
        # stored and still linked to the conversation.
        if description:
            add("image", "user", f"Attached image: {description}", blob_id=blob_id)

    add("turn", "assistant", reply.get("text") or "")

    for payload in reply.get("charts") or []:
        add("chart", "assistant", _describe_chart(payload), payload=payload)
    for payload in reply.get("diagrams") or []:
        add("diagram", "assistant", _describe_diagram(payload), payload=payload)

    return units
