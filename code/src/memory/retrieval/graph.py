"""Optional graph expansion for current-safe memory recall.

The graph layer is request-local. It reads identity-level relations from the
Task 1 repository, applies a bounded personalized random walk, and returns
current revisions only. It never creates, updates, or deletes memory data.
"""

from __future__ import annotations

from dataclasses import replace
from src.memory.retrieval.models import (
    RecallCandidate,
    RecallRequest,
    RecallResult,
    RecallScoreBreakdown,
    RecallTrace,
)
from src.memory.retrieval.protocols import MemoryRetriever
from src.memory.storage.repository import MemoryReadRepository


class PersonalizedGraphMemoryRetriever:
    METHOD = "personalized_graph_walk_v1"

    def __init__(
        self,
        base_retriever: MemoryRetriever,
        repository: MemoryReadRepository,
        *,
        damping: float = 0.85,
        iterations: int = 12,
        candidate_limit: int = 50,
    ) -> None:
        if not 0.0 <= damping < 1.0:
            raise ValueError("damping 必须位于 [0, 1)")
        if iterations <= 0 or candidate_limit <= 0:
            raise ValueError("iterations/candidate_limit 必须大于 0")
        self._base = base_retriever
        self._repository = repository
        self._damping = damping
        self._iterations = iterations
        self._candidate_limit = candidate_limit

    def recall(self, query: str, top_k: int = 8) -> RecallResult:
        return self.recall_request(RecallRequest(query=query, top_k=top_k))

    def recall_request(self, request: RecallRequest) -> RecallResult:
        base = self._base.recall_request(request)
        snapshots = {item.memory_id: item for item in self._repository.list_current()}
        get_memory = getattr(self._repository, "get_memory", None)
        if not base.candidates or not callable(get_memory) or not snapshots:
            return self._with_trace(base, "graph_skipped")

        adjacency: dict[str, tuple[tuple[str, float], ...]] = {}
        for source_id in snapshots:
            item = get_memory(source_id)
            if item is None:
                continue
            edges = tuple(
                (relation.target_id, max(0.0, float(relation.weight)))
                for relation in item.graph_relations
                if relation.target_id in snapshots and relation.weight > 0
            )
            if edges:
                adjacency[source_id] = edges
        if not adjacency:
            return self._with_trace(base, "graph_no_edges")

        personalization = {item_id: 0.0 for item_id in snapshots}
        total_seed = sum(max(0.0, candidate.recall_score) for candidate in base.candidates)
        if total_seed <= 0:
            return self._with_trace(base, "graph_no_positive_seed")
        for candidate in base.candidates:
            personalization[candidate.snapshot.memory_id] = max(0.0, candidate.recall_score) / total_seed

        scores = dict(personalization)
        for _ in range(self._iterations):
            next_scores = {
                item_id: (1.0 - self._damping) * seed
                for item_id, seed in personalization.items()
            }
            for source_id, edges in adjacency.items():
                outgoing = sum(weight for _, weight in edges)
                if outgoing <= 0:
                    continue
                for target_id, weight in edges:
                    next_scores[target_id] += self._damping * scores[source_id] * weight / outgoing
            scores = next_scores

        by_id = {candidate.snapshot.memory_id: candidate for candidate in base.candidates}
        for memory_id, graph_score in scores.items():
            if graph_score <= 0:
                continue
            candidate = by_id.get(memory_id)
            if candidate is None:
                snapshot = snapshots[memory_id]
                by_id[memory_id] = RecallCandidate(
                    snapshot=snapshot,
                    rank=0,
                    recall_score=graph_score,
                    score_breakdown=RecallScoreBreakdown(0.0, 0.0, graph_random_walk=graph_score),
                    matched_terms=(),
                    matched_metadata_fields=(),
                )
            else:
                by_id[memory_id] = replace(
                    candidate,
                    recall_score=graph_score,
                    score_breakdown=replace(candidate.score_breakdown, graph_random_walk=graph_score),
                )

        ranked = sorted(
            by_id.values(),
            key=lambda item: (-item.recall_score, item.snapshot.memory_id),
        )[: min(request.top_k, self._candidate_limit)]
        candidates = tuple(replace(candidate, rank=index) for index, candidate in enumerate(ranked, start=1))
        warnings = base.warnings if candidates else ("no_graph_matches",)
        return RecallResult(
            query=request.query,
            method=self.METHOD,
            corpus_size=base.corpus_size,
            candidates=candidates,
            warnings=warnings,
            trace=RecallTrace(
                query_variants=base.trace.query_variants,
                candidate_counts=tuple((*base.trace.candidate_counts, ("graph", len(candidates)))),
                stages=tuple((*base.trace.stages, "personalized_graph_walk")),
                warnings=warnings,
            ),
        )

    @staticmethod
    def _with_trace(result: RecallResult, stage: str) -> RecallResult:
        return replace(
            result,
            trace=replace(result.trace, stages=tuple((*result.trace.stages, stage))),
        )
