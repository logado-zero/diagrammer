"""
Mermaid syntax rules that more than one module has to agree on.

`end` is the case that forced this file to exist: it is a reserved Mermaid
keyword (it closes subgraph blocks), so a node called `end` breaks parsing
wherever it appears. subagents.py rewrites it in the generated source and
agent.py has to apply the identical rewrite to the per-node color map, or the
colors land on ids that no longer exist. Both had their own copy of the rule,
the second commented as "does the same rewrite as the first".

Import-free on purpose beyond the standard library, same as tools.py: this is
a leaf module every layer can use.
"""

import re
from typing import Any

# A node id only becomes a color if it's a bare Mermaid identifier. The client
# turns each entry into a `class <id> ncolorN` statement, and Mermaid's parser
# rejects anything else outright — `class "weird id" ncolor0` is a parse error
# that takes the entire diagram down with it, not a skipped line (measured; an
# id that simply doesn't exist in the graph is the harmless case, it renders
# fine and is ignored).
SAFE_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")

_UNSAFE_ID_CHARS_RE = re.compile(r"[^A-Za-z0-9_]")
_BARE_END_RE = re.compile(r"\bend\b")

# What a node id or class name called `end` becomes. One spelling, because
# agent.py's color map and subagents.py's generated source have to agree on it.
RESERVED_END = "end"
RESERVED_END_REPLACEMENT = "endNode"

# The "end" kind's class name, for the same reason.
CLASS_NAME = {RESERVED_END: RESERVED_END_REPLACEMENT}


def rename_reserved_id(node_id: str) -> str:
    """Input: a node id from the intent. Output: it, or `endNode` if it was the reserved `end`."""
    return RESERVED_END_REPLACEMENT if node_id == RESERVED_END else node_id


def safe_id(raw: Any) -> str:
    """Input: a node id from the intent. Output: a safe Mermaid node identifier (alphanumeric/underscore, starting with a letter)."""
    safe = _UNSAFE_ID_CHARS_RE.sub("_", str(raw))
    return safe if safe and safe[0].isalpha() else f"n_{safe}"


def sanitize_text(text: Any) -> str:
    """Strips characters known to silently break Mermaid parsing (see PROGRESS.md's semicolon gotcha) from label/edge text."""
    return re.sub(r'[;|`"]', "", str(text)).strip()


def rename_reserved_end(mermaid: str) -> str:
    """
    Deterministic backstop for the `end`-reserved-keyword gotcha (see PROGRESS.md): `end` breaks
    Mermaid parsing wherever it's used bare, as a node id or a class name, not just as a class
    name. A prompt instruction alone isn't reliable here — the subagent is also told to preserve
    the intent's node ids exactly, and the primary model sometimes names its end node "end", so
    the two instructions collide. Runs on every emit_mermaid_flowchart output (LLM or fallback)
    since there's no server-side flowchart validator to catch a slip before it reaches the client.
    Renames every bare `end` token to `endNode`, skipping quoted label text so a legitimate label
    containing the word "end" (e.g. "Weekend report") is left alone.
    """
    out_lines = []
    for line in mermaid.splitlines():
        parts = re.split(r'("[^"]*")', line)
        for i in range(0, len(parts), 2):  # even indices are outside quoted spans
            parts[i] = _BARE_END_RE.sub(RESERVED_END_REPLACEMENT, parts[i])
        out_lines.append("".join(parts))
    return "\n".join(out_lines)


def _demo() -> None:
    """Self-check: python -m server.mermaid"""
    assert safe_id("a b!") == "a_b_"
    assert safe_id("1st") == "n_1st"
    assert sanitize_text('a;b|c`d"e') == "abcde"

    assert rename_reserved_id("end") == "endNode"
    assert rename_reserved_id("start") == "start"

    # The rename must reach bare tokens and leave quoted labels alone — the
    # "Weekend report" case is the whole reason it splits on quotes.
    assert rename_reserved_end("A --> end") == "A --> endNode"
    assert rename_reserved_end('end["Weekend report"]') == 'endNode["Weekend report"]'
    assert rename_reserved_end('X["the end"]') == 'X["the end"]'

    # agent.py's color map and subagents.py's source must land on the same id.
    assert rename_reserved_id("end") == rename_reserved_end("end")

    print("mermaid: all checks passed")


if __name__ == "__main__":
    _demo()
