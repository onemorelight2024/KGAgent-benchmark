"""Subgraph sampler for KG benchmark generation."""

from __future__ import annotations

import random
import logging
from collections import defaultdict
from typing import Any

from kgagent.benchmark.types import (
    Answer,
    KGQGConstraints,
    KGQGMethodInputItem,
    KGQGSource,
    NormalizedKG,
    SamplingStats,
    Subgraph,
    SubgraphEdge,
    SubgraphNode,
)

logger = logging.getLogger(__name__)


def sample_subgraphs(
    kg: NormalizedKG,
    sample_count: int = 100,
    hop_distribution: dict[str, float] | None = None,
    difficulty_distribution: dict[str, float] | None = None,
    answer_type_distribution: dict[str, float] | None = None,
    seed: int = 42,
) -> dict[str, Any]:
    """Sample subgraphs from KG according to distributions.

    Args:
        kg: Normalized KG
        sample_count: Number of samples to generate
        hop_distribution: {"1": 0.3, "2": 0.5, "3": 0.2}
        difficulty_distribution: {"easy": 0.3, "medium": 0.5, "hard": 0.2}
        answer_type_distribution: {"entity": 0.8, "relation": 0.2}
        seed: Random seed

    Returns:
        Dict with 'samples' and 'stats'
    """
    random.seed(seed)
    logger.info("Sampling subgraphs: graph_id=%s sample_count=%s seed=%s", kg.get("graph_id"), sample_count, seed)

    # Default distributions
    if hop_distribution is None:
        hop_distribution = {"1": 0.3, "2": 0.5, "3": 0.2}
    if difficulty_distribution is None:
        difficulty_distribution = {"easy": 0.3, "medium": 0.5, "hard": 0.2}
    if answer_type_distribution is None:
        answer_type_distribution = {"entity": 1.0}

    # Build adjacency index
    adj = _build_adjacency(kg)

    # Calculate target counts per hop
    hop_targets = {
        hop: int(sample_count * ratio)
        for hop, ratio in hop_distribution.items()
    }
    # Adjust for rounding
    total_allocated = sum(hop_targets.values())
    if total_allocated < sample_count:
        # Add remainder to most common hop
        max_hop = max(hop_targets.keys(), key=lambda h: hop_targets[h])
        hop_targets[max_hop] += sample_count - total_allocated

    samples: list[KGQGMethodInputItem] = []
    sample_set: set[str] = set()  # For deduplication
    warnings: list[str] = []

    # Sample for each hop level
    for hop_str, target_count in sorted(hop_targets.items()):
        hop = int(hop_str)
        if target_count <= 0:
            continue

        hop_samples = _sample_hop_level(
            kg, adj, hop, target_count, difficulty_distribution,
            answer_type_distribution, sample_set, len(samples)
        )

        if len(hop_samples) < target_count:
            warnings.append(
                f"Only generated {len(hop_samples)}/{target_count} samples for hop={hop}. "
                f"Graph may be too small or sparse."
            )

        samples.extend(hop_samples)

    # Compute actual stats
    actual_hop_dist: dict[str, int] = defaultdict(int)
    actual_diff_dist: dict[str, int] = defaultdict(int)

    for sample in samples:
        hop = sample["constraints"]["hop"]
        difficulty = sample["constraints"]["difficulty"]
        actual_hop_dist[str(hop)] += 1
        actual_diff_dist[difficulty] += 1

    stats = SamplingStats(
        sample_count=len(samples),
        hop_distribution_actual=dict(actual_hop_dist),
        difficulty_distribution_actual=dict(actual_diff_dist),
    )

    return {
        "samples": samples,
        "stats": stats,
        "warnings": warnings,
    }


def _build_adjacency(kg: NormalizedKG) -> dict[str, list[dict]]:
    """Build undirected adjacency list for graph traversal."""
    adj: dict[str, list[dict]] = defaultdict(list)

    for rel in kg["relations"]:
        source = rel["source"]
        target = rel["target"]
        relation = rel["relation"]

        # Undirected: both directions
        adj[source].append({
            "node": target,
            "relation": relation,
            "direction": "forward",
        })
        adj[target].append({
            "node": source,
            "relation": relation,
            "direction": "backward",
        })

    return adj


