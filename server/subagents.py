"""
Two specialized second-pass LLM calls, inserted by agent.py between a
render_diagram/render_chart tool call from the primary chat model and what
gets sent to the client — pure visual/structural judgment, not geometry or
color (those stay client-side: Mermaid's own layout engine + a classDef
header keyed off each node's `kind` for diagrams, palette.ts's validated
light/dark palette for charts). Each function takes the primary model's
tool-call payload (the "intent") and returns a polished replacement in a
richer shape.

Reuses the already-constructed clients from server/providers/api.py and
server/providers/openai_provider.py rather than building new ones — no new
client construction/locking code needed here.
"""

import json
import os
import re
from typing import Any

from server.providers import api as api_provider
from server.providers import openai_provider

ANTHROPIC_SUBAGENT_MODEL = "claude-haiku-4-5"
OPENAI_SUBAGENT_MODEL = "gpt-4o-mini"
MAX_TOKENS = 2048

EMIT_CHART_OPTION_TOOL: dict[str, Any] = {
    "name": "emit_chart_option",
    "description": (
        "Return a polished Apache ECharts `option` object for the chart described by the "
        "given intent (title/chartType/xKey/series/data)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Short chart title, carried over from the intent."},
            "option": {
                "type": "object",
                "description": (
                    "A full ECharts `option` object. Pick whichever series type actually fits the "
                    "data's shape and the request, not just bar/line/pie: bar (grouped, stacked, or "
                    "waterfall via a transparent placeholder series) for comparisons; line/area "
                    "(smooth, optionally stacked) for trends over an ordered axis; scatter for two "
                    "numeric fields against each other; pie/donut/nightingale-rose (`roseType: "
                    "'radius'`) for a part-of-whole breakdown; radar when comparing a few named "
                    "items across several numeric attributes (each attribute becomes an `indicator`); "
                    "funnel for an ordered sequence of shrinking stage values; gauge for a single "
                    "score/percentage value against a min/max range; heatmap or calendar heatmap "
                    "(paired with a `visualMap`) for a magnitude spread across two categorical axes "
                    "or dates; candlestick for open/high/low/close style data; sunburst only for "
                    "genuinely hierarchical data with a real parent/child grouping (e.g. category "
                    "then sub-category) — never invent a hierarchy that isn't in the data. "
                    "Always include a `tooltip` (trigger 'axis' for bar/line/scatter, 'item' for "
                    "pie/heatmap/sunburst) and a `legend` only when there are 2+ series or "
                    "slices worth naming. NEVER use two y-axes on one chart (`yAxis: [{}, {}]`) — "
                    "if two measures have very different scales, index them to a common base and "
                    "plot one axis, or say in your reply that it should be two separate charts. "
                    "Cap categorical cardinality at 8 (bars in a series, pie/donut/rose slices, "
                    "radar comparison items): if the data has more than 8 categories, aggregate the "
                    "smallest into a single 'Other' bucket rather than showing all of them raw. "
                    "Apply these mark specs so the chart reads clearly at a glance: bars get "
                    "`barMaxWidth: 24` and a small `itemStyle.borderRadius` on the outer corners "
                    "only; lines get `lineStyle: {width: 2}`; markers/symbols are `symbolSize` 8 or "
                    "more; area fills use `areaStyle: {opacity: 0.1}` (a wash, not a solid block); "
                    "axis split lines are thin and solid, never dashed; label points sparingly (the "
                    "endpoint or the extreme, never a value on every single point) rather than "
                    "crowding the chart. Do not include `color`, `backgroundColor`, or any other "
                    "literal color values anywhere in the object — the caller applies its own theme "
                    "palette (including the right ramp for gauge zones, heatmap visualMap, and "
                    "candlestick up/down) on top, based on the chart's structure. Must be plain "
                    "JSON (no functions/callbacks)."
                ),
            },
        },
        "required": ["title", "option"],
    },
}

