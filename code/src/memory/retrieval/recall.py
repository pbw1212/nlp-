from __future__ import annotations

from typing import Sequence

from src.memory.core.schema import MemoryType, RetentionDisposition, SemanticValidity
from src.memory.retrieval.lexical import (
    DEFAULT_BM25_B,
    DEFAULT_BM25_K1,
    DEFAULT_METADATA_WEIGHT,
    LexicalBM25Index,
)
from src.memory.retrieval.models import RecallCandidate, RecallRequest, RecallResult, RecallTrace
from src.memory.storage.repository import MemoryReadRepository


class MemoryRecallEngine:
    METHOD = "lexical_bm25_v1"

    def __init__(
        self,
        repository: MemoryReadRepository,
        *,
        k1: float = DEFAULT_BM25_K1,
        b: float = DEFAULT_BM25_B,
        metadata_weight: float = DEFAULT_METADATA_WEIGHT,
    ) -> None:
        self._repository = repository
        self._k1 = k1
        self._b = b
        self._metadata_weight = metadata_weight

    def recall(
        self,
        query: str,
        top_k: int = 8,
        *,
        memory_types: Sequence[MemoryType] = (),
    ) -> RecallResult:
        return self.recall_request(
            RecallRequest(query=query, top_k=top_k, memory_types=tuple(memory_types))
        )

    def recall_request(self, request: RecallRequest) -> RecallResult:
        allowed_types = set(request.memory_types)
        snapshots = tuple(
            snapshot
            for snapshot in self._repository.list_current()
            if snapshot.retention_disposition is RetentionDisposition.ACTIVE
            and snapshot.revision.semantic_validity is SemanticValidity.CURRENT
            and snapshot.memory_id == snapshot.revision.memory_id
            and (not allowed_types or snapshot.memory_type in allowed_types)
        )
        index = LexicalBM25Index(
            snapshots, k1=self._k1, b=self._b, metadata_weight=self._metadata_weight
        )
        variants = self._query_variants(request)
        if request.top_k <= 0:
            warning = "non_positive_top_k"
            return RecallResult(
                request.query, self.METHOD, index.corpus_size, (), (warning,),
                RecallTrace(variants, (), ("lexical_bm25",), (warning,)),
            )
        if not request.query.strip():
            warning = "empty_query"
            return RecallResult(
                request.query, self.METHOD, index.corpus_size, (), (warning,),
                RecallTrace(variants, (), ("lexical_bm25",), (warning,)),
            )
        if index.corpus_size == 0:
            warning = "empty_current_corpus"
            return RecallResult(
                request.query, self.METHOD, 0, (), (warning,),
                RecallTrace(variants, (), ("lexical_bm25",), (warning,)),
            )

        by_memory = {}
        counts = []
        for variant in variants:
            variant_scores = index.score(variant)
            counts.append((variant, len(variant_scores)))
            for score in variant_scores:
                current = by_memory.get(score.snapshot.memory_id)
                if current is None or score.breakdown.total > current.breakdown.total:
                    by_memory[score.snapshot.memory_id] = score
        scores = sorted(
            by_memory.values(),
            key=lambda item: (-item.breakdown.total, item.snapshot.memory_id),
        )[: request.top_k]
        candidates = tuple(
            RecallCandidate(
                snapshot=score.snapshot,
                rank=rank,
                recall_score=score.breakdown.total,
                score_breakdown=score.breakdown,
                matched_terms=score.matched_terms,
                matched_metadata_fields=score.matched_metadata_fields,
            )
            for rank, score in enumerate(scores, start=1)
        )
        warnings = () if candidates else ("no_lexical_matches",)
        trace = RecallTrace(
            query_variants=variants,
            candidate_counts=tuple(counts),
            stages=("lexical_bm25", "multi_query_union") if len(variants) > 1 else ("lexical_bm25",),
            warnings=warnings,
        )
        return RecallResult(request.query, self.METHOD, index.corpus_size, candidates, warnings, trace)

    @staticmethod
    def _query_variants(request: RecallRequest) -> tuple[str, ...]:
        return tuple(dict.fromkeys((request.query, *request.expanded_queries)))
