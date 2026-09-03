# Diagrammer — Progress Log

Decisions, constraints, and hard-won gotchas worth knowing before changing this
project. Not a to-do list. **Current as of milestone 33 (2026-08-19)** — if the
code disagrees with a line here, the code is right and this file is stale.

This file holds *the decision and the constraint it locks in*. How any of it
works lives elsewhere: `HOWTHEYWORK.md` for runtime architecture, per-feature
mechanics and the file map (its designed web twin is
`docs/howtheywork.artifact.html` — read that file's closing section before
republishing); `CLAUDE.md` for provider-switch rationale and dev environment;
`DISCUSS_RAG.md` for the retrieval roadmap beyond what's built.

## Current state

- **Frontend**: React + Vite + TypeScript + Tailwind v4; Mermaid for flowcharts,
  ECharts for charts, both driven by tool-call JSON and polished by a
  second-pass subagent. Light/dark/system theme picker in the sidebar.
- **Backend**: Python + FastAPI. `main.py` builds the app; routes live in
  `server/routes/`. Three interchangeable providers — Claude direct API, Claude
  via a local `claude` CLI subprocess (personal-use only), OpenAI — chosen per
  request by the UI model picker. Everything touching I/O is `async def` and
  nothing is shared and mutable, so `uvicorn --workers N` needs no code changes.
  Every model can search the web, provider-natively.
- **Model picker is temporarily gated to GPT-5.6 Luna** (32), which is also the
  default; the other five are greyed out and refused by `POST /api/chat`. Two
  lines in `is_model_available()` — delete them to restore the catalog.
  Consequence for the rest of this file: `openai_provider.py` serves every turn.
- **Accounts + saved history**: user ID / password or guest sign-in;
  conversations in MongoDB (`chart-chatbot`). Reopening one redraws its charts
  with no LLM call.
- **Memory layer** (`server/memory/`): every finished turn is indexed into
  `memory_units`; `search()` fuses dense + lexical + entity-graph views. Two
  entry points, one core — **push** (`select_context()`, every gated turn) and
  **pull** (the `search_memory` tool). Phases 1–3 of `DISCUSS_RAG.md`; `Route(q)`
  is unbuilt, so the fusion weights are a fixed constant.
- **Short history + recall, not long history**: only the last `HISTORY_LEN = 3`
  messages are sent; older context returns through retrieval.
- **Timestamps are naive Vietnam local time** (33), one spelling: `shared.now()`.
- **Untested path**: no `ANTHROPIC_API_KEY` here, so `providers/api.py` has never
  run live — including `SERVER_TOOLS` search and the `pause_turn` branch. While
  the Luna gate holds, neither Claude path is reachable from the UI at all.
- **UI-only work needs no backend**: `?demo=1` renders a pre-seeded chat from
  `src/lib/demoData.ts` and skips sign-in.

## Locked-in decisions

- **One choke point for cross-provider behavior.** `agent.py`'s `run_agent()` is
  where every provider's output passes: attachment inlining, the subagent pass,
  `trace` events; inputs likewise via `tools_for_mode()`. A feature that needs
  per-provider handling is the signal it doesn't belong there.
- **Shared *tables and strings*, not shared *transports*.** `providers/
  dispatch.py` owns the tool-name → event mapping and trace labels; the three
  `run_*_agent()` bodies stay three. `agent_sdk.py` deliberately doesn't use
  `dispatch_tool_call()` — its MCP handlers are invoked by the SDK out of band
  and cannot yield. Don't "finish the job" with a provider base class. (32)
- **Web search is declared per provider, not in `tools.py`** — the one exception,
  because the *definition* isn't shared even though the capability is. `tools.py`
  is where `agent_sdk.py` derives MCP names, so a server tool there emits a bogus
  `mcp__diagrammer__web_search`. The *prompt* half is shared normally.
- **Subagents own structure, the client owns color.** Subagents pick node
  kinds/labels/edges/direction and ECharts series/axis shape, and never emit
  color; color comes from `src/lib/palette.ts` so theme switching stays instant.
  Layout is Mermaid's problem — neither side computes it.
