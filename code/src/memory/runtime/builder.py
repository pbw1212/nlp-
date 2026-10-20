"""Production Composition Root for Phase 4A AgentRuntime."""

from __future__ import annotations

from typing import Optional

from src.memory.api.memory_manager import LongTermMemoryManager
from src.memory.core.config import MemoryConfig
from src.memory.conversation.context_manager import resolve_context_window_tokens
from src.memory.retrieval.applicability import ApplicabilityEngine
from src.memory.retrieval.evidence import DeterministicEvidencePipeline
from src.memory.retrieval.facade import DefaultMemoryRetrievalFacade, MemoryRetrievalFacade
from src.memory.retrieval.interpretation_provider import (
    OpenAICompatibleSemanticInterpretationProvider,
    build_semantic_interpretation_provider,
)
from src.memory.retrieval.interpreters import ContextInterpreter, MemoryScopeInterpreter
from src.memory.retrieval.recall import MemoryRecallEngine
from src.memory.retrieval.graph import PersonalizedGraphMemoryRetriever
from src.memory.retrieval.semantic_pipeline import SemanticRetrievalPipeline
from src.memory.retrieval.rerank import OpenAICompatibleCandidateReranker
from src.memory.runtime.agent_runtime import AgentRuntime
from src.memory.runtime.config import AgentRuntimeConfig
from src.memory.runtime.task_router import ProviderTaskBindingResolver
from src.memory.llm.llm_client import build_llm_client
from src.memory.storage.sqlite_manager import SQLiteMemoryManager
from src.memory.task_state.interpreter import OpenAICompatibleTaskStateInterpreter
from src.memory.task_state.repository import TaskStateRepository
from src.memory.task_state.service import TaskStateService
from src.memory.task_state.context import TaskContextBudget, TaskContextBuilder


def build_agent_runtime(config: Optional[AgentRuntimeConfig] = None) -> AgentRuntime:
    """Production composition root assembling Task 1, Task 2, Task 3, and AgentRuntime.

    Construction is strictly local with NO NETWORK SIDE EFFECTS.
    Provider requests occur only during active turn processing.
    """
    cfg = config or AgentRuntimeConfig()
    mem_cfg = cfg.memory_config

    # Task 1 Storage & Read Boundary
    task1_storage = SQLiteMemoryManager(mem_cfg.sqlite_db_path)
    
    # Task 2 Retrieval Components & Facade
    retriever = PersonalizedGraphMemoryRetriever(
        MemoryRecallEngine(task1_storage),
        task1_storage,
    )
    semantic_provider = (
        OpenAICompatibleSemanticInterpretationProvider(
            cfg.llm_client,
            model=mem_cfg.llm_model_name,
            provider=mem_cfg.llm_provider_name,
        )
        if cfg.llm_client is not None
        else build_semantic_interpretation_provider(mem_cfg)
    )
    context_interpreter = ContextInterpreter(provider=semantic_provider)
    scope_interpreter = MemoryScopeInterpreter(provider=semantic_provider)
    applicability_engine = ApplicabilityEngine()
    
    reranker = None
    if mem_cfg.enable_model_rerank:
        reranker_client = cfg.llm_client
        if reranker_client is None:
            reranker_client = build_llm_client(mem_cfg)
        reranker = OpenAICompatibleCandidateReranker(
            reranker_client,
            provider=mem_cfg.llm_provider_name,
            model=mem_cfg.llm_model_name,
        )

    semantic_pipeline = SemanticRetrievalPipeline(
        retriever=retriever,
        context_interpreter=context_interpreter,
        scope_interpreter=scope_interpreter,
        applicability_engine=applicability_engine,
        reranker=reranker,
    )
    evidence_pipeline = DeterministicEvidencePipeline()
    retrieval_facade: MemoryRetrievalFacade = DefaultMemoryRetrievalFacade(
        semantic_pipeline=semantic_pipeline,
        evidence_pipeline=evidence_pipeline,
    )

    # Task 3 State Repository & Service
    task3_repo = TaskStateRepository(db_path=cfg.task3_db_path)
    context_window_tokens, _ = resolve_context_window_tokens(
        mem_cfg.llm_model_name,
        mem_cfg.llm_context_window_tokens,
    )
    task_context_builder = TaskContextBuilder(
        task3_repo,
        budget=TaskContextBudget.from_model_window(
            context_window_tokens,
            reserve_tokens=mem_cfg.context_reserve_tokens,
            compact_at_ratio=mem_cfg.context_compaction_ratio,
        ),
    )
    task3_service = TaskStateService(task3_repo, context_builder=task_context_builder)
    task3_interpreter = (
        OpenAICompatibleTaskStateInterpreter(
            cfg.llm_client,
            provider=mem_cfg.llm_provider_name,
            model=mem_cfg.llm_model_name,
        )
        if cfg.llm_client is not None
        else OpenAICompatibleTaskStateInterpreter.from_config(mem_cfg)
    )

    # Task binding is a per-turn Provider proposal. The deterministic runtime
    # validates IDs/status and applies lifecycle changes; it never selects the
    # sole active Task as an implicit global context.
    binding_resolver = ProviderTaskBindingResolver(
        cfg.llm_client if cfg.llm_client is not None else build_llm_client(mem_cfg),
        provider=mem_cfg.llm_provider_name,
        model=mem_cfg.llm_model_name,
    )

    return AgentRuntime(
        task_service=task3_service,
        retrieval_facade=retrieval_facade,
        task_interpreter=task3_interpreter,
        task_binding_resolver=binding_resolver,
        config=cfg,
    )
