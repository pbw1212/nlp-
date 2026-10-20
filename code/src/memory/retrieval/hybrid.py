from __future__ import annotations

from dataclasses import dataclass

from src.memory.retrieval.models import RecallCandidate, RecallRequest, RecallResult, RecallScoreBreakdown, RecallTrace
from src.memory.retrieval.protocols import MemoryRetriever


DEFAULT_RRF_K = 60
DEFAULT_RRF_CANDIDATE_LIMIT = 50


@dataclass(frozen=True)
class _FusionEntry:
    candidate: RecallCandidate
    lexical_rank: int | None = None
    dense_rank: int | None = None
    lexical_score: float = 0.0
    metadata_score: float = 0.0
    dense_score: float | None = None


class HybridRRFMemoryRetriever:
    METHOD = "hybrid_rrf_v1"

    def __init__(
        self,
        lexical_retriever: MemoryRetriever,
        dense_retriever: MemoryRetriever,
        *,
        rrf_k: int = DEFAULT_RRF_K,
        candidate_limit: int = DEFAULT_RRF_CANDIDATE_LIMIT,
    ) -> None:
        if rrf_k <= 0 or candidate_limit <= 0:
            raise ValueError("rrf_k/candidate_limit 必须大于 0")
        self._lexical = lexical_retriever
        self._dense = dense_retriever
        self._rrf_k = rrf_k
        self._candidate_limit = candidate_limit

    def recall_request(self, request: RecallRequest) -> RecallResult:
        if request.top_k <= 0 or not request.query.strip():
            lexical = self._lexical.recall_request(request)
            warning = "non_positive_top_k" if request.top_k <= 0 else "empty_query"
            return RecallResult(
                request.query,
                self.METHOD,
                lexical.corpus_size,
                (),
                (warning,),
                RecallTrace(
                    query_variants=lexical.trace.query_variants,
                    stages=("hybrid_rrf",),
                    warnings=(warning,),
                ),
            )
        depth = max(request.top_k, self._candidate_limit)
        child_request = RecallRequest(
            request.query,
            depth,
            request.memory_types,
            request.expanded_queries,
        )
        lexical = self._lexical.recall_request(child_request)
        dense = self._dense.recall_request(child_request)
        if max(lexical.corpus_size, dense.corpus_size) == 0:
            warning = "empty_current_corpus"
            return RecallResult(
                request.query,
                self.METHOD,
                0,
                (),
                (warning,),
                RecallTrace(
                    query_variants=tuple(dict.fromkeys((request.query, *request.expanded_queries))),
                    stages=("lexical", "dense", "hybrid_rrf"),
                    warnings=(warning,),
                ),
            )

        entries: dict[str, _FusionEntry] = {}
        for candidate in lexical.candidates:
            entries[candidate.snapshot.memory_id] = _FusionEntry(
                candidate=candidate,
                lexical_rank=candidate.rank,
                lexical_score=candidate.score_breakdown.content_bm25,
                metadata_score=candidate.score_breakdown.metadata_overlap,
            )
        for candidate in dense.candidates:
            memory_id = candidate.snapshot.memory_id
            previous = entries.get(memory_id)
            if previous is None:
                entries[memory_id] = _FusionEntry(
                    candidate=candidate,
                    dense_rank=candidate.rank,
                    dense_score=candidate.recall_score,
                )
            else:
                entries[memory_id] = _FusionEntry(
                    candidate=previous.candidate,
                    lexical_rank=previous.lexical_rank,
                    dense_rank=candidate.rank,
                    lexical_score=previous.lexical_score,
                    metadata_score=previous.metadata_score,
                    dense_score=candidate.recall_score,
                )

        fused = []
        for memory_id, entry in entries.items():
            score = sum(
                1.0 / (self._rrf_k + rank)
                for rank in (entry.lexical_rank, entry.dense_rank)
                if rank is not None
            )
            if score <= 0:
                continue
            fused.append((score, memory_id, entry))
        fused.sort(key=lambda item: (-item[0], item[1]))
        candidates = tuple(
            RecallCandidate(
                snapshot=entry.candidate.snapshot,
                rank=rank,
                recall_score=score,
                score_breakdown=RecallScoreBreakdown(
                    entry.lexical_score,
                    entry.metadata_score,
                    dense_similarity=entry.dense_score,
                    fusion_rrf=score,
                ),
                matched_terms=entry.candidate.matched_terms,
                matched_metadata_fields=entry.candidate.matched_metadata_fields,
            )
            for rank, (score, _memory_id, entry) in enumerate(fused[: request.top_k], start=1)
        )
        warnings = tuple(sorted(set(lexical.warnings + dense.warnings))) if not candidates else ()
        final_warnings = warnings or (("no_hybrid_matches",) if not candidates else ())
        return RecallResult(
            request.query,
            self.METHOD,
            max(lexical.corpus_size, dense.corpus_size),
            candidates,
            final_warnings,
            RecallTrace(
                query_variants=tuple(dict.fromkeys((request.query, *request.expanded_queries))),
                candidate_counts=(
                    ("lexical", len(lexical.candidates)),
                    ("dense", len(dense.candidates)),
                    ("hybrid", len(candidates)),
                ),
                stages=("lexical", "dense", "hybrid_rrf"),
                warnings=final_warnings,
            ),
        )
