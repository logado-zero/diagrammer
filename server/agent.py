"""
Dispatcher: the one place that decides which of the three providers
(server/providers/{api,agent_sdk,openai_provider}.py) handles a chat turn.
server/main.py calls only run_agent() below and never branches on
provider itself.
"""

import os
import re
from typing import Any, Literal

from server.attachments import inline_file_attachment
from server.memory import Scope, select_context
from server.models import find_model_option
from server.providers.agent_sdk import run_agent_sdk_agent
from server.providers.api import run_api_agent
from server.providers.openai_provider import run_openai_agent
from server.providers.types import AgentEventStream
from server.subagents import run_chart_subagent, run_flowchart_subagent
from server.types import ChatRequestMessage

HISTORY_LEN = 3
ClaudeTransport = Literal["api", "agent-sdk"]

# Seen live: a title carried stray C0 control bytes in place of freshly-
# generated accented text (agent-sdk mode, local `claude` CLI transport,
# no subagent client configured so _fallback_mermaid_flowchart copies
# `title` through verbatim) — a transport-level glitch, not anything this
# app's code does to the string. Stripped here, the one point every
# provider's diagram/chart payload already passes through, so it's a single
# guard against any provider rather than one per transport.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def _clean_title(title: Any) -> Any:
    """Input: a diagram/chart payload's `title` field. Output: it with stray control characters removed and whitespace collapsed, or unchanged if it isn't a string."""
    if not isinstance(title, str):
        return title
    return re.sub(r"\s+", " ", _CONTROL_CHARS_RE.sub("", title)).strip()


_HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
# A node id only becomes a color if it's a bare Mermaid identifier. The client
# turns each entry into a `class <id> ncolorN` statement, and Mermaid's parser
# rejects anything else outright — `class "weird id" ncolor0` is a parse error
# that takes the entire diagram down with it, not a skipped line (measured; an
# id that simply doesn't exist in the graph is the harmless case, it renders
# fine and is ignored). Colors are cosmetic, so a weird id loses its color
# rather than the user losing their diagram.
_SAFE_MERMAID_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def _node_colors(intent: dict[str, Any]) -> dict[str, str]:
    """
    Input: a render_diagram tool-call payload, before the flowchart subagent
    reshapes it. Output: {node id: hex color} for the nodes the model gave an
    explicit color, or {} for the usual case where it gave none.

    Colors travel as data on the payload rather than as Mermaid syntax, and
    they're read off the *intent* rather than the subagent's output, for the
    same reason: the client owns the final styling. DiagramCard.tsx's hand-drawn
    toggle needs a wider stroke-width to make rough.js's hachure fill read as
    solid and classic must not have one, and that toggle lives in the browser —
    the server can't know which look is on, so it can't emit a correct classDef.
    Handing over {id: color} lets the client build the right one per look and
    per theme, and keeps subagents.py's "never set a style" rule intact.

    Hex only. Models reach for "red" or "rgb(220, 20, 60)" given half a chance,
    and the client's ink-contrast pick has to parse the value; anything else is
    dropped, which degrades to the themed default rather than to a broken node.
    """
    colors: dict[str, str] = {}
    for node in intent.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        node_id, color = node.get("id"), node.get("color")
        if not isinstance(node_id, str) or not isinstance(color, str):
            continue
        if not _HEX_COLOR_RE.match(color.strip()) or not _SAFE_MERMAID_ID_RE.match(node_id):
            continue
        # The one id the subagent is allowed to rename (bare `end` is a reserved
        # Mermaid keyword) — see subagents._rename_reserved_end, which does the
        # same rewrite on the source side.
        colors["endNode" if node_id == "end" else node_id] = color.strip()
    return colors


def resolve_claude_transport() -> ClaudeTransport:
    """
    Claude has no separate "not configured" state the way OpenAI does: if
    ANTHROPIC_API_KEY is set, use the metered API directly; otherwise fall
    back to the local `claude` CLI via the Agent SDK (see CLAUDE.md
    "Backend provider switch" for agent-sdk's caveats — dev-only, no images).
    """
    return "api" if os.environ.get("ANTHROPIC_API_KEY") else "agent-sdk"


