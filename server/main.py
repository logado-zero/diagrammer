"""
FastAPI app construction: middleware, the app-wide error handler, startup, and
the routers that make up the HTTP surface.

The routes themselves live in server/routes/ (meta, conversations,
attachments, chat) and server/auth.py. Nothing in this file handles a request
body — if you are looking for what a route *does*, it is in one of those.

Concurrency (multiple users at once): every route is `async def`, so
FastAPI/uvicorn services many concurrent requests on one event loop without
blocking each other while awaiting network I/O (calls to Anthropic/OpenAI, and
MongoDB) or subprocess I/O (the agent-sdk provider's local `claude` CLI). There
is no shared mutable state per request — the Mongo client is a connection pool,
not request state — so this process can also be scaled across CPU cores by
running multiple uvicorn worker processes (`uvicorn server.main:app --workers
4`) with zero code changes; see README.md for the exact command.
"""

from dotenv import load_dotenv

# Must run before importing anything below that reads an env var at import
# time (e.g. providers/openai_provider.py's OPENAI_MODEL default,
# providers/agent_sdk.py's AGENT_SDK_MODEL default) — same ordering
# constraint as the Node build's `import 'dotenv/config'` being the first line.
load_dotenv()

import os  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from pymongo.errors import PyMongoError  # noqa: E402

from server import auth, memory  # noqa: E402
from server.agent import resolve_claude_transport  # noqa: E402
from server.db import ensure_indexes  # noqa: E402
from server.routes import attachments, chat, conversations, meta  # noqa: E402
from server.shared import spawn  # noqa: E402

PORT = int(os.environ.get("PORT", "8787"))

# Matches the original Express body limit (headroom for base64 images);
# Starlette has no default cap, so this is an explicit ceiling, not a
# behavior port of something Starlette already did.
MAX_BODY_BYTES = 20 * 1024 * 1024


def _spawn_encoder_warmup() -> None:
    """
    Input: none. Output: none — schedules one throwaway encode and returns.

    The encoder loads lazily on first use, and that load is ~4s of reading
    weights off disk and building an onnxruntime session, against ~145ms for
    every encode afterwards. That was fine while the only caller was background
    indexing, which nobody is waiting on. It stopped being fine when retrieval
    moved onto the request path: without this, the first chat turn after every
    boot — and after every `--reload` — pays the load in front of the user, and
    reliably enough that it trips `context.TIMEOUT_SECONDS` and silently answers
    that turn with no memory at all.

    Scheduled rather than awaited, for the same reason ingest is: startup must
    not block on it, and a machine that can't load the encoder must still serve
    a working chat app. Failure is swallowed — the load will simply be retried
    on first real use, which is exactly the behaviour this replaces.
    """
    if not memory.memory_enabled():
        return

    async def warm() -> None:
        try:
            from server.memory.encoder import encode

            await encode(["warm up"], is_query=True)
            print("Memory encoder warmed.")
        except Exception as err:  # noqa: BLE001 - a cold encoder is slow, not broken
            print(f"memory: encoder warm-up skipped ({err.__class__.__name__}: {err})")

    spawn(warm())


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Startup banner, index creation, and the encoder warm-up. Nothing to tear down."""
    transport = resolve_claude_transport()
    print(f"Diagrammer API listening on http://localhost:{PORT}")
    try:
        await ensure_indexes()
    except Exception as err:  # noqa: BLE001 - a dead Mongo must not stop the server from booting
        # Deliberately non-fatal: the server still starts and says why login
        # and history are broken, instead of dying with a connection trace
        # that looks like a code bug.
        print(f"MongoDB unreachable at startup ({err.__class__.__name__}) — login and saved history will fail.")
        print("Start MongoDB and restart, or check MONGODB_URI in .env.")
    print(f"Claude transport: {transport} (auto-detected from ANTHROPIC_API_KEY)")
    if transport == "agent-sdk":
        print(
            "No ANTHROPIC_API_KEY set — Claude requests fall back to agent-sdk mode, which relies on a local "
            "`claude` CLI login on this machine and does not support image input. Personal/dev use only, see "
            "CLAUDE.md for why this is not something to deploy for other users."
        )
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is not set — OpenAI models will be marked unavailable until it is.")
    _spawn_encoder_warmup()
    yield


app = FastAPI(lifespan=lifespan)
# allow_credentials is required for the session cookie to be sent on
# cross-origin requests — and CORS forbids pairing it with allow_origins=["*"],
# so the wildcard becomes an explicit dev-origin list. In normal use the app is
# same-origin anyway (Vite proxies /api to this server), so this only matters
# if you point a differently-served frontend at it.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(meta.router)
app.include_router(conversations.router)
app.include_router(attachments.router)
app.include_router(chat.router)


@app.exception_handler(PyMongoError)
async def _mongo_unavailable(request: Request, err: PyMongoError):
    """
    Turns any unhandled MongoDB failure into a 503 the UI can show, instead of
    a bare 500. Registered app-wide rather than caught per route because every
    authenticated route touches Mongo — one handler covers all of them, and
    covers routes added later for free. (POST /api/chat never reaches here:
    its errors are already relayed as SSE `error` events mid-stream.)
    """
    print(f"MongoDB error on {request.url.path}: {err.__class__.__name__}: {err}")
    return JSONResponse(
        status_code=503,
        content={"detail": "Can't reach the database. Is MongoDB running? See README.md."},
    )


@app.middleware("http")
async def limit_body_size(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_BODY_BYTES:
        return JSONResponse(status_code=413, content={"error": "Request body too large."})
    return await call_next(request)
