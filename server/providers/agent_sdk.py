"""
Claude provider backed by the Claude Agent SDK, which shells out to a
local `claude` CLI subprocess and authenticates as whatever account that
CLI is logged into (including a Pro/Max subscription) instead of a
metered API key. Dev-only — see CLAUDE.md for the full caveats: no image
input, no real multi-turn session (each request replays the whole
history as one prompt string), per-request subprocess cost, and this is
never something to deploy for other users.
"""

import os

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    StreamEvent,
    ToolUseBlock,
    create_sdk_mcp_server,
    query,
    tool,
)

from server.memory import Scope, search_memory
from server.providers.types import AgentEventStream
from server.tools import (
    RENDER_CHART_TOOL,
    RENDER_DIAGRAM_TOOL,
    SEARCH_MEMORY_TOOL,
    system_prompt,
    tools_for_mode,
)
from server.types import ChatRequestMessage

# Same default the Node build documented as a valid example model string
# for this SDK generation. Override with AGENT_SDK_MODEL if a newer model
# string is confirmed to work — don't assume it matches api.py's
# DEFAULT_MODEL, the two transports aren't guaranteed to accept the same
# model id strings.
MODEL = os.environ.get("AGENT_SDK_MODEL", "claude-opus-4-8")

# Claude Code built-ins this app wants. Everything that touches the local
# filesystem or shell (Bash, Read, Write, …) stays off — see ClaudeAgentOptions
# below, where `tools` is this list and nothing else.
BUILTIN_TOOLS = ["WebSearch", "WebFetch"]

# Status lines for the client while a built-in runs (api.py has the same map
# keyed by that transport's own server-tool names). A CLI subprocess doing a
# web search streams nothing for a while, so say what's happening.
_BUILTIN_TOOL_LABELS = {"WebSearch": "Searching the web…", "WebFetch": "Reading a page…"}

# Emitted when the model calls search_memory. Separate from the map above
# because that one is keyed by Claude Code's own tool names, while this arrives
# under the mcp__diagrammer__ prefix.
_MEMORY_TOOL_NAME = f"mcp__diagrammer__{SEARCH_MEMORY_TOOL['name']}"
_MEMORY_LABEL = "Searching memory…"


def _allowed_tools_for_mode(mode: str | None, memory: bool) -> list[str]:
    """MCP-prefixed tool names for the draw-mode selector, plus the web tools — reuses tools.py's tools_for_mode() rather than a second mode->tool-name mapping."""
    names = [f"mcp__diagrammer__{tool['name']}" for tool in tools_for_mode(mode, memory=memory)]
    return names + BUILTIN_TOOLS


async def _render_diagram_handler(_args: dict) -> dict:
    return {"content": [{"type": "text", "text": "Rendered to the user."}]}


async def _render_chart_handler(_args: dict) -> dict:
    return {"content": [{"type": "text", "text": "Rendered to the user."}]}


# MCP tools reusing tools.py's JSON Schema dicts directly (no second, Zod-
# style definition needed like the Node build had — the installed
# claude-agent-sdk passes a dict straight through unchanged whenever it
# already has "type"/"properties", verified by reading
# claude_agent_sdk/__init__.py's tool()/create_sdk_mcp_server() source
# rather than guessing).
_render_diagram_tool = tool(
    RENDER_DIAGRAM_TOOL["name"],
    RENDER_DIAGRAM_TOOL["description"],
    RENDER_DIAGRAM_TOOL["input_schema"],
)(_render_diagram_handler)

_render_chart_tool = tool(
    RENDER_CHART_TOOL["name"],
    RENDER_CHART_TOOL["description"],
    RENDER_CHART_TOOL["input_schema"],
)(_render_chart_handler)


def _build_mcp_server(scope: Scope | None, pending_records: list[dict]):
    """
    Input: this request's memory scope (None to expose only the two drawing
    tools) and a list the memory handler appends its log record to.
    Output: an in-process MCP server for query().

    Built per request rather than once at import, which is the one structural
    difference from the other two providers. An MCP handler receives only the
    tool's arguments — there is no request context parameter — so the only way
    to give `search_memory` an owner is to close over it, and a closure over a
    per-request value cannot live in a module-level server. The alternative, a
    ContextVar read inside a module-level handler, would work until the day a
    handler ran on a different task than the one that set it, and would fail by
    searching the wrong person's memory rather than by raising.

    `pending_records` rides along for the same reason. A handler cannot yield —
    it is called by the MCP server, not from the provider's loop — so what the
    search found is written to a list the loop drains instead.

    ponytail: rebuilt every call. This provider already spawns a whole `claude`
    CLI subprocess per request (see the module docstring), so constructing a
    handful of tool descriptors alongside it does not register. If this ever
    moves to a persistent session, cache by owner id.
    """
    tools = [_render_diagram_tool, _render_chart_tool]

    if scope is not None:

        async def _search_memory_handler(args: dict) -> dict:
            text = await search_memory(scope, args, pending_records)
            return {"content": [{"type": "text", "text": text}]}

        tools.append(
            tool(
                SEARCH_MEMORY_TOOL["name"],
                SEARCH_MEMORY_TOOL["description"],
                SEARCH_MEMORY_TOOL["input_schema"],
            )(_search_memory_handler)
        )

    return create_sdk_mcp_server(name="diagrammer", version="1.0.0", tools=tools)