- **The theme is a class on `<html>`, not a media query.** `src/lib/theme.ts`
  owns both the `.dark` class every `dark:` utility binds to and the
  `useIsDark()` boolean ECharts and Mermaid read, so Tailwind and the canvas
  libraries can't disagree. It's outside React because `ChartCard`/`DiagramCard`
  are memo'd leaves — prop-drilling the theme would re-render them constantly.
- **One gray ramp (`stone`) and one surface ladder, in both themes** — ground →
  app card → rail → canvas → chart card (table in `HOWTHEYWORK.md`), which only
  works as a set. Two rungs are load-bearing: **the chart card stays pure white**
  because `CARD_SURFACE.light` is what an exported PNG bakes in (also why the
  ground is low-chroma gray, not a colored wash), and **the sidebar rail is dark
  in both themes** because in light mode it's the only edge the white content
  area has to sit against.
- **The app is a fixed-height card, not a scrolling page.** `App.tsx` is `h-svh
  overflow-hidden`, every screen inside `h-full min-h-0` with its own scroll
  container. A new screen reaching for `min-h-svh` overflows instead of scrolling.
- **A user-requested node color is *data*, not syntax.** `agent.py`'s
  `_node_colors()` lifts the model's `color` fields into a map that
  `DiagramCard.tsx` compiles to classDefs — not Mermaid `style` lines, because
  each look needs a different declaration and the hand-drawn toggle is client
  state the server can't see.
- **Chart ramps come from the `dataviz` skill**, verbatim: categorical (8 hues,
  never cycled — extras fold into "Other"), sequential, status pair. Which ramp
  applies is chosen in `ChartCard.tsx` from the option's *structure*, not a field
  the model self-reports.
- **Draw-mode restricts tool availability, never forces `tool_choice`.** A hard
  force makes a plain "hi" in Flowchart mode draw an empty diagram.
