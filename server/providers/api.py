"""
Claude provider backed directly by the Anthropic Messages API
(the `anthropic` package's AsyncAnthropic client), billed against
ANTHROPIC_API_KEY. Supports image input. See CLAUDE.md "Backend provider
switch" for how this compares to agent_sdk.py's local-CLI transport.
"""

from typing import Any

import anthropic

from server.memory import Scope
from server.providers.dispatch import (
    IMAGE_ONLY_PROMPT,
    WEB_FETCH_LABEL,
    WEB_SEARCH_LABEL,
    dispatch_tool_call,
)
from server.providers.types import AgentEvent, AgentEventStream
from server.tools import system_prompt, tools_for_mode
from server.types import ChatRequestMessage

DEFAULT_MODEL = "claude-opus-5"
MAX_TOKENS = 4096
# 8, not 6: a server tool that hits the API's own internal iteration limit ends
# the turn with stop_reason "pause_turn" and needs a round trip just to resume,
# and search_memory adds another before the model has even started drawing.
# "Recall the old chart, search the web for this year's numbers, then render"
# is a single user request that spends four of these.
MAX_TOOL_ITERATIONS = 8

# Anthropic-run tools: the API executes these itself and puts the results in the
# same response, so unlike render_diagram/render_chart there's no handler here
# and no tool_result to send back. web_search returns ranked search hits (titles,
# URLs, snippets); web_fetch opens one page and reads it. Declared per provider
# rather than in tools.py because they're native to this transport — tools.py's
# list is also what agent_sdk.py derives its `mcp__diagrammer__*` names from.
SERVER_TOOLS: list[dict[str, Any]] = [
    {"type": "web_search_20260209", "name": "web_search", "max_uses": 5},
    {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 5},
]

# Status lines for the client, keyed by *Anthropic's* server tool names. The
# strings themselves come from providers/dispatch.py so all three transports
# say the same thing (see agent.py, which emits the same "trace" event type
# around the diagram/chart subagents).
_SERVER_TOOL_LABELS = {
    "web_search": WEB_SEARCH_LABEL,
    "web_fetch": WEB_FETCH_LABEL,
}

# Anthropic's client does not throw if ANTHROPIC_API_KEY is unset (unlike
# OpenAI's — see providers/openai_provider.py) — it only fails on first
# request. Safe to construct once at import time, and safe to share across
# concurrent requests: it's stateless per call. timeout=60.0 because the
# SDK default connect timeout (5s) is too tight on slow/high-latency
# networks and fails before the request even starts.
_client = anthropic.AsyncAnthropic(timeout=60.0)


def _to_anthropic_messages(messages: list[ChatRequestMessage]) -> list[dict[str, Any]]:
    """
    Converts client chat history into Anthropic's message format, attaching
    an image to every user message that carries one, as an image content
    block. Input: ChatRequestMessage list. Output: Anthropic message dicts.

    Every message, not just the last: the client resends an image on each turn
    of the conversation it belongs to (lib/api.ts's toOutgoing) and agent.py's
    HISTORY_LEN keeps it in the window, so restricting this to the final message
    was what made "now make the blocks match the colors in that image" a
    question asked of a model that could no longer see the image. HISTORY_LEN
    caps how many can arrive at once.
    """
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "user" and m.image:
            content = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": m.image.media_type,
                        "data": m.image.data,
                    },
                },
                {"type": "text", "text": m.text or IMAGE_ONLY_PROMPT},
            ]
            out.append({"role": "user", "content": content})
        else:
            out.append({"role": m.role, "content": m.text})
    return out


def _serialize_assistant_content(blocks: list[Any]) -> list[dict[str, Any]]:
    """
    Converts the SDK's response content blocks back into plain dicts so they
    can be replayed as history on the next tool-loop iteration.
    Input: the final message's content blocks. Output: plain dicts.

    Every block type is kept, not just text/tool_use: a turn that searched
    also carries server_tool_use + web_search_tool_result blocks, and dropping
    those loses the search results from history the moment the loop runs a
    second iteration (the "search this, then chart it" turn).
    """
    return [block.model_dump(exclude_none=True) for block in blocks]


