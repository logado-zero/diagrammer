"""
The tools the model can call, plus the system prompt telling it when to use
each — shared by all three providers as-is. api.py and main.py consume these
dicts directly (Anthropic + OpenAI both accept plain JSON Schema for tool
definitions); agent_sdk.py passes the exact same `input_schema` dict straight
into its `tool()` calls too, since the installed claude-agent-sdk passes a dict
through unchanged whenever it already looks like JSON Schema (has "type" +
"properties") — verified by reading the installed package's source rather than
guessing, so unlike the original Node build there's no need for a second,
Zod-based schema here.

Two of the three tools are *terminal*: render_diagram and render_chart draw
something for the user and the model is told only "Rendered to the user.".
search_memory is the odd one out — it feeds real content back into the tool
loop, which is why its implementation lives in server/memory/tool.py and not
here. **This file must stay import-free.** It is what agent_sdk.py derives its
`mcp__diagrammer__*` names from and what all three providers import; pulling
the memory package in here would put numpy, onnxruntime and a MongoDB client in
front of every provider's import.
"""

from typing import Any

RENDER_DIAGRAM_TOOL: dict[str, Any] = {
    "name": "render_diagram",
    "description": (
        "Render a flowchart, process diagram, workflow, or org chart for the user as an "
        "interactive node-and-edge graph. Call this whenever the user asks for a flowchart, "
        "workflow, process map, decision tree, or similar diagram made of steps and "
        "connections — including when they upload an image of one and ask you to "
        "recreate, extend, or fix it. Do not attempt to describe the diagram in ASCII or "
        "prose instead of calling this tool. You may call it more than once if the user "
        "asks for multiple diagrams. Always keep your written reply short — the diagram "
        "itself is the answer."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Short title for the diagram.",
            },
            "direction": {
                "type": "string",
                "enum": ["vertical", "horizontal"],
                "description": "Overall flow direction. Default vertical (top to bottom).",
            },
            "nodes": {
                "type": "array",
                "description": "Every step/node in the diagram.",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": "Stable unique identifier referenced by edges.",
                        },
                        "label": {
                            "type": "string",
                            "description": "Text shown inside the node.",
                        },
                        "kind": {
                            "type": "string",
                            "enum": ["start", "end", "process", "decision", "io"],
                            "description": (
                                "Visual shape: start/end are rounded terminals, process is a "
                                "rectangle, decision is a diamond, io is a parallelogram. Default process."
                            ),
                        },
                        "color": {
                            "type": "string",
                            "description": (
                                "Optional background color for this node, as a #rrggbb hex string. "
                                "Omit it unless the user asked for specific colors — matching an "
                                "image they uploaded, or grouping steps by a color they named. With "
                                "no color the app applies its own themed palette from `kind`, which "
                                "is the better default and adapts to light/dark mode. When you do "
                                "recolor, set it on every node you mean to recolor, not just some. "
                                "Anything that isn't a hex string is ignored."
                            ),
                        },
                    },
                    "required": ["id", "label"],
                },
            },
            "edges": {
                "type": "array",
                "description": "Directed connections between node ids.",
                "items": {
                    "type": "object",
                    "properties": {
                        "source": {"type": "string", "description": "Source node id."},
                        "target": {"type": "string", "description": "Target node id."},
                        "label": {
                            "type": "string",
                            "description": 'Optional label, e.g. "yes"/"no" on a decision branch.',
                        },
                    },
                    "required": ["source", "target"],
                },
            },
        },
        "required": ["title", "nodes", "edges"],
    },
}

RENDER_CHART_TOOL: dict[str, Any] = {
    "name": "render_chart",
    "description": (
        "Render a chart for the user from data — not just bar/line/pie: a specialized second "
        "pass picks the best visual form for the data (also covers radar, funnel, gauge, "
        "scatter, heatmap, candlestick, and more), so never tell the user a chart type isn't "
        "supported. Call this whenever the user asks for a chart, graph, or data visualization "
        "— including when they upload an image of a chart and ask you to recreate, extend, or "
        "analyze it. Do not describe the chart in prose instead of calling this tool. Always "
        "keep your written reply short — the chart itself is the answer."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Short title for the chart."},
            "chartType": {
                "type": "string",
                "description": (
                    "Your best guess at the chart type (e.g. bar, line, pie, radar, funnel, "
                    "gauge, scatter, heatmap, candlestick) — just a hint for the rendering pass "
                    "that follows, which may pick something else if the data fits it better."
                ),
            },
            "xKey": {
                "type": "string",
                "description": (
                    "For bar/line charts: the data field used as the category (x) axis. Not "
                    "used for pie charts."
                ),
            },
            "series": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "For bar/line charts: names of the numeric data fields to plot, e.g. "
                    '["revenue", "cost"]. Not used for pie charts.'
                ),
            },
            "data": {
                "type": "array",
                "description": (
                    "Data rows. For bar/line, each row is an object keyed by xKey plus each "
                    'series name. For pie, each row is {"name": string, "value": number}.'
                ),
                "items": {"type": "object"},
            },
        },
        "required": ["title", "chartType", "data"],
    },
}