def _build_prompt(messages: list[ChatRequestMessage]) -> str:
    """
    Flattens chat history into one text transcript, since query()'s prompt
    is a single string rather than a message list.
    Input: ChatRequestMessage list. Output: a "User: ...\\n\\nAssistant: ..." transcript string.
    """
    parts = [f"{'User' if m.role == 'user' else 'Assistant'}: {m.text}" for m in messages]
    return "\n\n".join(parts)


async def run_agent_sdk_agent(
    request_messages: list[ChatRequestMessage],
    mode: str | None = None,
    scope: Scope | None = None,
) -> AgentEventStream:
    """
    Runs one chat turn via a local `claude` CLI subprocess.
    Input: full message history (rejected with an error event if the last
    message carries an image — unsupported in this mode) + the draw-mode
    selector (see tools.py's tools_for_mode()) + the memory scope for this
    request, or None to run without the search_memory tool.
    Output: an async generator of AgentEvent dicts, same shape as
    run_api_agent, built by translating the SDK's StreamEvent /
    AssistantMessage / ResultMessage instances.
    """
    last = request_messages[-1] if request_messages else None
    if last and last.image:
        yield {
            "type": "error",
            "message": (
                "Image input is not supported in agent-sdk mode yet. Configure "
                "ANTHROPIC_API_KEY to use image uploads via the api provider instead."
            ),
        }
        return

    prompt = _build_prompt(request_messages)

    memory = scope is not None
    # Filled by the search_memory MCP handler, drained by the loop below — see
    # _build_mcp_server.
    pending_records: list[dict] = []
    options = ClaudeAgentOptions(
        model=MODEL,
        system_prompt=system_prompt(memory=memory),
        tools=BUILTIN_TOOLS,
        mcp_servers={"diagrammer": _build_mcp_server(scope, pending_records)},
        allowed_tools=_allowed_tools_for_mode(mode, memory),
        include_partial_messages=True,
    )

    streamed_text = False

    try:
        async for message in query(prompt=prompt, options=options):
            # Whatever the memory handler logged since the last message. These
            # are log-only records (main.py files them, never relays them), so
            # unlike a status line their exact ordering doesn't matter — which is
            # just as well, since the handler runs between two SDK messages.
            while pending_records:
                yield {"type": "retrieval", **pending_records.pop(0)}

            if isinstance(message, StreamEvent):
                event = message.event
                delta = event.get("delta", {})
                if event.get("type") == "message_start" and streamed_text:
                    # Claude Code emits several assistant messages per turn
                    # (narration, then the answer). Concatenated raw they run
                    # together mid-sentence — "…Let me pull it.Data is spread
                    # across sources." — so start each one on its own paragraph.
                    yield {"type": "text", "text": "\n\n"}
                elif event.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                    streamed_text = True
                    yield {"type": "text", "text": delta["text"]}
                continue

            if isinstance(message, AssistantMessage):
                if message.error:
                    yield {"type": "error", "message": f"Agent SDK error: {message.error}"}
                    return
                for block in message.content:
                    if isinstance(block, ToolUseBlock):
                        if block.name == "mcp__diagrammer__render_diagram":
                            yield {"type": "diagram", "payload": block.input}
                        elif block.name == "mcp__diagrammer__render_chart":
                            yield {"type": "chart", "payload": block.input}
                        elif block.name == _MEMORY_TOOL_NAME:
                            # The handler itself can't yield — it's called by
                            # the MCP server, not from this loop — so the trace
                            # is emitted here, off the tool-use block instead.
                            yield {"type": "trace", "label": _MEMORY_LABEL}
                        elif block.name in _BUILTIN_TOOL_LABELS:
                            yield {"type": "trace", "label": _BUILTIN_TOOL_LABELS[block.name]}
                continue

            if isinstance(message, ResultMessage) and message.subtype != "success":
                yield {
                    "type": "error",
                    "message": f"Agent SDK run did not complete successfully ({message.subtype}).",
                }
                return
    except Exception as err:  # noqa: BLE001 - surfaced to the client as an error event
        yield {"type": "error", "message": str(err) or "Agent SDK request failed."}
        return

    # Anything the handler wrote during the final message has no next iteration
    # to be drained by.
    while pending_records:
        yield {"type": "retrieval", **pending_records.pop(0)}

    yield {"type": "done"}