async def run_api_agent(
    request_messages: list[ChatRequestMessage],
    model: str = DEFAULT_MODEL,
    mode: str | None = None,
    scope: Scope | None = None,
) -> AgentEventStream:
    """
    Runs one chat turn against Claude, looping up to MAX_TOOL_ITERATIONS
    times so the model can give a short reply after a tool call.
    Input: full message history + a Claude model id string + the
    draw-mode selector (see tools.py's tools_for_mode()) + the memory scope
    for this request, or None to run without the search_memory tool.
    Output: an async generator of AgentEvent dicts — text deltas as they
    stream in, then a diagram/chart event per tool call, then done (or
    error on failure).
    """
    messages = _to_anthropic_messages(request_messages)
    tools = [*tools_for_mode(mode, memory=scope is not None), *SERVER_TOOLS]

    for _ in range(MAX_TOOL_ITERATIONS):
        try:
            async with _client.messages.stream(
                model=model,
                max_tokens=MAX_TOKENS,
                system=system_prompt(memory=scope is not None),
                tools=tools,
                messages=messages,
            ) as stream:
                async for event in stream:
                    if event.type == "content_block_delta" and event.delta.type == "text_delta":
                        yield {"type": "text", "text": event.delta.text}
                    elif event.type == "content_block_start" and event.content_block.type == "server_tool_use":
                        # Search runs server-side and can take a while with
                        # nothing streaming, so say what's happening.
                        label = _SERVER_TOOL_LABELS.get(event.content_block.name)
                        if label:
                            yield {"type": "trace", "label": label}
                message = await stream.get_final_message()
        except Exception as err:  # noqa: BLE001 - surfaced to the client as an error event
            yield {"type": "error", "message": str(err) or "Request failed."}
            return

        if message.stop_reason == "refusal":
            yield {"type": "error", "message": "I can't help with that request."}
            return

        # Text was already streamed above via content_block_delta events —
        # only tool_use blocks need handling here (their input arrives
        # fully parsed on the accumulated final message rather than as
        # incremental deltas).
        #
        # One pass builds both the client events and the results sent back to
        # the model, because the two are no longer independent: the drawing
        # tools are terminal and answer with a fixed string, while
        # search_memory's answer is the whole point of calling it.
        tool_use_blocks = [b for b in message.content if b.type == "tool_use"]
        tool_results: list[dict[str, Any]] = []
        for block in tool_use_blocks:
            events: list[AgentEvent] = []
            result = await dispatch_tool_call(block.name, block.input, scope, events)
            for event in events:
                yield event
            tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": result})

        messages.append({"role": "assistant", "content": _serialize_assistant_content(message.content)})

        # A server tool (web_search/web_fetch) hit the API's own internal
        # iteration limit mid-turn. Re-send the conversation as-is and the
        # server picks up where it left off — no extra user message, the
        # trailing server_tool_use block is what signals the resume.
        if message.stop_reason == "pause_turn":
            continue

        if message.stop_reason != "tool_use" or not tool_use_blocks:
            break

        messages.append({"role": "user", "content": tool_results})

    yield {"type": "done"}


if __name__ == "__main__":
    # python -m server.providers.api — the history replayed on a second loop
    # iteration has to keep every block type, or a turn that searched and then
    # drew something loses its search results (and the API rejects a
    # web_search_tool_result whose server_tool_use went missing).
    from anthropic.types import (
        ServerToolUseBlock,
        TextBlock,
        ToolUseBlock,
        WebSearchResultBlock,
        WebSearchToolResultBlock,
    )

    replayed = _serialize_assistant_content(
        [
            TextBlock(type="text", text="Here you go.", citations=None),
            ServerToolUseBlock(type="server_tool_use", id="s1", name="web_search", input={"query": "sjc gold"}),
            WebSearchToolResultBlock(
                type="web_search_tool_result",
                tool_use_id="s1",
                content=[
                    WebSearchResultBlock(
                        type="web_search_result",
                        title="SJC",
                        url="https://sjc.com.vn",
                        encrypted_content="...",
                        page_age=None,
                    )
                ],
            ),
            ToolUseBlock(type="tool_use", id="t1", name="render_chart", input={"title": "Gold"}),
        ]
    )
    assert [b["type"] for b in replayed] == [
        "text",
        "server_tool_use",
        "web_search_tool_result",
        "tool_use",
    ], replayed
    assert replayed[3]["input"] == {"title": "Gold"}, replayed[3]
    # exclude_none must not eat a field the API needs back.
    assert replayed[2]["tool_use_id"] == "s1", replayed[2]

    assert {t["name"] for t in SERVER_TOOLS} == set(_SERVER_TOOL_LABELS), "every server tool needs a status line"
    print("api provider: all checks passed")
