"""
OpenAI provider using the Responses API (`openai` package's AsyncOpenAI,
`client.responses.create(stream=True)`), billed against OPENAI_API_KEY.
Supports image input and live web search.

Responses, not Chat Completions, specifically because of web search: on
Chat Completions `web_search_options` and function tools are mutually
exclusive (`gpt-4o` rejects the parameter outright, `gpt-4o-search-preview`
rejects `tools`, `gpt-5.6-luna` calls it unknown), and this app can't give up
render_diagram/render_chart. On Responses the built-in `web_search` tool and
function tools coexist — verified live with this app's own schemas. The same
move retires the old `reasoning_effort: "none"` workaround, which only
existed because Chat Completions refused function tools on reasoning models.

Shape mirrors providers/api.py: stream the text deltas, then read tool calls
off the completed response and loop.
"""

import asyncio
import json
import os
from typing import Any

from openai import AsyncOpenAI

from server.memory import Scope, search_memory
from server.providers.types import AgentEventStream
from server.tools import system_prompt, tools_for_mode
from server.types import ChatRequestMessage

DEFAULT_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")
# 6, not 4: search_memory adds a round trip before the model starts drawing,
# and "recall the old chart, then render an updated one" has to fit.
MAX_TOOL_ITERATIONS = 6

# OpenAI-run tools: the API executes these itself and the results never reach
# this process, so there's no handler and nothing to send back — same deal as
# providers/api.py's SERVER_TOOLS, which is why it has the same name here.
SERVER_TOOLS: list[dict[str, Any]] = [{"type": "web_search"}]

# Status lines for the client, matching the Claude providers' wording.
_SEARCH_LABEL = "Searching the web…"
_MEMORY_LABEL = "Searching memory…"

# Constructed lazily (not at import time) because the OpenAI SDK throws
# eagerly in its constructor when no API key is configured, and this module
# is imported unconditionally by agent.py regardless of the active
# provider. Guarded by a lock so two concurrent requests racing to
# construct it can't create it twice.
_client: AsyncOpenAI | None = None
_client_lock = asyncio.Lock()


async def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        async with _client_lock:
            if _client is None:
                # The SDK default connect timeout (5s) is too tight on slow/high-
                # latency networks and fails before the request even starts.
                _client = AsyncOpenAI(timeout=60.0)
    return _client


def _to_openai_tool(tool: dict[str, Any]) -> dict[str, Any]:
    """
    Responses puts a function tool's fields at the top level, unlike Chat
    Completions' nested {"function": {...}} — but `parameters` is the same
    plain JSON Schema as Anthropic's `input_schema`, so tools.py stays the
    single definition.
    """
    return {
        "type": "function",
        "name": tool["name"],
        "description": tool["description"],
        "parameters": tool["input_schema"],
    }


def _to_openai_input(messages: list[ChatRequestMessage]) -> list[dict[str, Any]]:
    """
    Converts client chat history into the Responses API's `input` list,
    attaching an image to every user message that carries one.
    Input: ChatRequestMessage list. Output: Responses input items.
    The system prompt is not in here — it goes in `instructions`.

    Every message, not just the last — same fix and same reasoning as
    providers/api.py's _to_anthropic_messages(): a follow-up turn about an
    image uploaded earlier was being answered by a model that had been handed
    only the text of that message.
    """
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "user" and m.image:
            out.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": m.text or "Recreate this as an editable diagram or chart.",
                        },
                        {
                            "type": "input_image",
                            "detail": "auto",
                            "image_url": f"data:{m.image.media_type};base64,{m.image.data}",
                        },
                    ],
                }
            )
        else:
            out.append({"role": m.role, "content": m.text})
    return out


async def run_openai_agent(
    request_messages: list[ChatRequestMessage],
    model: str = DEFAULT_MODEL,
    mode: str | None = None,
    scope: Scope | None = None,
) -> AgentEventStream:
    """
    Runs one chat turn against an OpenAI model, looping up to
    MAX_TOOL_ITERATIONS times so the model can reply again after a tool call.
    Input: full message history + a model id + the draw-mode selector (see
    tools.py's tools_for_mode()) + the memory scope for this request, or None
    to run without the search_memory tool.
    Output: an async generator of AgentEvent dicts, same shape as the Claude
    providers — text deltas as they stream, a trace event per web search,
    then a diagram/chart event per tool call, then done (or error).
    """
    conversation = _to_openai_input(request_messages)
    client = await _get_client()
    memory = scope is not None
    tools = [*(_to_openai_tool(t) for t in tools_for_mode(mode, memory=memory)), *SERVER_TOOLS]

    for _ in range(MAX_TOOL_ITERATIONS):
        try:
            stream = await client.responses.create(
                model=model,
                instructions=system_prompt(memory=memory),
                input=conversation,  # type: ignore
                tools=tools,  # type: ignore
                stream=True,
                # Stateless per request, like the rest of this backend — no
                # conversation is persisted on OpenAI's side between turns.
                store=False,
            )
        except Exception as err:  # noqa: BLE001 - surfaced to the client as an error event
            yield {"type": "error", "message": str(err) or "Request failed."}
            return

        output: list[Any] = []
        try:
            async for event in stream:
                if event.type == "response.output_text.delta":
                    yield {"type": "text", "text": event.delta}
                elif event.type == "response.output_item.added" and event.item.type == "web_search_call":
                    # Search runs on OpenAI's side and streams nothing while
                    # it works, so say what's happening.
                    yield {"type": "trace", "label": _SEARCH_LABEL}
                elif event.type == "response.completed":
                    output = list(event.response.output)
                elif event.type == "error":
                    yield {"type": "error", "message": getattr(event, "message", None) or "Request failed."}
                    return
        except Exception as err:  # noqa: BLE001 - surfaced to the client as an error event
            yield {"type": "error", "message": str(err) or "Request failed."}
            return

        # One pass, both jobs — the client events and the outputs sent back to
        # the model. See providers/api.py: the drawing tools are terminal and
        # answer with a fixed string, while search_memory's answer is the
        # reason it was called.
        calls = [item for item in output if item.type == "function_call"]
        outputs: list[dict[str, Any]] = []
        for call in calls:
            try:
                payload = json.loads(call.arguments or "{}")
            except json.JSONDecodeError:
                # Malformed tool-call JSON. Still needs an answer — every
                # function_call must be followed by its function_call_output or
                # the next request is rejected outright.
                result = "That tool call could not be parsed. Try again with valid JSON arguments."
                payload = {}
            else:
                result = "Rendered to the user."
                if call.name == "render_diagram":
                    yield {"type": "diagram", "payload": payload}
                elif call.name == "render_chart":
                    yield {"type": "chart", "payload": payload}
                elif call.name == "search_memory" and scope is not None:
                    # Status line for the user, then the log-only record of what
                    # the search returned — see providers/api.py.
                    yield {"type": "trace", "label": _MEMORY_LABEL}
                    records: list[dict[str, Any]] = []
                    result = await search_memory(scope, payload, records)
                    for record in records:
                        yield {"type": "retrieval", **record}
                else:
                    result = f"Unknown tool: {call.name}."
            outputs.append({"type": "function_call_output", "call_id": call.call_id, "output": result})

        if not calls:
            break

        # Replay the model's own output before answering its tool calls: a
        # function_call_output is only valid next to the function_call it
        # answers. Reasoning items are dropped — replaying them needs their
        # encrypted content, which store=False doesn't return.
        conversation += [item.model_dump(exclude_none=True) for item in output if item.type != "reasoning"]
        conversation += outputs

    yield {"type": "done"}
