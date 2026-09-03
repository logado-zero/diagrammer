# Diagrammer

Chat-driven flowcharts and charts. Describe what you want, or upload an
image or spreadsheet, and the app draws it — flowcharts with Mermaid,
charts with ECharts. React + Vite frontend, Python/FastAPI backend, powered
by Claude or OpenAI (picked per message from a model picker in the UI).

## Quickstart (Docker)

```bash
cp .env.example .env   # fill in ANTHROPIC_API_KEY and/or OPENAI_API_KEY
docker compose up --build
```

Open http://localhost:8787.

## Running locally (without Docker)

Requires Node.js and Python 3.12, managed together via one conda environment:

```bash
conda env create -f environment.yml
conda activate ui-design
cp .env.example .env
npm install
npm run dev   # Vite client on :5173 + FastAPI server on :8787
```

**MongoDB must be running** — accounts and saved conversations live in a
`chart-chatbot` database (`MONGODB_URI` in `.env`, default
`mongodb://localhost:27017`). Without it the server still starts and
diagram/chart generation still works, but sign-in and saved history won't.

<details>
<summary>MongoDB on Windows + WSL2</summary>

If MongoDB runs on Windows rather than inside WSL, WSL2's default
networking mode can't reach `localhost:27017` on the Windows host. Enable
mirrored networking in `C:\Users\<you>\.wslconfig`:

```ini
[wsl2]
networkingMode=mirrored
```

Then run `wsl --shutdown` in PowerShell and reopen the shell. Requires
Windows 11 22H2+ and WSL 2.0.0+. Verify from WSL:

```bash
python -c "from pymongo import MongoClient; print(MongoClient(serverSelectionTimeoutMS=3000).list_database_names())"
```

</details>

## Features

- **Model picker** — a dropdown in the composer lists Claude Opus 5 /
  Sonnet 5 / Haiku 4.5 and OpenAI GPT-4o / GPT-4o mini / GPT-5.6 Luna. The
  selected model is sent with each request and remembered across reloads.
  (Currently only GPT-5.6 Luna is enabled by default — see
  `is_model_available()` in `server/models.py` to open up the rest.)
- **Claude** needs no separate "mode" setting: if `ANTHROPIC_API_KEY` is
  set, requests go straight to the Claude API; if not, they fall back to a
  local Claude CLI session instead (see below).
- **OpenAI** models need `OPENAI_API_KEY`; without it they're shown but
  disabled in the picker.
- **Web search** — every model can search the live web for current
  information (prices, rates, news) and for chart data it doesn't already
  know, with sources cited. No separate search API key needed.
- **Sign-in** — pick a user ID and password, or continue as a guest. Guest
  chats are saved and appear in the sidebar, but the account can't be
  signed back into once its cookie is gone. Conversations are titled
  automatically once they produce a chart/diagram, or after a few
  exchanges.
- **UI-only preview** — no API key, database, or account needed:
  `npm run dev:client`, then open `http://localhost:5173/?demo=1` for a
  pre-seeded chat view.

## Checks

```bash
npm run lint                       # frontend lint
npm run typecheck                  # frontend types
python -c "import server.main"     # backend sanity check
python -m pytest server/tests --ignore=server/tests/integration   # backend unit tests
python -m server.memory.demo       # memory/retrieval self-check
```

CI (`.github/workflows/ci.yml`) runs these plus a security scan, integration
tests against a real MongoDB, a retrieval quality eval, and a Docker build.

## Claude without an API key

If `ANTHROPIC_API_KEY` is unset, Claude requests fall back to the
`claude-agent-sdk` package, which shells out to a local `claude` CLI
session and uses whatever account it's logged into (including a Claude
Pro/Max subscription) instead of billing an API key. Text only — no image
uploads in this mode.

This fallback is for running the app locally under your own `claude
login`. Anthropic's Agent SDK terms don't allow a third-party product to
offer claude.ai login or rate limits to other users without prior
approval, so this mode should never be deployed or shared.

## License

MIT — see [LICENSE](LICENSE).
