"""Stable on-disk layout for Personal Agent runtime data."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict

from src.memory.core.paths import default_agent_data_dir


def migrate_legacy_project_data(legacy_dir: str, target_dir: str) -> bool:
    """Move the old project-local runtime directory to the global Agent home."""
    legacy = Path(legacy_dir).expanduser().resolve()
    target = Path(target_dir).expanduser().resolve()
    if not legacy.is_dir() or target.exists() or legacy == target:
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(legacy, target)
    except OSError:
        return False
    return True


def migrate_legacy_config(legacy_path: str, target_path: str) -> bool:
    """Move a project-local dotenv file into the global Agent config store."""
    legacy = Path(legacy_path).expanduser().resolve()
    target = Path(target_path).expanduser().resolve()
    if not legacy.is_file() or target.exists() or legacy == target:
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(legacy, target)
    except OSError:
        return False
    return True


class AgentStorageLayout:
    """Creates the product-owned data tree and migrates legacy flat files."""

    def __init__(self, data_dir: str) -> None:
        self.data_dir = Path(data_dir).resolve()
        self.long_term_dir = self.data_dir / "long_term_memory"
        self.conversations_dir = self.data_dir / "conversations"
        self.task_state_dir = self.data_dir / "task_state"
        self.execution_dir = self.data_dir / "execution"
        self.cli_dir = self.data_dir / "cli"
        self.workspaces_dir = self.data_dir / "workspaces"
        self.config_dir = self.data_dir / "config"
        self.plugins_dir = self.data_dir / "plugins"
        self.cache_dir = self.data_dir / "cache"
        for directory in (
            self.long_term_dir,
            self.conversations_dir,
            self.task_state_dir,
            self.execution_dir,
            self.cli_dir,
            self.workspaces_dir,
            self.config_dir,
            self.plugins_dir,
            self.cache_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

        self.memory_db_path = self._migrate("memory.db", self.long_term_dir / "memory.db")
        self.task_db_path = self._migrate("task_state.db", self.task_state_dir / "task_state.db")
        self.journal_db_path = self._migrate("agent_journal.db", self.execution_dir / "agent_journal.db")
        self.events_db_path = self._migrate("agent_events.db", self.execution_dir / "agent_events.db")
        self.conversation_db_path = self._migrate("conversation.db", self.conversations_dir / "conversation.db")
        self.retrieval_trace_path = self._migrate("retrieval-traces.jsonl", self.execution_dir / "retrieval-traces.jsonl")
        self.cli_history_path = self._migrate("cli_history", self.cli_dir / "cli_history")
        self.log_path = self._migrate("agent.log", self.execution_dir / "agent.log")
        self.workspace_scope_path = self._migrate("workspace_scope.json", self.workspaces_dir / "workspace_scope.json")
        self.config_env_path = self.config_dir / ".env.local"
        self.mcp_config_path = self.config_dir / "mcp.json"

    def _migrate(self, legacy_name: str, structured_path: Path) -> str:
        if structured_path.exists():
            return str(structured_path)
        legacy_path = self.data_dir / legacy_name
        if legacy_path.exists():
            try:
                os.replace(legacy_path, structured_path)
            except OSError:
                return str(legacy_path)
        return str(structured_path)

    def describe(self) -> Dict[str, str]:
        return {
            "data_dir": str(self.data_dir),
            "memory_db": self.memory_db_path,
            "long_term_memory": str(self.long_term_dir),
            "conversation_db": self.conversation_db_path,
            "conversations": str(self.conversations_dir),
            "conversation_store": str(self.conversations_dir),
            "task_db": self.task_db_path,
            "task_state": str(self.task_state_dir),
            "journal_db": self.journal_db_path,
            "events_db": self.events_db_path,
            "retrieval_traces": self.retrieval_trace_path,
            "execution": str(self.execution_dir),
            "cli_history": self.cli_history_path,
            "cli": str(self.cli_dir),
            "workspace_scope": self.workspace_scope_path,
            "workspaces": str(self.workspaces_dir),
            "config": str(self.config_dir),
            "config_env": str(self.config_env_path),
            "mcp_config": str(self.mcp_config_path),
            "plugins": str(self.plugins_dir),
            "cache": str(self.cache_dir),
        }