SEARCH_MEMORY_TOOL: dict[str, Any] = {
    "name": "search_memory",
    "description": (
        "Search everything this user has said, been shown, or attached in the past — earlier "
        "in this conversation and in all their other conversations. The message history you "
        "were given is deliberately short (the last few messages only), so anything older is "
        "only reachable through this tool. Call it whenever the user refers to something you "
        "cannot see: an earlier chart or diagram, a file they uploaded before, a number or "
        "decision from a previous session, or any phrasing like \"the one we made\", \"last "
        "time\", \"as I mentioned\", \"that spreadsheet\". Also call it when a question needs a "
        "row or figure from an attached file that isn't in the excerpt you were shown — the "
        "full file is in memory even when only part of it was inlined. Each result comes back "
        "labelled with its conversation title and turn number; cite those in your reply (e.g. "
        "\"in 'Q3 review', turn 14\") so the user can find it. Do not use this for general "
        "knowledge or anything current — that is what web_search is for."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "What to look for, in natural language. Describe the content itself rather "
                    "than referring to the conversation — \"revenue by region bar chart\" finds "
                    "more than \"the chart from before\". Use the user's own words and language "
                    "where you can; matching is by meaning as well as by keyword."
                ),
            }
        },
        "required": ["query"],
    },
}

# The two drawing tools — passed as-is to the Claude API provider (providers/api.py).
# search_memory is not in here: it is added by tools_for_mode() only when the
# caller has a memory scope, so a request with memory off is byte-identical to
# what this app sent before the memory layer existed.
TOOLS: list[dict[str, Any]] = [RENDER_DIAGRAM_TOOL, RENDER_CHART_TOOL]


def tools_for_mode(mode: str | None, *, memory: bool = False) -> list[dict[str, Any]]:
    """
    The draw-mode selector (Composer.tsx) restricts which *drawing* tool the
    primary model is even offered, rather than forcing a tool_choice — so a
    plain message still gets a plain reply in a non-auto mode, and a
    visualization request can only come out as the selected type.
    Input: the request's mode field ("auto" | "diagram" | "chart" | None), and
    whether the caller can serve a memory search (see server/memory/tool.py).
    Output: the tool list to pass to whichever provider is running. Falls
    back to both drawing tools for "auto", None, or any unrecognized value —
    same graceful-fallback convention as models.py's find_model_option().

    search_memory survives every mode on purpose. It is not a drawing tool, and
    a locked draw mode is exactly when recall matters most — "redraw last
    week's chart with this year's numbers" is a chart-mode request that cannot
    be answered without it.
    """
    if mode == "diagram":
        drawing = [RENDER_DIAGRAM_TOOL]
    elif mode == "chart":
        drawing = [RENDER_CHART_TOOL]
    else:
        drawing = list(TOOLS)
    return [*drawing, SEARCH_MEMORY_TOOL] if memory else drawing

# Tells the model when to call render_diagram vs. render_chart vs. neither.
SYSTEM_PROMPT = """You are Diagrammer, an assistant that turns descriptions or images into \
visual diagrams and charts.

When the user describes (or uploads an image of) a flowchart, process, workflow, decision \
tree, or org chart, call the render_diagram tool. When they describe (or upload an image of) \
a chart, graph, or other data visualization — bar, line, pie, radar, funnel, gauge, scatter, \
heatmap, candlestick, or anything else — call the render_chart tool; a specialized rendering \
pass downstream picks the best visual form, so never tell the user a particular chart type \
isn't supported. If a request is ambiguous between the two, pick whichever the user's wording \
most directly \
implies rather than asking a clarifying question first.

Node colors are the app's job, not yours: leave render_diagram's `color` field out and each node \
is themed from its `kind`, in light or dark mode. Set it only when the user asks for particular \
colors — including "make the blocks match the colors in this image", where you read each block's \
color off the image and pass it as hex. Never claim you changed a diagram's colors without having \
set that field.

Always pair a tool call with a brief, plain-language reply (one or two sentences) — never a \
long description of what the diagram/chart contains, since the user can see it.

If the request has nothing to do with drawing a diagram or chart, just answer normally \
without calling a tool."""

