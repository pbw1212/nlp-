"""Phase 4A AgentRuntime configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from src.memory.core.config import MemoryConfig
from src.memory.llm.llm_client import LLMClientBase


@dataclass
class AgentRuntimeConfig:
    """System-level orchestration runtime configuration.

    Wraps/references MemoryConfig for provider and domain defaults, while
    housing runtime-owned settings like task3_db_path and routing policies.
    """

    memory_config: MemoryConfig = field(default_factory=MemoryConfig)
    llm_client: Optional[LLMClientBase] = None
    task3_db_path: str = "task_state.db"
    enable_auto_retrieval: bool = True
    default_top_k: int = 8
    max_history_turns: int = 10

    def __post_init__(self) -> None:
        if not self.task3_db_path:
            raise ValueError("task3_db_path 不能为空")
        if self.default_top_k <= 0:
            raise ValueError("default_top_k 必须大于 0")
        if self.max_history_turns < 0:
            raise ValueError("max_history_turns 不能为负数")
