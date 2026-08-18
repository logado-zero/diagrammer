"""
The `search_memory` tool's implementation — what actually runs when the model
decides it needs to remember something.

The split is deliberate. `server/tools.py` owns the tool's *schema* and the
prompt text that teaches the model when to call it; this file owns what happens
when it does. They stay apart because tools.py is imported by every provider
and by agent_sdk.py's MCP name derivation, and it must not drag numpy,
onnxruntime and a MongoDB client into that import graph.

All three providers call `run_search_memory()` and paste its return value back
into their own tool-result shape. It returns a plain string rather than a
structure because that is the only thing all three transports agree on — an
Anthropic `tool_result` content, an OpenAI `function_call_output`, and an MCP
text block are three different envelopes around the same text.

Two things this file is careful about, both of which come from the consumer
being a language model rather than a screen:

**Never return an empty string.** A blank tool result reads as a malfunction,
and the usual reaction is to call the tool again with a slightly reworded query
— burning a round trip to learn the same nothing. "No matches" is information;
"" is not.

**Budget the output.** A `sheet` unit is a hundred-row markdown table. Ten of
them returned verbatim would cost more context than the truncated history this
tool exists to compensate for, which would make recall a net loss.

It also records what it did, for later inspection rather than for the user:
`retrieval_record()` builds one structured log entry per search — the query, and
one entry per hit with its provenance and scores. That goes to `agent_logs` via
main.py and is deliberately **never** sent to the browser. It first shipped as
one `trace` event per hit, which was wrong twice over: it put raw retrieval
internals in the chat UI, and it flattened a single structured thing into N
unstructured rows in the log too.
"""

import json
import os
from typing import Any

from server.memory.retrieve import search
from server.memory.types import Scope, SearchHit

# How many units come back from one call. Deliberately modest: this lands in the
# middle of a turn the user is waiting on, and ten well-ranked hits beat thirty
# the model has to wade through.
DEFAULT_LIMIT = 10
MAX_LIMIT = 50

# Per-hit text kept in the log record. Much tighter than MAX_TEXT_CHARS below:
# the log's job is to let someone judge whether a hit was relevant, not to
# reproduce it. The query itself is stored whole — it's the model's own search
# string, and truncating the one field you'd search the logs by is backwards.
MAX_LOG_TEXT_CHARS = 300

# ponytail: fixed character budgets, not a token count. A tokenizer is already
# loaded in this process (encoder.py) but counting per hit would add real work
# on the response path to enforce a limit that only needs to be roughly right.
# Revisit if a model starts complaining about truncated tables rather than
# about missing ones.
MAX_TEXT_CHARS = 1200
MAX_PAYLOAD_CHARS = 800

# What the model is told when the query matched nothing. Phrased to close the
# subject: without the second sentence the usual next move is to try three
# more spellings of the same query.
NO_RESULTS = (
    "No matches in memory for that query. Nothing was found in earlier turns or "
    "other conversations, so answer from what you already have rather than searching again."
)

# What it is told when retrieval itself broke, as opposed to finding nothing.
# The distinction matters: the first is an answer, the second is a missing
# answer, and the model should be more careful about what it claims after this.
UNAVAILABLE = (
    "Memory search is temporarily unavailable. Answer from the conversation in front "
    "of you and say plainly if something can't be recalled."
)


def _limit() -> int:
    """
    Input: none. Output: how many hits one search returns.

    Read at call time rather than at import, same convention as
    retrieve.memory_enabled(), so `MEMORY_SEARCH_LIMIT=3 npm run dev` is enough
    to try a different width without editing code. Clamped because the value is
    a string from the environment and a typo'd 1000 would push a thousand
    markdown tables into a live turn; anything unparseable falls back to the
    default rather than raising, since this runs mid-answer.
    """
    try:
        return max(1, min(MAX_LIMIT, int(os.environ.get("MEMORY_SEARCH_LIMIT", DEFAULT_LIMIT))))
    except ValueError:
        return DEFAULT_LIMIT


def _truncate(text: str, limit: int) -> str:
    """Input: text and a character budget. Output: it, cut at the budget with a visible marker."""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + f"… [truncated, {len(text)} chars total]"


def _header(hit: SearchHit, index: int) -> str:
    """
    Input: one hit and its 1-based position. Output: the citation line above it.

    This line is the whole reason the tool returns more than raw text. The model
    can only attribute what it can name, so the conversation title and turn
    number go first, and `kind` follows so a partial source announces itself —
    "sheet" tells the model it is looking at one window of a spreadsheet and
    that neighbouring rows exist, which bare text never would.
    """
    parts = [f'"{hit.conversation_title}"' if hit.conversation_title else "(untitled conversation)"]
    parts.append(f"turn {hit.seq}")
    parts.append(hit.role)
    if hit.at is not None:
        parts.append(hit.at.strftime("%Y-%m-%d"))
    parts.append(hit.kind)
    # A closure unit didn't match the query — it's the turn next to one that
    # did. Saying so stops the model citing it as an answer while still letting
    # it read the context the real hit depends on.
    if hit.is_context:
        parts.append("context, adjacent to a match")
    return f"[{index}] " + " · ".join(parts)


