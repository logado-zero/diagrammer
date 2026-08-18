# Diagrammer — Progress Log

Decisions, constraints, and hard-won gotchas worth knowing before changing
this project. Not a to-do list. **Current as of milestone 30 (2026-08-17)** —
if the code disagrees with a line here, the code is right and this file is
stale.

This file holds *the decision and the constraint it locks in*. How any of it
actually works lives elsewhere:

- Runtime architecture, per-feature mechanics and the file map →
  `HOWTHEYWORK.md` (and its designed web version, source in
  `docs/howtheywork.artifact.html` — see that file's closing section before
  republishing)
- Provider-switch rationale and dev-environment specifics → `CLAUDE.md`
- Retrieval/memory roadmap beyond what's built → `DISCUSS_RAG.md`

## Current state

- **Frontend**: React + Vite + TypeScript + Tailwind v4; Mermaid for
  flowcharts, ECharts for charts, both driven by tool-call JSON and polished by
  a second-pass subagent. Light/dark/system theme picker in the sidebar.
- **Backend**: Python + FastAPI. Three interchangeable providers — Claude
  direct API, Claude via a local `claude` CLI subprocess (personal-use only),
  OpenAI — chosen per request by the UI model picker. Every route is
  `async def` and nothing is shared and mutable, so `uvicorn --workers N` needs
  no code changes. Every model can search the web, provider-natively.
- **Accounts + saved history**: user ID / password or guest sign-in;
  conversations in MongoDB (`chart-chatbot`), listed in a sidebar behind a
  confirm dialog for delete. Reopening one redraws its charts with no LLM call.
- **Memory layer** (`server/memory/`): every finished turn is indexed into
  `memory_units`; `search()` fuses dense + lexical + entity-graph views. Two
  entry points, one core: **push** (`select_context()`, on every gated turn)
  and **pull** (the `search_memory` tool). Phases 1–3 of `DISCUSS_RAG.md`;
  `Route(q)` is unbuilt, so the fusion weights are a fixed constant.
- **Short history + recall, not long history**: only the last `HISTORY_LEN = 3`
  messages are sent; older context returns through retrieval.
- **Untested path**: no `ANTHROPIC_API_KEY` on this machine, so
  `providers/api.py` has never run live here — including `SERVER_TOOLS` web
  search and the `pause_turn` branch. `agent-sdk` is what actually runs.
- **UI-only work needs no backend**: `?demo=1` renders a pre-seeded chat from
  `src/lib/demoData.ts` and skips sign-in.

## Locked-in decisions

- **One choke point for cross-provider behavior.** `agent.py`'s `run_agent()`
  is the single place every provider's output passes through: attachment
  inlining, the subagent pass, `trace` events. Same for inputs via
  `tools_for_mode()`. A feature needing different handling per provider is the
  signal it doesn't belong here.
- **Web search is declared per provider, not in `tools.py`** — the one
  deliberate exception, because the *tool definition* isn't shared even though
  the capability is. `tools.py` is also where `agent_sdk.py` derives its MCP
  names, so a server tool there would emit a bogus
  `mcp__diagrammer__web_search`. The *prompt* half is shared normally.
- **Subagents own structure, the client owns color.** Subagents pick node
  kinds/labels/edges/direction and ECharts series/axis shape, and are told never
  to emit color; color comes from `src/lib/palette.ts` client-side so theme
  switching stays instant. Layout is Mermaid's problem; neither side computes it.
- **The theme is a class on `<html>`, not a media query.** `src/lib/theme.ts` is
  the single source of truth: it owns the `.dark` class that `index.css`'s
  `@custom-variant` binds every `dark:` utility to, *and* the `useIsDark()`
  boolean ECharts and Mermaid read for their palettes. One value, two consumers,
  so Tailwind and the canvas libraries can't disagree. It lives outside React
  because `ChartCard`/`DiagramCard` are memo'd leaves — prop-drilling the theme
  would re-render them on every parent render.
- **One gray ramp (`stone`) and one surface ladder, in both themes.** Light mode
  ran on the cool `neutral` ramp until the light redesign and read as washed out
  next to the warm dark theme; it's warm `stone` now too. The surfaces step
  ground → app card → rail → canvas → chart card (table in `HOWTHEYWORK.md`) and
  only work as a set. Two rungs are load-bearing: **the chart card stays pure
  white**, because a chart's own palette is the most saturated thing on screen
  and `CARD_SURFACE.light` is what gets baked into an exported PNG — which is
  also why the ground is a low-chroma gray, not a colored wash; and **the
  sidebar rail is dark in both themes**, because in light mode it's the only
  thing giving the white content area an edge to sit against.
