"""
The push path: retrieval that happens because the user sent a message, not
because the model asked for it.

Until now this app only had the pull path — `search_memory`, a tool the model
may call if it decides it needs to. That leaves a real gap, and it isn't the
model being lazy: to know it should search, the model has to first notice that
something is missing, and the whole problem with a truncated history is that
what's missing leaves no trace in what remains. A question that needed the
warehouse study from three weeks ago looks, from inside a three-message window,
exactly like a question that didn't.

So retrieval also runs unprompted, before the provider does, on every turn that
looks like it has a subject. Both paths run the same `search()` — same three
views, same fusion, same evidence closure — and both write the same
`retrieval_record()` to `agent_logs`, distinguished only by `source`. This file
is the entry point, not a second retrieval implementation.

Three rules it follows, each answering a way this could make the product worse
rather than better:

**Never cost a turn that has nothing to gain.** The gate (profile.py) skips
greetings and acknowledgements before any encoding happens, because ~150ms in
front of "thanks" is the most noticeable latency in the app.

**Never fail the turn.** Everything is caught and time-boxed. The worst outcome
available here is that the model answers from the last three messages — which
is exactly what it did before this file existed, so the total failure mode of
the push path is the previous product.

**Never quietly pretend to be the user.** Evidence is appended to the last user
message as a clearly-marked block, not smuggled in as though the user typed it.
That placement is also what keeps a future prompt-caching change viable
(DISCUSS_RAG.md §10): evidence changes every turn, so it has to sit *behind*
everything stable, or it invalidates the whole cached prefix each time.
"""

import asyncio
import os
from typing import Any

from server.memory.profile import build_query, profile
from server.memory.retrieve import search
from server.memory.tool import format_hits, retrieval_record
from server.memory.types import Scope
from server.types import ChatRequestMessage

# Half the tool's default. This lands in *every* qualifying turn whether or not
# it turns out to be needed, while the tool's ten arrive only when the model
# decided it wanted them — so the automatic one has to be the cheaper of the two.
DEFAULT_CONTEXT_LIMIT = 5
MAX_CONTEXT_LIMIT = 20

# Hard ceiling on the injected block. A `sheet` hit is a hundred-row markdown
# table, and the point of retrieval is to spend *less* context than replaying
# the whole history would have.
MAX_EVIDENCE_CHARS = 3000

# Retrieval sits in front of a user who is waiting. Past this, answering from
# the visible history beats answering late. A warm search is ~145ms, so this is
# not a tight budget — but note what it does *not* cover: the encoder's one-time
# model load is ~4s, which would eat almost all of this on the first turn after
# a boot. That's why main.py warms the encoder at startup rather than letting
# the first user discover it.
TIMEOUT_SECONDS = 5.0

# How the block introduces itself to the model. Mirrors the framing lib/api.ts
# already uses for replayed visuals ("[Previously rendered chart: …]") — a
# bracketed, obviously-machine-inserted preamble the model reads as context
# rather than as something the user typed.
EVIDENCE_HEADER = (
    "[Recalled from memory — earlier turns and other conversations, retrieved automatically "
    "for this message. It may or may not be relevant: use what applies, ignore what doesn't, "
    "and cite the conversation and turn when you use something.]"
)


def _limit() -> int:
    """
    Input: none. Output: how many units the automatic path retrieves.

    Read at call time, clamped, and falling back rather than raising on a typo —
    same convention as tool.py's `_limit()`, for the same reason: this runs
    mid-turn and an unparseable environment variable must not be able to end it.
    """
    try:
        return max(1, min(MAX_CONTEXT_LIMIT, int(os.environ.get("MEMORY_CONTEXT_LIMIT", DEFAULT_CONTEXT_LIMIT))))
    except ValueError:
        return DEFAULT_CONTEXT_LIMIT


