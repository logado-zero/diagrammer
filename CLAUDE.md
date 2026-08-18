# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**Diagrammer** — a chat app that draws flowcharts and charts from a description, an image, or a spreadsheet. A React + Vite + TypeScript frontend talks over SSE to a Python/FastAPI backend, which asks an LLM (Claude or OpenAI, picked per request in the UI) to answer and optionally call a `render_diagram` / `render_chart` tool; a second-pass subagent polishes that payload before the client renders it with Mermaid or ECharts. Signed-in users get saved conversations in MongoDB, and every finished turn is indexed into a retrieval layer (`server/memory/`) that feeds older context back into later turns.

It started life as a bare Vite scaffold — the directory is still named `ui-design` for that reason. There is no routing library and no component library; `src/App.tsx` owns all state and every component under `src/components/` is a props-in view.

Two docs go deeper, and this file defers to them rather than repeating them:

- **`HOWTHEYWORK.md`** — how a request actually flows, per-feature mechanics, and the full file map. Read this before changing anything non-trivial. It has a designed web twin whose source is `docs/howtheywork.artifact.html`; keep the two in sync.
- **`PROGRESS.md`** — the decisions and the constraints they lock in, rejected alternatives, gotchas, and the milestone timeline.

## Environment

Both Node.js and Python are managed via one conda environment (`ui-design`), not system Node/npm/`nvm` or a separate venv/pip install. The environment is defined in `environment.yml` and pinned to the `conda-forge` channel, plus a `pip:` block for packages not on conda-forge (`fastapi`, `uvicorn`, `anthropic`, `openai`, `claude-agent-sdk`, `python-dotenv`, `pydantic`).

Before running any `npm`/`node`/`python`/`uvicorn` command, activate the env:

```bash
conda activate ui-design
```

If the env doesn't exist yet (fresh checkout), create it from the lockfile spec:

```bash
conda env create -f environment.yml
```

## Commands

```bash
npm run dev       # start Vite dev server (:5173) + FastAPI server (:8787) together
npm run dev:client # Vite only
npm run dev:server # uvicorn only: conda run -n ui-design --no-capture-output uvicorn server.main:app --reload --reload-dir server --port 8787
npm run build     # production build to dist/
npm run preview   # preview the production build locally
npm run lint      # lint the frontend with oxlint (see .oxlintrc.json)
npm run typecheck # typecheck the frontend only — see "Backend" below for the server
```

There is no test runner configured in this project. What stands in for one:

```bash
python -m server.memory.demo   # self-check; its tenant-isolation assertion is the merge gate
python -c "import server.main" # no Python typechecker is configured
```

`demo.py` runs the other memory modules' self-checks too, so it is the one
command to remember rather than one per module.

## Architecture notes

