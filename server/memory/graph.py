"""
The entity-graph view: the third retrieval signal, alongside dense and lexical.

What it is for, in one example. Dense retrieval finds units that *sound like*
the query and lexical finds units that *contain* its words — both of which need
the answer to live in a single unit. The graph finds units connected to the
query through something else: ask about pallets, and a unit that never says
"pallets" but shares the warehouse and the quarter with one that does still
surfaces. That is the cross-conversation, multi-hop recall Zero-Mem's ablation
attributes to this view specifically (graph-only 62.50 vs. hierarchy-only 54.88
on HotpotQA), and nothing else in this system provides it.

The walk is Personalized PageRank over the co-occurrence graph, exactly as in
the paper and in HippoRAG before it: start a random walker on the entities the
query mentions, let it wander the edges, and see which entities it spends time
on. Units are then scored by the entities they carry. **No `networkx`** — this
is a textbook power iteration over a dict-of-dicts and the graph is hundreds of
nodes, so a dependency would buy nothing (it is a standing rejected alternative
in PROGRESS.md).

Two properties of the implementation worth knowing before changing it:

**Mass is allowed to leak.** A node with no outgoing edges in the loaded
subgraph drops whatever reaches it, so the ranks are not a probability
distribution. That is deliberate — only the *ordering* is consumed, and
retrieve.py min-max normalizes every view anyway, so renormalizing here would
be arithmetic nobody reads.

**The frontier is loaded, not queried mid-walk.** `load_adjacency()` pulls a
bounded neighbourhood up front and the iteration runs over that dict. This is
the tenant boundary as much as it is a performance choice: every edge in the
subgraph came through `store.neighbours()`, which filters on `ownerId`, so the
walk *cannot* reach another account's graph however many hops it takes. Entity
strings are global — "sjc" is a real node in many users' graphs — so a walk that
re-queried the collection as it went would be one forgotten filter away from
joining two people's memories.
"""

from typing import Any

from server.memory import store
from server.memory.types import Scope

# Probability the walker follows an edge rather than restarting at the query's
# own entities. Zero-Mem uses 0.6, tuned on LoCoMo/HotpotQA — copied here as a
# starting point and explicitly *not* trusted: DISCUSS_RAG.md §2 is about this
# exact number. It is a knob waiting for an eval set, not a finding.
DAMPING = 0.6

# How far the frontier is expanded before the walk runs. One hop makes this a
# fancy entity match — the walker has nowhere to go after its first step. Two
# is what makes it a graph, and is where the paper's multi-hop benefit lives.
HOPS = 2

# Ceilings on the loaded subgraph. A hub entity can have thousands of
# neighbours, and past a few hundred nodes the walk is diffuse enough that
# nothing ranks anyway — so this bounds cost without costing quality.
MAX_NODES = 600

ITERATIONS = 20
EPSILON = 1e-6


def personalized_pagerank(
    adjacency: dict[str, dict[str, float]],
    seeds: dict[str, float],
    *,
    damping: float = DAMPING,
    iterations: int = ITERATIONS,
) -> dict[str, float]:
    """
    Input: a weighted adjacency map, the seed nodes to restart from, and the
    damping factor.
    Output: {node: activation} — how much of the walk each node held.

    Pure, so the ranking can be asserted against a hand-built graph with no
    database anywhere near it.

    Isolated seeds still come back with their restart share rather than zero,
    which is what makes this degrade gracefully: on a fresh install, or before
    the backfill has run, there are no edges at all and this returns the seeds
    themselves — the view contributes exact entity matches instead of nothing.
    """
    if not seeds:
        return {}

    total = sum(seeds.values()) or 1.0
    restart = {node: weight / total for node, weight in seeds.items()}
    ranks = dict(restart)

    for _ in range(iterations):
        nxt = {node: (1.0 - damping) * weight for node, weight in restart.items()}
        for node, rank in ranks.items():
            edges = adjacency.get(node)
            if not edges:
                continue  # dangling: its mass leaks, see the module docstring
            outgoing = sum(edges.values()) or 1.0
            share = damping * rank / outgoing
            for neighbour, weight in edges.items():
                nxt[neighbour] = nxt.get(neighbour, 0.0) + share * weight

        delta = sum(abs(nxt.get(node, 0.0) - ranks.get(node, 0.0)) for node in set(nxt) | set(ranks))
        ranks = nxt
        if delta < EPSILON:
            break

    return ranks


