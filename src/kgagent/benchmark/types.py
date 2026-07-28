"""Type definitions for benchmark generation."""

from __future__ import annotations

from typing import Any, Literal, TypedDict


# ── KG Types ────────────────────────────────────────────────────────

class Entity(TypedDict, total=False):
    id: str
    name: str
    type: str
    aliases: list[str]
    attributes: dict[str, Any]


class Relation(TypedDict, total=False):
    id: str
    source: str
    relation: str
    target: str
    attributes: dict[str, Any]
    evidence: list[str]


class NormalizedKG(TypedDict):
    graph_id: str
    graph_type: Literal["KG"]
    entities: list[Entity]
    relations: list[Relation]
    metadata: dict[str, Any]


# ── TKG Types ───────────────────────────────────────────────────────

class TimeInfo(TypedDict, total=False):
    type: Literal["point", "interval"]
    value: str
    start: str | None
    end: str | None


class TemporalFact(TypedDict, total=False):
    id: str
    subject: str
    relation: str
    object: str
    time: TimeInfo
    attributes: dict[str, Any]
    evidence: list[str]


class NormalizedTKG(TypedDict):
    graph_id: str
    graph_type: Literal["TKG"]
    entities: list[Entity]
    temporal_facts: list[TemporalFact]
    metadata: dict[str, Any]


# ── Subgraph Types ──────────────────────────────────────────────────

class SubgraphNode(TypedDict):
    id: str
    name: str
    type: str


class SubgraphEdge(TypedDict):
    source: str
    relation: str
    target: str


class Subgraph(TypedDict):
    nodes: list[SubgraphNode]
    edges: list[SubgraphEdge]


class TemporalSubgraph(TypedDict):
    facts: list[TemporalFact]


# ── Answer Types ────────────────────────────────────────────────────

class Answer(TypedDict):
    text: str
    type: Literal["entity", "relation", "time", "count", "boolean"]
    id: str | None


# ── KGQG Method Input Types ─────────────────────────────────────────

class KGQGConstraints(TypedDict, total=False):
    hop: int
    difficulty: Literal["easy", "medium", "hard"]
    question_type: str
    requires_reasoning: bool


class KGQGSource(TypedDict):
    graph_id: str
    sample_strategy: str


class KGQGMethodInputItem(TypedDict):
    sample_id: str
    task: Literal["KGQG"]
    graph_type: Literal["KG"]
    subgraph: Subgraph
    answer: Answer
    constraints: KGQGConstraints
    source: KGQGSource


# ── Temporal KGQG Method Input Types ────────────────────────────────

class TemporalKGQGConstraints(TypedDict, total=False):
    temporal_question_type: str
    hop: int
    difficulty: Literal["easy", "medium", "hard"]


class TemporalBenchmarkInputItem(TypedDict):
    sample_id: str
    task: Literal["temporal_KGQG"]
    graph_type: Literal["TKG"]
    temporal_subgraph: TemporalSubgraph
    answer: Answer
    constraints: TemporalKGQGConstraints
    source: KGQGSource


# ── Benchmark Output Types ──────────────────────────────────────────

class Reasoning(TypedDict, total=False):
    hop: int
    difficulty: str
    chain: list[str]
    temporal_type: str | None


class BenchmarkMetadata(TypedDict):
    method: str
    source_graph: str
    created_by: str
    version: str


class QualityInfo(TypedDict):
    valid: bool
    warnings: list[str]


class BenchmarkItem(TypedDict):
    id: str
    benchmark_type: str
    graph_type: Literal["KG", "TKG"]
    question: str
    answer: Answer
    supporting_graph: Subgraph | TemporalSubgraph
    reasoning: Reasoning
    metadata: BenchmarkMetadata
    quality: QualityInfo


# ── Configuration Types ─────────────────────────────────────────────

class BenchmarkConfig(TypedDict, total=False):
    benchmark_type: Literal["KGQG", "KGQA", "temporal_KGQG", "temporal_KGQA"]
    graph_type: Literal["KG", "TKG"]
    task: Literal["KGQG", "KGQA", "temporal_KGQG", "temporal_KGQA"]
    sample_count: int
    method: str
    hop_distribution: dict[str, float]
    difficulty_distribution: dict[str, float]
    answer_type_distribution: dict[str, float]
    temporal_question_distribution: dict[str, float]
    seed: int
    output_path: str | None
    intermediate_dir: str | None


# ── Stats Types ─────────────────────────────────────────────────────

class GraphStats(TypedDict):
    entity_count: int
    relation_count: int
    format_detected: str


class TKGStats(TypedDict):
    entity_count: int
    temporal_fact_count: int
    time_type_distribution: dict[str, int]


class SamplingStats(TypedDict):
    sample_count: int
    hop_distribution_actual: dict[str, int]
    difficulty_distribution_actual: dict[str, int]
