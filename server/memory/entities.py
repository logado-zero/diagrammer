"""
What a unit is *about*, as a flat list of strings — the entry points for the
entity-graph view (graph.py) and the seeds a query walks from.

**No NER model, and that is a decision rather than a shortcut.** DISCUSS_RAG.md
§22.1 works through the options: spaCy's `en_core_web_sm` is English-only and
fails outright on the Vietnamese this app is used in, and its multilingual
sibling would be a new dependency, a download, and a forward pass per unit on
the ingest path. What is left is regex plus term extraction — and the thing
worth noticing is that it is *not* a downgrade here. Zero-Mem's entity graph
records observed **co-occurrence**, not inferred semantics: two strings are
linked because they showed up together, so precision matters far less than it
would for a knowledge graph. And what this app's units actually contain —
tickers, currency amounts, dates, quarters, quoted chart titles — is precisely
the category NER models are weakest at and a regex is strongest at.

Two rules keep the graph useful rather than merely large:

**Specific beats frequent.** The families are tried in priority order and the
budget is spent from the top, so a unit full of codes and dates contributes
those, and prose contributes ordinary words only with the room left over. A
graph whose highest-degree node is "chart" tells you nothing — every unit
touches it, so Personalized PageRank spreads evenly and ranks nothing.

**Everything is capped.** Edges are built pairwise within a unit, so entity
count is quadratic in the edge count. `sheet` units are the dangerous case: one
window is a hundred rows of numbers that are individually meaningless, so those
read their header only.

Casefolded on both sides — a query says "sjc" and a document says "SJC", and
they have to meet somewhere.
"""

import re

from server.memory.types import UnitKind

# Per unit. 24 entities is at most 276 co-occurrence pairs, which is a sane
# amount of edge writing for one turn; letting a units's entity list grow
# unbounded is how one attached spreadsheet becomes tens of thousands of edges.
MAX_ENTITIES_PER_UNIT = 24

# A sheet unit's text is a markdown table. Only the first few lines — the label
# and the header row — say what the table is *about*; the rest is data whose
# individual cells are not entities in any useful sense.
SHEET_HEADER_LINES = 4

# Shortest ordinary word kept. Two-letter words are almost entirely function
# words in both languages this app sees, and they would be hub nodes.
MIN_TERM_LEN = 3

# Quoted strings longer than this are sentences, not names.
MAX_QUOTED_LEN = 40

# Ordered by how specific the family is — see the module docstring. The budget
# is spent from the top, so the last family only fills what is left over.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # "Revenue by region" — chart titles, product names, anything the user
    # bothered to quote is something they would search for later.
    ("quoted", re.compile(r"[\"“”']([^\"“”'\n]{2,%d})[\"“”']" % MAX_QUOTED_LEN)),
    # 2026-08-14, 14/08/2026, Q3 2026, 2026-Q3.
    ("date", re.compile(r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}|Q[1-4][ -]?\d{4}|\d{4}[ -]?Q[1-4])\b")),
    # SJC, VN30, USD, GDP — all-caps tokens, which in this app's material are
    # tickers, currencies and metric names.
    ("code", re.compile(r"\b([A-Z][A-Z0-9]{1,9})\b")),
    # 4,820 · 12.5% · $1,200 · 79.5tr — a number is only an entity if it is
    # big enough or marked enough to be memorable. A bare "3" is not.
    ("number", re.compile(r"([$€£₫]\s?\d[\d.,]*|\d[\d.,]*\s?%|\b\d[\d.,]{2,}\b)")),
]

_WORD_RE = re.compile(r"\w+", re.UNICODE)
_HAS_LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)

# Deliberately small. This is not a linguistic stopword list — it is the set of
# words that would otherwise become hub nodes in the co-occurrence graph, in the
# two languages this app is actually used in. Anything more elaborate is tuning
# without an eval set (DISCUSS_RAG.md §2).
_STOPWORDS = frozenset(
    """
    the and for that this with from you your are was were has have had not but
    all can will what when where which who why how any our their them they its
    about into over under more most some such than then there these those out
    one two get got make made use used using see seen show shows please thanks
    thank yes okay sure just like also very much many now new old only same
    được của và cho với những này đó là các một trong khi nếu thì mà nhưng hay
    hoặc không có thể như để từ đến theo về trên dưới bằng vì nên đã đang sẽ rất
    bạn tôi mình chúng nó họ ai gì nào sao vậy hãy nhé ạ à ừ vâng
    """.split()
)