- **The app is a fixed-height card, not a scrolling page.** `App.tsx` is
  `h-svh overflow-hidden` and every screen inside is `h-full min-h-0` with its
  own scroll container, so the rail, header and composer stay put. A new screen
  reaching for `min-h-svh` will overflow the card instead of scrolling in it.
- **A user-requested node color is *data*, not syntax.** The primary model may
  set a node's `color`; `agent.py`'s `_node_colors()` lifts those into a
  `colors` map and `DiagramCard.tsx` compiles them to classDefs. Not sent as
  Mermaid `style` lines, because the same color needs a different declaration
  per look and the hand-drawn toggle is client state the server can't see.
- **Chart ramps come from the `dataviz` skill**, verbatim: categorical (8 hues,
  never cycled — extras fold into "Other"), sequential, status pair. Which ramp
  applies is chosen in `ChartCard.tsx` from the option's own *structure*, not a
  field the model self-reports.
- **Draw-mode restricts tool availability, never forces `tool_choice`.** A hard
  force makes the model call a tool on every message, so a plain "hi" in
  Flowchart mode would draw an empty diagram.
- **Canvas shows only the latest visual**, replaced in place. Cross-turn edits
  work by replaying the prior payload as history (`lib/api.ts`'s
  `withVisualContext`), not a special "edit" tool.
- **Process Trace steps must stay real** — actual phases already happening.
  Never add fabricated progress theater.
- **Destructive actions confirm in-app, not via `window.confirm`.** Deleting a
  conversation cascades server-side across messages, GridFS blobs and memory
  units, and the trash button sits 8px from the row that *opens* the chat. The
  confirm is a native `<dialog>` (backdrop, Escape and focus trapping come free)
  styled to match; the browser dialog would ignore the theme entirely.
- **Passwords use stdlib `hashlib.scrypt`**, and sessions are Mongo rows keyed
  by a random cookie token, not JWTs — so signing out actually revokes access.
  `current_user()` in `auth.py` is the only place a cookie becomes a user.
- **Guest mode is a real account, not a second code path** — an ordinary user
  row with a generated `guest-xxxxxxxx` id and an empty `passwordHash` that
  `verify_password()` can never match.
- **Messages are embedded in the conversation document**, not a second
  collection. One read reopens a chat.
- **One app-wide `PyMongoError` handler, not per-route try/except.** Paired
  with `serverSelectionTimeoutMS=3000` in `db.py`, because pymongo's 30s default
  made the login form hang for half a minute before a bare 500.
- **Persistence lives in `main.py`, never in `agent.py`.** `run_agent()` is the
  choke point for *generating* an answer; saving it is a different concern. Same
  reasoning puts memory ingest in `main.py`.
- **The model catalog is one file** — `server/models.py`.
- One conda env (`ui-design`) hosts both Node and Python.

### Memory-layer decisions

- **`Scope` is the only way to filter a memory query.** No function in
  `server/memory/` accepts a raw Mongo filter, and `Scope` can't be built
  without an owner id. A forgotten tenant filter raises nothing, logs nothing,
  passes every single-user test, and quietly makes one account's private
  conversation retrievable by another — so it's prevented in the type system
  rather than by remembering. `python -m server.memory.demo` asserts it from
  both directions; that assertion is the merge gate.
- **Store the original, never a summary.** A chart's payload holds its exact
  numbers; a paraphrase throws away what a follow-up needs. The embedded text is
  a *retrieval handle*, the payload is what gets used.
- **Indexing is fire-and-forget, and failure is non-fatal.** It runs after the
  SSE stream closes. If the encoder is down the unit is stored with `vec: null`
  and a backfill can re-encode; refusing to store would lose data permanently.
- **A local encoder, not an embedding API.** Harrier-270m on CPU via
  onnxruntime, no torch. Verified multilingual: an English query retrieves a
  Vietnamese document with the lexical view contributing literally zero.
- **`MEMORY_ENABLED=0` is a real kill switch**, shipped with the feature. With
  it off, `main.py` passes `scope=None`, and the tool list and system prompt are
  byte-identical to pre-memory — asserted in `server/tools.py`'s self-check.
- **The scope is the tool's entire security boundary.** `search_memory`'s schema
  has exactly one field, `query`. No owner or conversation parameter, so the
  model cannot widen what it sees and a prompt injection inside a retrieved
  document has nothing to aim at.