EMIT_TITLE_TOOL: dict[str, Any] = {
    "name": "emit_title",
    "description": "Return a short title naming what a saved conversation is about, for a history sidebar.",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": (
                    "A short title for the conversation, 5 words or fewer. Name the subject "
                    "matter, not the activity — 'Q3 revenue by region', not 'Chart request' or "
                    "'User asks for a chart'. No surrounding quotes, no trailing period, no "
                    "'Conversation about' preamble. Write it in the same language the user "
                    "wrote in. If a diagram or chart was produced, title it after what that "
                    "visual shows."
                ),
            }
        },
        "required": ["title"],
        "additionalProperties": False,
    },
}

TITLE_SUBAGENT_PROMPT = """You name saved conversations for a history sidebar. You'll be given the \
opening turns of a conversation with a diagram/chart assistant, including any diagram or chart it \
produced. Call emit_title with a short, specific title following the guidance in the tool's schema \
in full. The title has to be recognizable at a glance in a list of dozens, so favor the concrete \
subject over generic words like chart, diagram, request, or help."""

EMIT_MERMAID_FLOWCHART_TOOL: dict[str, Any] = {
    "name": "emit_mermaid_flowchart",
    "description": (
        "Return a polished Mermaid flowchart for the diagram described by the given intent "
        "(title/direction/nodes/edges) and the user's own wording."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Short diagram title, carried over from the intent."},
            "mermaid": {
                "type": "string",
                "description": (
                    "Complete, valid Mermaid flowchart source, starting with `flowchart TD` or "
                    "`flowchart LR` (pick based on the graph's shape: LR for wide graphs with "
                    "parallel branches, TD for linear/tall chains). Preserve the intent's node ids "
                    "and connections exactly — don't add, remove, or reconnect anything, just style "
                    "and label them well.\n\n"
                    "Tag every node with its kind as a Mermaid class, e.g. "
                    'id["Label"]:::start, :::endNode, :::process, :::decision, :::io — these five '
                    "class names only, matching each node's kind from the intent (refine the kind "
                    "if the intent got it wrong; most flows have exactly one start and at least one "
                    "end). Do NOT define your own classDef or set any style/fill/color — the caller "
                    "applies its own theme palette to these five class names after the fact.\n\n"
                    "Only add a YAML frontmatter block setting look: handDrawn (a `---` line, then "
                    "`config:` then `  look: handDrawn` then `---`, before the `flowchart` line) if "
                    "the user's own message explicitly asks for a hand-drawn, sketchy, whiteboard, "
                    "or doodle style — omit the frontmatter entirely otherwise (classic look is the "
                    "default).\n\n"
                    "Labels: tighten to 4 words or fewer where possible. Add a short label (e.g. "
                    '"Yes"/"No") to edges leaving a decision node that don\'t already have one, '
                    "written as source -->|Yes| target.\n\n"
                    "Syntax rules that matter (violating these silently breaks rendering): never "
                    "use a semicolon anywhere in a label or edge text; always wrap node labels in "
                    "double quotes inside their brackets; never put an arrow-like substring (-> or "
                    "-->) inside label text; keep node ids plain alphanumeric/underscore with no "
                    "spaces or punctuation; make sure every bracket you open is closed on the same "
                    "node; the bare word `end` is a reserved Mermaid keyword (it closes subgraph "
                    "blocks) — never use it as a class name OR a node id, even if the intent's own "
                    "node id is literally \"end\" (rename that one node id to endNode and update any "
                    "edges that reference it too — this is the one case where preserving the "
                    "intent's ids exactly must be broken), which is exactly why the end-of-flow "
                    "class is called endNode, not end."
                ),
            },
        },
        "required": ["title", "mermaid"],
        "additionalProperties": False,
    },
}

