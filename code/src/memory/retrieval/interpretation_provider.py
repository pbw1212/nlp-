from __future__ import annotations

import json
from dataclasses import replace
from typing import Any, Mapping, Protocol

from src.memory.llm.llm_client import LLMClientBase
from src.memory.core.config import MemoryConfig, ProviderMode
from src.memory.llm.errors import ProviderConfigurationError
from src.memory.llm.llm_client import build_llm_client
from src.memory.retrieval.interpretation_models import (
    InterpretationProviderIdentity,
    SemanticInterpretationTask,
)


class SemanticInterpretationProvider(Protocol):
    @property
    def identity(self) -> InterpretationProviderIdentity: ...

    def interpret(self, task: SemanticInterpretationTask) -> Mapping[str, Any]: ...


DEFAULT_STRUCTURED_COMPLETION_TOKENS = 2048


class OpenAICompatibleSemanticInterpretationProvider:
    """复用 Task 1 OpenAI-compatible JSON client，不拥有 HTTP/error/fallback 语义。"""

    def __init__(
        self, client: LLMClientBase, *, model: str, provider: str = "openai-compatible"
    ) -> None:
        self._client = client
        self._identity = InterpretationProviderIdentity(provider, model)

    @property
    def identity(self) -> InterpretationProviderIdentity:
        return self._identity

    @property
    def usage(self) -> Mapping[str, int]:
        usage = getattr(self._client, "usage", {})
        return dict(usage) if isinstance(usage, Mapping) else {}

    def interpret(self, task: SemanticInterpretationTask) -> Mapping[str, Any]:
        user_prompt = json.dumps(
            {
                "task": task.kind.value,
                "prompt_version": task.prompt_version,
                "schema_version": task.schema_version,
                "input": task.payload,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return self._client.generate_json(
            task.system_prompt,
            user_prompt,
            response_schema=dict(task.output_schema),
            schema_name=task.schema_name,
        )


def build_semantic_interpretation_provider(
    config: MemoryConfig,
) -> OpenAICompatibleSemanticInterpretationProvider:
    """真实 semantic interpretation 必须显式 REQUIRED；deterministic test tests 注入 fake。"""
    if config.provider_mode is not ProviderMode.REQUIRED:
        raise ProviderConfigurationError(
            "semantic interpretation real backend requires MEMORY_PROVIDER_MODE=REQUIRED",
            provider=config.llm_provider_name,
            operation="semantic_interpretation.configure",
        )
    # Reasoning models can spend part of the completion budget before emitting
    # the schema-constrained object. Keep the default specific to semantic
    # structured extraction, while preserving an explicit provider-agnostic
    # LLM_MAX_COMPLETION_TOKENS override.
    if config.llm_max_completion_tokens is None:
        config = replace(
            config,
            llm_max_completion_tokens=DEFAULT_STRUCTURED_COMPLETION_TOKENS,
        )
    return OpenAICompatibleSemanticInterpretationProvider(
        build_llm_client(config), model=config.llm_model_name,
        provider=config.llm_provider_name,
    )

