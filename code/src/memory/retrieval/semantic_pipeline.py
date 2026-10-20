from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from src.memory.llm.errors import ProviderError
from src.memory.retrieval.applicability import ApplicabilityEngine
from src.memory.retrieval.applicability_models import (
    ApplicabilityContext,
    ApplicabilityResult,
    MemoryApplicabilityProfile,
)
from src.memory.retrieval.interpretation_models import (
    ContextInterpretation,
    ContextSource,
    MemoryScopeInterpretation,
)
from src.memory.retrieval.interpreters import ContextInterpreter, MemoryScopeInterpreter
from src.memory.retrieval.models import RecallRequest, RecallResult
from src.memory.retrieval.protocols import MemoryRetriever
from src.memory.retrieval.rerank import CandidateReranker


@dataclass(frozen=True)
class InterpretedApplicabilityResult:
    recall_result: RecallResult
    context_interpretation: ContextInterpretation
    scope_interpretations: Tuple[MemoryScopeInterpretation, ...]
    applicability_result: ApplicabilityResult


class SemanticRetrievalPipeline:
    """Natural language → recall → independent interpretations → frozen applicability。"""

    def __init__(
        self,
        retriever: MemoryRetriever,
        context_interpreter: ContextInterpreter,
        scope_interpreter: MemoryScopeInterpreter,
        applicability_engine: ApplicabilityEngine | None = None,
        reranker: CandidateReranker | None = None,
    ) -> None:
        self._retriever = retriever
        self._context_interpreter = context_interpreter
        self._scope_interpreter = scope_interpreter
        self._applicability = applicability_engine or ApplicabilityEngine()
        self._reranker = reranker

    def run(
        self,
        query: str,
        *,
        top_k: int = 8,
        query_id: str = "current-query",
        recent_context: Tuple[ContextSource, ...] = (),
    ) -> InterpretedApplicabilityResult:
        recall = self._retriever.recall_request(RecallRequest(query, top_k))
        if not recall.candidates:
            context = ContextInterpretation(
                context=ApplicabilityContext(()),
                facts=(),
                warnings=("context_interpretation_skipped_no_recall_candidates",),
                provider=self._context_interpreter.provider_identity,
            )
            applicability = self._applicability.assess_recall_result(
                recall,
                context.context,
                {},
            )
            return InterpretedApplicabilityResult(recall, context, (), applicability)

        context = self._context_interpreter.interpret(
            query, query_id=query_id, recent_context=recent_context
        )
        expanded_queries = self._context_query_expansions(context)
        if expanded_queries:
            # Expansion is grounded in the provider's explicit current-context
            # values. It is request-local and never becomes a synonym table or
            # a persisted memory rule.
            recall = self._retriever.recall_request(
                RecallRequest(query, top_k, expanded_queries=expanded_queries)
            )
        if self._reranker is not None and recall.candidates:
            recall = self._reranker.rerank(query, recall)
        # Scope interpretation is an optional semantic filter layered on top of
        # lexical/dense recall.  A provider response problem here must not turn
        # an otherwise retrievable memory into a total user-visible failure:
        # preserve the candidate, mark its scope as unknown, and let the
        # applicability engine retain the uncertainty explicitly.
        scope_interpretations = []
        scope_warnings = []
        for candidate in recall.candidates:
            try:
                scope_interpretations.append(
                    self._scope_interpreter.interpret(candidate.snapshot)
                )
            except ProviderError as exc:
                scope_interpretations.append(
                    MemoryScopeInterpretation(
                        profile=MemoryApplicabilityProfile(
                            candidate.snapshot.memory_id, ()
                        ),
                        rules=(),
                        warnings=("scope_interpretation_unavailable",),
                        provider=self._scope_interpreter.provider_identity,
                    )
                )
                scope_warnings.append(
                    "scope_interpretation_unavailable:"
                    f"{exc.provider}:{exc.operation}"
                )
        scopes = tuple(scope_interpretations)
        profiles = {item.profile.memory_id: item.profile for item in scopes}
        applicability = self._applicability.assess_recall_result(
            recall, context.context, profiles
        )
        if scope_warnings:
            applicability = ApplicabilityResult(
                query=applicability.query,
                recall_result=applicability.recall_result,
                assessments=applicability.assessments,
                warnings=(*applicability.warnings, *scope_warnings),
            )
        return InterpretedApplicabilityResult(recall, context, scopes, applicability)

    @staticmethod
    def _context_query_expansions(context: ContextInterpretation) -> Tuple[str, ...]:
        values: list[str] = []
        for fact in context.facts:
            if fact.value.values:
                values.extend(fact.value.values)
        return tuple(dict.fromkeys(value.strip() for value in values if value.strip()))