FLOWCHART_SUBAGENT_PROMPT = """You are a flowchart styling specialist producing Mermaid flowchart \
syntax. You'll be given a rough flowchart intent (title, direction, nodes, edges) produced by \
another assistant, plus the user's own most recent message. Call emit_mermaid_flowchart with a \
polished Mermaid flowchart, following the guidance in the tool's schema in full — the kind \
classes, the handDrawn-only-when-asked rule, and the syntax rules all matter as much as the \
content itself. Base the `look` decision only on this message: use handDrawn if it explicitly \
asks for a hand-drawn, sketchy, whiteboard, or doodle style, classic otherwise."""

CHART_SUBAGENT_PROMPT = """You are a data visualization specialist producing Apache ECharts \
configuration. You'll be given a rough chart intent (title, chartType, xKey, series, data) \
produced by another assistant. Call emit_chart_option with a polished, best-fit ECharts option \
for that data, following the guidance in the tool's schema in full — the chart TYPE, the 8-category \
cap, the single-y-axis rule, and the mark specs all matter as much as the data itself. Pick \
whichever chart type actually fits the data's shape and the user's request; the intent's \
chartType is only a rough hint from an assistant that wasn't specialized for this, not a \
constraint — many requests read much better as a radar, funnel, gauge, or scatter than as a plain \
bar/line/pie, and a wide/high-cardinality table often reads better as a heatmap than as a crowded \
bar chart. Never sacrifice legibility for variety: don't force an exotic chart type onto data that \
doesn't actually fit it."""


async def _call_subagent(
    system_prompt: str,
    tool_name: str,
    tool_description: str,
    tool_input_schema: dict[str, Any],
    user_json: str,
) -> dict[str, Any] | None:
    """
    Runs one forced-tool-call LLM turn against whichever provider has a key
    configured, mirroring the forced-structured-output pattern the primary
    providers already use for their own tool loop.
    Input: a system prompt + tool definition + the intent as a JSON string.
    Output: the tool call's input dict, or None if no subagent client is
    available or the call failed (callers fall back to a deterministic
    default rather than erroring the whole turn over a styling pass).
    """
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            message = await api_provider._client.messages.create(
                model=ANTHROPIC_SUBAGENT_MODEL,
                max_tokens=MAX_TOKENS,
                system=system_prompt,
                tools=[{"name": tool_name, "description": tool_description, "input_schema": tool_input_schema}],
                tool_choice={"type": "tool", "name": tool_name},
                messages=[{"role": "user", "content": user_json}],
            )
        except Exception:  # noqa: BLE001 - styling pass is best-effort, fall back instead of erroring the turn
            return None
        for block in message.content:
            if block.type == "tool_use":
                return block.input
        return None

    if os.environ.get("OPENAI_API_KEY"):
        client = await openai_provider._get_client()
        try:
            response = await client.chat.completions.create(
                model=OPENAI_SUBAGENT_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_json},
                ],
                tools=[
                    {
                        "type": "function",
                        "function": {"name": tool_name, "description": tool_description, "parameters": tool_input_schema},
                    }
                ],
                tool_choice={"type": "function", "function": {"name": tool_name}},
            )
        except Exception:  # noqa: BLE001 - styling pass is best-effort, fall back instead of erroring the turn
            return None
        tool_calls = response.choices[0].message.tool_calls
        if not tool_calls:
            return None
        try:
            return json.loads(tool_calls[0].function.arguments)
        except json.JSONDecodeError:
            return None

    return None


