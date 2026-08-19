"""
Turns text into vectors with `microsoft/harrier-oss-v1-270m`, running locally
on CPU through onnxruntime.

Why a local model rather than an embedding API: this runs on the critical
path of indexing every turn for every user, an embedding API would put a
third-party dependency and a per-request cost there, and Harrier is small
enough not to need one — 270M parameters, 640 dimensions, 94 languages
(Vietnamese included, which matters: this app already handles Vietnamese
spreadsheets and gold-price queries end to end), MIT licensed, with a
published ONNX build so nothing here pulls in torch.

Why it can't just call the model inline: the routes that do real work in
server/routes/ are `async def` on one event loop, and that is the whole
basis of this app serving many users at once. A forward pass is CPU-bound
and would stall *every* concurrent request for its duration. So the encode
goes through `asyncio.to_thread` — onnxruntime releases the GIL during
inference, so the loop keeps running — behind a semaphore that bounds how
many passes happen at once. The one-time `_load()` is bounded by the lock
instead, not by that semaphore, so a cold load can overlap with in-flight
encodes; main.py's lifespan warms it at startup for that reason.

Three model-specific details are load-bearing and easy to get wrong:

1. Queries and documents are encoded *differently*. Harrier expects a
   task-instruction prefix on a query and none on a document. Encode both the
   same way and retrieval quality drops with no error to tell you, which is
   why `encode()` makes `is_query` a required keyword rather than inferring it.
2. Pooling is last-token, not mean. Harrier is decoder-only (a Gemma3 text
   backbone), so the final position holds the sentence representation.
3. The vector is L2-normalized on the way out, which is what lets store.py
   score with a plain dot product instead of a full cosine.
"""

import asyncio
import os
from pathlib import Path
from typing import Any

import numpy as np

# The model this file loads. Stored on every unit as `embedModel` so a future
# encoder swap is a backfill rather than silent quality rot (see types.py).
MODEL_ID = "onnx-community/harrier-oss-v1-270m-ONNX"
EMBED_MODEL_NAME = "harrier-oss-v1-270m"
EMBED_DIM = 640

# Copied verbatim from the source model's config_sentence_transformers.json
# ("web_search_query" prompt, microsoft/harrier-oss-v1-270m). Do not paraphrase
# it — the model was trained against this exact wording, and a reworded
# instruction degrades retrieval without failing.
QUERY_INSTRUCTION = (
    "Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery: "
)

# Harrier accepts 32k tokens, but a chat turn or sheet window is nowhere near
# that and a long CPU forward pass is pure latency. Anything longer is
# truncated; extract.py already splits large inputs into windows.
MAX_TOKENS = 1024

# From the model's tokenizer_config.json.
PAD_TOKEN_ID = 0
EOS_TOKEN_ID = 1

_ONNX_FILE = os.environ.get("MEMORY_ONNX_FILE", "onnx/model_quantized.onnx")
_MODEL_DIR = os.environ.get("MEMORY_MODEL_DIR", ".cache/harrier")
_CONCURRENCY = int(os.environ.get("MEMORY_ENCODER_CONCURRENCY", "4"))

# Built on first use, not at import time — the same reasoning as
# providers/openai_provider.py's lazily-constructed client, plus two more:
# loading the weights costs a few hundred MB of RSS per worker process, and
# they may need downloading first. A server that only ever serves chat should
# not pay either cost. The lock stops concurrent first-requests from building
# it twice.
_session: Any = None
_tokenizer: Any = None
_lock = asyncio.Lock()

# Bounds concurrent forward passes per worker process. Each one holds a thread
# from asyncio's default executor for the duration.
_semaphore = asyncio.Semaphore(_CONCURRENCY)


class EncoderUnavailable(RuntimeError):
    """Raised when the model can't be loaded. Callers treat encoding as best-effort and store `vec: None`."""


async def _load() -> tuple[Any, Any]:
    """
    Input: none. Output: (onnxruntime session, tokenizer), constructed once
    per process. Downloads the weights on first call — a few hundred MB, so
    the first indexing job after a fresh checkout is slow and later ones are not.
    """
    global _session, _tokenizer
    if _session is not None and _tokenizer is not None:
        return _session, _tokenizer

    async with _lock:
        if _session is not None and _tokenizer is not None:
            return _session, _tokenizer
        try:
            _session, _tokenizer = await asyncio.to_thread(_load_blocking)
        except Exception as err:  # noqa: BLE001 - surfaced as EncoderUnavailable, never as a 500
            raise EncoderUnavailable(f"could not load {MODEL_ID}: {err}") from err
        return _session, _tokenizer


def _load_blocking() -> tuple[Any, Any]:
    """Input: none. Output: the same pair, built synchronously. Runs in a worker thread, never on the event loop."""
    import onnxruntime as ort
    from huggingface_hub import snapshot_download
    from tokenizers import Tokenizer

    # An ONNX model over 2GB keeps its weights in a sibling `.onnx_data` file
    # that onnxruntime resolves by relative path, so both have to be fetched.
    local_dir = snapshot_download(
        repo_id=MODEL_ID,
        local_dir=_MODEL_DIR,
        allow_patterns=[_ONNX_FILE, f"{_ONNX_FILE}_data", "tokenizer.json", "config.json"],
    )

    tokenizer = Tokenizer.from_file(str(Path(local_dir) / "tokenizer.json"))
    tokenizer.enable_truncation(max_length=MAX_TOKENS)

    options = ort.SessionOptions()
    # Without this each uvicorn worker spawns a thread pool sized to every core,
    # so `--workers 4` oversubscribes the CPU by 4x and every request gets
    # slower. Encoding is already parallel across requests via the semaphore.
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(
        str(Path(local_dir) / _ONNX_FILE),
        sess_options=options,
        providers=["CPUExecutionProvider"],
    )
    return session, tokenizer