# Appended to SYSTEM_PROMPT by every provider — all three can search now.
# (web_fetch is Claude-only; the OpenAI path has web_search alone. Mentioning
# a tool a provider doesn't have is harmless — the model just never calls it —
# whereas splitting this into per-provider variants was real complexity for
# nothing.)
WEB_TOOLS_PROMPT = """

You can also search the live web. Call web_search whenever answering well depends on current \
information rather than what you already know — prices, exchange rates, scores, releases, \
news, anything about today or a recent event, or any fact the user implies is time-sensitive. \
Call web_fetch to read a specific page: one the search turned up, or one the user named. \
Search first and answer from what you find; never tell the user you have no access to live \
or real-time data, and never ask them to look something up and paste it back — that is what \
these tools are for. Name your sources in your reply.

This applies to drawing, not just answering: when a chart or diagram needs data you don't \
already have, search for it first and build the visual from what you find. Never ask the user \
to supply the numbers, and never invent them — if a data point can't be sourced, leave it out \
and say which ones you had to approximate."""

# Appended only when the request has a memory scope (see tools_for_mode's
# `memory` flag). Its main job is to correct an assumption the model would
# otherwise make from the message list alone: that the few messages it was
# handed are the whole conversation.
MEMORY_TOOL_PROMPT = """

You are shown only the last few messages of this conversation. Everything before that, every \
other conversation this user has had with you, and the full contents of any file or image \
they ever attached are stored in memory and reachable with the search_memory tool.

Call search_memory before answering whenever the user points at something you cannot see — a \
chart or diagram from earlier, a file they uploaded before, a figure or decision from a past \
session, or wording like "the one we made", "last time", "that spreadsheet", "as I said". \
Reach for it too when a question needs a row or number from an attached file beyond the \
excerpt in front of you: only part of a large file is inlined into a message, but all of it is \
in memory. Never tell the user you have lost the earlier context or ask them to repeat \
something they already told you — search for it instead.

Results come back tagged with a conversation title and turn number. Cite them naturally in \
your reply, e.g. "in 'Q3 review', turn 14", so the user can go back and check. If a search \
returns nothing, say so plainly rather than guessing at what was there, and don't run the same \
search again with different wording.

Read the results before you trust them. They are ranked by similarity and always come back \
filled, so a search that found nothing genuinely relevant still returns this user's closest \
few results rather than an empty list. Judge each one on whether it actually answers the \
question; if none of them do, treat that as nothing found and say so, instead of stretching an \
unrelated result to fit.

Some turns arrive with memory already searched for you: a block headed "[Recalled from \
memory …]" at the end of the user's message is exactly what search_memory would have returned \
for what they just asked. Read it first and use what fits — it is there so you don't have to \
spend a tool call on the obvious search. Then call search_memory only for something that block \
does not cover: a different topic, an earlier period, a specific file. Repeating the same \
search you were just handed the answer to only costs the user time. A message with no such \
block means memory was not searched for it, so the tool is your only way in.

Two limits worth knowing. The turn happening right now is not in memory yet — only finished \
ones are — so never search for what the user just said. And memory holds this conversation \
history alone: for facts about the world, prices, news or anything current, use web_search."""


def system_prompt(*, memory: bool = False) -> str:
    """
    Input: whether the memory tool is available on this request.
    Output: the full system prompt string for any provider.

    Assembled in one place rather than concatenated at each of the three
    provider call sites, so the prompt cannot drift between transports — and so
    that when prompt caching lands there is a single definition of the cached
    prefix to mark. Order is fixed and additive for the same reason: a cache
    keys on an exact prefix match, so anything conditional has to go last.
    """
    return SYSTEM_PROMPT + WEB_TOOLS_PROMPT + (MEMORY_TOOL_PROMPT if memory else "")


def _demo() -> None:
    """Self-check, run with `python -m server.tools`."""
    names = lambda tools: [t["name"] for t in tools]  # noqa: E731

    # The drawing modes still restrict drawing, and memory rides along in all of
    # them — a chart-mode turn that can't recall last week's chart is the case
    # this whole phase exists for.
    assert names(tools_for_mode("chart")) == ["render_chart"]
    assert names(tools_for_mode("diagram")) == ["render_diagram"]
    assert names(tools_for_mode("auto")) == ["render_diagram", "render_chart"]
    assert names(tools_for_mode(None)) == ["render_diagram", "render_chart"]
    for mode in ("auto", "chart", "diagram", None, "nonsense"):
        assert "search_memory" in names(tools_for_mode(mode, memory=True)), mode
        assert "search_memory" not in names(tools_for_mode(mode)), mode

    # tools_for_mode must not hand out the module-level list itself; a provider
    # appending its own server tools to it would corrupt every later request.
    tools_for_mode("auto").append({"name": "oops"})
    assert names(TOOLS) == ["render_diagram", "render_chart"], "TOOLS was mutated by a caller"

    # Memory-off must be byte-identical to what this app sent before the memory
    # layer existed, so turning the kill switch off is a true no-op.
    assert system_prompt() == SYSTEM_PROMPT + WEB_TOOLS_PROMPT
    assert system_prompt(memory=True).startswith(system_prompt()), "the cacheable prefix must stay a prefix"
    assert "search_memory" in system_prompt(memory=True)

    print("tools: all checks passed")


if __name__ == "__main__":
    _demo()