async def load_adjacency(scope: Scope, seeds: list[str], *, hops: int = HOPS) -> dict[str, dict[str, float]]:
    """
    Input: a scope, the entities to start from, and how many hops to expand.
    Output: the owner's subgraph around those entities, as {node: {node: weight}}.

    One `store.neighbours()` call per hop rather than one per node — a hop is a
    single `$in` query over an index, so widening the frontier costs a round
    trip, not a round trip per entity.
    """
    adjacency: dict[str, dict[str, float]] = {}
    frontier = list(dict.fromkeys(seeds))

    for _ in range(max(1, hops)):
        pending = [node for node in frontier if node not in adjacency]
        if not pending or len(adjacency) >= MAX_NODES:
            break
        found = await store.neighbours(scope, pending)
        adjacency.update(found)
        frontier = [neighbour for edges in found.values() for neighbour in edges]

    return adjacency


async def graph_view(scope: Scope, entities: list[str], limit: int) -> list[tuple[dict[str, Any], float]]:
    """
    Input: a scope, the entities the query resolved to, and how many candidates
    to return.
    Output: (unit document, score) pairs — the same shape `store.dense()` and
    `store.lexical()` return, so retrieve.py's `_fuse()` consumes this view
    without knowing it is different from the other two.
    """
    if not entities:
        return []
    adjacency = await load_adjacency(scope, entities)
    activation = personalized_pagerank(adjacency, {entity: 1.0 for entity in entities})
    return await store.units_by_entities(scope, activation, limit)


def _demo() -> None:
    """Self-check for the walk, run with `python -m server.memory.graph`."""
    # A ── B ── C, plus an unrelated island D ── E. Starting at A, activation
    # must fall off with distance and never reach the island.
    adjacency = {
        "a": {"b": 1.0},
        "b": {"a": 1.0, "c": 1.0},
        "c": {"b": 1.0},
        "d": {"e": 1.0},
        "e": {"d": 1.0},
    }
    ranks = personalized_pagerank(adjacency, {"a": 1.0})
    assert ranks["a"] > ranks["b"] > ranks["c"], ranks
    assert ranks.get("d", 0.0) == 0.0 and ranks.get("e", 0.0) == 0.0, ranks

    # Two hops is what earns the dependency on a walk at all: C is reachable
    # from A only through B, and a one-hop view would score it zero.
    assert ranks["c"] > 0.0, ranks

    # Edge weight is observation count, so the heavier-travelled edge wins.
    weighted = personalized_pagerank({"a": {"b": 1.0, "c": 9.0}}, {"a": 1.0})
    assert weighted["c"] > weighted["b"], weighted

    # No edges at all — the pre-backfill and fresh-install case. The seeds must
    # survive, or the view would silently contribute nothing rather than
    # degrading to exact entity matching.
    bare = personalized_pagerank({}, {"a": 1.0, "b": 3.0})
    assert bare["b"] > bare["a"] > 0.0, bare

    assert personalized_pagerank(adjacency, {}) == {}

    # Two seeds: the node between them outranks either one's far side.
    both = personalized_pagerank(adjacency, {"a": 1.0, "c": 1.0})
    assert both["b"] > 0.0 and abs(both["a"] - both["c"]) < 1e-9, both

    print("memory.graph: all checks passed")


if __name__ == "__main__":
    _demo()