- **The tool result is text, not a structure** — the only shape all three
  transports agree on. Formatting lives in `memory/tool.py`; the schema and
  prompt stay in `tools.py`, which is import-free so `agent_sdk.py` can derive
  MCP names without dragging numpy and MongoDB into every provider.
- **What a search returned is logged, not displayed.** Each call appends one
  `retrieval` record to `agent_logs.steps`; `main.py` files it and `continue`s
  before the SSE yield, so it's the one provider event the browser never sees.
  The chat UI gets a status line and nothing more.
- **Push and pull are two entry points, never two implementations.** Both call
  the same `search()`, format with the same `format_hits()` and log the same
  `retrieval_record()`; only `source` differs. Push exists because pull can't be
  sufficient alone — to call the tool the model must first notice something is
  missing, and a truncated history leaves no trace of what was truncated.
  Anything that changes ranking, formatting or logging must land in the shared
  core, or the two paths drift and only one gets fixed.
- **Retrieval is gated, not unconditional.** `profile()` skips greetings and
  inputs too short to carry a subject, before any encoding happens. The gate
  reads **only the current user message** while the query is built from the
  **last two** — profiling the pair would make "hi" after a long reply look
  substantial, and querying on one would lose the subject of every bare
  follow-up.
- **The graph view ships on, against `DISCUSS_RAG.md` §9's "flag, default
  off".** That plan assumed an evaluation set would exist by the time the view
  did, and it doesn't — shipping it dark would have meant nothing ever measured
  it. `MEMORY_GRAPH=0` is the way out, and it removes only the view: entities
  and edges keep being written, so re-enabling never needs another backfill.
- **Entity extraction is regex + terms, not an NER model.** `en_core_web_sm` is
  English-only and fails the Vietnamese this app is used in. Precision matters
  less than it looks: the graph records *observed co-occurrence*, not inferred
  semantics, and what these units contain — tickers, amounts, dates, quoted
  titles — is what regex is best at and NER is worst at.
- **Retrieval failure degrades, never fails the turn.** `run_search_memory()`
  catches everything and returns a sentence saying memory is unavailable; a
  missing encoder already drops `search()` to lexical-only.

## Environment gotchas (read before debugging "why won't it start")

Setup recipe → `README.md`. Why `dev:server` needs `conda run
--no-capture-output` and `--reload-dir server`, and the WSL mirrored-networking
config for the Windows MongoDB → `CLAUDE.md` and `README.md`. What's only here:

- **`conda env update -n ui-design -f environment.yml`**, not `create`, when
  `environment.yml` gains deps — and note it re-resolves the whole `pip:` block.
  The memory-layer install bumped `anthropic` 0.120.2→0.121.0,
  `claude-agent-sdk` 0.2.128→0.2.134, `openai`→2.53.0, `uvicorn`→0.52.1. If a
  provider starts behaving oddly, check whether its SDK moved under you.
- **MongoDB has to be running**, or login and history fail with the server
  otherwise looking healthy — startup prints a warning instead of crashing.
  `GET /api/health` stays green either way (it touches no collection); the
  honest check is any auth route, which answers `503` in ~3s.
- **`ECONNREFUSED 127.0.0.1:8787` in the first seconds of `npm run dev` is a
  startup race, not a failure** — Vite serves immediately while `conda run` +
  uvicorn take a few seconds. The real failure is the *permanent* one where
  `dev:client` also dies.
- **Adding a frontend dep requires a full Vite restart**, not just deleting
  `node_modules/.vite`. Symptom: 504 "Outdated Optimize Dep".
- **Client timeouts**: `AsyncOpenAI`/`AsyncAnthropic` use `timeout=60.0`; this
  sandbox has ~12s latency to external APIs, past the SDKs' 5s default.