async def run_flowchart_subagent(intent: dict[str, Any], user_text: str = "") -> dict[str, Any]:
    """
    Turns a render_diagram tool-call payload (title/direction/nodes/edges)
    into {title, mermaid}, a Mermaid flowchart. user_text (the latest user
    message) lets the subagent decide the handDrawn-or-classic look per
    request — see EMIT_MERMAID_FLOWCHART_TOOL's schema. Falls back to a
    small deterministic Mermaid mapping (_fallback_mermaid_flowchart) if no
    subagent client is configured or the call fails.
    """
    user_json = json.dumps({"intent": intent, "userMessage": user_text})
    result = await _call_subagent(
        FLOWCHART_SUBAGENT_PROMPT,
        EMIT_MERMAID_FLOWCHART_TOOL["name"],
        EMIT_MERMAID_FLOWCHART_TOOL["description"],
        EMIT_MERMAID_FLOWCHART_TOOL["input_schema"],
        user_json,
    )
    payload = result if result is not None else _fallback_mermaid_flowchart(intent)
    if isinstance(payload.get("mermaid"), str):
        payload = {**payload, "mermaid": _rename_reserved_end(payload["mermaid"])}
    return payload


async def run_chart_subagent(intent: dict[str, Any]) -> dict[str, Any]:
    """
    Turns a render_chart tool-call payload (title/chartType/xKey/series/data)
    into {title, option}, an Apache ECharts option object. Falls back to a
    small deterministic mapping (_fallback_chart_option) if no subagent
    client is configured or the call fails, so the app still works with
    zero LLM calls set up for this pass.
    """
    result = await _call_subagent(
        CHART_SUBAGENT_PROMPT,
        EMIT_CHART_OPTION_TOOL["name"],
        EMIT_CHART_OPTION_TOOL["description"],
        EMIT_CHART_OPTION_TOOL["input_schema"],
        json.dumps(intent),
    )
    return result if result is not None else _fallback_chart_option(intent)


MAX_TITLE_CHARS = 60


async def run_title_subagent(messages: list[dict[str, Any]]) -> str | None:
    """
    Names a saved conversation from its opening turns, for the history
    sidebar (see main.py, which decides *when* to call this).
    Input: the stored message dicts so far — text plus any diagram/chart
    titles, which is all the naming needs. Output: a short title, or None
    if no subagent client is configured or the call failed, in which case
    the caller keeps the placeholder title. A title is cosmetic; it must
    never fail a turn.
    """
    digest = [
        {
            "role": m.get("role"),
            "text": (m.get("text") or "")[:1000],
            "visuals": [v.get("title", "") for v in (m.get("diagrams") or []) + (m.get("charts") or [])],
        }
        for m in messages
    ]
    result = await _call_subagent(
        TITLE_SUBAGENT_PROMPT,
        EMIT_TITLE_TOOL["name"],
        EMIT_TITLE_TOOL["description"],
        EMIT_TITLE_TOOL["input_schema"],
        json.dumps(digest, ensure_ascii=False),
    )
    title = (result or {}).get("title")
    if not isinstance(title, str):
        return None
    # Models still occasionally wrap the title in quotes despite the schema
    # saying not to, and an over-long one would just be clipped by the
    # sidebar's CSS anyway.
    title = title.strip().strip('"').strip()
    return title[:MAX_TITLE_CHARS] or None


def _fallback_chart_option(intent: dict[str, Any]) -> dict[str, Any]:
    """
    Deterministic, LLM-free mapping from the old render_chart payload shape
    straight into a basic ECharts option — used when no subagent client is
    configured. Input: {title, chartType, xKey, series, data}. Output:
    {title, option}.
    """
    title = intent.get("title", "")
    chart_type = intent.get("chartType", "bar")
    data = intent.get("data", [])

    if chart_type == "pie":
        option = {
            "tooltip": {"trigger": "item"},
            "series": [{"type": "pie", "radius": "70%", "data": data}],
        }
        return {"title": title, "option": option}

    x_key = intent.get("xKey", "name")
    series_keys = intent.get("series") or [
        k for k in (data[0].keys() if data else []) if k != x_key and isinstance(data[0][k], (int, float))
    ]
    option = {
        "tooltip": {"trigger": "axis"},
        "legend": {} if len(series_keys) > 1 else None,
        "xAxis": {"type": "category", "data": [row.get(x_key) for row in data]},
        "yAxis": {"type": "value"},
        "series": [
            {"type": chart_type, "name": key, "data": [row.get(key) for row in data]} for key in series_keys
        ],
    }
    option = {k: v for k, v in option.items() if v is not None}
    return {"title": title, "option": option}


