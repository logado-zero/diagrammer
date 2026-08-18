"""
Hybrid retrieval over stored units. No LLM call, no LLM token — the whole
point of the Zero-Mem design this follows is that finding evidence is
arithmetic, not generation, and only the final answer needs a model.

Three views run:

- **dense** (encoder.py + store.dense) catches paraphrase and cross-language
  matches — "how much did we earn" finding a unit that says "revenue".
- **lexical** (Mongo `$text`) catches the things embeddings are worst at:
  exact names, dates, numbers, product codes, quoted phrases.
- **graph** (graph.py) catches what neither can: units that share no words and
  no meaning with the query, but are connected to it through an entity two hops
  away. That is the only cross-conversation, multi-hop signal in the system.

They are combined the way Zero-Mem combines its own views (Eq. 12–13):
normalize each view's scores into 0..1 on its own, then take a weighted sum.
Normalizing per view is what makes the sum meaningful at all — a `$text` score,
a cosine similarity and a PageRank mass are not on the same scale, and adding
them raw would just let whichever view happens to produce bigger numbers decide
the ranking.

After fusion comes **evidence closure**: the units immediately before and after
the best hits are pulled in even though they matched nothing themselves. A hit
is a sentence, and some sentences have their subject in the one before them —
"change the colour to blue" is the standing example.

**What is still a seam rather than a feature.** `_fuse()` takes a list of named
views, so a fourth is one more entry. `weights` is a parameter, so the paper's
query-dependent `Route(q)` is a change to what gets passed in, not to anything
below — and it stays a fixed constant until there is an evaluation set to tune
it against, because a weight tuned by intuition is worse than an honest default.

(Named `retrieve` rather than `search` because the package exports a `search()`
function; a submodule of the same name would shadow it on the package object,
which is exactly the bug the self-check caught the first time round.)
"""

import os
from dataclasses import replace
from typing import Any

from server.memory import encoder, graph, store
from server.memory.encoder import EncoderUnavailable
from server.memory.entities import extract_entities
from server.memory.types import Scope, SearchHit

# How many candidates each view contributes before fusion. Wider than the final
# `k` on purpose: a unit that ranks 8th in one view and 2nd in the other should
# still be able to win overall, which can't happen if each view only offers its
# own top few.
CANDIDATES_PER_VIEW = 40

# Fixed for now. Zero-Mem derives these per query from a lightweight profile
# (`Route(q)`), which is worth doing only once there's an evaluation set to
# tune it against — see DISCUSS_RAG.md. A constant that's honest about being a
# constant beats a tuned-looking number copied from someone else's benchmark.
#
# The graph's share is the smallest of the three deliberately: it is the newest
# view, the one whose quality depends on a backfill having run, and the one
# whose contribution is most visible in the logs (`viewScores` records it per
# hit), so it is also the easiest to raise later on evidence.
DEFAULT_WEIGHTS: dict[str, float] = {"dense": 0.5, "lexical": 0.3, "graph": 0.2}

# Evidence closure. Only the very top hits get their neighbours pulled — a
# neighbour of a mediocre match is noise, and every added unit costs context in
# a turn the user is waiting on.
CLOSURE_FOR_TOP = 3
MAX_CLOSURE_UNITS = 4


def _normalize(scored: list[tuple[dict[str, Any], float]]) -> dict[Any, tuple[dict[str, Any], float]]:
    """
    Input: one view's (document, raw score) pairs.
    Output: the same, keyed by unit id, with scores min-max scaled to 0..1.

    When every candidate scored the same there is no spread to preserve, so
    they all become 1.0 — the view is saying "these all match equally", and
    scaling them to 0 would silently delete its entire opinion.
    """
    if not scored:
        return {}
    values = [s for _, s in scored]
    low, high = min(values), max(values)
    span = high - low
    return {
        doc["_id"]: (doc, 1.0 if span <= 0 else (score - low) / span)
        for doc, score in scored
    }


def _fuse(
    views: list[tuple[str, list[tuple[dict[str, Any], float]]]],
    weights: dict[str, float],
    limit: int,
) -> list[SearchHit]:
    """
    Input: each view's name and its raw results, the weight per view, and how
    many hits to return.
    Output: the merged ranking, best first.

    A unit found by both views scores the sum of both contributions, so
    agreement between views is what pushes something to the top — which is the
    behaviour that makes hybrid retrieval better than either half.
    """
    totals: dict[Any, float] = {}
    per_view: dict[Any, dict[str, float]] = {}
    documents: dict[Any, dict[str, Any]] = {}

    for name, results in views:
        weight = weights.get(name, 0.0)
        for unit_id, (doc, score) in _normalize(results).items():
            totals[unit_id] = totals.get(unit_id, 0.0) + weight * score
            per_view.setdefault(unit_id, {})[name] = round(score, 4)
            documents[unit_id] = doc

    ranked = sorted(totals.items(), key=lambda pair: pair[1], reverse=True)[:limit]
    return [_to_hit(documents[unit_id], unit_id, totals[unit_id], per_view[unit_id]) for unit_id, _ in ranked]