def _clip(text: str, kind: UnitKind) -> str:
    """
    Input: a unit's text and what it was derived from.
    Output: the portion worth extracting entities from.

    Only `sheet` is treated differently, and the reason is in the module
    docstring: the header says what the table is about, the hundred data rows
    below it do not.
    """
    if kind != "sheet":
        return text
    return "\n".join(text.splitlines()[:SHEET_HEADER_LINES])


def extract_entities(text: str, kind: UnitKind = "turn") -> list[str]:
    """
    Input: the text of a unit (or of a query), and the kind of unit it is.
    Output: up to MAX_ENTITIES_PER_UNIT casefolded entity strings, most
    specific first, deduplicated with order preserved.

    The same function runs on both sides — a stored unit at ingest and a user's
    query at retrieval. That is not a convenience: an entity only links a query
    to a document if *identical* strings come out of both, so any asymmetry here
    is a silent recall failure of exactly the kind the encoder's `is_query` flag
    exists to prevent on the dense side.
    """
    text = _clip(text or "", kind)
    if not text.strip():
        return []

    found: dict[str, None] = {}  # dict, not set — insertion order is the priority order

    for _name, pattern in _PATTERNS:
        for match in pattern.findall(text):
            value = match.strip().strip(".,;:").casefold()
            if value:
                found.setdefault(value, None)

    # Ordinary words last, filling whatever budget the specific families left.
    for word in _WORD_RE.findall(text):
        if len(found) >= MAX_ENTITIES_PER_UNIT:
            break
        value = word.casefold()
        if len(value) < MIN_TERM_LEN or value in _STOPWORDS:
            continue
        # Pure digits already had their chance in the "number" family above; a
        # bare "300" as a word is noise.
        if not _HAS_LETTER_RE.search(value):
            continue
        found.setdefault(value, None)

    return list(found)[:MAX_ENTITIES_PER_UNIT]


def _demo() -> None:
    """Self-check, run with `python -m server.memory.entities`."""
    # The specific families win the budget, and casefolding is symmetric.
    ents = extract_entities('SJC sell price hit 79,500 on 2026-08-14 for "Q3 gold review"')
    assert "sjc" in ents, ents
    assert "2026-08-14" in ents, ents
    assert "79,500" in ents, ents
    assert "q3 gold review" in ents, ents

    # Vietnamese has no capitalisation signal to lean on, so the term family is
    # what carries it — and diacritics must survive casefolding intact.
    vi = extract_entities("Giá vàng SJC hôm nay tăng lên 79,5 triệu đồng mỗi lượng")
    assert "sjc" in vi, vi
    assert "vàng" in vi, vi
    assert "triệu" in vi, vi
    # Function words must not become hub nodes.
    assert "của" not in vi and "một" not in vi, vi

    # Stopwords and short words are dropped; the noun survives.
    assert "the" not in extract_entities("the chart of revenue")
    assert "revenue" in extract_entities("the chart of revenue")

    # A query and the document it should match produce overlapping entities —
    # this is the whole mechanism, so it gets an explicit assertion.
    doc = set(extract_entities("Warehouse capacity study: 4,820 pallets across VN30 sites"))
    query = set(extract_entities("how many pallets did the VN30 warehouse study find"))
    assert doc & query >= {"pallets", "vn30"}, doc & query

    # A sheet reads its header only — the data rows below must not contribute.
    sheet = extract_entities(
        "Attached file `q3.csv` (part 1 of 5):\n\n| region | revenue |\n| --- | --- |\n"
        + "\n".join(f"| zone{i} | {i}00,000 |" for i in range(50)),
        kind="sheet",
    )
    assert "region" in sheet and "revenue" in sheet, sheet
    assert not any(e.startswith("zone") for e in sheet), sheet

    # The cap holds even on text that would otherwise blow past it, because the
    # edge count is quadratic in this number.
    many = extract_entities(" ".join(f"alpha{i}" for i in range(200)))
    assert len(many) == MAX_ENTITIES_PER_UNIT, len(many)

    assert extract_entities("") == []
    assert extract_entities("   \n  ") == []
    # No duplicates, ever — an entity repeated in a unit would self-loop in the graph.
    dupes = extract_entities("revenue revenue REVENUE Revenue")
    assert dupes.count("revenue") == 1, dupes

    print("memory.entities: all checks passed")


if __name__ == "__main__":
    _demo()