def _sample_hop_level(
    kg: NormalizedKG,
    adj: dict[str, list[dict]],
    hop: int,
    target_count: int,
    difficulty_distribution: dict[str, float],
    answer_type_distribution: dict[str, float],
    sample_set: set[str],
    sample_id_offset: int,
) -> list[KGQGMethodInputItem]:
    """Sample subgraphs for a specific hop level."""
    samples: list[KGQGMethodInputItem] = []
    attempts = 0
    max_attempts = target_count * 50  # Prevent infinite loop

    entity_ids = [e["id"] for e in kg["entities"]]

    while len(samples) < target_count and attempts < max_attempts:
        attempts += 1

        # Random walk from random entity
        if not entity_ids:
            break

        start_entity = random.choice(entity_ids)

        # Perform random walk for 'hop' steps. Keep the actual endpoint as the
        # answer; edges are stored in canonical KG direction for support graph.
        walk_result = _random_walk(start_entity, hop, adj)

        if not walk_result:
            continue
        path, answer_entity_id = walk_result

        # Extract subgraph and answer from path
        subgraph_result = _extract_subgraph_from_path(kg, path, answer_entity_id)
        if not subgraph_result:
            continue

        subgraph, answer_entity = subgraph_result

        # Create canonical key for deduplication
        canonical_key = _canonical_subgraph_key(subgraph, answer_entity)
        if canonical_key in sample_set:
            continue

        sample_set.add(canonical_key)

        # Estimate difficulty
        difficulty = _estimate_difficulty(subgraph, hop)

        # Create sample
        sample_id = f"sample_{sample_id_offset + len(samples):06d}"
        sample = KGQGMethodInputItem(
            sample_id=sample_id,
            task="KGQG",
            graph_type="KG",
            subgraph=subgraph,
            answer=Answer(
                text=answer_entity["name"],
                type="entity",
                id=answer_entity["id"],
            ),
            constraints=KGQGConstraints(
                hop=hop,
                difficulty=difficulty,
                question_type="what",
                requires_reasoning=hop > 1,
            ),
            source=KGQGSource(
                graph_id=kg["graph_id"],
                sample_strategy="random_walk",
            ),
        )

        samples.append(sample)

    return samples


def _random_walk(
    start: str,
    hops: int,
    adj: dict[str, list[dict]],
) -> tuple[list[tuple[str, str, str]], str] | None:
    """Perform a simple random walk.

    The returned path stores edges in their original KG direction, while the
    second return value is the actual endpoint reached by traversal. Keeping
    those separate matters for backward traversals.

    Returns None if walk fails.
    """
    if start not in adj or not adj[start]:
        return None

    path: list[tuple[str, str, str]] = []
    current = start
    visited_nodes = {start}
    used_edges: set[tuple[str, str, str]] = set()

    for _ in range(hops):
        neighbors = []
        for neighbor in adj.get(current, []):
            next_node = neighbor["node"]
            if next_node in visited_nodes:
                continue
            if neighbor["direction"] == "forward":
                edge_key = (current, neighbor["relation"], next_node)
            else:
                edge_key = (next_node, neighbor["relation"], current)
            if edge_key in used_edges:
                continue
            neighbors.append((neighbor, edge_key))
        if not neighbors:
            return None

        next_edge, edge_key = random.choice(neighbors)
        next_node = next_edge["node"]
        relation = next_edge["relation"]

        # Record edge (always as forward direction for simplicity)
        if next_edge["direction"] == "forward":
            path.append((current, relation, next_node))
        else:
            path.append((next_node, relation, current))

        used_edges.add(edge_key)
        visited_nodes.add(next_node)
        current = next_node

    return (path, current) if path else None


def _extract_subgraph_from_path(
    kg: NormalizedKG,
    path: list[tuple[str, str, str]],
    answer_entity_id: str,
) -> tuple[Subgraph, dict] | None:
    """Extract subgraph and answer entity from path."""
    if not path:
        return None

    # Collect all nodes and edges from path
    nodes_dict: dict[str, SubgraphNode] = {}
    edges: list[SubgraphEdge] = []

    for source, relation, target in path:
        # Add nodes
        for entity_id in [source, target]:
            if entity_id not in nodes_dict:
                # Find entity info
                entity_info = next(
                    (e for e in kg["entities"] if e["id"] == entity_id),
                    None
                )
                if entity_info:
                    nodes_dict[entity_id] = SubgraphNode(
                        id=entity_info["id"],
                        name=entity_info["name"],
                        type=entity_info["type"],
                    )
                else:
                    nodes_dict[entity_id] = SubgraphNode(
                        id=entity_id,
                        name=entity_id,
                        type="Entity",
                    )

        # Add edge
        edges.append(SubgraphEdge(
            source=source,
            relation=relation,
            target=target,
        ))

    answer_entity_info = nodes_dict.get(answer_entity_id)

    if not answer_entity_info:
        return None

    subgraph = Subgraph(
        nodes=list(nodes_dict.values()),
        edges=edges,
    )

    return subgraph, answer_entity_info


def _canonical_subgraph_key(subgraph: Subgraph, answer: dict) -> str:
    """Create canonical key for subgraph deduplication."""
    # Sort nodes and edges for canonical representation
    node_ids = sorted(n["id"] for n in subgraph["nodes"])
    edge_tuples = sorted(
        (e["source"], e["relation"], e["target"])
        for e in subgraph["edges"]
    )
    return f"{','.join(node_ids)}|{','.join(f'{s}-{r}->{t}' for s, r, t in edge_tuples)}|{answer['id']}"


def _estimate_difficulty(subgraph: Subgraph, hop: int) -> str:
    """Estimate difficulty based on graph structure.

    Heuristic:
    - 1-hop with 1-2 edges: easy
    - 2-hop with 2-3 edges: medium
    - 3+ hop or many branches: hard
    """
    edge_count = len(subgraph["edges"])
    node_count = len(subgraph["nodes"])

    # Calculate branching factor
    branching = edge_count / max(1, node_count - 1)

    if hop == 1:
        return "easy"
    elif hop == 2:
        if branching <= 1.2:
            return "medium"
        else:
            return "hard"
    else:  # hop >= 3
        if branching <= 1.0:
            return "medium"
        else:
            return "hard"
