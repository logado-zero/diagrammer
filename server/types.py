"""
Shared request/response shapes for the FastAPI app and for the
render_diagram / render_chart tool payloads. Mirrors src/types.ts (the
frontend's copy) by hand — keep both in sync if you change these.

Only ChatRequestMessage/ChatRequestBody are ever actually parsed by
pydantic (they're the POST /api/chat request body). The diagram/chart
TypedDicts below are documentation only: a tool call's `input` arrives as
an untyped dict from the model and is forwarded to the client as-is,
exactly like the Node backend's `payload: unknown` — never validated
server-side.
"""

from typing import Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field

DiagramNodeKind = Literal["start", "end", "process", "decision", "io"]


class DiagramNodeInput(TypedDict, total=False):
    """One node inside a render_diagram tool call."""

    id: str
    label: str
    kind: DiagramNodeKind


class DiagramEdgeInput(TypedDict, total=False):
    """One directed connection between two node ids inside a render_diagram tool call."""

    source: str
    target: str
    label: str


class RenderDiagramInput(TypedDict, total=False):
    """Full input the model sends when it calls render_diagram (see tools.py)."""

    title: str
    direction: Literal["vertical", "horizontal"]
    nodes: list[DiagramNodeInput]
    edges: list[DiagramEdgeInput]


class RenderDiagramOutput(TypedDict, total=False):
    """
    The diagram event payload actually sent to the client over SSE, after
    server/subagents.py's run_flowchart_subagent() turns a
    RenderDiagramInput intent into Mermaid flowchart source (see
    src/components/DiagramCard.tsx).
    """

    title: str
    mermaid: str


class RenderChartInput(TypedDict, total=False):
    """
    Full input the model sends when it calls render_chart (see tools.py).
    chartType is a free-text hint (bar/line/pie/radar/funnel/gauge/...),
    not a closed enum — the chart subagent (server/subagents.py) picks the
    actual rendered form and may override it.
    """

    title: str
    chartType: str
    xKey: str
    series: list[str]
    data: list[dict]


class RenderChartOutput(TypedDict, total=False):
    """
    The chart event payload actually sent to the client over SSE, after
    server/subagents.py's run_chart_subagent() turns a RenderChartInput
    intent into an Apache ECharts option (see src/components/ChartCard.tsx).
    """

    title: str
    option: dict


class ChatImageInput(BaseModel):
    """Base64-encoded image attached to a chat message (wire field names match the frontend's JSON)."""

    model_config = ConfigDict(populate_by_name=True)

    media_type: str = Field(alias="mediaType")
    data: str


class ChatFileInput(BaseModel):
    """
    Base64-encoded text/spreadsheet file attached to a chat message —
    separate from ChatImageInput because these never reach a provider's
    multimodal API; server/attachments.py's inline_file_attachment()
    decodes/parses one into plain text before any provider sees the
    message, keyed off media_type ("text/plain" or the xlsx mimetype).
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str
    media_type: str = Field(alias="mediaType")
    data: str


class ChatRequestMessage(BaseModel):
    """One turn of chat history, as sent by the client."""

    role: Literal["user", "assistant"]
    text: str
    image: ChatImageInput | None = None
    file: ChatFileInput | None = None


class ChatRequestBody(BaseModel):
    """Body of POST /api/chat."""

    model_config = ConfigDict(populate_by_name=True)

    messages: list[ChatRequestMessage]
    # Which saved conversation this turn belongs to (server/db.py). None means
    # "start a new one" — main.py creates the document and tells the client its
    # id over SSE, so there's no separate create route.
    conversation_id: str | None = Field(None, alias="conversationId")
    # Model catalog id from GET /api/models (see server/models.py). Falls back to DEFAULT_MODEL_ID.
    model: str | None = None
    # Draw-mode selector ("auto" | "diagram" | "chart"), see tools.py's tools_for_mode().
    # Falls back to "auto" for None or any unrecognized value, same convention as `model`.
    mode: str | None = None