def _build_feeds(session: Any, input_ids: np.ndarray, attention_mask: np.ndarray) -> dict[str, np.ndarray]:
    """
    Input: the session and a padded batch. Output: the feed dict it declares.

    Built by introspecting `session.get_inputs()` rather than assuming a fixed
    signature, because ONNX exports of a decoder model vary: some take
    `position_ids`, some declare empty `past_key_values.*` cache inputs. An
    input this doesn't recognize raises instead of being fed zeros — a wrong
    guess there produces silently wrong embeddings, which is worse than a
    startup failure.
    """
    batch, length = input_ids.shape
    feeds: dict[str, np.ndarray] = {}

    for spec in session.get_inputs():
        name = spec.name
        if name == "input_ids":
            feeds[name] = input_ids
        elif name == "attention_mask":
            feeds[name] = attention_mask
        elif name == "position_ids":
            # Position of each token among the non-padding ones before it.
            feeds[name] = np.maximum(np.cumsum(attention_mask, axis=1) - 1, 0).astype(np.int64)
        elif name.startswith("past_key_values"):
            # No cache on a single forward pass: feed a zero-length tensor of
            # the declared shape, substituting 0 for the sequence dimension and
            # the real batch size for the symbolic one.
            shape = [batch if isinstance(d, str) and "batch" in d.lower() else d for d in spec.shape]
            shape = [0 if isinstance(d, str) else d for d in shape]
            feeds[name] = np.zeros(shape, dtype=np.float32)
        else:
            raise EncoderUnavailable(
                f"{MODEL_ID} declares an ONNX input this encoder does not know how to fill: {name!r}"
            )
    return feeds


def _encode_blocking(session: Any, tokenizer: Any, texts: list[str]) -> np.ndarray:
    """
    Input: a batch of already-prefixed strings. Output: an (n, 640) float32
    array of L2-normalized embeddings. Runs in a worker thread.
    """
    encodings = tokenizer.encode_batch(texts)
    rows = []
    for encoding in encodings:
        ids = list(encoding.ids)
        # Last-token pooling reads the final position, and the model was trained
        # with an EOS there. The raw `tokenizers` library doesn't apply
        # tokenizer_config.json's `add_eos_token`, so add it when it's absent
        # rather than pooling over whatever content token happens to be last.
        if not ids or ids[-1] != EOS_TOKEN_ID:
            ids.append(EOS_TOKEN_ID)
        rows.append(ids[:MAX_TOKENS])

    width = max(len(r) for r in rows)
    input_ids = np.full((len(rows), width), PAD_TOKEN_ID, dtype=np.int64)
    attention_mask = np.zeros((len(rows), width), dtype=np.int64)
    for i, r in enumerate(rows):
        input_ids[i, : len(r)] = r
        attention_mask[i, : len(r)] = 1

    outputs = session.run(None, _build_feeds(session, input_ids, attention_mask))
    names = [o.name for o in session.get_outputs()]
    hidden = outputs[names.index("last_hidden_state")] if "last_hidden_state" in names else outputs[0]

    if hidden.ndim == 3:
        # (batch, seq, hidden) -> take each row's last non-padding position.
        last = attention_mask.sum(axis=1) - 1
        hidden = hidden[np.arange(hidden.shape[0]), last]

    hidden = hidden.astype(np.float32)
    norms = np.linalg.norm(hidden, axis=1, keepdims=True)
    # A zero vector would divide by zero; it can only come from a degenerate
    # input, and leaving it unnormalized just makes it score 0 against everything.
    return hidden / np.maximum(norms, 1e-12)


async def encode(texts: list[str], *, is_query: bool) -> list[bytes]:
    """
    Input: the texts to embed, and whether they are queries or documents —
    Harrier prefixes a task instruction onto queries only, and getting this
    wrong quietly costs retrieval quality.
    Output: one packed float32 vector per input text, ready to store as BSON
    Binary or to score against one.

    Raises EncoderUnavailable if the model can't be loaded. Callers treat that
    as a degraded mode (store the unit without a vector, skip the dense view),
    never as a request failure.
    """
    if not texts:
        return []

    session, tokenizer = await _load()
    prepared = [QUERY_INSTRUCTION + t for t in texts] if is_query else list(texts)

    async with _semaphore:
        matrix = await asyncio.to_thread(_encode_blocking, session, tokenizer, prepared)
    return [row.tobytes() for row in matrix]


async def encode_one(text: str, *, is_query: bool) -> bytes:
    """Input: a single text. Output: its packed vector. Convenience wrapper over encode()."""
    vectors = await encode([text], is_query=is_query)
    return vectors[0]


def to_array(vec: bytes) -> np.ndarray:
    """Input: a packed vector as stored. Output: it as a float32 numpy array."""
    return np.frombuffer(vec, dtype=np.float32)
