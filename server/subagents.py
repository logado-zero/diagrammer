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

from server import mermaid
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


def _subagent_provider(preferred: str | None) -> str | None:
    """
    Input: the provider that answered this turn ("claude"/"openai"), or None
    when the caller has no opinion. Output: which provider runs the styling
    pass, or None if neither has a key.

    Prefers the turn's own provider so a diagram drawn by an OpenAI model is
    polished by one too. It used to check ANTHROPIC_API_KEY first regardless,
    which meant the picker chose one vendor for the answer and the environment
    silently chose another for the styling.

    Falls back when the preferred one has no key to bill — notably Claude in
    agent-sdk mode, which authenticates through the local CLI and has no API
    key at all, so its subagent pass has to run on OpenAI or not at all.
    """
    if preferred == "claude" and os.environ.get("ANTHROPIC_API_KEY"):
        return "claude"
    if preferred == "openai" and os.environ.get("OPENAI_API_KEY"):
        return "openai"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "claude"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    return None


async def _call_subagent(
    system_prompt: str,
    tool: dict[str, Any],
    user_json: str,
    provider: str | None = None,
) -> dict[str, Any] | None:
    """
    Runs one forced-tool-call LLM turn, mirroring the forced-structured-output
    pattern the primary providers already use for their own tool loop.
    Input: a system prompt, one of this file's *_TOOL dicts, the intent as a
    JSON string, and the provider that answered the turn.
    Output: the tool call's input dict, or None if no subagent client is
    available or the call failed (callers fall back to a deterministic
    default rather than erroring the whole turn over a styling pass).
    """
    tool_name = tool["name"]
    tool_description = tool["description"]
    tool_input_schema = tool["input_schema"]
    chosen = _subagent_provider(provider)

    if chosen == "claude":
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

    if chosen == "openai":
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


async def run_flowchart_subagent(
    intent: dict[str, Any], user_text: str = "", provider: str | None = None
) -> dict[str, Any]:
    """
    Turns a render_diagram tool-call payload (title/direction/nodes/edges)
    into {title, mermaid}, a Mermaid flowchart. user_text (the latest user
    message) lets the subagent decide the handDrawn-or-classic look per
    request — see EMIT_MERMAID_FLOWCHART_TOOL's schema. Falls back to a
    small deterministic Mermaid mapping (_fallback_mermaid_flowchart) if no
    subagent client is configured or the call fails.
    """
    user_json = json.dumps({"intent": intent, "userMessage": user_text})
    result = await _call_subagent(FLOWCHART_SUBAGENT_PROMPT, EMIT_MERMAID_FLOWCHART_TOOL, user_json, provider)
    payload = result if result is not None else _fallback_mermaid_flowchart(intent)
    if isinstance(payload.get("mermaid"), str):
        payload = {**payload, "mermaid": mermaid.rename_reserved_end(payload["mermaid"])}
    return payload


async def run_chart_subagent(intent: dict[str, Any], provider: str | None = None) -> dict[str, Any]:
    """
    Turns a render_chart tool-call payload (title/chartType/xKey/series/data)
    into {title, option}, an Apache ECharts option object. Falls back to a
    small deterministic mapping (_fallback_chart_option) if no subagent
    client is configured or the call fails, so the app still works with
    zero LLM calls set up for this pass.
    """
    result = await _call_subagent(CHART_SUBAGENT_PROMPT, EMIT_CHART_OPTION_TOOL, json.dumps(intent), provider)
    return result if result is not None else _fallback_chart_option(intent)


MAX_TITLE_CHARS = 60


async def run_title_subagent(messages: list[dict[str, Any]], provider: str | None = None) -> str | None:
    """
    Names a saved conversation from its opening turns, for the history
    sidebar (see server/routes/chat.py, which decides *when* to call this).
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
        TITLE_SUBAGENT_PROMPT, EMIT_TITLE_TOOL, json.dumps(digest, ensure_ascii=False), provider
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
    ids = {n["id"]: mermaid.safe_id(n["id"]) for n in nodes if "id" in n}

    lines = [f"flowchart {direction}"]
    for n in nodes:
        kind = n.get("kind") if n.get("kind") in _MERMAID_SHAPE else "process"
        open_b, close_b = _MERMAID_SHAPE[kind]
        label = mermaid.sanitize_text(n.get("label", n.get("id", "")))
        class_name = mermaid.CLASS_NAME.get(kind, kind)
        lines.append(f"    {ids[n['id']]}{open_b}{label}{close_b}:::{class_name}")
    for e in edges:
        source, target = ids.get(e.get("source")), ids.get(e.get("target"))
        if not source or not target:
            continue
        label = e.get("label")
        if label:
            lines.append(f"    {source} -->|{mermaid.sanitize_text(label)}| {target}")
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
    sanitized = mermaid.rename_reserved_end(end_as_id["mermaid"])
    assert "endNode([" in sanitized, sanitized
    assert not re.search(r"\bend\b", sanitized), sanitized
    assert "-->" in sanitized and "endNode" in sanitized.splitlines()[-1]
    weekend = mermaid.rename_reserved_end('    a["Weekend report"]:::process')
    assert "Weekend report" in weekend, "a label merely containing the word end must be left alone"

    # _subagent_provider: the turn's own vendor wins when it has a key, and
    # the fallback still fires when it doesn't (agent-sdk Claude has no key).
    import os as _os

    saved = {k: _os.environ.get(k) for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY")}
    try:
        _os.environ["ANTHROPIC_API_KEY"] = "a"
        _os.environ["OPENAI_API_KEY"] = "o"
        assert _subagent_provider("openai") == "openai"
        assert _subagent_provider("claude") == "claude"
        assert _subagent_provider(None) == "claude"

        _os.environ.pop("ANTHROPIC_API_KEY")
        assert _subagent_provider("claude") == "openai", "agent-sdk Claude must fall back, not stall"

        _os.environ.pop("OPENAI_API_KEY")
        assert _subagent_provider("openai") is None
    finally:
        for k, v in saved.items():
            if v is None:
                _os.environ.pop(k, None)
            else:
                _os.environ[k] = v

    print("subagents: all checks passed")


if __name__ == "__main__":
    _demo()
