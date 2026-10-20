"""Task 2 public retrieval facade.

Decouples external callers (like AgentRuntime) from Task 2's multi-stage
internal pipeline components (recall engine, interpreters, applicability, evidence pipeline).
"""

from __future__ import annotations

from typing import Protocol, Tuple

from src.memory.retrieval.evidence import DeterministicEvidencePipeline, EvidenceBundle
from src.memory.retrieval.interpretation_models import ContextSource
from src.memory.retrieval.semantic_pipeline import SemanticRetrievalPipeline


class MemoryRetrievalFacade(Protocol):
    """Public contract for Task 2 context-aware memory retrieval."""

    def retrieve_evidence(
        self,
        query: str,
        *,
        top_k: int = 8,
        query_id: str = "current-query",
        recent_context: Tuple[ContextSource, ...] = (),
    ) -> EvidenceBundle: ...


class DefaultMemoryRetrievalFacade:
    """Default production implementation wrapping SemanticRetrievalPipeline and DeterministicEvidencePipeline."""

    def __init__(
        self,
        semantic_pipeline: SemanticRetrievalPipeline,
        evidence_pipeline: DeterministicEvidencePipeline | None = None,
    ) -> None:
        self.semantic_pipeline = semantic_pipeline
        self.evidence_pipeline = evidence_pipeline or DeterministicEvidencePipeline()

    def retrieve_evidence(
        self,
        query: str,
        *,
        top_k: int = 8,
        query_id: str = "current-query",
        recent_context: Tuple[ContextSource, ...] = (),
    ) -> EvidenceBundle:
        return self.evidence_pipeline.run_natural_language(
            self.semantic_pipeline,
            query,
            top_k=top_k,
            query_id=query_id,
            recent_context=recent_context,
        )
