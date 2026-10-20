from __future__ import annotations

from typing import Sequence

from src.memory.core.schema import MemorySnapshot, MemoryType, RetentionDisposition, SemanticValidity
from src.memory.llm.errors import ProviderResponseError
from src.memory.retrieval.embedding import validate_vector
from src.memory.retrieval.models import RecallCandidate, RecallRequest, RecallResult, RecallScoreBreakdown, RecallTrace
from src.memory.retrieval.protocols import EmbeddingProvider
from src.memory.storage.repository import MemoryReadRepository


class DenseEmbeddingRetriever:
    METHOD = "dense_embedding_v1"

    def __init__(self, repository: MemoryReadRepository, provider: EmbeddingProvider) -> None:
        self._repository = repository
        self._provider = provider

    def recall(
        self,
        query: str,
        top_k: int = 8,
        *,
        memory_types: Sequence[MemoryType] = (),
    ) -> RecallResult:
        return self.recall_request(RecallRequest(query, top_k, tuple(memory_types)))

    def _current_snapshots(self, request: RecallRequest) -> tuple[MemorySnapshot, ...]:
        allowed_types = set(request.memory_types)
        return tuple(
            snapshot
            for snapshot in self._repository.list_current()
            if snapshot.retention_disposition is RetentionDisposition.ACTIVE
            and snapshot.revision.semantic_validity is SemanticValidity.CURRENT
            and snapshot.memory_id == snapshot.revision.memory_id
            and (not allowed_types or snapshot.memory_type in allowed_types)
        )

    def recall_request(self, request: RecallRequest) -> RecallResult:
        snapshots = self._current_snapshots(request)
        variants = tuple(dict.fromkeys((request.query, *request.expanded_queries)))
        if request.top_k <= 0:
            warning = "non_positive_top_k"
            return RecallResult(request.query, self.METHOD, len(snapshots), (), (warning,), RecallTrace(variants, (), ("dense_embedding",), (warning,)))
        if not request.query.strip():
            warning = "empty_query"
            return RecallResult(request.query, self.METHOD, len(snapshots), (), (warning,), RecallTrace(variants, (), ("dense_embedding",), (warning,)))
        if not snapshots:
            warning = "empty_current_corpus"
            return RecallResult(request.query, self.METHOD, 0, (), (warning,), RecallTrace(variants, (), ("dense_embedding",), (warning,)))

        identity = self._provider.identity
        document_vectors = self._provider.embed_documents(
            tuple(snapshot.revision.content for snapshot in snapshots)
        )
        if len(document_vectors) != len(snapshots):
            raise ProviderResponseError(
                "document embedding count mismatch",
                provider=identity.provider,
                operation="embed_documents",
            )

        query_vectors = [
            validate_vector(
                self._provider.embed_query(variant),
                expected_dimension=identity.dimension,
                provider=identity.provider,
                operation="embed_query",
            )
            for variant in variants
        ]
        scored = []
        counts = []
        for snapshot, raw_vector in zip(snapshots, document_vectors):
            vector = validate_vector(
                raw_vector,
                expected_dimension=identity.dimension,
                provider=identity.provider,
                operation="embed_documents",
            )
            similarities = [sum(left * right for left, right in zip(query, vector)) for query in query_vectors]
            similarity = max(similarities, default=0.0)
            if similarity <= 0:
                continue
            scored.append((similarity, snapshot))
        counts.append(("max_similarity", len(scored)))
        scored.sort(key=lambda item: (-item[0], item[1].memory_id))
        candidates = tuple(
            RecallCandidate(
                snapshot=snapshot,
                rank=rank,
                recall_score=score,
                score_breakdown=RecallScoreBreakdown(0.0, 0.0, dense_similarity=score),
                matched_terms=(),
                matched_metadata_fields=(),
            )
            for rank, (score, snapshot) in enumerate(scored[: request.top_k], start=1)
        )
        warnings = () if candidates else ("no_dense_matches",)
        return RecallResult(
            request.query,
            self.METHOD,
            len(snapshots),
            candidates,
            warnings,
            RecallTrace(
                query_variants=variants,
                candidate_counts=tuple(counts),
                stages=("dense_embedding", "multi_query_max") if len(variants) > 1 else ("dense_embedding",),
                warnings=warnings,
            ),
        )