_MERMAID_SHAPE = {
    "start": ('(["', '"])'),
    "end": ('(["', '"])'),
    "decision": ('{"', '"}'),
    "io": ('[/"', '"/]'),
    "process": ('["', '"]'),
}
# `end` alone is a reserved Mermaid keyword (closes subgraph blocks) — using it as a class name
# breaks parsing right after it, so the "end" kind gets a differently-named class, endNode.
_MERMAID_CLASS_NAME = {"end": "endNode"}
_MERMAID_ID_RE = re.compile(r"[^A-Za-z0-9_]")


def _mermaid_id(raw: str) -> str:
    """Input: a node id from the intent. Output: a safe Mermaid node identifier (plain alphanumeric/underscore, starting with a letter)."""
    safe = _MERMAID_ID_RE.sub("_", str(raw))
    return safe if safe and safe[0].isalpha() else f"n_{safe}"


def _sanitize_mermaid_text(text: Any) -> str:
    """Strips characters known to silently break Mermaid parsing (see PROGRESS.md's semicolon gotcha) from label/edge text."""
    return re.sub(r'[;|`"]', "", str(text)).strip()


_BARE_END_RE = re.compile(r"\bend\b")


def _rename_reserved_end(mermaid: str) -> str:
    """
    Deterministic backstop for the `end`-reserved-keyword gotcha (see PROGRESS.md): `end` breaks
    Mermaid parsing wherever it's used bare, as a node id or a class name, not just as a class
    name. A prompt instruction alone isn't reliable here — the subagent is also told to preserve
    the intent's node ids exactly, and the primary model sometimes names its end node "end", so
    the two instructions collide. Runs on every emit_mermaid_flowchart output (LLM or fallback)
    since there's no server-side flowchart validator to catch a slip before it reaches the client.
    Renames every bare `end` token to `endNode`, skipping quoted label text so a legitimate label
    containing the word "end" (e.g. "Weekend report") is left alone.
    """
    out_lines = []
    for line in mermaid.splitlines():
        parts = re.split(r'("[^"]*")', line)
        for i in range(0, len(parts), 2):  # even indices are outside quoted spans
            parts[i] = _BARE_END_RE.sub("endNode", parts[i])
        out_lines.append("".join(parts))
    return "\n".join(out_lines)


def _fallback_mermaid_flowchart(intent: dict[str, Any]) -> dict[str, Any]:
    """
    Deterministic, LLM-free mapping from the render_diagram payload shape
    into basic Mermaid flowchart syntax — used when no subagent client is
    configured. Input: {title, direction, nodes, edges}. Output:
    {title, mermaid}. Always classic look (no handDrawn without a subagent
    to read the user's request) and no classDef (the client's kind-based
    header covers the same five class names either way).
    """
    title = intent.get("title", "")
    direction = "LR" if intent.get("direction") == "horizontal" else "TD"
    nodes = intent.get("nodes", [])
    edges = intent.get("edges", [])
    ids = {n["id"]: _mermaid_id(n["id"]) for n in nodes if "id" in n}

    lines = [f"flowchart {direction}"]
    for n in nodes:
        kind = n.get("kind") if n.get("kind") in _MERMAID_SHAPE else "process"
        open_b, close_b = _MERMAID_SHAPE[kind]
        label = _sanitize_mermaid_text(n.get("label", n.get("id", "")))
        class_name = _MERMAID_CLASS_NAME.get(kind, kind)
        lines.append(f"    {ids[n['id']]}{open_b}{label}{close_b}:::{class_name}")
    for e in edges:
        source, target = ids.get(e.get("source")), ids.get(e.get("target"))
        if not source or not target:
            continue
        label = e.get("label")
        if label:
            lines.append(f"    {source} -->|{_sanitize_mermaid_text(label)}| {target}")
        else:
            lines.append(f"    {source} --> {target}")

    return {"title": title, "mermaid": "\n".join(lines)}


