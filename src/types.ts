/**
 * Client-side mirror of the shapes in server/src/types.ts (kept in sync by
 * hand), plus types the server never sees (ChatMessage, ModelOption)
 * because they carry client-only UI state like isStreaming/previewUrl.
 */

/**
 * A render_diagram tool call, after the server's flowchart subagent
 * (server/subagents.py's run_flowchart_subagent) turns it into Mermaid
 * flowchart source — see components/DiagramCard.tsx.
 */
export interface RenderDiagramInput {
  title: string
  mermaid: string
  /**
   * Per-node background colors ({nodeId: '#rrggbb'}), present only when the
   * user asked for specific ones — see server/agent.py's _node_colors(). Kept
   * out of the Mermaid source on purpose: DiagramCard turns it into classDefs
   * itself, because the right ones depend on the client-side hand-drawn toggle
   * and theme, neither of which the server can see.
   */
  colors?: Record<string, string>
}

/**
 * A render_chart tool call, after the server's chart subagent
 * (server/subagents.py's run_chart_subagent) turns it into an Apache
 * ECharts option — see components/ChartCard.tsx.
 */
export interface RenderChartInput {
  title: string
  option: Record<string, unknown>
}

/** An image attached to a chat message, plus the local blob: URL used to preview it before/while sending. */
export interface MessageImage {
  mediaType: string
  data: string
  previewUrl: string
}

/** Which attach option the composer offers (Image / Text / Sheet — Sheet covers .xlsx and .csv). */
export type AttachmentKind = 'image' | 'text' | 'sheet'

/**
 * A file the user has picked but not yet sent. Held by useComposer until
 * submit turns it into a ChatMessage's `image`/`file`; `previewUrl` is an
 * object URL (images only) that has to be revoked when it is dropped.
 */
export interface PendingAttachment {
  kind: AttachmentKind
  name: string
  previewUrl?: string
  base64: string
  mediaType: string
}

/**
 * A reference to an attachment persisted server-side (GridFS, see
 * server/db.py's `attachment` field), for a message loaded from saved
 * history — display-only. Unlike MessageImage/MessageFile it carries no
 * base64 `data` (the server never sends it back wholesale) and is never
 * read by lib/api.ts's toOutgoing(); `url` is fetched directly by an
 * `<img>`/download link instead.
 */
export interface StoredAttachment {
  url: string
  mediaType: string
  kind: 'image' | 'file'
  name?: string | null
}

/**
 * A text/spreadsheet attachment (separate from MessageImage — these are
 * parsed to plain text server-side, see server/attachments.py, never sent
 * to a provider's multimodal API).
 */
export interface MessageFile {
  kind: Exclude<AttachmentKind, 'image'>
  name: string
  mediaType: string
  data: string
}

/** One message in App.tsx's chat state — the UI's unit of truth for a turn. */
export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  text: string
  image?: MessageImage
  file?: MessageFile
  attachment?: StoredAttachment | null
  diagrams?: RenderDiagramInput[]
  charts?: RenderChartInput[]
  trace?: string[]
  isStreaming?: boolean
  error?: string
}

/**
 * One parsed SSE event from POST /api/chat (see lib/api.ts streamChat).
 * `conversation` and `title` come from server/main.py's persistence layer
 * rather than from a provider — the first says which saved conversation this
 * turn was filed under (sent only when the server had to create one), the
 * second delivers the LLM-generated title once there's enough conversation
 * to name.
 */
export type ServerEvent =
  | { type: 'text'; text: string }
  | { type: 'diagram'; payload: RenderDiagramInput }
  | { type: 'chart'; payload: RenderChartInput }
  | { type: 'trace'; label: string }
  | { type: 'conversation'; id: string; title: string }
  | { type: 'title'; title: string }
  | { type: 'error'; message: string }
  | { type: 'done' }

/** The signed-in account, from GET /api/auth/me. `guest` marks a throwaway account with no password. */
export interface User {
  id: string
  userId: string
  guest: boolean
}

/** One row in the history sidebar, from GET /api/conversations. */
export interface ConversationSummary {
  id: string
  title: string
  updatedAt: string
}

export type ModelProvider = 'claude' | 'openai'

/**
 * Draw-mode selector (Composer.tsx): restricts which tool the primary model
 * is offered server-side (server/tools.py's tools_for_mode()) — "auto" (default)
 * offers both render_diagram/render_chart, "diagram"/"chart" offer only one.
 */
export type DrawMode = 'auto' | 'diagram' | 'chart'

/** One entry from GET /api/models, as shown in the Composer's model picker. */
export interface ModelOption {
  id: string
  label: string
  shortLabel: string
  provider: ModelProvider
  description?: string
  available: boolean
}