- **Styling**: Tailwind CSS v4, wired in via the `@tailwindcss/vite` plugin in `vite.config.ts` — there is no `tailwind.config.js` or PostCSS config. `src/index.css` is the entire stylesheet and is two lines: `@import "tailwindcss";` and a `@custom-variant dark` line (see Theming below). Use Tailwind utility classes directly in JSX rather than adding new CSS files.
- **Theming**: `dark:` is **class-driven, not `prefers-color-scheme`** — `@custom-variant dark (&:where(.dark, .dark *))` in `src/index.css` binds every `dark:` utility to a `.dark` class on `<html>`. `src/lib/theme.ts` is the single source of truth for that class *and* for the `useIsDark()` boolean that ECharts and Mermaid read to pick a palette from `src/lib/palette.ts`; the picker in `Sidebar.tsx` cycles light → dark → system, persisted at `localStorage['diagrammer:theme']`. The inline `<script>` in `index.html` applies the same value before first paint and must stay a mirror of `theme.ts`'s `apply()`. Anything new that needs to know the theme should call `useIsDark()`, never `matchMedia` directly. Full rationale: `HOWTHEYWORK.md`'s Theming section.
- **Surfaces**: one warm gray ramp (`stone`) in both themes — never reach for `neutral`, `gray`, `zinc` or `slate`. The app is a **fixed-height card**: `App.tsx` is `h-svh overflow-hidden` and every screen inside is `h-full min-h-0` with its own scroll container, so a new screen using `min-h-svh` will overflow rather than scroll. Surfaces step ground → app card → sidebar rail → canvas → chart card; the chart card must stay pure white in light mode, since that's what an exported PNG bakes in. Table in `HOWTHEYWORK.md`'s "surface ladder".
- **Linting**: uses `oxlint` (Rust-based, not ESLint) for the frontend. Config is `.oxlintrc.json` with `react` and `oxc` plugins enabled. No linter is configured for the Python backend.
- **Entry point**: `src/main.tsx` mounts `<App />` from `src/App.tsx` into `#root` in `index.html`. `public/` holds static assets served as-is — `favicon.svg` is a static twin of `src/components/icons/DonutMark.tsx` (same geometry and gradients, on the mark's own `#232839` as a background so it survives a light tab bar); change one and change the other.

## Backend: Python (FastAPI), not Node

**The backend is `server/*.py`.** It was Node/Express/TypeScript once, so if
some older note points you at `server/src/index.ts`, `server/tsconfig.json`, or
npm deps like `express`/`@anthropic-ai/sdk`, none of those exist — the routes
and SSE event shapes survived the rewrite unchanged, only the language moved.

- `server/main.py` — the FastAPI app and the whole HTTP surface except auth:
  `/api/chat`, `/api/models`, `/api/health`, `/api/conversations` (list, get,
  delete, and `/{id}/logs`), `/api/attachments/{blobId}`. Plus CORS, a
  body-size guard, one app-wide `PyMongoError` handler, turn persistence,
  memory ingest scheduling, and the delete cascade. Loads `.env` via
  `python-dotenv` as the very first thing it does (before importing anything
  that reads an env var at import time).
- `server/auth.py` — the `/api/auth/*` router (`register`, `login`, `guest`,
  `logout`, `me`), scrypt hashing, and `current_user()`, the only place a
  session cookie becomes a user.
- `server/agent.py` — `run_agent(request_messages, model_id, mode, scope)`, the
  dispatcher and the one cross-provider choke point. Exactly one function other
  code calls; it never leaks which provider ran.
- `server/models.py` / `server/tools.py` / `server/types.py` — `types.py`'s
  pydantic models are the only ones actually validated at runtime (the
  `POST /api/chat` body); the diagram/chart shapes are `TypedDict`s for
  documentation only, since a tool call's `input` is forwarded to the client as
  an untyped dict and never parsed server-side.
- `server/memory/` — the retrieval layer (`memory_units` + `memory_edges`,
  GridFS attachment blobs, a local Harrier-270m ONNX encoder, and a hybrid
  `search()` fusing dense, lexical and entity-graph views). **It has two entry
  points on the chat path and one off it.** `main.py` schedules `index_turn()`
  as a background task once the SSE stream has closed (off the path) and
  cascades deletes; `agent.py` calls `select_context()` on every turn that
  clears a cheap gate (on the path, before any provider runs), and the
  `search_memory` tool lets the model pull more (also on the path). Read
  `server/memory/__init__.py` first — it names the five public functions and
  maps the other twelve files. Self-check: `python -m server.memory.demo`
  (includes a tenant-isolation assertion that is the merge gate). Mechanics:
  `HOWTHEYWORK.md`'s memory chapter. Roadmap and the reasoning behind the
  design: `DISCUSS_RAG.md`.
- `server/providers/` — three interchangeable backends, all
  `async def ... yield ...` generator functions producing the same `AgentEvent`
  dict shape (`{"type": "text"/"diagram"/"chart"/"trace"/"retrieval"/"error"/"done"`, ...}`),
  so `main.py` never needs to know which one ran. See "Backend provider
  switch" below.

**No separate Python typecheck step is configured** (no mypy/pyright in
this repo) — sanity-check the server with `python -c "import server.main"`
or just boot it via `npm run dev:server`.

**`dev:server` runs through `conda run -n ui-design`, not a bare
`uvicorn`, and that's required, not stylistic.** `npm`/`node` on this
machine come from `nvm`, which is on `PATH` in any shell — but Python only
has `uvicorn` (and the rest of this backend's deps) installed inside the
`ui-design` conda env, which is **not** active by default in a plain
shell (a fresh terminal, a new VS Code integrated terminal, etc. — `conda
activate` is opt-in per shell, not automatic here). In an unactivated
shell, `python`/`pip` resolve to conda's `base` env instead, which doesn't
have `uvicorn` at all. `conda run -n ui-design <cmd>` sidesteps this
entirely: it invokes `<cmd>` inside that env's `bin/` regardless of
whether the calling shell ever ran `conda activate`, as long as the
`conda` binary itself is on `PATH` (it is, via `condabin`, in every shell
on this machine). Without it, `dev:server` fails immediately with
"uvicorn: command not found" in any shell that hasn't manually activated
`ui-design` first, `concurrently` (no restart handling) kills the sibling
`dev:client` process, and the symptom is a permanent Vite proxy
`ECONNREFUSED` on every `/api/*` request. `--no-capture-output` on the
`conda run` call is also required — without it, uvicorn's stdout (all the
`INFO:` reload/request logs, and this file's startup prints) gets
buffered and swallowed instead of streaming live through `concurrently`.

**`--reload-dir server` is a second, independent fix and still required.**
Without it, uvicorn's file watcher defaults to watching the whole repo
root, including `node_modules` — which Vite continuously rewrites
(dependency pre-bundling cache under `node_modules/.vite/`) while its own
dev server runs. Every one of those writes looks like a source change, so
the backend keeps restarting out from under the running frontend, which
hits the same `concurrently`-kills-the-sibling-process failure mode
above, just from a different trigger. If `npm run dev` ever goes back to
crashing like that, check both of these flags haven't been dropped before
looking anywhere else — either one going missing reproduces the same
symptom (`ECONNREFUSED` + `dev:client` exiting) for a different reason.

### Concurrency: this backend serves multiple users at once

Every route in `main.py` is `async def`, so FastAPI/uvicorn services many
concurrent requests on a single event loop without blocking each other
while awaiting network I/O (Claude/OpenAI API calls) or subprocess I/O
(the agent-sdk provider's local `claude` CLI). The app is fully stateless
per request — no session, no in-memory store, no shared mutable state
except the lazily-constructed OpenAI client (guarded by an `asyncio.Lock`
so concurrent requests can't race to construct it twice) — so it also
scales across CPU cores with zero code changes by running more uvicorn
worker processes: `uvicorn server.main:app --workers 4 --port 8787`. The
one mode that doesn't benefit from more workers is `agent-sdk` (each
request spawns its own `claude` CLI subprocess regardless of worker
count, and that mode isn't meant for concurrent/shared use anyway — see
below).

## Model catalog and the UI model picker

`Composer.tsx`'s model badge (the "Opus 5 ▾"-style control next to the
attach button) is a real, working model picker, not the decorative stub it
started as — it's a native `<select>` layered invisibly over the styled
badge text (accessible, no popover component needed). It's populated from
`GET /api/models` and the choice is sent as `model` on every `POST
/api/chat` request (`ChatRequestBody.model` in `server/types.py`), and
persisted client-side in `localStorage` (`diagrammer:model`).

- `server/models.py` — the single source of truth for selectable
  models: `MODEL_CATALOG` (id, display label, provider, underlying model
  id), `DEFAULT_MODEL_ID`, `find_model_option()` (resolves an id to
  a catalog entry, falling back to the default for an unknown/missing id),
  and `is_model_available()` (OpenAI entries need `OPENAI_API_KEY`; Claude
  entries are always "available" — see below for why). Add a model by
  adding one entry here; nothing else needs to change.
- `GET /api/models` (`server/main.py`) returns the catalog annotated
  with `available` per entry, plus `defaultModelId`. The client
  (`src/lib/api.ts` → `fetchModels()`) fetches this once on load; it never
  hardcodes the model list itself.
- `server/agent.py` → `run_agent(..., model_id, ...)` resolves the
  catalog entry and dispatches: an `openai`-provider entry always goes to
  `run_openai_agent`; a `claude`-provider entry goes through
  `resolve_claude_transport()` (below) to pick `api` vs `agent-sdk`.

## Web search — declared per provider, never in `tools.py`

The model can answer questions that need live data (prices, rates, news,
anything about "today") by searching the web. This is a **native
provider-side capability, not a tool this repo implements** — there is no
search API key, no new dependency, and no fetch/scrape loop here.

- **`api` transport** — `SERVER_TOOLS` in `providers/api.py` declares
  Anthropic's `web_search_20260209` and `web_fetch_20260209` (both present
  in the installed `anthropic`; `_20260318` also exists and is a
  superset if a reason to move ever appears). The API runs them and returns
  the results inline, so there's no handler and no `tool_result` to send
  back. `max_uses: 5` each.
- **`agent-sdk` transport** — Claude Code's `WebSearch` / `WebFetch`
  built-ins, see the `BUILTIN_TOOLS` bullet below. **This is the transport
  that actually runs on this machine** (`.env` has an empty
  `ANTHROPIC_API_KEY`, so `resolve_claude_transport()` picks `agent-sdk`),
  and it is verified working end-to-end — a "gold price in Vietnam today"
  question comes back with real SJC buy/sell figures and source links.
- **`openai` transport** — OpenAI's built-in `web_search` tool
  (`SERVER_TOOLS` in `providers/openai_provider.py`), which is **why that
  provider is on the Responses API and not Chat Completions.** On Chat
  Completions `web_search_options` and function tools are mutually
  exclusive, measured against the live API: `gpt-4o` + `web_search_options`
  → 400 *"Web search options not supported with this model"*;
  `gpt-4o-search-preview` + `tools` → 404 *"tools is not supported in this
  model"*; `gpt-5.6-luna` → 400 *"Unknown parameter"*. On Responses they
  coexist — verified with this app's real `render_chart` schema on
  `gpt-5.6-luna`, `gpt-4o` and `gpt-4o-mini`, each of which searches and
  then calls `render_chart` in one turn. No `web_fetch` equivalent is
  declared; search alone covers the cases this app hits.

**Why the tool definitions are not in `tools.py`:** that file's `TOOLS` /
`tools_for_mode()` list is also what `agent_sdk.py` derives its
`mcp__diagrammer__<name>` MCP names from, so a server tool added there
would come out as a bogus `mcp__diagrammer__web_search`. Each transport
declares its own native search; `tools.py` stays the single source of truth
for *this app's* two tools only.

`tools.py` does own the prompt half: `SYSTEM_PROMPT` plus
`WEB_TOOLS_PROMPT`, appended by **all three** providers — search first,
never claim you have no live data, and (the part that fixes charting a
number you don't have) search for a chart's data rather than asking the
user for it or inventing it. An earlier `NO_WEB_TOOLS_PROMPT` existed for
the pre-Responses OpenAI path and is gone; don't reintroduce a per-provider
prompt split unless a provider genuinely loses the capability.

## Backend provider switch (Claude API key vs. Claude Agent SDK, or OpenAI)

Three interchangeable backend implementations exist, all `async def ...
yield ...` generator functions producing the same `AgentEvent` dict shape,
so `server/main.py` never needs to know which one ran:

- `server/providers/types.py` — the shared `AgentEvent` dict shape (a
  type-hint only, not a validated model — see "Backend: Python" above).
- `server/providers/api.py` (`run_api_agent(messages, model)`) — the
  original implementation: the `anthropic` package's `AsyncAnthropic`
  client (`client.messages.stream()`) with a hand-rolled tool-use loop,
  billed against `ANTHROPIC_API_KEY`. Supports image input. `model` comes
  from the selected catalog entry's `model` field (e.g. `claude-opus-5`,
  `claude-sonnet-5`, `claude-haiku-4-5`). The client is constructed once at
  import time — Anthropic's client, unlike OpenAI's, does not throw if no
  key is configured, it only fails on first request. Also declares
  Anthropic's `web_search_20260209` / `web_fetch_20260209` server tools
  (`SERVER_TOOLS`) — see "Web search" below.
- `server/providers/agent_sdk.py` (`run_agent_sdk_agent`) — the
  `claude-agent-sdk` Python package's `query()`, which shells out to a
  local `claude` CLI subprocess and authenticates as whatever account that
  CLI is logged into via `claude login` (including a Claude Pro/Max
  subscription), instead of a metered API key.
- `server/providers/openai_provider.py` (`run_openai_agent(messages,
  model, mode)`) — OpenAI's **Responses API** (`openai` package's
  `AsyncOpenAI`) via `client.responses.create(stream=True, store=False)`,
  using the same shape as `api.py`: stream the text deltas, then read tool
  calls off the completed response and loop. Billed against
  `OPENAI_API_KEY`. Supports image input (an `input_image` content part
  carrying a base64 data URL built from the same `ChatRequestMessage.image`
  field the other two providers consume) and live web search.
  **Responses, not Chat Completions, because of web search** — see the "Web
  search" section above for the measured 400s that rule out the Chat
  Completions path. Three shape differences to remember when editing it:
  a function tool's fields are **flat** (`{"type": "function", "name",
  "description", "parameters"}`), not nested under `"function"`; the system
  prompt goes in `instructions` rather than a message; and a tool result is
  a `{"type": "function_call_output", "call_id", "output"}` input item that
  must be preceded by the `function_call` item it answers, which is why the
  loop replays `response.output` before appending results. `reasoning`
  items are dropped from that replay — they'd need their encrypted content,
  which `store=False` doesn't return.
  **The `AsyncOpenAI` client is constructed lazily** (on first request, not
  at import time) because the OpenAI SDK — unlike Anthropic's — throws
  eagerly in its constructor when no API key is configured, and this
  module is imported unconditionally; a module-level `AsyncOpenAI()` would
  crash server startup whenever `OPENAI_API_KEY` is unset, even when only
  Claude models are ever selected. Guarded by an `asyncio.Lock` (double-
  checked) so two concurrent requests racing to construct it can't create
  it twice.
  **There is no longer a `reasoning_effort: 'none'` workaround**, and no
  `reasoning` flag on `ModelOption`. Both existed only because Chat
  Completions rejected function tools on reasoning models; Responses
  accepts them, so `gpt-5.6-luna` now reasons normally. `subagents.py`
  still calls Chat Completions directly for its forced-tool-call pass —
  that's fine, it never searches — and only borrows this module's
  `_get_client()`.

Claude's transport is **auto-detected, not configured**:
`resolve_claude_transport()` in `agent.py` returns `'api'` when
`ANTHROPIC_API_KEY` is set and `'agent-sdk'` otherwise — there is no env
var to force one or the other (same design as the original Node backend;
see `PROGRESS.md` for why `AGENT_PROVIDER`-style startup switches don't fit
a per-request model picker).

Whichever Claude transport is active applies to **every** Claude catalog
entry the user might pick (Opus/Sonnet/Haiku) — `agent-sdk` mode ignores
the specific sub-model selection and always runs `AGENT_SDK_MODEL`,
because (per the constraint below) API model ID strings aren't guaranteed
to work with the Agent SDK. `api` mode and `openai` mode are both ordinary
metered, API-key-billed paths (no special caveats like `agent-sdk` below),
so either is fine to deploy or share; `agent-sdk` mode is not.

### Why this exists, and its real limits

This was added specifically so the app *could* run against a personal
Claude Pro/Max login instead of API billing — but **Anthropic's own Agent
SDK docs state that third-party products may not offer claude.ai
login/rate limits to other users without prior approval.** There is no
supported "log in with your subscription" flow for a backend serving
other people; the subscription-login pickup only works because `query()`
shells out to the *same machine's* local `claude` CLI session. Practical
consequence: **`agent-sdk` mode is for you running this app locally under
your own `claude login`, full stop — never something to deploy, host for
others, or point multiple users at.** If you want to share this app with
anyone else, that has to run in `api` mode with your own
`ANTHROPIC_API_KEY`.

Other real constraints, not just policy:

- **No image input in `agent-sdk` mode.** `query()`'s `prompt` is built
  here as a plain flattened text transcript (`server/providers/agent_sdk.py`
  → `_build_prompt()`) — if the last message carries an image, the
  provider yields an `error` event telling the user to switch to `api`
  mode rather than silently dropping the image.
- **No true multi-turn session state.** Each HTTP request replays the
  whole conversation as one prompt string rather than using the SDK's
  `resume=` option, since the server is stateless per request and adding
  session persistence was out of scope for what's meant to be a
  dev-convenience path. Fine for short exchanges; don't expect it to
  behave identically to a real multi-turn Claude Code session.
- **Per-request subprocess cost.** `query()` spawns a Claude Code
  subprocess per call — noticeably higher latency than the direct API path,
  and not something to put under real concurrent load (see "Concurrency"
  above).
- **`AGENT_SDK_MODEL`** (`.env`, default `claude-opus-4-8`) is separate from
  the `api` provider's hardcoded `claude-opus-5` — the two providers aren't
  guaranteed to accept the same model ID strings, so don't assume swapping
  the env var to `claude-opus-5` works without checking the installed
  `claude-agent-sdk` package's `types.py` first (e.g. via `python -c
  "import claude_agent_sdk, inspect; print(inspect.getsourcefile(claude_agent_sdk))"`).
- Custom tools (`render_diagram`/`render_chart`) are registered via
  `create_sdk_mcp_server` + `tool()`, reusing `server/tools.py`'s JSON
  Schema dicts **directly** — no second, Zod-style definition needed like
  the original Node build had. This was verified by downloading the
  actually-installed `claude-agent-sdk` wheel and reading
  `claude_agent_sdk/__init__.py`: `tool()`'s `input_schema` passes a dict
  straight through unchanged whenever it already looks like JSON Schema
  (has both `"type"` and `"properties"` keys), only falling back to
  treating it as a flat `{field_name: python_type}` map otherwise. If you
  change one tool's shape, `tools.py` is now the only place to change it.
- Built-in Claude Code tools are restricted to `BUILTIN_TOOLS =
  ["WebSearch", "WebFetch"]` (`tools=`, plus the same names appended to
  `allowed_tools` so they run without a permission prompt). Everything that
  touches the local filesystem or shell — Bash, Read, Write, Edit, Glob,
  Grep — stays off; this app never wants the agent near either. A
  `ToolUseBlock` named `WebSearch`/`WebFetch` is turned into a `trace`
  event so the UI shows "Searching the web…" during the long silent
  stretch while the subprocess searches.

Verified against the actually-installed `claude-agent-sdk` during the Python
migration — and `conda env update` re-resolves the whole `pip:` block, so the
installed version moves without anyone asking; **re-check the package's own
source if anything below stops matching** rather than trusting a version number
written here. `ClaudeAgentOptions` fields
(`model`, `system_prompt`, `tools`, `mcp_servers`, `allowed_tools`,
`include_partial_messages`), the `StreamEvent`/`AssistantMessage`/
`ResultMessage`/`ToolUseBlock` dataclasses, and the `tool()`/
`create_sdk_mcp_server()` signatures were all read from the installed
package's source rather than guessed — see `PROGRESS.md` for the
verification notes. Not yet re-verified with a live `claude` CLI session
the way the original Node `agent-sdk` provider was (see `PROGRESS.md`'s
"Verified working end-to-end" note on that implementation) — do that
before relying on this mode for anything beyond a quick smoke test.
