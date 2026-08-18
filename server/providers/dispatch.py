"""
The parts of a tool call that are the same whichever transport is running.

Each provider streams differently and wraps a tool result differently — that
much is genuinely per-transport and stays in api.py / openai_provider.py /
agent_sdk.py. What is *not* per-transport is the table underneath: which tool
name produces which client event, what string the model gets back, and the
status line the user sees while it runs. Those were written out three times,
each copy carrying a comment saying it matched the other two by hand.

`ProcessTrace.tsx` renders the labels verbatim, so drift here is visible to
the user, not just untidy.
"""

from typing import Any

from server.memory import Scope, search_memory
from server.providers.types import AgentEvent

# What the model gets back after a drawing tool runs. The drawing tools are
# terminal: the payload has already gone to the client, so there is nothing to
# report except that it landed. tools.py documents this as the contract.
RENDERED_RESULT = "Rendered to the user."

# Status lines for the client. One spelling each, shared by every transport —
# api.py keys the first two by Anthropic's server-tool names, agent_sdk.py by
# Claude Code's built-in names, openai_provider.py uses WEB_SEARCH directly.
WEB_SEARCH_LABEL = "Searching the web…"
WEB_FETCH_LABEL = "Reading a page…"
MEMORY_SEARCH_LABEL = "Searching memory…"

# Stands in for the user's text when they upload an image and say nothing.
IMAGE_ONLY_PROMPT = "Recreate this as an editable diagram or chart."


async def dispatch_tool_call(
    name: str,
    payload: dict[str, Any],
    scope: Scope | None,
    events: list[AgentEvent],
) -> str:
    """
    Runs one of this app's own tools.
    Input: the tool name, its parsed input, the caller's memory scope (None
    disables search_memory), and a list to append client events to.
    Output: the string to hand back to the model.

    Events go into a list rather than being yielded because the callers are
    already inside their own `async def ... yield ...` loops and need the
    result string in the same pass — the same out-parameter shape
    search_memory() itself already uses for its retrieval records.

    Not used by agent_sdk.py: there the MCP server invokes the tool handlers
    out of band, so that transport can only *observe* tool-use blocks and emit
    the trace events itself. It shares the constants above and nothing else.
    """
    if name == "render_diagram":
        events.append({"type": "diagram", "payload": payload})
        return RENDERED_RESULT

    if name == "render_chart":
        events.append({"type": "chart", "payload": payload})
        return RENDERED_RESULT

    if name == "search_memory" and scope is not None:
        # Nothing streams while this runs — an encode plus a vector scan — so
        # say what's happening, same as for a web search. The "retrieval"
        # events that follow are the log-only record of what came back
        # (memory/tool.py's retrieval_record); main.py files them and does not
        # relay them.
        events.append({"type": "trace", "label": MEMORY_SEARCH_LABEL})
        records: list[dict[str, Any]] = []
        result = await search_memory(scope, payload, records)
        events.extend({"type": "retrieval", **record} for record in records)
        return result

    # Unreachable unless the model hallucinates a tool name. Saying so beats a
    # silent empty result, which reads as a malfunction and gets retried.
    return f"Unknown tool: {name}."


def _demo() -> None:
    """Self-check: python -m server.providers.dispatch"""
    import asyncio

    async def run() -> None:
        events: list[AgentEvent] = []
        assert await dispatch_tool_call("render_diagram", {"a": 1}, None, events) == RENDERED_RESULT
        assert events == [{"type": "diagram", "payload": {"a": 1}}], events

        events = []
        assert await dispatch_tool_call("render_chart", {"b": 2}, None, events) == RENDERED_RESULT
        assert events == [{"type": "chart", "payload": {"b": 2}}], events

        # No scope means the tool was never offered, so a call to it is as
        # unknown as a hallucinated name — and must not touch the database.
        events = []
        result = await dispatch_tool_call("search_memory", {"query": "x"}, None, events)
        assert result == "Unknown tool: search_memory.", result
        assert events == [], events

        events = []
        assert await dispatch_tool_call("nope", {}, None, events) == "Unknown tool: nope."
        assert events == [], events

    asyncio.run(run())
    print("dispatch: all checks passed")


if __name__ == "__main__":
    _demo()
