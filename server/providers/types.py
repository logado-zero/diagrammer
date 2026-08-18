"""
The event shape every agent provider (api.py, agent_sdk.py,
openai_provider.py) yields from its async generator, so the chat route can
relay events straight to the client as SSE without knowing which provider
ran. A plain dict (not a pydantic model) on purpose — these are produced ad
hoc by three different providers and only ever consumed by `json.dumps()` in
server/routes/chat.py, so runtime validation would add cost with no benefit.
AgentEvent below is a type-hint only, mirroring src/types.ts's ServerEvent
union for documentation.

  {"type": "text", "text": str}              a chunk of streamed assistant text
  {"type": "diagram", "payload": dict}        a completed render_diagram tool call
  {"type": "chart", "payload": dict}          a completed render_chart tool call
  {"type": "trace", "label": str}             a live agent-step update: agent.py around the
                                               subagent pass, and each provider around a web or
                                               memory search. Shown in the client's Process Trace
  {"type": "retrieval", ...}                  what one memory search returned — `label`, `query`,
                                               `status`, `source` and a `list_result` array (see
                                               memory/tool.py's retrieval_record()). `source` is
                                               "tool" when the model called search_memory and
                                               "auto" when the turn retrieved on its own before
                                               the provider ran (memory/context.py), which is the
                                               only place agent.py emits one of these itself.
                                               **The one event the chat route does not relay**: it is
                                               filed into the agent_logs document and never
                                               reaches the browser, because it is diagnostic data
                                               rather than a user-facing status
  {"type": "error", "message": str}           something went wrong; no more events follow
  {"type": "done"}                            the turn finished successfully

The chat route adds two more event types of its own on top of these, for the
saved-history feature:

  {"type": "conversation", "id": str, "title": str}
                                              a new conversation was created for this turn;
                                               the client stores the id and sends it back on
                                               the next one
  {"type": "title", "title": str}             the title subagent named the conversation

Both are persistence concerns — no provider and not even agent.py emits
them. See src/types.ts's ServerEvent for the full client-side union.
"""

from collections.abc import AsyncGenerator
from typing import Any

AgentEvent = dict[str, Any]
AgentEventStream = AsyncGenerator[AgentEvent, None]