def _to_hit(
    doc: dict[str, Any],
    unit_id: Any,
    total: float,
    view_scores: dict[str, float],
    is_context: bool = False,
) -> SearchHit:
    """
    Input: a unit document and its fused score. Output: the SearchHit for it.

    `seq`, `role` and `at` are read straight off the document — store.py has
    always projected them, they just had nowhere to go until the tool needed to
    tell the model *when* in a conversation something was said.
    """
    return SearchHit(
        unit_id=unit_id,
        kind=doc.get("kind", "turn"),
        text=doc.get("text", ""),
        score=round(total, 4),
        view_scores=view_scores,
        payload=doc.get("payload"),
        conversation_id=doc.get("conversationId"),
        seq=doc.get("seq", 0),
        role=doc.get("role", "user"),
        at=doc.get("at"),
        is_context=is_context,
    )


async def _close_evidence(scope: Scope, hits: list[SearchHit]) -> list[SearchHit]:
    """
    Input: a scope and the ranked hits. Output: extra hits for the units
    adjacent to the best of them, flagged as context.

    Zero-Mem's evidence closure. A retrieved unit is one message out of a
    conversation, and a message often means nothing without the one before it:
    "change the colour to blue" is a perfect dense match for a query about
    colours and completely useless on its own. Its neighbour names the diagram.

    These carry `is_context=True` and score 0 — they did not match anything and
    must not be presented as though they did. They are appended after the real
    hits rather than ranked among them, so nothing about the ranking changes.
    """
    already = {hit.unit_id for hit in hits}
    keys = [(hit.conversation_id, hit.seq) for hit in hits[:CLOSURE_FOR_TOP] if hit.conversation_id is not None]
    if not keys:
        return []

    extra: list[SearchHit] = []
    for doc in await store.local_neighbours(scope, keys):
        if doc["_id"] in already:
            continue
        already.add(doc["_id"])
        extra.append(_to_hit(doc, doc["_id"], 0.0, {}, is_context=True))
        if len(extra) >= MAX_CLOSURE_UNITS:
            break
    return extra


async def search(
    scope: Scope,
    query: str,
    *,
    k: int = 8,
    weights: dict[str, float] | None = None,
) -> list[SearchHit]:
    """
    Input: whose memory to search (and optionally which conversation), the
    query text, how many hits to return, and optionally how much to trust each
    view.
    Output: up to `k` matching units, best first, followed by up to
    MAX_CLOSURE_UNITS neighbouring units flagged `is_context` — each carrying
    the title and turn number of the conversation it came from.

    `weights` is the Zero-Mem `Route(q)` seam. Passing None uses the fixed
    DEFAULT_WEIGHTS; computing them per query from the profile in profile.py (a
    question full of proper nouns and dates wants lexical, a vague one wants
    dense) is a change to what goes *into* this function, not to the scoring
    below it, and waits on an evaluation set.

    **Every view is allowed to fail on its own.** If the encoder is unavailable
    — weights not downloaded, model failed to load — the dense view is skipped.
    If the graph has no edges yet because the backfill hasn't run, the graph
    view returns little or nothing. Either way the others still answer. Memory
    is an enhancement, and a retrieval problem must never become the caller's
    problem.
    """
    if not query.strip():
        return []

    views: list[tuple[str, list[tuple[dict[str, Any], float]]]] = []

    try:
        query_vec = await encoder.encode_one(query, is_query=True)
        views.append(("dense", await store.dense(scope, query_vec, CANDIDATES_PER_VIEW)))
    except EncoderUnavailable as err:
        print(f"memory: dense view unavailable, falling back to lexical only ({err})")

    views.append(("lexical", await store.lexical(scope, query, CANDIDATES_PER_VIEW)))

    if graph_enabled():
        # The same extractor that ran over the unit at ingest runs over the
        # query here — an entity only links the two if identical strings come
        # out of both sides.
        views.append(("graph", await graph.graph_view(scope, extract_entities(query), CANDIDATES_PER_VIEW)))

    hits = _fuse(views, weights or DEFAULT_WEIGHTS, k)
    hits += await _close_evidence(scope, hits)

    # Titles last, and only for the handful that survived ranking — resolving
    # them before fusion would mean a lookup for up to 120 candidates to label 8.
    titles = await store.conversation_titles(scope, [h.conversation_id for h in hits if h.conversation_id])
    return [replace(hit, conversation_title=titles.get(hit.conversation_id)) for hit in hits]


def memory_enabled() -> bool:
    """
    Input: none. Output: whether the memory layer should do anything at all.

    Read at call time rather than import time so tests and the self-check can
    flip it. This is the kill switch: set MEMORY_ENABLED=0 and ingest becomes a
    no-op with the rest of the app completely unaffected.
    """
    return os.environ.get("MEMORY_ENABLED", "1") not in ("0", "false", "False", "")


def graph_enabled() -> bool:
    """
    Input: none. Output: whether the entity-graph view takes part in ranking.

    On by default, unlike DISCUSS_RAG.md §9's original "flag, default off" —
    that plan assumed an evaluation set would arrive before the view did, and it
    didn't. Shipping it dark would mean nothing ever measured it. `MEMORY_GRAPH=0`
    is the way back out, and it removes only the view: entities and edges keep
    being written either way, so turning it back on never needs a backfill.
    """
    return os.environ.get("MEMORY_GRAPH", "1") not in ("0", "false", "False", "")