- **Playwright/Chromium**: `sudo env "PATH=$PATH" npx playwright install
  --with-deps chromium` (plain `sudo npx` loses the conda env's `npx`).
- **First memory encode downloads ~348MB** into `MEMORY_MODEL_DIR`
  (`.cache/harrier`, gitignored). A fresh checkout's first indexed turn is slow;
  every one after is not.
- **`$text` queries need the text index**, which only `ensure_indexes()`
  creates. Anything touching `memory_units` outside the running server has to
  call it first, or Mongo answers `IndexNotFound`.

Mermaid/rough.js rendering gotchas — the semicolon, the reserved `end`, quoted
ids, `htmlLabels: false`, classDef source order and the `SKETCH_STROKE_WIDTH`
floor, every one found by breaking it live — now live in `HOWTHEYWORK.md`'s
"Flowchart rendering: Mermaid" section, next to the code they constrain.

## Known limitations (accepted ceilings, not oversights)

Memory-layer cost ceilings, edge caps, the missing relevance floor and the fixed
fusion weights are stated with their numbers in `HOWTHEYWORK.md`'s memory
chapter; they're ceilings that live next to the mechanism. What's listed here is
everything else.

- **Wide `flowchart LR` diagrams shrink and leave vertical dead space.**
  Mermaid's `useMaxWidth` caps the SVG at the panel width. The fix — natural
  size plus a scrolling panel — is a real trade-off (legible text vs. horizontal
  scrolling), deliberately not taken.
- **The no-LLM-client fallbacks are a safety net, not the experience.**
  `_fallback_chart_option` handles bar/line/pie only; `_fallback_mermaid_flowchart`
  produces basic syntax. Both exist so the app works end-to-end with zero setup.
- **`agent-sdk` mode**: no image input, no true multi-turn session state, a
  subprocess per request. Details in `CLAUDE.md`.
- **Hand-drawn font** falls back to a serif on a bare Linux box. Fix would be
  bundling an Excalifont/Virgil woff2 in `public/`.
- **The session cookie is `secure=False`** unless `COOKIE_SECURE=1`. Flip it
  before deploying anywhere real.
- **No password reset, no login rate limiting, no guest reaping.**
- **An uploaded image only reaches the model for as long as the tab lives.**
  Every user message carrying one now sends it (within `HISTORY_LEN`), so
  follow-ups about a picture work — but a reloaded conversation has only the
  display-only `attachment` reference and `toOutgoing()` sends `image: null`.
  Same root cause as the retry gap below; both are fixed by re-inlining the
  GridFS blob server-side.
- **Retrying a turn whose attachment came from reloaded history 422s** — there's
  no base64 left to resend. Not on the common path.
- **Attachments are persisted in GridFS but only read back for display.** A
  reopened conversation shows what was attached; the UI doesn't re-render the
  original image or sheet into the canvas.
- **Node colors are per-node fills only.** No edge colors, no border-distinct-
  from-fill (hand-drawn can't have one), and nothing validates the model's
  colors against the image it claims to be matching.
- **Memory: dense scoring is brute-force in-process**, roughly fine to ~10k
  units per user (~25MB of float32 at 640 dims). MongoDB Community has no
  `$vectorSearch` (Atlas-only). Every query is owner-scoped, so cost tracks one
  user's history depth rather than the user count. Past the ceiling, a real
  vector index goes behind the same `store.dense()` signature.
- **Memory: the image description is the one generative call in the memory
  path.** It breaks Zero-Mem's zero-token property by design — at ingest, off
  the response path, and there's no text in an image to store otherwise.
- **Memory: `embedModel`/`embedDim`/`embedSpace` are recorded but no backfill
  job exists**, so swapping encoders is a manual migration today.
- **Memory: push/pull overlap is handled in the prompt, not by id-level dedup.**
  Threading excluded unit ids through three providers would be the alternative;
  the `source` field makes the overlap measurable first.
- **Memory: the backfill is manual.** `python -m server.memory.backfill` must be
  run once per deployment, or units written before phase 3 stay invisible to the
  graph view. It is idempotent and safe to re-run.
- **Memory: the model decides when to recall, and nothing double-checks it.**
  No router forcing a search, no fallback if it doesn't call the tool.
- **The theme picker is a client-only preference.** It lives in `localStorage`,
  not on the user row, so it doesn't follow an account across browsers.

## Rejected alternatives — don't re-propose without new information

- **Excalidraw as a rendering library** — Mermaid chosen, and the Excalidraw
  *look* emulated via `look: handDrawn`.
- **React Flow + dagre** (`src/lib/layout.ts`, `FlowNode.tsx`) — deleted, fully
  replaced by Mermaid's own layout engine.
- **Recharts** — replaced by Apache ECharts.
- **`AGENT_PROVIDER`-style startup env switch** — doesn't fit a per-request
  model picker; Claude transport is auto-detected instead.
- **Forced `tool_choice` for draw modes** — see Locked-in decisions.
- **A search API key / custom `web_search` tool** (Brave, Tavily, Serper) — all
  three providers have native search needing no key and no dependency.
- **`web_search_options` on the OpenAI provider** — measured against the live
  API, not assumed; the three failing combinations are recorded in
  `CLAUDE.md` and `HOWTHEYWORK.md`. Search and function calling are mutually
  exclusive **on Chat Completions**, and this app can't give up
  `render_diagram`/`render_chart`. That forced the Responses migration.
- **A `search: bool` flag on `ModelOption`**, and **a "Search" toggle in the
  composer** — every model can search, and a default-off switch reproduces the
  exact bug it was meant to fix.
- **Rendering web-search citations as a sources list** — the prompt asks the
  model to name sources inline instead, which it does, with links.
- **Composite hue+shade encoding for >8 categories** — the `dataviz` skill's
  documented fallback (fold to "Other") is what's prompted.
- **Artifact history/switcher on the canvas** — latest-only is the decision.
- **OS-only dark mode (`prefers-color-scheme` and nothing else)** — was the
  entire implementation until milestone 30, and survives as the `system` option,
  which is still the default. A user who never touches the picker sees exactly
  the old behavior.
- **A theme context provider / theme in `App.tsx` state** — the two consumers
  that matter are memo'd leaves; a module store keeps their memoization.
- **A tinted or gradient app background** (the reference design's warm mauve
  wash) — the canvas is a chart, and anything with chroma in it competes with
  `CATEGORICAL_LIGHT`. The layering idea was worth taking; the color wasn't.
- **The cool `neutral` gray ramp in light mode** — replaced wholesale by `stone`.
  Two ramps meant light and dark were different-temperature designs sharing a
  DOM.
- **`window.confirm()` for delete** — one line, but OS chrome that ignores the
  app's theme. A native `<dialog>` costs ~30 lines and styles like the app.
- **JWTs for sessions** — server-side session rows, so logout revokes.
- **motor** — deprecated; `pymongo` 4.9+ ships `AsyncMongoClient`.
- **pydantic's `EmailStr`** — would pull in `email-validator` to RFC-validate an
  address nothing here ever mails.
- **A separate `messages` collection** — embedded in the conversation document.
- **OpenAI embeddings for the memory layer** — a local model instead: otherwise
  every user's indexing path carries a third-party dependency and a per-request
  cost.
- **BGE-M3 as the encoder** — the original pick, replaced by Harrier-270m before
  any code was written: half the parameters, 640 dims instead of 1024, 32k
  context instead of 8k, a better multilingual benchmark, and a published ONNX
  build. The one loss is BGE-M3's sparse output, floated as a free multilingual
  entity signal.
- **A separate encoder sidecar service** — deferred. `asyncio.to_thread` keeps
  the event loop free because onnxruntime releases the GIL, so a second process
  buys independent scaling and a GPU path at the cost of another thing to run.
  Revisit when encode latency shows up in traces.
- **MinIO/S3 for attachment blobs** — GridFS instead, since MongoDB is already
  running and this repo had already named GridFS as the intended fix.
- **`networkx` for the graph view** — Personalized PageRank is ~15 lines of
  power iteration over a dict-of-dicts at this graph size.
- **spaCy (or any NER model) for entity extraction** — `en_core_web_sm` can't do
  Vietnamese, and a co-occurrence graph doesn't need the precision an NER model
  is bought for.
- **Querying the edge collection during the PPR walk** — the subgraph is loaded
  up front instead. That is a tenant-isolation decision more than a performance
  one: every edge then arrives through one owner-filtered function, so a walk
  cannot reach another account however many hops it takes.

## Debugging playbook — techniques that worked here

- **Read the actually-installed package**, don't guess its API. Verifying
  `claude-agent-sdk` this way found that `tool()` passes a JSON Schema dict
  straight through, letting `agent_sdk.py` reuse `tools.py`'s schemas. Same
  method confirmed Mermaid's `look`/`handDrawnSeed` config and its hardcoded
  `fillStyle`.
- **Read the model's own config, don't invent its contract.** Harrier's query
  instruction prefix was copied verbatim from
  `config_sentence_transformers.json`; a reworded instruction would have
  degraded retrieval with nothing failing.
- **Parse Mermaid source through real `mermaid.js`** in a throwaway Playwright
  page (`mermaid.parse(src)`) for the exact line/token error. `mermaid-cli`'s
  bundled puppeteer hangs in this sandbox; load
  `node_modules/mermaid/dist/mermaid.min.js` (UMD), since the ESM build's
  chunked imports can't resolve from a Blob URL.
- **Dump rendered DOM *attributes*, not computed styles**, when a renderer's
  output is confusing. (Rendered nodes are `g.node` in classic and
  `g.rough-node` in hand-drawn, with ids like `<renderId>-flowchart-n1-0`.)
- **Assert geometry in Playwright instead of eyeballing screenshots** for
  layout bugs. Same for color: read `getComputedStyle(node).fill` off the live
  SVG rather than judging a screenshot — that is how the theme switch was
  verified to reach Mermaid's nodes.
- **Intercept a dev-server module instead of editing a source file** when a
  UI-only check needs different fixtures. Vite serves `src/lib/demoData.ts` as
  its own module, so `page.route()` can rewrite it in flight — that's how the
  canvas was forced to show the diagram instead of the chart, with nothing to
  revert afterwards. Same trick stubs `/api/*` so the sidebar has rows without
  writing to the real database.
- **The bar for "done" is a live run**, not reasoning: `npm run typecheck` +
  `npm run lint` + `python -c "import server.main"`, plus curl-level or browser
  round trips for anything behavioral. For memory, also
  `python -m server.memory.demo`.

## Lessons worth carrying to the next feature

Numbered tags point at the milestone that produced each one.

**Diagnosing**

- **Trace the whole path before blaming the end of it.** "The image is gone"
  looked like history truncation and was a one-line `i == last_index` condition
  three layers down. (27)
- **Check which provider is actually live before assuming the default one is.**
  An *empty* `ANTHROPIC_API_KEY` is falsy, so the running transport was
  `agent_sdk.py`, whose `tools=[]` had every built-in off. (21)
- **"The provider can't do X" deserves the same live probe as "it can."**
  Milestone 21 tested one API surface and generalized to the whole vendor; the
  docs then said OpenAI couldn't search, and the user pushed back with a link. (22)
- **Probe the rendering library before designing around it.** One throwaway
  Playwright render produced both Mermaid class facts, and the second would
  otherwise have shipped as an intermittent "the diagram won't render". (27)
- **One box per accented character means the text was destroyed upstream** — it
  is never a missing font, since `à`/`á`/`ã` exist in every font ever shipped. (19)
- **A test that fails because reality is stranger than the assertion is the test
  earning its keep.** Expecting "no matches" for a nonsense query is how dense
  retrieval's missing relevance floor got found. (26)
- **A feature whose only user is a future debugging session rots silently** —
  `?demo=1` had been dead for five milestones. (22)
- **A design doc's "nothing to backfill" is a claim about code, not a fact about
  it.** §9 said the graph would be populated from day one. The *field* and its
  index shipped; the writer never did, so `entities` was `[]` on every unit ever
  stored and phase 3 opened with exactly the migration §9 existed to avoid. A
  promise that something is being written needs an assertion that it is. (29)
- **Lazy loading is fine until something moves onto the request path.** The
  encoder's 4s cold load was invisible while only background indexing used it;
  auto-retrieval put it in front of the first user of every process, where it
  ate a 5s timeout whole. Found by measuring, not by reasoning. (29)
- **An unplanned failure proved the degradation ladder.** A transient Hugging
  Face fetch error took the encoder out mid-run; the search fell through to
  lexical + graph and still returned ranked hits. That path had been designed
  and asserted but never exercised against real data. (29)

**Working with models**

- **When a model claims it did something and nothing changed, check whether the
  request was even representable** before touching the prompt. Three layers,
  each individually a good decision, made "recolor these blocks" unanswerable;
  the tool call genuinely succeeded. (27)
- **A capability the model can't express is one it will fake.** The schema gap
  produced a confident false claim, not a refusal. The schema is the fix; the
  prompt line telling it never to claim a recolor is only the backstop. (27)
- **A prompt instruction that has already failed once needs a code backstop, not
  stronger wording.** (15)
- **The primary model's tool description has to advertise what the subagent can
  actually do** — `render_chart` still said "bar, line, or pie", so the model
  told users a gauge "isn't available". (9)

**Code traps**

- **Match the event shape to the thing, not to the transport that's already
  there.** Reusing `trace` for retrieval hits was the smaller diff and cost the
  structure: a search *is* one event containing a list. (28)
- **"Show the user what happened" and "record what happened" are different
  requirements** that happened to share one pipe. Splitting them let the record
  get richer and the UI get quieter at the same time. (28)
- **Two strings, not one.** The retrieval gate and the retrieval query read
  different text, and swapping them breaks the feature in opposite directions.
  Obvious once written down, invisible in code that just says
  `messages[-1].text`. (29)
- **A graph traversal is a new way to leave a tenant, and the quietest one yet.**
  Every previous query filtered `ownerId` once, at the leaf; a walk hops unit →
  entity → unit, and entity strings are inherently global — "warehouse" is a real
  node in every account's graph. The structural answer was to load the subgraph
  through one owner-filtered function up front rather than query mid-walk. (29)
- **Stringify ObjectIds at the boundary of anything a route returns.** FastAPI's
  encoder takes `datetime` and raises on `ObjectId`; a raw id breaks only the one
  route that reads the record, and only when someone finally reads one. (28)
- **A serializer that enumerates the cases it knows about is a bug waiting for
  the next case.** `_serialize_assistant_content` hand-built `text`/`tool_use`
  and silently dropped search blocks; the fix deleted more than it added. (21)
- **A truncation limit belongs to the consumer, not the file.** `MAX_ROWS = 300`
  is a *prompt* budget; memory inheriting it would make row 900 permanently
  unanswerable — observed live, the assistant said "300 regions" while memory
  held all 420. (23)
- **A package that exports a function must not have a submodule of the same
  name** — `from .search import search` rebound the package attribute. Renamed
  to `retrieve.py`; the self-check caught it on its first run. (23)
- **`asyncio.create_task` needs a strong reference** or the GC can cancel it
  mid-flight. Hence the module-level `_ingest_tasks` set. (23)
- **An MCP handler has no request context** — module-level functions receiving
  only tool arguments, so the MCP server is built per request with the scope
  closed over. A `ContextVar` would have worked until a handler ran on a
  different task, then failed by searching the wrong person's memory. (26)
- **Every `function_call` needs a `function_call_output`, malformed ones
  included** — the Responses API rejects an unanswered call, so a `continue` on
  `JSONDecodeError` would have failed the *next* request rather than itself. (26)
- **Returning a module-level list lets a caller mutate it for the whole
  process** — `tools_for_mode()` did, and any provider appending server tools
  would have corrupted every later request. (26)
- **`??` and `||` are not interchangeable against a server that normalizes
  absent strings to `""`.** The chat header rendered blank where the sidebar,
  guarding the same data with `||`, was fine. (24)
- **A blind `$push` per turn silently duplicates history on retry** — fixed by
  truncate-to-the-client's-length-then-append in `_begin_turn`. (20)
- **`allow_credentials=True` is incompatible with `allow_origins=["*"]`** per
  the CORS spec. (20)
- **Min-max fusion puts each view's worst candidate at exactly 0** — standard
  for Zero-Mem Eq. 12, but it surprises anyone reading a ranking, so it's pinned
  in the self-check. (23)
- **An `absolute` child with no `left` falls back to its static position**,
  which inside a `<button>` is centered — that's how the toggle thumb escaped
  its track. (17)
- **A second GridFS copy beat threading a blob id two layers into a subsystem**
  that had no other reason to change. (25)
- **Tailwind's preflight zeroes the margin that centers a `<dialog>`.** The UA
  stylesheet centers a modal dialog with `margin: auto`; preflight's blanket
  `margin: 0` wins, and the dialog silently pins to the top-left corner. `m-auto`
  puts it back. The same class of trap applies to any element whose default
  layout comes from the UA stylesheet. (30)
- **A pre-paint theme has to be set outside React.** React mounts after first
  paint, so a theme applied in an effect — or even a `useState` initializer —
  flashes the wrong one on every reload. Six lines of inline `<script>` in
  `index.html` is the whole fix, and it must stay a mirror of the module that
  owns the class afterwards. (30)

## Milestone timeline

One line each; the durable content is in the sections above and the narration
is in git history.

1. **v1** — Node/Express/TS backend + React frontend, Claude-only.
2. **Claude Agent SDK transport** — second Claude path via the local `claude`
   CLI. Personal-use only per Anthropic's policy: never deploy this mode.
3. **Multi-provider + model picker** — OpenAI's client throws eagerly with no
   key, hence lazy construction behind an `asyncio.Lock`.
4. **Node → Python/FastAPI rewrite** — same routes, same SSE shapes, zero
   frontend changes.
5. **Dev-server startup bugs** — two distinct causes, one symptom.
6. **Model picker dropdown + dark theme** — a native `<select>`'s open option
   list can't be themed, so it became a `role="listbox"` popover; dismiss logic
   later extracted to `lib/useDismissablePopover.ts`.
7. **Markdown rendering** — `react-markdown` + `remark-gfm` (GFM for tables).
8. **Subagent quality pass** — `subagents.py` at `run_agent()`'s choke point;
   Recharts → ECharts.
9. **Chart subagent v2** — real chart-type range, principled palette ramps.
10. **Composer cleanup + draw-mode selector.**
11. **Attachments + split-pane canvas + Process Trace** — plus
    `withVisualContext()` so follow-ups revise what's on canvas.
12. **Doc diagram wouldn't render** — the semicolon gotcha; source of the
    `mermaid.parse()` harness technique.
13. **"Sheet" attachments** — added `.csv`; `mediaTypeFor(file, kind)` replaced
    a static per-kind lookup once one kind mapped to two mediaTypes.
14. **Flowchart Agent v2: React Flow → Mermaid** — deleted `layout.ts`,
    `FlowNode.tsx`, `@xyflow/react`, `@dagrejs/dagre`.
15. **`end`-keyword bug, part 2** — it was the node *id*, not just the class;
    fixed with the `_rename_reserved_end()` code backstop.
16. **Image lightbox + hand-drawn toggle** — the lightbox is portal-mounted
    because the chat layout's `overflow` ancestors would clip a fixed element.
17. **Toggle + hand-drawn polish** — the escaped switch thumb.
18. **Hand-drawn: solid fill** — the `SKETCH_STROKE_WIDTH` floor.
19. **Vietnamese CSV mojibake** — `_decode_text()`'s lossy UTF-8 decode against
    Excel's cp1258 output; replaced with a BOM check + strict decode ladder.
20. **Accounts and saved history** — broke this app's founding "no database, no
    sessions" property.
21. **Live web search** — also produced the `pause_turn` branch, without which a
    server tool hitting the API's iteration limit reads as "done" and truncates.
22. **OpenAI provider: Chat Completions → Responses API** — plus streaming-
    smoothness fixes and resurrecting `?demo=1`.
23. **Memory layer, phase 1: store and ingest** — `memory_units`, GridFS blobs,
    the Harrier-270m encoder, hybrid `search()`. Chat path untouched.
24. **Chat-header title fallback + persisted agent-step log** — `agent_logs`,
    one document per turn, same delete cascade as everything else.
25. **Attachments survive reopening a conversation** — a blob reference plus the
    *existing* `memory/store.py` GridFS helpers; `GET /api/attachments/{blobId}`
    authorizes against the blob's own `ownerId`.
26. **Memory layer, phase 2: the `search_memory` tool** — the read half, owner-
    wide across conversations, each hit tagged so the reply can cite it. This is
    what makes `HISTORY_LEN = 3` safe rather than lossy.
27. **"Make the blocks match the colors in the image" did nothing** — two
    separate impossibilities: no field to put a color in, and both provider
    converters dropping the image on every message but the last.
28. **Retrieval results moved from the Process Trace to one structured DB step**
    — a `retrieval` event carrying `list_result`, filed into `agent_logs` and
    never relayed.
29. **Zero-Mem applied: the graph view, and retrieval that runs unasked.** Phase
    3 of `DISCUSS_RAG.md` plus §4's request-path retrieval. Six new pieces:
    `entities.py`, `memory_edges` + three owner-scoped store reads, `graph.py`
    (PPR, no networkx), `profile.py`, evidence closure in `retrieve.py`, and
    `context.py`. `main.py` needed no changes — milestone 28's `retrieval` event
    already carried it. Warm push retrieval measured at 242ms end to end.
30. **Theme picker + delete confirmation** — `dark:` repointed from
    `prefers-color-scheme` to a `.dark` class via one `@custom-variant` line, so
    the existing 190-odd dark utilities and both canvas libraries follow one
    store (`src/lib/theme.ts`). Deleting a conversation now goes through a
    native `<dialog>`. The tab icon finally became the app's own brand mark —
    `public/favicon.svg` was still the scaffold's purple placeholder.
31. **Light-mode redesign** — flat white everywhere, reported as "not pretty."
    Whole app moved off the cool `neutral` ramp onto warm `stone`, and gained
    the surface ladder above: the app is now one card floating on a tinted
    ground, with a dark sidebar rail as its only high-contrast surface. Layout
    went from a scrolling page to a fixed-height card with internal scroll,
    which also deleted the composer's fade-out gradient — nothing slides under
    it anymore.
