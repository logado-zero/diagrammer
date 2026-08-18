# Diagrammer

A chat UI that draws flowcharts, workflows, and charts from a text
description or an uploaded image. The frontend is React + Vite; the
backend is a Python FastAPI server, powered by Claude (`anthropic`) or
OpenAI (`openai`), selectable per message from a model picker in the UI.

## Running it

Both Node and Python are managed via one conda env (`ui-design`) — see the
root `CLAUDE.md` for setup. Once activated:

```bash
cp .env.example .env   # then fill in ANTHROPIC_API_KEY and/or OPENAI_API_KEY
npm install
npm run dev             # runs the Vite client (:5173) + FastAPI server (:8787) together
```

**MongoDB has to be running** — accounts and saved conversations live in a
`chart-chatbot` database (`MONGODB_URI` in `.env`, default
`mongodb://localhost:27017`). Without it the server still starts and the
diagram/chart pipeline is unaffected, but signing in and loading history
fail; the startup log says so explicitly.

On this machine that's the **MongoDB Server 7.0 installed on Windows**, not
one inside WSL. Its `mongod.cfg` has `bindIp: 127.0.0.1`, and WSL2 in the
default NAT mode is a separate host to Windows — so reaching it requires
WSL's mirrored networking, configured in `C:\Users\<you>\.wslconfig`:

```ini
[wsl2]
networkingMode=mirrored
```

Then run `wsl --shutdown` in PowerShell and reopen the shell. WSL now shares
the Windows host's interfaces, so `localhost:27017` from the backend reaches
the Windows MongoDB and `MONGODB_URI` needs no host juggling. Mirrored mode
needs Windows 11 22H2+ and WSL 2.0.0+; on anything older WSL ignores the
setting and falls back to NAT, and the symptom is the startup warning above.
Verify from WSL with:

```bash
python -c "from pymongo import MongoClient; print(MongoClient(serverSelectionTimeoutMS=3000).list_database_names())"
```

`npm run dev:server` runs `conda run -n ui-design --no-capture-output
uvicorn server.main:app --reload --reload-dir server --port 8787` directly
if you want the API server on its own. It goes through `conda run` rather
than a bare `uvicorn` so it works even in a shell that never ran `conda
activate` (Node comes from `nvm` here, so `npm run dev` can otherwise look
like it "works" while the Python side silently isn't on `PATH`). The
`--reload-dir` matters too — without it uvicorn watches the whole repo,
including `node_modules`, and reloads spuriously while Vite writes its dep
cache there. For handling many
concurrent users, answering a request reads nothing from the database and
the process holds no in-memory session state (sessions are rows in
MongoDB), so it scales across CPU cores with no code changes — just run
more uvicorn workers: `uvicorn server.main:app --workers 4 --port 8787`.

- The model badge next to the attach button in the composer (default: "Opus
  5") is a live dropdown, populated from `GET /api/models`. It lists Claude
  Opus 5 / Sonnet 5 / Haiku 4.5 and OpenAI GPT-4o / GPT-4o mini / GPT-5.6
  Luna; whichever one is selected is sent with each chat request and
  remembered in `localStorage` across reloads.
- **Claude** needs no explicit "mode" setting: if `ANTHROPIC_API_KEY` is set
  in `.env`, Claude requests hit the API directly; if it's unset, they
  automatically fall back to Agent SDK mode (see below) instead — the
  Claude entries in the model picker are always shown as available.
- **OpenAI** models need `OPENAI_API_KEY` set — without it they still show
  in the picker but are grayed out (`available: false` from `GET
  /api/models`), and `/api/chat` returns a clean error if one is selected
  anyway.
- **Web search**: every model can search the live web, so questions that
  depend on current information — today's prices, exchange rates, scores,
  news — get real answers with sources instead of "I don't have access to
  live data". This extends to drawing: ask for a chart of something the
  model doesn't know and it searches for the numbers first rather than
  asking you to paste them in. It's always available, the model decides when
  a question needs it, and the status line says "Searching the web…" while
  it works. No search API key needed — the search runs on the provider's
  side (Anthropic's `web_search`/`web_fetch`, OpenAI's `web_search`).
- **Signing in**: the app opens on a sign-in screen — pick a user ID and
  password, or "Continue as guest" for a throwaway account. Guest chats
  are still saved and still show up in the sidebar; the account just can't
  be signed back into once the cookie is gone. Conversations are titled
  automatically by an LLM once they produce their first chart/diagram, or
  after the third question and answer.
- For UI/visual work only, nothing else is needed — no API key, no MongoDB,
  no account: `npm run dev:client` and open
  `http://localhost:5173/?demo=1` to see the chat view pre-seeded with a
  sample diagram + chart (`src/lib/demoData.ts`). `?demo=1` also skips the
  sign-in screen; plain `/` shows the welcome screen once you're signed in.
- `npm run typecheck` checks the client TypeScript project; `npm run lint`
  runs `oxlint`; `npm run build` produces a production client bundle in
  `dist/`. The Python server has no separate typecheck step configured —
  sanity-check it by importing the app (`python -c "import server.main"`)
  or just booting it (`npm run dev:server`).

## Claude backend: API key vs. Agent SDK fallback (personal use only)

`server/agent.py` → `resolve_claude_transport()` auto-detects which Claude
backend to use — there's nothing to configure:

- **`ANTHROPIC_API_KEY` set** (recommended) — calls the Claude Messages API
  directly via the `anthropic` package's `AsyncAnthropic` client. Supports
  image uploads. This is the supported path for anyone besides you running
  this app.
- **`ANTHROPIC_API_KEY` unset** — falls back to the `claude-agent-sdk`
  Python package instead, which shells out to a local `claude` CLI
  subprocess and uses whatever account that CLI is logged into (`claude
  login`) — including a Claude Pro/Max subscription — instead of a metered
  API key. No image uploads in this mode (text only), and it always uses
  `AGENT_SDK_MODEL` regardless of which Claude entry (Opus/Sonnet/Haiku) is
  selected in the picker.

**Read `CLAUDE.md` → "Backend provider switch" before relying on the
fallback** — Anthropic's own Agent SDK docs say third-party products
aren't allowed to offer claude.ai login/rate limits to other users without
prior approval. This mode is scoped to you running the app locally under
your own login, never something to deploy for anyone else.

---

# React + Vite

This template provides a minimal setup to get React working in Vite with HMR and some Oxlint rules.

Currently, two official plugins are available:

- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react) uses [Oxc](https://oxc.rs)
- [@vitejs/plugin-react-swc](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react-swc) uses [SWC](https://swc.rs/)

## React Compiler

The React Compiler is not enabled on this template because of its impact on dev & build performances. To add it, see [this documentation](https://react.dev/learn/react-compiler/installation).

## Expanding the Oxlint configuration

If you are developing a production application, we recommend using TypeScript with type-aware lint rules enabled. Check out the [TS template](https://github.com/vitejs/vite/tree/main/packages/create-vite/template-react-ts) for information on how to integrate TypeScript and Oxlint's TypeScript related rules in your project.
