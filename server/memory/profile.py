"""
φ(q) — the cheap, deterministic read of a query that happens before any
retrieval work. Two jobs, and they run on *different strings*, which is the one
thing to get right in this file.

**The gate** (`profile().is_trivial`) decides whether to retrieve at all. It
reads only the message the user just sent. Auto-retrieval costs an encoder
forward pass plus a vector scan — ~130–160ms in front of a user who is waiting —
and "hi", "thanks" and "yes" have nothing to retrieve *for*. Skipping them is
free latency on the turns where latency is most obvious, because a one-word
message is one the user expects an instant answer to.

**The query** (`build_query()`) is what actually gets encoded, and it is the
last *two* messages. On its own, "change the colour to blue" retrieves colour
theory: the message before it is what names the thing being recoloured. This is
the same problem evidence closure solves on the result side (store.py's
`local_neighbours`), applied to the question instead of the answer.

Reading both for the gate would defeat it — a "hi" that follows a long assistant
reply would look substantial and retrieve every time. Reading only the current
message for the query would lose the subject of every follow-up. So: gate on
one, search on two.

The rest of the profile — extracted entities, whether a time cue is present — is
consumed by the graph view and is where Zero-Mem's `Route(q)` would attach if
there were ever an evaluation set to tune weights against (DISCUSS_RAG.md §16).
Today the weights stay constant, deliberately: a binary skip decision and a
continuous weighting are different risk classes, and only one of them can be
made honestly without measurement.
"""

import re
from dataclasses import dataclass, field

from server.memory.entities import extract_entities
from server.types import ChatRequestMessage

# The assistant's half of the query. Capped because a long reply would dominate
# the embedding of a short follow-up — the previous message is context for the
# question, not the question. In this app the cap rarely binds: a turn that drew
# something replies "Updated the diagram — see canvas" and little else, which is
# exactly why including it is cheap.
MAX_CONTEXT_CHARS = 400

# Below this many characters of real content, there is nothing to search for.
MIN_QUERY_CHARS = 8

# Messages that are pure social protocol. Matched whole (after stripping
# punctuation), never as substrings — "thanks, now show me Q3 revenue" is a
# real query that happens to open with a pleasantry.
_TRIVIAL = frozenset(
    """
    hi hey hello yo sup ok okay k sure yes yep yeah no nope nah thanks thank ty
    thankyou cheers cool nice great perfect awesome done stop wait continue go
    chào xin ok oke vâng dạ ừ ờ có không cảm ơn cám ơn tốt được rồi tiếp
    """.split()
)

# Anything that points at a moment rather than a topic. Used by the graph view
# and reported in the log so a ranking that ignored an explicit date is visible.
_TIME_CUE_RE = re.compile(
    r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}|q[1-4]|yesterday|today|"
    r"last (?:week|month|year|time)|earlier|before|ago|previous|recent|"
    r"hôm qua|hôm nay|tuần trước|tháng trước|năm ngoái|trước đó|vừa rồi|lúc nãy)\b",
    re.IGNORECASE,
)

_WORD_RE = re.compile(r"\w+", re.UNICODE)


@dataclass(frozen=True)
class QueryProfile:
    """
    What a cheap look at the user's message tells us before any retrieval runs.

    `is_trivial` is the only field that changes control flow today; the others
    are inputs to the graph view and to the retrieval log, where they are the
    evidence for tuning any of this later.
    """

    text: str
    terms: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    has_time_cue: bool = False
    is_trivial: bool = True


def profile(text: str) -> QueryProfile:
    """
    Input: the message the user just sent — not the conversation, just that one
    message.
    Output: its profile, whose `is_trivial` is the retrieval gate.

    A message is trivial when it is too short to carry a subject, or when every
    word in it is social protocol. Both halves are needed: "ok" is caught by the
    length rule, "thanks a lot" only by the vocabulary rule.
    """
    text = (text or "").strip()
    words = _WORD_RE.findall(text)
    lowered = [w.casefold() for w in words]

    if not words:
        trivial = True
    else:
        trivial = len(text) < MIN_QUERY_CHARS or all(word in _TRIVIAL for word in lowered)

    return QueryProfile(
        text=text,
        terms=lowered,
        entities=extract_entities(text),
        has_time_cue=bool(_TIME_CUE_RE.search(text)),
        is_trivial=trivial,
    )


def build_query(messages: list[ChatRequestMessage]) -> str:
    """
    Input: the conversation as sent by the client.
    Output: the text to retrieve with — the last two messages joined, the
    earlier one truncated.

    Deliberately not "the last user message". See the module docstring: a bare
    follow-up carries its subject in the message before it, and that message is
    usually the assistant's. Empty messages are skipped rather than joined as
    blank lines, so a turn whose reply was pure tool output (the drawn diagram
    goes to canvas, not into `text`) doesn't pad the query with nothing.
    """
    if not messages:
        return ""

    parts: list[str] = []
    for message in messages[-2:-1]:
        earlier = (message.text or "").strip()
        if earlier:
            parts.append(earlier[:MAX_CONTEXT_CHARS])
    current = (messages[-1].text or "").strip()
    if current:
        parts.append(current)

    return "\n".join(parts)


def _demo() -> None:
    """Self-check, run with `python -m server.memory.profile`."""

    def msg(role: str, text: str) -> ChatRequestMessage:
        return ChatRequestMessage(role=role, text=text)  # type: ignore[arg-type]

    # The gate: social protocol and one-word messages skip retrieval.
    for trivial in ["hi", "ok", "thanks", "Thanks!", "yes", "cảm ơn", "vâng", "", "   "]:
        assert profile(trivial).is_trivial, trivial

    # Real questions do not, in either language.
    for real in [
        "what were the pallet numbers in the warehouse study",
        "change the colour of the blocks to blue",
        "giá vàng SJC hôm qua là bao nhiêu",
        "thanks, now show me Q3 revenue",  # opens with a pleasantry, still a query
    ]:
        assert not profile(real).is_trivial, real

    # Time cues are detected in both languages.
    assert profile("what did we say last week").has_time_cue
    assert profile("giá vàng hôm qua").has_time_cue
    assert not profile("draw a workflow for onboarding").has_time_cue

    # The query is built from two messages; the gate is not. This pairing is the
    # whole point of the file, so both directions get pinned.
    long_reply = "Updated the diagram — see canvas. " * 40
    conversation = [msg("user", "draw the warehouse workflow"), msg("assistant", long_reply), msg("user", "hi")]
    assert profile(conversation[-1].text).is_trivial, "a greeting after a long reply must still gate out"

    follow_up = [msg("user", "draw the warehouse workflow"), msg("assistant", "Updated the diagram — see canvas"), msg("user", "change the colour to blue")]
    query = build_query(follow_up)
    assert "change the colour to blue" in query, query
    assert "Updated the diagram" in query, query
    assert "warehouse workflow" not in query, "only the last two messages, not three"

    # The earlier half is capped so it cannot swamp the current question.
    capped = build_query([msg("assistant", long_reply), msg("user", "and the totals?")])
    assert len(capped) <= MAX_CONTEXT_CHARS + len("and the totals?") + 1, len(capped)
    assert capped.endswith("and the totals?"), capped

    # A reply that was pure tool output has no text to contribute.
    assert build_query([msg("assistant", ""), msg("user", "what about Q4")]) == "what about Q4"
    assert build_query([msg("user", "only one")]) == "only one"
    assert build_query([]) == ""

    print("memory.profile: all checks passed")


if __name__ == "__main__":
    _demo()