def _truncate_evidence(text: str) -> str:
    """Input: the formatted block. Output: it, cut to MAX_EVIDENCE_CHARS at a line boundary."""
    if len(text) <= MAX_EVIDENCE_CHARS:
        return text
    # Cut at the last newline inside the budget so a hit is dropped whole rather
    # than left as half a markdown table the model has to guess the end of.
    clipped = text[:MAX_EVIDENCE_CHARS]
    boundary = clipped.rfind("\n")
    return (clipped[:boundary] if boundary > 0 else clipped).rstrip() + "\n[…truncated]"


async def select_context(
    scope: Scope,
    messages: list[ChatRequestMessage],
) -> tuple[str | None, dict[str, Any] | None]:
    """
    Input: whose memory to search (from the session cookie, never from the
    model) and the conversation as the client sent it.
    Output: (evidence block to append to the last user message, log record) —
    either may be None.

    The two strings this works with are different on purpose, and mixing them up
    is the one way to break this quietly:

    - the **gate** reads only `messages[-1]`, so a "hi" that follows a long
      assistant reply still gates out;
    - the **query** is the last *two* messages (profile.build_query), because a
      bare follow-up carries its subject in the message before it.

    A skipped turn still returns a record, with `status: "skipped"` and no hits.
    That costs one small document and buys the only evidence there is for
    whether the gate is drawing the line in the right place — "it retrieved
    nothing" and "it never ran" look identical in a log that omits the skip.
    Nothing is yielded to the UI for a skip, so the user sees no difference.
    """
    if not messages:
        return None, None

    query_profile = profile(messages[-1].text)
    if query_profile.is_trivial:
        return None, retrieval_record(query_profile.text, [], status="skipped", source="auto")

    query = build_query(messages)
    if not query.strip():
        return None, None

    try:
        async with asyncio.timeout(TIMEOUT_SECONDS):
            hits = await search(scope, query, k=_limit())
    except TimeoutError:
        print(f"memory: auto-retrieval timed out after {TIMEOUT_SECONDS}s")
        return None, retrieval_record(query, [], status="timeout", source="auto")
    except Exception as err:  # noqa: BLE001 - the turn must survive a broken memory layer
        print(f"memory: auto-retrieval failed ({err.__class__.__name__}: {err})")
        return None, retrieval_record(query, [], status=f"unavailable: {err.__class__.__name__}", source="auto")

    record = retrieval_record(query, hits, source="auto")
    if not hits:
        # `format_hits` would return the "no matches" sentence here, which is
        # written for a tool result. Injecting it into the prompt would tell the
        # model a search failed when it never asked for one.
        return None, record

    return f"{EVIDENCE_HEADER}\n{_truncate_evidence(format_hits(hits))}", record


def _demo() -> None:
    """Self-check for the pure parts, run with `python -m server.memory.context`."""
    assert _limit() == DEFAULT_CONTEXT_LIMIT

    os.environ["MEMORY_CONTEXT_LIMIT"] = "3"
    assert _limit() == 3
    os.environ["MEMORY_CONTEXT_LIMIT"] = "999"
    assert _limit() == MAX_CONTEXT_LIMIT, "an unclamped limit would push 999 units into a live turn"
    os.environ["MEMORY_CONTEXT_LIMIT"] = "not a number"
    assert _limit() == DEFAULT_CONTEXT_LIMIT, "a typo must not raise mid-turn"
    del os.environ["MEMORY_CONTEXT_LIMIT"]

    short = "a\nb\nc"
    assert _truncate_evidence(short) == short

    long_block = "\n".join(f"line {i} " + "x" * 100 for i in range(200))
    cut = _truncate_evidence(long_block)
    assert len(cut) <= MAX_EVIDENCE_CHARS + len("\n[…truncated]"), len(cut)
    assert cut.endswith("[…truncated]")
    # Cut on a line boundary: the last kept line must be a whole one.
    assert cut.splitlines()[-2].endswith("x"), cut.splitlines()[-2][-40:]

    print("memory.context: all checks passed")


if __name__ == "__main__":
    _demo()