def _demo() -> None:
    """Self-check for _fallback_chart_option — the no-subagent-client path, run with `python -m server.subagents`."""
    bar = _fallback_chart_option(
        {"title": "Revenue", "chartType": "bar", "xKey": "q", "series": ["revenue"], "data": [{"q": "Q1", "revenue": 100}]}
    )
    assert bar["option"]["series"][0]["data"] == [100]
    assert bar["option"]["xAxis"]["data"] == ["Q1"]

    pie = _fallback_chart_option(
        {"title": "Share", "chartType": "pie", "data": [{"name": "A", "value": 1}, {"name": "B", "value": 2}]}
    )
    assert pie["option"]["series"][0]["type"] == "pie"
    assert "color" not in pie["option"]

    inferred = _fallback_chart_option({"title": "Two series", "chartType": "line", "data": [{"name": "x", "a": 1, "b": 2}]})
    assert {s["name"] for s in inferred["option"]["series"]} == {"a", "b"}
    assert "legend" in inferred["option"]

    flow = _fallback_mermaid_flowchart(
        {
            "title": "Onboarding",
            "direction": "vertical",
            "nodes": [
                {"id": "a", "label": "Sign up", "kind": "start"},
                {"id": "b", "label": "Choose plan?", "kind": "decision"},
                {"id": "c", "label": "Done", "kind": "end"},
            ],
            "edges": [
                {"source": "a", "target": "b"},
                {"source": "b", "target": "c", "label": "Yes"},
            ],
        }
    )
    lines = flow["mermaid"].splitlines()
    assert lines[0] == "flowchart TD"
    assert any(line.endswith(":::start") for line in lines)
    assert any(line.endswith(":::decision") for line in lines)
    assert any(line.endswith(":::endNode") for line in lines), "the 'end' kind must map to endNode, not the reserved word end"
    assert not any(line.endswith(":::end") for line in lines), "bare :::end is a reserved Mermaid keyword and breaks parsing"
    assert '-->|Yes|' in flow["mermaid"]
    assert ";" not in flow["mermaid"]

    unsafe = _fallback_mermaid_flowchart(
        {
            "title": "T",
            "nodes": [{"id": "weird id!", "label": 'say "hi"; ok', "kind": "process"}],
            "edges": [],
        }
    )
    assert "weird_id" in unsafe["mermaid"], unsafe["mermaid"]
    assert 'say hi ok' in unsafe["mermaid"], unsafe["mermaid"]
    assert ";" not in unsafe["mermaid"]

    # end used as the literal node id (not just a class name) — the bug seen live, where the
    # primary model's intent named its final node "end" and the subagent preserved that id verbatim.
    end_as_id = _fallback_mermaid_flowchart(
        {
            "title": "T",
            "nodes": [
                {"id": "start", "label": "Begin", "kind": "start"},
                {"id": "end", "label": "Finish", "kind": "end"},
            ],
            "edges": [{"source": "start", "target": "end"}],
        }
    )
    assert "end([" in end_as_id["mermaid"], "fallback should still emit the raw id before sanitizing"
    sanitized = _rename_reserved_end(end_as_id["mermaid"])
    assert "endNode([" in sanitized, sanitized
    assert not re.search(r"\bend\b", sanitized), sanitized
    assert "-->" in sanitized and "endNode" in sanitized.splitlines()[-1]
    weekend = _rename_reserved_end('    a["Weekend report"]:::process')
    assert "Weekend report" in weekend, "a label merely containing the word end must be left alone"

    print("subagents: all checks passed")


if __name__ == "__main__":
    _demo()