def format_hits(hits: list[SearchHit]) -> str:
    """
    Input: the ranked hits. Output: the text handed back to the model.

    Pure and separate from the search call so it can be asserted without a
    database — the formatting is the part that is easy to break silently, since
    a wrong-but-plausible layout still "works", it just stops the model from
    citing anything.
    """
    if not hits:
        return NO_RESULTS

    blocks: list[str] = []
    for index, hit in enumerate(hits, start=1):
        block = [_header(hit, index), _truncate(hit.text, MAX_TEXT_CHARS)]
        # A chart/diagram unit keeps its raw tool payload precisely so a
        # follow-up can reuse the exact numbers instead of a paraphrase of them
        # (see extract.py). Handing back only the description would defeat the
        # reason the payload is stored at all.
        if hit.payload:
            block.append("data: " + _truncate(json.dumps(hit.payload, ensure_ascii=False), MAX_PAYLOAD_CHARS))
        blocks.append("\n".join(block))

    # Closure units are not results and must not be counted as though the query
    # found them — the model would report having found more than it did.
    matches = sum(1 for hit in hits if not hit.is_context)
    context = len(hits) - matches
    count = f"{matches} result{'s' if matches != 1 else ''} from this workspace's memory"
    if context:
        count += f" (plus {context} adjacent for context)"
    return f"{count}, most relevant first.\n\n" + "\n\n".join(blocks)


def retrieval_record(
    query: str,
    hits: list[SearchHit],
    status: str = "ok",
    source: str = "tool",
) -> dict[str, Any]:
    """
    Input: the query that ran, what it found, how the search ended
    ("ok" | "empty-query" | "unavailable" | "skipped"), and which entry point
    ran it ("tool" when the model called search_memory, "auto" when the turn
    retrieved on its own — see memory/context.py).
    Output: one log entry for `agent_logs.steps` (see main.py) describing the
    whole search — never sent to the client.

    `source` exists because the two paths are the only way to tell them apart
    after the fact, and the question they answer is a real one: when a turn
    retrieved automatically *and* the model then searched anyway, either the
    automatic query was wrong or the model didn't notice what it had been
    given. Both are worth seeing, and neither is visible without this field.

    One record with a `list_result` array, not one entry per hit. A search *is*
    one event that happens to contain a list; splitting it turns a 2-step turn
    into a 12-step one and loses which hits belonged to which search.

    `viewScores` is the field worth having here. It is each view's normalized
    contribution, so it answers "why did this rank third" — and a view missing
    from the dict returned nothing at all for the query, which is usually the
    first clue when a ranking looks wrong.

    **Ids are stringified.** `GET /api/conversations/{id}/logs` hands `steps`
    straight to FastAPI's JSON encoder, which handles `datetime` but raises on
    `ObjectId` — leaving one raw here would 500 the only route that exists to
    read this. Asserted in the self-check rather than remembered.

    Pure, so the self-check can assert the shape without a database.
    """
    matches = sum(1 for hit in hits if not hit.is_context)
    return {
        "label": f'Memory: {matches} result{"s" if matches != 1 else ""}',
        "query": query,
        "status": status,
        "source": source,
        "list_result": [
            {
                "rank": index,
                "conversation": hit.conversation_title,
                "conversationId": str(hit.conversation_id) if hit.conversation_id else None,
                "unitId": str(hit.unit_id),
                "seq": hit.seq,
                "role": hit.role,
                "kind": hit.kind,
                "at": hit.at,
                "score": hit.score,
                "viewScores": hit.view_scores,
                # True when this unit was pulled in next to a match rather than
                # matching itself, which is why its score is 0 — without the
                # flag a zero score here reads as a bug in the ranking.
                "isContext": hit.is_context,
                "text": _truncate(hit.text, MAX_LOG_TEXT_CHARS),
            }
            for index, hit in enumerate(hits, start=1)
        ],
    }


async def run_search_memory(
    scope: Scope,
    tool_input: dict[str, Any],
    log: list[dict[str, Any]] | None = None,
) -> str:
    """
    Input: the caller's scope (from the session, never from the model), the
    tool call's raw input dict, and optionally a list to append this search's
    `retrieval_record()` to.
    Output: the tool result string.

    The scope argument is the security boundary in tool form. The model supplies
    a query string and nothing else — there is no owner or conversation
    parameter in the schema for it to set, so a prompt injection in a retrieved
    document has no field to aim at. Widening the search is not something the
    model is able to express.

    `log` is a sink rather than a second return value because of who calls this:
    two of the three providers can yield an event the moment this returns, but
    agent_sdk.py's MCP handler cannot yield at all and only shares a closure
    with its provider loop. A list both can see is the one shape that works
    everywhere, and it stays a list rather than a single slot because one turn
    can search more than once. Passing None skips logging entirely.

    Every path appends exactly one record, including the two that found nothing
    — "the model searched and retrieval was down" is precisely the thing a log
    should not be silent about.

    Everything is caught. A turn that could have been answered without memory
    must not fail because memory did; the worst outcome available here is a
    slightly less informed answer, and that is by design.
    """
    query = str(tool_input.get("query", "")).strip()
    if not query:
        if log is not None:
            log.append(retrieval_record(query, [], status="empty-query"))
        return NO_RESULTS

    try:
        hits = await search(scope, query, k=_limit())
    except Exception as err:  # noqa: BLE001 - a retrieval failure must not fail the user's turn
        print(f"memory: search_memory failed ({err.__class__.__name__}: {err})")
        if log is not None:
            log.append(retrieval_record(query, [], status=f"unavailable: {err.__class__.__name__}"))
        return UNAVAILABLE

    if log is not None:
        log.append(retrieval_record(query, hits))

    return format_hits(hits)
