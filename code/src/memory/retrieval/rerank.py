"""Provider-backed candidate reranking with strict, request-local validation."""

from __future__ import annotations

import json
from typing import Any, Protocol

from src.memory.llm.errors import ProviderResponseError
from src.memory.llm.llm_client import LLMClientBase
from src.memory.retrieval.models import RecallCandidate, RecallResult, RecallTrace


class CandidateReranker(Protocol):
    def rerank(self, query: str, result: RecallResult) -> RecallResult: ...


RERANK_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "ranked_memory_ids": {"type": "array", "items": {"type": "string"}},
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["ranked_memory_ids", "warnings"],
}


class OpenAICompatibleCandidateReranker:
    """LLM reranker; it may reorder candidates but cannot invent candidates."""

    SYSTEM_PROMPT = (
        "You rerank memory candidates for a user query. Return only JSON. "
        "Use only the supplied memory IDs. Do not judge truth, modify content, "
        "or invent IDs. Rank by direct usefulness to the query."
    )

    def __init__(self, client: LLMClientBase, *, provider: str, model: str) -> None:
        self._client = client
        self.provider = provider
        self.model = model

    def rerank(self, query: str, result: RecallResult) -> RecallResult:
        if not result.candidates:
            return result
        candidates = result.candidates
        payload = {
            "query": query,
            "candidates": [
                {
                    "memory_id": item.snapshot.memory_id,
                    "content": item.snapshot.revision.content,
                    "recall_rank": item.rank,
                    "recall_score": item.recall_score,
                }
                for item in candidates
            ],
        }
        try:
            raw = self._client.generate_json(
                self.SYSTEM_PROMPT,
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
                response_schema=RERANK_SCHEMA,
                schema_name="memory_candidate_rerank",
            )
        except Exception as exc:
            if hasattr(exc, "provider"):
                raise
            raise ProviderResponseError(
                "memory reranker provider response failed",
                provider=self.provider,
                operation="rerank",
            ) from exc
        if not isinstance(raw, dict):
            raise ProviderResponseError(
                "memory reranker response must be an object",
                provider=self.provider,
                operation="rerank",
            )
        ranked_ids = raw.get("ranked_memory_ids")
        if not isinstance(ranked_ids, list) or not all(isinstance(item, str) for item in ranked_ids):
            raise ProviderResponseError(
                "memory reranker ranked_memory_ids must be a string array",
                provider=self.provider,
                operation="rerank",
            )
        by_id = {item.snapshot.memory_id: item for item in candidates}
        if len(set(ranked_ids)) != len(ranked_ids) or any(item not in by_id for item in ranked_ids):
            raise ProviderResponseError(
                "memory reranker returned unknown or duplicate memory IDs",
                provider=self.provider,
                operation="rerank",
            )
        ordered = [by_id[item] for item in ranked_ids]
        ordered.extend(item for item in candidates if item.snapshot.memory_id not in set(ranked_ids))
        reranked = tuple(
            item if item.rank == rank else RecallCandidate(
                snapshot=item.snapshot,
                rank=rank,
                recall_score=item.recall_score,
                score_breakdown=item.score_breakdown,
                matched_terms=item.matched_terms,
                matched_metadata_fields=item.matched_metadata_fields,
            )
            for rank, item in enumerate(ordered, start=1)
        )
        warnings = tuple(raw.get("warnings", ())) if isinstance(raw.get("warnings", ()), list) else ()
        return RecallResult(
            query=result.query,
            method=f"{result.method}+model_rerank_v1",
            corpus_size=result.corpus_size,
            candidates=reranked,
            warnings=tuple(dict.fromkeys((*result.warnings, *warnings))),
            trace=RecallTrace(
                query_variants=result.trace.query_variants,
                candidate_counts=tuple((*result.trace.candidate_counts, ("model_rerank", len(reranked)))),
                stages=tuple((*result.trace.stages, "model_rerank")),
                warnings=tuple(dict.fromkeys((*result.warnings, *warnings))),
            ),
        )