async def run_agent(
    request_messages: list[ChatRequestMessage],
    model_id: str | None = None,
    mode: str | None = None,
    scope: Scope | None = None,
) -> AgentEventStream:
    """
    Entry point main.py calls for every POST /api/chat.
    Input: the full chat history, plus an optional model catalog id,
    draw-mode selector ("auto" | "diagram" | "chart" — see tools.py's
    tools_for_mode(), which mode is threaded down to), and the memory scope
    this request may search.
    Output: an async generator of AgentEvent dicts (see providers/types.py)
    streamed straight through to the client as SSE. A text/spreadsheet
    attachment on the last message is inlined to plain text before any
    provider runs (server/attachments.py) — providers only ever see text.
    diagram/chart events are intercepted here — the one point every
    provider's output passes through — and run through a second,
    specialized LLM call (server/subagents.py) that polishes the payload
    before it's forwarded, with "trace" events emitted around that call so
    the client can show live progress; every other event type passes
    through untouched.

    `scope` is forwarded rather than used here: unlike diagram/chart, a
    search_memory call has to be answered *inside* each provider's own tool
    loop, so it can't be intercepted at this choke point the way the events
    below are. Passing None (which main.py does when memory is off) removes
    the tool and its prompt text entirely.
    """
    # Only the last HISTORY_LEN messages go to the model. Everything older is
    # reachable through the search_memory tool instead — which is what makes
    # this truncation safe rather than lossy, and why it was written this way
    # before the tool existed (see DISCUSS_RAG.md).
    if len(request_messages) > HISTORY_LEN:
        request_messages = request_messages[-HISTORY_LEN:]

    # The push path, and it runs *before* attachment inlining on purpose: after
    # it, a spreadsheet's entire markdown table is sitting in `.text`, and the
    # retrieval query would be the spreadsheet rather than the question about it.
    evidence, retrieval = (None, None)
    if scope is not None:
        evidence, retrieval = await select_context(scope, request_messages)

    # Inline any text/spreadsheet attachment on the last user message before any provider sees it.
    # The provider never needs to know attachments exist beyond `image` (which stays provider-specific multimodal handling, untouched).
    request_messages = inline_file_attachment(request_messages)

    # Captured before the evidence block is appended: the flowchart subagent
    # uses this to understand what the user actually asked for, and several
    # thousand characters of recalled memory is noise for that job.
    last_user_text = request_messages[-1].text if request_messages else ""

    if evidence:
        # Appended, never prepended, and never as a system message: retrieved
        # evidence changes every single turn, so it has to sit behind everything
        # stable or it invalidates a cached prefix on every request
        # (DISCUSS_RAG.md §10). Same reason attachments are prepended and this
        # is not — one is stable for the turn, the other is the volatile part.
        last = request_messages[-1]
        request_messages = [
            *request_messages[:-1],
            last.model_copy(update={"text": f"{last.text}\n\n{evidence}"}),
        ]

    option = find_model_option(model_id)

    if option.provider == "openai":
        stream = run_openai_agent(request_messages, option.model, mode, scope)
    elif resolve_claude_transport() == "api":
        stream = run_api_agent(request_messages, option.model, mode, scope)
    else:
        stream = run_agent_sdk_agent(request_messages, mode, scope)

    if retrieval is not None:
        # A skipped turn is logged but silent: the gate fired before any work
        # happened, so there is nothing for the user to watch and claiming
        # otherwise would be the fabricated progress the trace box exists to
        # avoid. main.py files the retrieval event into agent_logs either way
        # and never relays it — see providers/types.py.
        if retrieval.get("status") != "skipped":
            yield {"type": "trace", "label": "Recalling from memory…"}
        yield {"type": "retrieval", **retrieval}

    async for event in stream:
        if event["type"] == "diagram":
            yield {"type": "trace", "label": "Active agent: Flowchart Agent"}
            yield {"type": "trace", "label": "Calling render_diagram tool…"}
            colors = _node_colors(event["payload"])
            payload = await run_flowchart_subagent(event["payload"], last_user_text)
            yield {"type": "trace", "label": "Result ready"}
            # `colors` is omitted entirely when the model set none, so an
            # ordinary diagram's payload stays exactly what it was before.
            event = {
                "type": "diagram",
                "payload": {
                    **payload,
                    "title": _clean_title(payload.get("title")),
                    **({"colors": colors} if colors else {}),
                },
            }
        elif event["type"] == "chart":
            yield {"type": "trace", "label": "Active agent: Data Chart Agent"}
            yield {"type": "trace", "label": "Calling render_chart tool…"}
            payload = await run_chart_subagent(event["payload"])
            yield {"type": "trace", "label": "Result ready"}
            event = {"type": "chart", "payload": {**payload, "title": _clean_title(payload.get("title"))}}
        yield event


def _demo() -> None:
    """Self-check for _clean_title and _node_colors, run with `python -m server.agent`."""
    corrupted = "Workflow Automation Testing — m\x0e\x1e theo h\x005ng m\x1e\x121u"
    assert _clean_title(corrupted) == "Workflow Automation Testing — m theo h5ng m1u", _clean_title(corrupted)
    assert _clean_title("Quy trình hướng dẫn") == "Quy trình hướng dẫn"
    assert _clean_title(None) is None
    assert _clean_title(42) == 42

    # The common case: no color anywhere, so the payload must not grow a key.
    assert _node_colors({"nodes": [{"id": "a", "label": "A", "kind": "process"}]}) == {}
    assert _node_colors({}) == {}

    colors = _node_colors(
        {
            "nodes": [
                {"id": "a", "color": "#7c3aed"},
                {"id": "b", "color": " #FFF "},
                {"id": "c", "color": "red"},
                {"id": "d", "color": "rgb(1, 2, 3)"},
                {"id": "e", "color": 16711680},
                {"id": "f"},
                {"id": "end", "color": "#e8564f"},
                # Ids Mermaid's `class <id> …` statement can't take. Letting one
                # through is a parse error that kills the whole diagram, so
                # these must be dropped, not sanitized into something else.
                {"id": "weird id", "color": "#111111"},
                {"id": "1step", "color": "#111111"},
                {"id": "a-b", "color": "#111111"},
                "not a dict",
            ]
        }
    )
    assert colors == {"a": "#7c3aed", "b": "#FFF", "endNode": "#e8564f"}, colors

    print("agent: all checks passed")


if __name__ == "__main__":
    _demo()
