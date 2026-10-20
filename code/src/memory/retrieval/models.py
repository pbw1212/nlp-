from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple

from src.memory.core.schema import MemorySnapshot, MemoryType


@dataclass(frozen=True)
class RecallRequest:
    """Recall-only request；不包含 context applicability state。"""

    query: str
    top_k: int = 8
    memory_types: Tuple[MemoryType, ...] = ()
    expanded_queries: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.query, str):
            raise TypeError("query 必须是字符串")
        if not isinstance(self.top_k, int):
            raise TypeError("top_k 必须是整数")
        if not all(isinstance(memory_type, MemoryType) for memory_type in self.memory_types):
            raise TypeError("memory_types 必须只包含 MemoryType")
        if not all(isinstance(item, str) and item.strip() for item in self.expanded_queries):
            raise TypeError("expanded_queries 必须只包含非空字符串")


@dataclass(frozen=True)
class RecallScoreBreakdown:
    content_bm25: float
    metadata_overlap: float
    dense_similarity: Optional[float] = None
    fusion_rrf: Optional[float] = None
    graph_random_walk: Optional[float] = None

    @property
    def total(self) -> float:
        if self.fusion_rrf is not None:
            return self.fusion_rrf
        if self.graph_random_walk is not None:
            return self.graph_random_walk
        if self.dense_similarity is not None:
            return self.dense_similarity
        return self.content_bm25 + self.metadata_overlap


@dataclass(frozen=True)
class RecallCandidate:
    """Request-local view；canonical memory 仍由 snapshot 引用。"""

    snapshot: MemorySnapshot
    rank: int
    recall_score: float
    score_breakdown: RecallScoreBreakdown
    matched_terms: Tuple[str, ...]
    matched_metadata_fields: Tuple[Tuple[str, Tuple[str, ...]], ...]


@dataclass(frozen=True)
class RecallTrace:
    """Deterministic request-local trace for recall and fusion inspection."""

    query_variants: Tuple[str, ...] = ()
    candidate_counts: Tuple[Tuple[str, int], ...] = ()
    stages: Tuple[str, ...] = ()
    warnings: Tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "query_variants": list(self.query_variants),
            "candidate_counts": {name: count for name, count in self.candidate_counts},
            "stages": list(self.stages),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class RecallResult:
    query: str
    method: str
    corpus_size: int
    candidates: Tuple[RecallCandidate, ...]
    warnings: Tuple[str, ...] = ()
    trace: RecallTrace = field(default_factory=RecallTrace)