- **Canvas shows only the latest visual**, replaced in place. Cross-turn edits
  replay the prior payload as history (`lib/api.ts`'s `withVisualContext`), not
  via a special "edit" tool.
- **Process Trace steps must stay real** — actual phases already happening. No
  fabricated progress theater.
- **Destructive actions confirm in-app, not via `window.confirm`.** Delete
  cascades across messages, GridFS blobs and memory units, and the trash button
  sits 8px from the row that *opens* the chat. A native `<dialog>` gives backdrop,
  Escape and focus trapping free and styles like the app.
- **A control that renders as interactive must have a handler.** Milestone 32
  deleted three that didn't rather than wiring them; a focusable no-op is an
  accessibility bug, not a placeholder.
- **Passwords use stdlib `hashlib.scrypt`; sessions are Mongo rows keyed by a
  random cookie token, not JWTs** — so signing out actually revokes.
  `current_user()` in `auth.py` is the only place a cookie becomes a user.
- **Guest mode is a real account, not a second code path** — an ordinary row with
  a `guest-xxxxxxxx` id and an empty `passwordHash` `verify_password()` can never
  match.
- **Messages are embedded in the conversation document.** One read reopens a chat.
- **One app-wide `PyMongoError` handler**, paired with
  `serverSelectionTimeoutMS=3000`, because pymongo's 30s default made the login
  form hang for half a minute before a bare 500.
- **Persistence lives in the routes, never in `agent.py`.** `run_agent()` is the
  choke point for *generating* an answer; saving it is a different concern. Same
  reasoning puts memory ingest in `routes/chat.py`.
- **One timestamp spelling: `shared.now()`, naive Vietnam local.** Naive because
  BSON has no timezone, so what goes in is what a database client reads back —
  and the database is the only UI these timestamps have. What made the earlier
  local-time write a bug was that it *disagreed* with every other write, not the
  clock it used; every comparison here is a `now()` against a value from `now()`.
  Changing the zone means another `server/migrations/shift_to_ict.py`. (33)
- **The model catalog is one file**, `server/models.py`. `POST /api/chat` calls
  `is_model_available()` itself — greying an option out is presentation, the
  route is enforcement.
- One conda env (`ui-design`) hosts both Node and Python.

### Memory-layer decisions

- **`Scope` is the only way to filter a memory query.** No function in
  `server/memory/` takes a raw Mongo filter, and `Scope` can't be built without
  an owner id. A forgotten tenant filter raises nothing, logs nothing, passes
  every single-user test, and quietly makes one account's conversation
  retrievable by another — so it's prevented in the type system.
  `python -m server.memory.demo` asserts it both directions; that's the merge gate.
- **Store the original, never a summary.** A chart's payload holds its exact
  numbers. The embedded text is a *retrieval handle*; the payload is what's used.
- **Indexing is fire-and-forget, and failure is non-fatal.** It runs after the
  SSE stream closes; a down encoder stores `vec: null` for a later backfill,
  because refusing to store would lose data permanently.
- **A local encoder, not an embedding API.** Harrier-270m on CPU via onnxruntime,
  no torch. Verified multilingual: an English query retrieves a Vietnamese
  document with the lexical view contributing literally zero.
- **`MEMORY_ENABLED=0` is a real kill switch**, shipped with the feature — tool
  list and system prompt go byte-identical to pre-memory, asserted in
  `tools.py`'s self-check.
- **The scope is the tool's entire security boundary.** `search_memory`'s schema
  has one field, `query` — no owner or conversation parameter, so the model can't
  widen what it sees and an injection inside a retrieved document has nothing to
  aim at.
- **The tool result is text, not a structure** — the only shape all three
  transports agree on. Formatting lives in `memory/tool.py`; the schema and
  prompt stay in `tools.py`, which is import-free so `agent_sdk.py` can derive
  MCP names without dragging numpy and MongoDB into every provider.
- **What a search returned is logged, not displayed.** Each call appends one
  `retrieval` record to `agent_logs.steps`; the route files it and `continue`s
  before the SSE yield, so it's the one provider event the browser never sees.
- **Push and pull are two entry points, never two implementations.** Both call
  the same `search()`, `format_hits()` and `retrieval_record()`; only `source`
  differs. Push exists because pull can't be sufficient alone — to call the tool
  the model must first notice something is missing, and a truncated history
  leaves no trace of what was truncated. Anything changing ranking, formatting or
  logging lands in the shared core, or the two paths drift and one gets fixed.
- **Retrieval is gated, not unconditional.** `profile()` skips greetings and
  too-short inputs before any encoding. The gate reads **only the current user
  message** while the query is built from the **last two**: profiling the pair
  makes "hi" after a long reply look substantial, and querying on one loses the
  subject of every bare follow-up.
- **The graph view ships on, against `DISCUSS_RAG.md` §9's "flag, default off".**
  That plan assumed an eval set would exist by then and it doesn't, so shipping
  it dark meant nothing would ever measure it. `MEMORY_GRAPH=0` removes only the
  view — entities and edges keep being written, so re-enabling needs no backfill.
- **Entity extraction is regex + terms, not an NER model.** `en_core_web_sm` is
  English-only and fails the Vietnamese this app is used in. Precision matters
  less than it looks: the graph records *observed co-occurrence*, not inferred
  semantics, and tickers/amounts/dates/quoted titles are what regex is best at.
- **Retrieval failure degrades, never fails the turn.** `run_search_memory()`
  catches everything and says memory is unavailable; a missing encoder already
  drops `search()` to lexical-only.

## Environment gotchas (read before debugging "why won't it start")

Setup → `README.md`. Why `dev:server` needs `conda run --no-capture-output` and
`--reload-dir server`, and the WSL mirrored-networking config for the Windows
MongoDB → `CLAUDE.md`. What's only here:

- **`conda env update`, not `create`**, when `environment.yml` gains deps — and
  it re-resolves the whole `pip:` block. The memory-layer install silently bumped
  `anthropic`, `claude-agent-sdk`, `openai` and `uvicorn`. If a provider starts
  behaving oddly, check whether its SDK moved under you.
- **MongoDB has to be running**, or login and history fail while the server looks
  healthy — startup warns instead of crashing, and `GET /api/health` touches no
  collection. The honest check is any auth route, which answers `503` in ~3s.
- **`ECONNREFUSED 127.0.0.1:8787` in the first seconds of `npm run dev` is a
  startup race**, not a failure. The real failure is the *permanent* one where
  `dev:client` also dies.
- **Adding a frontend dep requires a full Vite restart**, not just deleting
  `node_modules/.vite`. Symptom: 504 "Outdated Optimize Dep".
- **Client timeouts are `timeout=60.0`** — this sandbox has ~12s latency to
  external APIs, past the SDKs' 5s default.
- **Playwright**: `sudo env "PATH=$PATH" npx playwright install --with-deps
  chromium` (plain `sudo npx` loses the conda env's `npx`).
- **First memory encode downloads ~348MB** into `MEMORY_MODEL_DIR`
  (`.cache/harrier`, gitignored). Only the first indexed turn is slow.
- **`$text` queries need the text index**, which only `ensure_indexes()` creates.
  Anything touching `memory_units` outside the running server must call it first.

Mermaid/rough.js rendering gotchas — the semicolon, the reserved `end`, quoted
ids, `htmlLabels: false`, classDef source order, the `SKETCH_STROKE_WIDTH` floor
— live in `HOWTHEYWORK.md`, next to the code they constrain.

## Known limitations (accepted ceilings, not oversights)

Memory cost ceilings, edge caps, the missing relevance floor and the fixed fusion
weights are stated with numbers in `HOWTHEYWORK.md`'s memory chapter. Everything
else:

- **Wide `flowchart LR` diagrams shrink and leave vertical dead space** —
  Mermaid's `useMaxWidth`. The fix (natural size + scrolling panel) trades
  legible text for horizontal scrolling; deliberately not taken.
- **The no-LLM-client fallbacks are a safety net, not the experience.**
  `_fallback_chart_option` does bar/line/pie only. They exist so the app works
  end to end with zero setup.
- **`agent-sdk` mode**: no image input, no multi-turn session state, a subprocess
  per request. Details in `CLAUDE.md`.
- **Hand-drawn font** falls back to a serif on a bare Linux box; the fix is
  bundling an Excalifont/Virgil woff2.
- **The session cookie is `secure=False`** unless `COOKIE_SECURE=1`. Flip it
  before deploying anywhere real.
- **No password reset, no login rate limiting, no guest reaping.**
- **An uploaded image only reaches the model while the tab lives.** A reloaded
  conversation has only the display-only `attachment` reference, so
  `toOutgoing()` sends `image: null` — which is also why **retrying a turn whose
  attachment came from reloaded history 422s**. Both are fixed by re-inlining the
  GridFS blob server-side.
- **Attachments are persisted in GridFS but only read back for display** — the UI
  doesn't re-render the original image or sheet into the canvas.
- **Node colors are per-node fills only.** No edge colors, no border distinct
  from fill (hand-drawn can't have one), and nothing checks the model's colors
  against the image it claims to match.
- **Memory: dense scoring is brute-force in-process**, roughly fine to ~10k units
  per user (~25MB of float32 at 640 dims); MongoDB Community has no
  `$vectorSearch`. Cost tracks one user's history depth, not the user count. Past
  the ceiling, a real vector index goes behind the same `store.dense()` signature.
- **Memory: the image description is the one generative call in the path.** It
  breaks Zero-Mem's zero-token property by design — at ingest, off the response
  path, and there's no text in an image to store otherwise.
- **Memory: `embedModel`/`embedDim`/`embedSpace` are recorded but no backfill job
  exists**, so swapping encoders is a manual migration.
- **Memory: push/pull overlap is handled in the prompt, not id-level dedup.** The
  alternative threads excluded unit ids through three providers; the `source`
  field makes the overlap measurable first.
- **Memory: `python -m server.memory.backfill` is manual** — run once per
  deployment or pre-phase-3 units stay invisible to the graph. Idempotent.
- **Memory: the model decides when to recall, and nothing double-checks it.** No
  router forcing a search, no fallback if it doesn't call the tool.
- **The theme picker is a client-only preference** in `localStorage`, so it
  doesn't follow an account across browsers.
- **The database stores wall-clock Vietnam time, not UTC.** Single-timezone by
  design; serving another zone means `shared.now()` back to UTC plus per-user
  formatting at the edge.

## Rejected alternatives — don't re-propose without new information

- **Excalidraw as a rendering library** — Mermaid, with the *look* emulated via
  `look: handDrawn`.
- **React Flow + dagre** (`lib/layout.ts`, `FlowNode.tsx`) — deleted; Mermaid's
  own layout engine replaced it. **Recharts** — replaced by ECharts.
- **A provider base class** — see "shared tables, not shared transports" above.
- **`AGENT_PROVIDER`-style startup env switch** — doesn't fit a per-request model
  picker; Claude transport is auto-detected.
- **Forced `tool_choice` for draw modes** — see Locked-in decisions.
- **A search API key / custom `web_search` tool** (Brave, Tavily, Serper) — all
  three providers search natively, no key, no dependency.
- **`web_search_options` on the OpenAI provider** — measured against the live API,
  not assumed; the three failing combinations are in `CLAUDE.md`. Search and
  function calling are mutually exclusive **on Chat Completions**, and this app
  can't give up its two tools — that forced the Responses migration.
- **A `search: bool` on `ModelOption`** and **a "Search" toggle in the composer**
  — every model can search, and a default-off switch reproduces the exact bug it
  was meant to fix.
- **Rendering web-search citations as a sources list** — the prompt asks for
  inline named sources instead, which it does, with links.
- **Composite hue+shade encoding for >8 categories** — the `dataviz` skill's
  documented fallback (fold to "Other") is what's prompted.
- **Artifact history/switcher on the canvas** — latest-only is the decision.
- **OS-only dark mode** — survives as the `system` option, still the default.
- **A theme context provider / theme in `App.tsx` state** — the consumers that
  matter are memo'd leaves; a module store keeps their memoization.
- **A tinted or gradient app background** — chroma competes with
  `CATEGORICAL_LIGHT`. The layering idea was worth taking; the color wasn't.
- **The cool `neutral` ramp in light mode** — two ramps meant light and dark were
  different-temperature designs sharing a DOM.
- **`window.confirm()` for delete** — OS chrome that ignores the app's theme.
- **JWTs for sessions** — server-side rows, so logout revokes.
- **motor** — deprecated; `pymongo` 4.9+ ships `AsyncMongoClient`.
- **pydantic's `EmailStr`** — pulls in `email-validator` to RFC-validate an
  address nothing here ever mails.
- **A separate `messages` collection** — embedded in the conversation document.
- **Timezone-aware datetimes in Mongo** — BSON drops the tzinfo anyway, so it
  changes nothing about what a database client displays.
- **OpenAI embeddings for memory** — a local model instead, so no user's indexing
  path carries a third-party dependency and a per-request cost.
- **BGE-M3 as the encoder** — replaced by Harrier-270m before any code was
  written: half the parameters, 640 dims, 32k context, better multilingual
  benchmark, published ONNX build. The one loss is BGE-M3's sparse output.
- **A separate encoder sidecar** — deferred; `asyncio.to_thread` keeps the event
  loop free because onnxruntime releases the GIL. Revisit when encode latency
  shows up in traces.
- **MinIO/S3 for attachment blobs** — GridFS; MongoDB is already running.
- **`networkx`** — Personalized PageRank is ~15 lines of power iteration over a
  dict-of-dicts at this graph size.
- **spaCy or any NER model** — can't do Vietnamese, and a co-occurrence graph
  doesn't need the precision NER is bought for.
- **Querying the edge collection during the PPR walk** — the subgraph is loaded
  up front, a tenant-isolation decision more than a performance one: every edge
  arrives through one owner-filtered function, so a walk can't reach another
  account however many hops it takes.

## Debugging playbook — techniques that worked here

- **Read the actually-installed package**, don't guess its API. That's how
  `claude-agent-sdk`'s `tool()` was found to pass a JSON Schema dict straight
  through, letting `agent_sdk.py` reuse `tools.py`'s schemas; same method
  confirmed Mermaid's `look`/`handDrawnSeed` config and hardcoded `fillStyle`.
- **Read the model's own config, don't invent its contract.** Harrier's query
  instruction prefix was copied verbatim from
  `config_sentence_transformers.json`; rewording it degrades retrieval silently.
- **Parse Mermaid source through real `mermaid.js`** in a throwaway Playwright
  page (`mermaid.parse(src)`) for the exact line/token error. `mermaid-cli`'s
  puppeteer hangs here; load `node_modules/mermaid/dist/mermaid.min.js` (UMD),
  since the ESM build's chunked imports can't resolve from a Blob URL.
- **Dump rendered DOM *attributes*, not computed styles**, when a renderer's
  output is confusing. (Nodes are `g.node` classic, `g.rough-node` hand-drawn,
  ids like `<renderId>-flowchart-n1-0`.)
- **Assert geometry and color in Playwright instead of eyeballing screenshots** —
  reading `getComputedStyle(node).fill` off the live SVG is how the theme switch
  was verified to reach Mermaid's nodes.
- **Intercept a dev-server module instead of editing a source file** when a
  UI-only check needs different fixtures: Vite serves `src/lib/demoData.ts` as
  its own module, so `page.route()` rewrites it in flight with nothing to revert.
  Same trick stubs `/api/*` so the sidebar has rows without touching the database.
- **The bar for "done" is a live run**, not reasoning: `npm run typecheck`, `npm
  run lint`, `python -c "import server.main"`, plus a curl or browser round trip
  for anything behavioral — and `python -m server.memory.demo` for memory.

## Lessons worth carrying to the next feature

Tags point at the milestone that produced each one. Kept only where the lesson
outlives its incident.

- **Trace the whole path before blaming the end of it.** "The image is gone"
  looked like history truncation and was a one-line `i == last_index` three
  layers down. (27)
- **Check which provider is actually live before assuming the default one is.**
  An *empty* `ANTHROPIC_API_KEY` is falsy, so the running transport was
  `agent_sdk.py`, whose `tools=[]` had every built-in off. (21)
- **"The provider can't do X" deserves the same live probe as "it can."** (22)
- **A design doc's "nothing to backfill" is a claim about code, not a fact.** The
  field and its index shipped; the writer never did, so `entities` was `[]` on
  every unit. A promise that something is written needs an assertion that it is. (29)
- **Lazy loading is fine until something moves onto the request path.** The
  encoder's 4s cold load was invisible while only background indexing used it;
  auto-retrieval put it in front of every process's first user. (29)
- **A capability the model can't express is one it will fake** — so when a model
  claims it did something and nothing changed, check whether the request was even
  representable before touching the prompt. The schema is the fix; the prompt
  line is the backstop. (27)
- **A prompt instruction that has already failed once needs a code backstop, not
  stronger wording.** (15)
- **The primary model's tool description has to advertise what the subagent can
  actually do** — `render_chart` said "bar, line, or pie", so the model told
  users a gauge "isn't available". (9)
- **Match the event shape to the thing, not the transport that's already there**,
  and keep "show the user" separate from "record it". Reusing `trace` for
  retrieval cost the structure: a search *is* one event containing a list. (28)
- **Two strings, not one.** The retrieval gate and the retrieval query read
  different text; swapping them breaks the feature in opposite directions, and
  it's invisible in code that just says `messages[-1].text`. (29)
- **A graph traversal is a new way to leave a tenant, and the quietest yet.**
  Every previous query filtered `ownerId` once at the leaf; a walk hops unit →
  entity → unit and entity strings are inherently global. Load the subgraph
  through one owner-filtered function up front. (29)
- **Stringify ObjectIds at the boundary of anything a route returns.** FastAPI's
  encoder takes `datetime` and raises on `ObjectId` — it breaks only the route
  that reads the record, and only when someone finally reads one. (28)
- **A serializer that enumerates the cases it knows about is a bug waiting for
  the next case.** `_serialize_assistant_content` silently dropped search blocks. (21)
- **A truncation limit belongs to the consumer, not the file.** `MAX_ROWS = 300`
  is a *prompt* budget; memory inheriting it made row 900 permanently
  unanswerable — "300 regions" while memory held all 420. (23)
- **`asyncio.create_task` needs a strong reference** or the GC can cancel it
  mid-flight — hence `shared.spawn()`. (23)
- **An MCP handler has no request context**, so the MCP server is built per
  request with the scope closed over. A `ContextVar` works until a handler runs
  on a different task, then searches the wrong person's memory. (26)
- **Returning a module-level list lets a caller mutate it process-wide** —
  `tools_for_mode()` did, and a provider appending server tools would have
  corrupted every later request. (26)
- **A blind `$push` per turn silently duplicates history on retry.** (20)
- **Two writers on one collection must share the value, not the recipe.** Both
  the local-time bug and the timezone switch were one function's output, so each
  was a one-line change. (32, 33)

## Milestone timeline

One line each; the durable content is above, the narration is in git history.

1. **v1** — Node/Express/TS backend + React frontend, Claude-only.
2. **Claude Agent SDK transport** — via the local `claude` CLI; personal-use only.
3. **Multi-provider + model picker** — OpenAI's client throws with no key, hence
   lazy construction behind an `asyncio.Lock`.
4. **Node → Python/FastAPI rewrite** — same routes, same SSE shapes, no frontend
   changes.
5. **Dev-server startup bugs** — two distinct causes, one symptom.
6. **Model picker dropdown + dark theme** — a native `<select>`'s option list
   can't be themed, so it became a `role="listbox"` popover.
7. **Markdown rendering** — `react-markdown` + `remark-gfm`.
8. **Subagent quality pass** — `subagents.py` at the choke point; Recharts → ECharts.
9. **Chart subagent v2** — real chart-type range, principled palette ramps.
10. **Composer cleanup + draw-mode selector.**
11. **Attachments + split-pane canvas + Process Trace** — plus `withVisualContext()`.
12. **Doc diagram wouldn't render** — the semicolon; source of the `mermaid.parse()`
    harness technique.
13. **"Sheet" attachments** — `.csv`, and `mediaTypeFor(file, kind)`.
14. **Flowchart Agent v2: React Flow → Mermaid** — deleted `layout.ts`,
    `FlowNode.tsx`, `@xyflow/react`, `@dagrejs/dagre`.
15. **`end`-keyword bug, part 2** — it was the node *id*, not just the class.
16. **Image lightbox + hand-drawn toggle** — portal-mounted past the layout's
    `overflow` ancestors.
17. **Toggle + hand-drawn polish** — the escaped switch thumb.
18. **Hand-drawn: solid fill** — the `SKETCH_STROKE_WIDTH` floor.
19. **Vietnamese CSV mojibake** — BOM check + strict decode ladder, against
    Excel's cp1258 output.
20. **Accounts and saved history** — broke the founding "no database" property.
21. **Live web search** — also produced the `pause_turn` branch.
22. **OpenAI: Chat Completions → Responses API** — plus streaming smoothness and
    resurrecting `?demo=1`.
23. **Memory phase 1: store and ingest** — `memory_units`, GridFS blobs, the
    Harrier encoder, hybrid `search()`. Chat path untouched.
24. **Chat-header title fallback + `agent_logs`** — one document per turn, same
    delete cascade as everything else.
25. **Attachments survive reopening a conversation** — a blob reference plus the
    existing `memory/store.py` GridFS helpers.
26. **Memory phase 2: the `search_memory` tool** — the read half, owner-wide.
    What makes `HISTORY_LEN = 3` safe rather than lossy.
27. **"Match the colors in the image" did nothing** — no field to put a color in,
    and both converters dropping all but the last image.
28. **Retrieval results moved from the Process Trace to one structured DB step.**
29. **Zero-Mem applied: the graph view, and retrieval that runs unasked.** Phase 3
    plus §4 — `entities.py`, `memory_edges`, `graph.py` (PPR, no networkx),
    `profile.py`, evidence closure, `context.py`. Warm push measured at 242ms.
30. **Theme picker + delete confirmation** — `dark:` repointed to a `.dark` class
    via one `@custom-variant` line; native `<dialog>`; real favicon.
31. **Light-mode redesign** — warm `stone`, the surface ladder, and a fixed-height
    card instead of a scrolling page.
32. **Refactor pass, and six real bugs** — routes into `server/routes/`, helpers
    into `shared.py`, the tool table into `providers/dispatch.py`, the Mermaid
    `end` rule into `mermaid.py`, client state into `src/hooks/`, card export into
    `useImageExport` + `CardChrome`. Nothing changed about what the app does.
33. **Timestamps to Vietnam local time** — `shared.now()` plus a one-off
    `server/migrations/shift_to_ict.py` backfill; this file condensed from 630.
