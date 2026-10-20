"""Personal Agent CLI Application surface and interactive chat console."""

from __future__ import annotations

import datetime
import io
import json
import logging
import os
import re
import sys
import threading
import time
import uuid
from contextvars import ContextVar
from contextlib import redirect_stdout
from dataclasses import asdict, dataclass, field, is_dataclass, replace
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.memory.agent.journal import AgentExecutionJournal
from src.memory.agent.main_agent import MainAgent
from src.memory.agent.models import (
    ActionConfirmation,
    AgentRequest,
    AgentResult,
    AgentResultStatus,
    ConfirmationDecision,
    TurnRequest,
)
from src.memory.agent.reasoner import OpenAICompatibleAgentReasoner
from src.memory.agent.prompt_composer import PromptComposer
from src.memory.agent.tooling import ToolEffect, ToolExecutionRequest
from src.memory.api.memory_manager import LongTermMemoryManager
from src.memory.app.tools import build_product_tool_registry
from src.memory.app.mcp import McpManager
from src.memory.app.workspace import WorkspaceManager, WorkspaceScopeError
from src.memory.app.storage import AgentStorageLayout, default_agent_data_dir
from src.memory.core.config import MemoryConfig, ProviderMode
from src.memory.core.diagnostics import log_boundary_failure
from src.memory.core.errors import MemoryNotFoundError
from src.memory.core.schema import MemoryItem, MemoryType, ProvenanceAuthority
from src.memory.llm.errors import ProviderConfigurationError, ProviderError
from src.memory.llm.llm_client import LLMClientBase, build_llm_client
from src.memory.runtime.agent_runtime import AgentRuntime
from src.memory.runtime.builder import build_agent_runtime
from src.memory.runtime.config import AgentRuntimeConfig
from src.memory.runtime.events import AgentEvent, AgentEventStore, AgentEventStream
from src.memory.runtime.models import ToolCallResult, ToolCallStatus
from src.memory.runtime.task_router import UNBOUND_TASK_ID
from src.memory.task_state.models import EntryKind
from src.memory.conversation import (
    ConversationRepository,
    ConversationRole,
)
from src.memory.conversation.context_manager import ConversationContextManager


logger = logging.getLogger(__name__)


# ANSI bracketed-paste markers are terminal protocol boundaries, not user
# content. When bracketed paste is enabled, readline keeps all pasted lines
# in one editing buffer and returns them from one input() call. Removing the
# markers as a defensive fallback also keeps the input layer safe on terminals
# that send the protocol markers without readline consuming them.
_BRACKETED_PASTE_START = "\x1b[200~"
_BRACKETED_PASTE_END = "\x1b[201~"

# Keep the CLI's supported composer shortcuts on readline's documented
# editing commands. The mappings are terminal/input behavior only; they do not
# interpret the natural-language content of a turn.
_READLINE_KEY_BINDINGS = (
    '"\\e[A": previous-history',
    '"\\eOA": previous-history',
    '"\\e[B": next-history',
    '"\\eOB": next-history',
    '"\\C-p": previous-history',
    '"\\C-n": next-history',
    '"\\C-r": reverse-search-history',
    '"\\C-s": forward-search-history',
    '"\\C-l": clear-screen',
    '"\\C-a": beginning-of-line',
    '"\\C-e": end-of-line',
    '"\\C-b": backward-char',
    '"\\C-f": forward-char',
    '"\\C-k": kill-line',
    '"\\C-u": unix-line-discard',
    '"\\C-w": unix-word-rubout',
    '"\\C-h": backward-delete-char',
    '"\\C-y": yank',
    '"\\e\\b": backward-word',
    '"\\e\\f": forward-word',
    '"\\e\\d": kill-word',
)


@dataclass
class _MemoryUpdateTurnContext:
    """Application-owned provenance for the current Agent memory-tool call."""

    user_input: str
    turn_id: str
    session_id: str
    message_id: str
    tool_call_ids: List[str] = field(default_factory=list)


class OutputMode(str, Enum):
    """Controls how much execution telemetry the interactive CLI renders."""

    USER = "USER"
    DEVELOPER = "DEVELOPER"

    @classmethod
    def parse(cls, value: str | "OutputMode") -> "OutputMode":
        if isinstance(value, cls):
            return value
        normalized = str(value).strip().upper()
        aliases = {
            "USER": cls.USER,
            "用户": cls.USER,
            "DEVELOPER": cls.DEVELOPER,
            "DEVELOPMENT": cls.DEVELOPER,
            "开发": cls.DEVELOPER,
        }
        try:
            return aliases[normalized]
        except KeyError as exc:
            raise ValueError("输出模式必须是 user 或 developer") from exc


def _setup_readline(data_dir: str, history_path: Optional[str] = None) -> Tuple[Optional[str], bool]:
    """Safely enables readline terminal line editing & persistent CLI history."""
    hist_file = os.path.abspath(history_path or os.path.join(os.path.abspath(data_dir), "cli_history"))
    try:
        import readline
        try:
            # Without this setting, a multiline paste can feed each embedded
            # newline to input() as a separate submitted line/command. This
            # is a readline capability setting, not a semantic input rule.
            readline.parse_and_bind("set enable-bracketed-paste on")  # type: ignore[attr-defined]
            for binding in _READLINE_KEY_BINDINGS:
                readline.parse_and_bind(binding)  # type: ignore[attr-defined]
        except Exception as exc:
            # Some libedit/readline variants do not expose one of these
            # settings. Keep line editing and history available; marker
            # cleanup below remains useful as a defensive compatibility path.
            logger.info(
                "readline_bracketed_paste_unavailable error_type=%s",
                type(exc).__name__,
            )
        try:
            readline.set_history_length(1000)  # type: ignore[attr-defined]
        except Exception as exc:
            log_boundary_failure(logger, "readline_history_limit_failed", exc)
        if os.path.exists(hist_file):
            try:
                readline.read_history_file(hist_file)  # type: ignore[attr-defined]
            except Exception as exc:
                log_boundary_failure(logger, "readline_history_read_failed", exc)
        return hist_file, True
    except ImportError as exc:
        logger.info("readline_unavailable error_type=%s", type(exc).__name__)
        return hist_file, False
    except Exception as exc:
        log_boundary_failure(logger, "readline_initialization_unavailable", exc)
        return hist_file, False


def _normalize_cli_input(raw_input: str) -> str:
    """Removes terminal-only bracketed-paste markers while preserving content."""
    return raw_input.replace(_BRACKETED_PASTE_START, "").replace(_BRACKETED_PASTE_END, "")


def _read_cli_submission(prompt: str) -> str:
    """Read one logical submission while retaining readline editing.

    Bracketed paste is the normal terminal protocol for multiline clipboard
    input. Some programmatic PTY clients (including automation harnesses) send
    the same payload as ordinary bytes without the bracket markers. In that
    case GNU readline returns the first physical line and leaves the rest
    readable on the TTY. Drain only already-buffered TTY lines and join them
    into the same submission. Non-TTY stdin keeps its conventional one-line
    command semantics, so shell pipes and test doubles are not changed.
    """
    first = input(prompt)
    if not (
        getattr(sys.stdin, "isatty", lambda: False)()
        and getattr(sys.stdout, "isatty", lambda: False)()
    ):
        return first

    import select

    lines = [first]
    while select.select([sys.stdin], [], [], 0)[0]:
        lines.append(input(""))
    return "\n".join(lines)


def _save_readline_history(hist_file: Optional[str]) -> None:
    if not hist_file:
        return
    try:
        import readline
        readline.write_history_file(hist_file)  # type: ignore[attr-defined]
    except Exception as exc:
        log_boundary_failure(logger, "readline_history_write_failed", exc)
        os.makedirs(os.path.dirname(os.path.abspath(hist_file)), exist_ok=True)
        with open(hist_file, "a", encoding="utf-8"):
            pass


def _read_picker_key() -> str:
    """Read one navigation key for the small cross-platform session picker."""
    if os.name == "nt":
        import msvcrt

        key = msvcrt.getwch()
        if key in ("\x00", "\xe0"):
            key = msvcrt.getwch()
            return {"H": "UP", "P": "DOWN", "K": "LEFT", "M": "RIGHT"}.get(key, "")
        if key in ("\r", "\n"):
            return "ENTER"
        if key == "\x1b":
            return "ESC"
        if key == "\x03":
            return "ESC"
        return key.lower()

    import select
    import termios
    import tty

    fd = sys.stdin.fileno()
    previous = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        key = sys.stdin.read(1)
        if key == "\x1b":
            # Arrow keys arrive as ESC [ A/B.  Do not block if this was a
            # standalone Escape used to cancel the picker.
            sequence = ""
            while select.select([sys.stdin], [], [], 0.05)[0] and len(sequence) < 2:
                sequence += sys.stdin.read(1)
            return {"[A": "UP", "[B": "DOWN", "[C": "RIGHT", "[D": "LEFT"}.get(sequence, "ESC")
        if key in ("\r", "\n"):
            return "ENTER"
        if key == "\x03":
            return "ESC"
        return key.lower()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, previous)


class PersonalAgentApp:
    """Minimal persistent Personal Agent Product CLI application."""

    def __init__(
        self,
        data_dir: Optional[str] = None,
        config: Optional[MemoryConfig] = None,
        llm_client: Optional[LLMClientBase] = None,
        output_mode: Optional[str | OutputMode] = None,
        execution_mode: str = "single",
        collaboration_limits: Optional[Any] = None,
    ) -> None:
        if execution_mode not in {"single", "multi_fixed"}:
            raise ValueError("execution_mode must be single or multi_fixed")
        self.execution_mode = execution_mode
        self.collaboration = None
        self.data_dir = os.path.abspath(data_dir or default_agent_data_dir())
        os.makedirs(self.data_dir, exist_ok=True)
        self.storage_layout = AgentStorageLayout(self.data_dir)
        # The product's local tools are scoped by a user-managed list.  The
        # launcher starts from the repository working directory, so that is
        # the initial visible/current folder for a new data directory.
        self.workspace = WorkspaceManager(self.data_dir, default_root=os.getcwd())
        # Development mode is the default while the product is under active
        # integration testing.  Switch the release default to USER later by
        # changing this one environment fallback, without changing telemetry.
        self.output_mode = OutputMode.parse(
            output_mode or os.environ.get("AGENT_OUTPUT_MODE", "DEVELOPER")
        )

        self.memory_db_path = self.storage_layout.memory_db_path
        self.task_db_path = self.storage_layout.task_db_path
        self.journal_db_path = self.storage_layout.journal_db_path
        self.events_db_path = self.storage_layout.events_db_path
        self.conversation_db_path = self.storage_layout.conversation_db_path
        self.retrieval_trace_path = self.storage_layout.retrieval_trace_path

        # 1. Memory & Provider Config
        if config is None:
            self.config = MemoryConfig.from_env(config_path=self.storage_layout.config_env_path)
            self.config.sqlite_db_path = self.memory_db_path
            self.config.provider_mode = ProviderMode.REQUIRED
        else:
            self.config = config
            self.config.sqlite_db_path = self.memory_db_path

        # multi_fixed is an explicitly selected P1 mode. All model operations,
        # including runtime interpretation, share one durable turn budget.
        if execution_mode == "multi_fixed":
            from src.memory.collaboration.budget import CollaborationLimits
            limits = collaboration_limits or CollaborationLimits()
            self.config = replace(
                self.config,
                llm_max_retries=1,
                llm_request_timeout_seconds=min(self.config.llm_request_timeout_seconds, 30.0),
                llm_max_completion_tokens=min(
                    self.config.llm_max_completion_tokens or limits.max_completion_tokens,
                    limits.max_completion_tokens,
                ),
            )
        # 2. Build LLM Client
        if llm_client is not None:
            self.llm_client = llm_client
        else:
            try:
                self.llm_client = build_llm_client(self.config)
            except ProviderConfigurationError:
                raise RuntimeError(
                    f"未配置 API 凭据，请在 .env.local 中配置 {self.config.llm_api_key_env} 后重试。"
                )
            except Exception as exc:
                metadata = log_boundary_failure(logger, "provider_client_initialization_failed", exc)
                raise RuntimeError(
                    f"初始化 Provider 客户端失败 ({metadata['error_type']})"
                ) from exc

        if execution_mode == "multi_fixed":
            from src.memory.collaboration.budget import BudgetedLLMClient
            from src.memory.collaboration.repository import CoordinationRepository
            self.coordination_repository = CoordinationRepository(
                str(self.storage_layout.execution_dir / "coordination.db")
            )
            self.llm_client = BudgetedLLMClient(self.llm_client, self.coordination_repository, limits)

        # 3. Task 1 Long-term Memory Manager.  The Agent may invoke the
        # registered internal memory tool once per user turn.  The callback
        # below binds that tool to the exact current user message.
        self.long_term_memory_mgr = LongTermMemoryManager(
            db_path=self.memory_db_path,
            llm_client=self.llm_client,
            config=self.config,
        )
        self._memory_lock = threading.RLock()
        self._memory_turn_context: ContextVar[Optional[_MemoryUpdateTurnContext]] = ContextVar(
            "personal_agent_memory_turn_context",
            default=None,
        )

        # 4. AgentRuntime
        runtime_cfg = AgentRuntimeConfig(
            memory_config=self.config,
            llm_client=self.llm_client,
            task3_db_path=self.task_db_path,
        )
        self.runtime: AgentRuntime = build_agent_runtime(config=runtime_cfg)

        # 5. Execution Journal & Conversation Repository
        self.journal = AgentExecutionJournal(db_path=self.journal_db_path)
        self.event_store = AgentEventStore(db_path=self.events_db_path)
        self.event_stream = AgentEventStream(self.event_store)
        self.conversation_repo = ConversationRepository(db_path=self.conversation_db_path)
        self.conversation_context_manager = ConversationContextManager(
            self.conversation_repo,
            self.llm_client,
            model_name=self.config.llm_model_name,
            configured_context_window_tokens=self.config.llm_context_window_tokens,
            reserve_tokens=self.config.context_reserve_tokens,
            compact_at_ratio=self.config.context_compaction_ratio,
            keep_recent_tokens=self.config.context_keep_recent_tokens,
        )

        # 6. Reasoner & Product Tool Registry
        self.reasoner = OpenAICompatibleAgentReasoner(
            llm_client=self.llm_client,
            prompt_composer=PromptComposer(workspace_manager=self.workspace),
        )
        self.registry = build_product_tool_registry(
            workspace=self.workspace,
            memory_update_callback=self._execute_memory_update_tool,
        )
        self.mcp_manager = McpManager(
            str(self.storage_layout.mcp_config_path), self.workspace, self.registry
        )
        self.mcp_manager.refresh()

        # 7. MainAgent
        self.agent = MainAgent(
            runtime=self.runtime,
            journal=self.journal,
            registry=self.registry,
            reasoner=self.reasoner,
            memory_writer=self._write_natural_memory,
        )
        if execution_mode == "multi_fixed":
            from src.memory.collaboration.coordinator import CollaborationCoordinator
            self.collaboration = CollaborationCoordinator(
                runtime=self.runtime, journal=self.journal, registry=self.registry,
                client=self.llm_client, repository=self.coordination_repository,
                memory_repository=self.long_term_memory_mgr.sqlite_mgr,
                prompt_composer=PromptComposer(workspace_manager=self.workspace),
            )
            self.agent.prepared_runner = self.collaboration

        # 8. Setup CLI Line Editing & Persistent History
        self.cli_history_file, self.readline_enabled = _setup_readline(
            self.data_dir, self.storage_layout.cli_history_path
        )

        # 9. Every process starts a fresh, lazy Conversation Session.  The
        # session ID exists in memory for the current process, but its row and
        # transcript are created only when the first real conversation turn
        # arrives.  No prior Session or Task is implicitly restored here.
        self.active_task_id: Optional[str] = None
        self.active_session_id = f"session_{uuid.uuid4().hex[:12]}"
        self._session_persisted = False
        self._pending_session_title: Optional[str] = None
        self.pending_confirmation_id: Optional[str] = None
        self.pending_turn_id: Optional[str] = None
        self._last_result: Optional[AgentResult] = None
        self._restore_pending_confirmation()

    def close(self) -> None:
        """Close application-owned external tool resources."""
        self.mcp_manager.close()

    def _restore_pending_confirmation(self) -> None:
        """Restore the newest durable confirmation after a process restart."""
        if not self.active_task_id:
            return
        pending = self.journal.list_pending_actions(task_id=self.active_task_id)
        if pending:
            self.pending_confirmation_id = pending[0].pending_action_id

    def _require_no_pending_confirmation(self) -> None:
        if self.pending_confirmation_id:
            raise ValueError("当前有等待确认的操作，请先输入 /confirm 或 /reject。")

    def _require_active_task(self) -> str:
        """Require an explicit/current Task binding for Task commands."""
        if not self.active_task_id:
            raise ValueError("当前会话尚未绑定 Task；请让 Agent 在对话中判断，或使用 /new 创建任务。")
        return self.active_task_id

    def run_direct_utility_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
    ) -> ToolCallResult:
        """Run an explicitly requested local utility without an LLM round trip."""
        spec = self.registry.get_spec(tool_name)
        executor = self.registry.get_executor(tool_name)
        if (
            spec is None
            or executor is None
            or spec.effect != ToolEffect.READ_ONLY
            or not spec.returns_user_facing_result
        ):
            raise ValueError(f"工具 '{tool_name}' 不是可直接执行的只读工具")
        valid, message = self.registry.validate_arguments(tool_name, arguments)
        if not valid:
            raise ValueError(message)
        request_id = uuid.uuid4().hex[:12]
        return executor.execute(
            ToolExecutionRequest(
                tool_call_id=f"cli_{tool_name}_{request_id}",
                tool_name=tool_name,
                arguments=arguments,
                idempotency_key=f"cli_idemp_{request_id}",
                agent_run_id=f"cli_run_{request_id}",
                agent_step_id=f"cli_step_{request_id}",
            )
        )

    def run_direct_external_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
    ) -> ToolCallResult:
        """Run a registered read-only external tool for an explicit CLI command."""
        if tool_name not in {"web_search", "web_fetch", "weather"}:
            raise ValueError(f"工具 '{tool_name}' 不是外部只读工具")
        spec = self.registry.get_spec(tool_name)
        executor = self.registry.get_executor(tool_name)
        if spec is None or executor is None or spec.effect is not ToolEffect.READ_ONLY:
            raise ValueError(f"外部工具 '{tool_name}' 不可用")
        valid, message = self.registry.validate_arguments(tool_name, arguments)
        if not valid:
            raise ValueError(message)
        request_id = uuid.uuid4().hex[:12]
        return executor.execute(
            ToolExecutionRequest(
                tool_call_id=f"cli_{tool_name}_{request_id}",
                tool_name=tool_name,
                arguments=arguments,
                idempotency_key=f"cli_idemp_{request_id}",
                agent_run_id=f"cli_run_{request_id}",
                agent_step_id=f"cli_step_{request_id}",
            )
        )

    def _ensure_persisted_session(self) -> str:
        """Materialize the current process session at its first real use."""
        if self._session_persisted:
            return self.active_session_id
        existing = self.conversation_repo.get_session(self.active_session_id)
        if existing is not None:
            # This is only possible for an explicitly resumed session; never
            # silently reinterpret it as the startup path.
            self._session_persisted = True
            return self.active_session_id
        session = self.conversation_repo.create_session(
            session_id=self.active_session_id,
            title=self._pending_session_title,
            active_task_id=self.active_task_id,
            **self._workspace_snapshot(),
        )
        self._session_persisted = True
        self._emit_event(
            "thread.started",
            session_id=session.session_id,
            payload={"task_id": self.active_task_id, "title": session.title},
        )
        return session.session_id

    def _workspace_snapshot(self) -> Dict[str, Any]:
        """Return the current folder scope in ConversationRepository format."""
        snapshot = self.workspace.snapshot()
        roots = tuple(snapshot.get("roots") or ())
        current_root = snapshot.get("current_root")
        if not roots or not current_root:
            raise ValueError("创建或恢复会话前，至少需要选择一个 Agent 工作文件夹")
        return {
            "workspace_roots": roots,
            "current_workspace_root": current_root,
        }

    def _restore_session_workspace(self, session: Any) -> None:
        """Restore a session-bound folder scope without falling back to another session."""
        roots = tuple(getattr(session, "workspace_roots", ()) or ())
        current_root = getattr(session, "current_workspace_root", None)
        if not roots or not current_root:
            # Sessions created before workspace binding inherit the existing
            # configured scope once, then become fully session-bound.
            snapshot = self._workspace_snapshot()
            roots = snapshot["workspace_roots"]
            current_root = snapshot["current_workspace_root"]
            self.conversation_repo.update_session_workspace(
                session.session_id, roots, current_root
            )
        self.workspace.replace_scope(list(roots), current_root)

    def _persist_active_session_workspace(self) -> None:
        if not getattr(self, "active_session_id", None) or not getattr(self, "_session_persisted", False):
            return
        snapshot = self._workspace_snapshot()
        self.conversation_repo.update_session_workspace(
            self.active_session_id,
            snapshot["workspace_roots"],
            snapshot["current_workspace_root"],
        )

    def list_tasks(self) -> List[Dict[str, Any]]:
        """Returns list of tasks with metadata."""
        tasks = self.runtime.task_service.repository.list_tasks()
        res = []
        for t in tasks:
            working_ctx = self.runtime.task_service.context_builder.build(
                t.task_id,
                include_inactive=True,
            )
            summary_text = "通用对话任务"
            if working_ctx and working_ctx.structured and working_ctx.structured.goal:
                summary_text = working_ctx.structured.goal[0].content
            res.append({
                "task_id": t.task_id,
                "status": t.status.value,
                "created_at": t.created_at,
                "is_active": (t.task_id == self.active_task_id),
                "summary": summary_text,
            })
        return res

    def create_new_task(self, title: Optional[str] = None) -> str:
        """Creates a new Task 3 task and switches to it."""
        self._require_no_pending_confirmation()
        task_num = len(self.runtime.task_service.repository.list_tasks()) + 1
        new_id = f"task_{task_num}_{uuid.uuid4().hex[:6]}"
        self.runtime.task_service.create_task(new_id)
        if title and title.strip():
            # A user-provided task title is an explicit initial goal.  Persist
            # it in the Task 3 ledger so /tasks and /status survive restart.
            self.runtime.task_service.repository.add_entry(
                new_id,
                EntryKind.GOAL,
                title.strip(),
            )
        self.active_task_id = new_id
        if self.active_session_id and self._session_persisted:
            self.conversation_repo.update_session_task(self.active_session_id, new_id)
        return new_id

    def switch_task(self, task_id: str) -> bool:
        """Switches active task if task_id exists."""
        self._require_no_pending_confirmation()
        tasks = self.runtime.task_service.repository.list_tasks()
        if any(t.task_id == task_id for t in tasks):
            self.active_task_id = task_id
            if self.active_session_id and self._session_persisted:
                self.conversation_repo.update_session_task(self.active_session_id, task_id)
            return True
        return False

    def create_new_session(self, title: Optional[str] = None) -> str:
        """Starts a fresh lazy conversation session without inheriting a Task."""
        self._require_no_pending_confirmation()
        self.active_session_id = f"session_{uuid.uuid4().hex[:12]}"
        self.active_task_id = None
        self._session_persisted = False
        self._pending_session_title = title.strip() if title and title.strip() else None
        self.pending_confirmation_id = None
        self.pending_turn_id = None
        self._last_result = None
        self.agent.last_turn_context = None
        return self.active_session_id

    def list_sessions(self) -> List[Dict[str, Any]]:
        """Lists conversation sessions with active marker."""
        sessions = self.conversation_repo.list_sessions()
        return [
            {
                "session_id": s.session_id,
                "title": s.title,
                "active_task_id": s.active_task_id,
                "topic": self._session_topic(s.session_id, s.title),
                "workspace_roots": list(s.workspace_roots),
                "current_workspace_root": s.current_workspace_root,
                "updated_at": datetime.datetime.fromtimestamp(s.updated_at).strftime("%Y-%m-%d %H:%M:%S"),
                "is_active": (s.session_id == self.active_session_id),
            }
            for s in sessions
        ]

    def _session_topic(self, session_id: str, fallback_title: str) -> str:
        """Use the first user sentence as a stable, provider-free session topic."""
        first_user = next(
            (
                message.content.strip()
                for message in self.conversation_repo.list_messages(session_id)
                if message.role is ConversationRole.USER and message.content.strip()
            ),
            "",
        )
        if not first_user:
            return fallback_title
        first_sentence = re.split(r"(?<=[。！？!?；;])|[\r\n]", first_user, maxsplit=1)[0].strip()
        topic = first_sentence or first_user
        return topic if len(topic) <= 72 else topic[:69].rstrip() + "..."

    def print_session_transcript(self, session_id: str) -> None:
        """Render the complete durable transcript after a session is resumed."""
        session = self.conversation_repo.get_session(session_id)
        if session is None:
            return
        print(f"\n=== 已恢复会话：{self._session_topic(session_id, session.title)} ===")
        print(f"Session ID: {session_id}")
        messages = self.conversation_repo.list_messages(session_id)
        if not messages:
            print("（该会话还没有对话内容）")
            return
        labels = {
            ConversationRole.USER: "你",
            ConversationRole.ASSISTANT: "Agent",
            ConversationRole.SYSTEM: "系统",
            ConversationRole.TOOL: "工具",
        }
        print("--- 完整历史对话 ---")
        for message in messages:
            print(f"\n{labels.get(message.role, message.role.value)} > {message.content}")
        print("--- 历史对话结束；现在继续输入 ---")

    def resume_session(self, session_id: str) -> bool:
        """Resumes an existing conversation session and its bound task."""
        self._require_no_pending_confirmation()
        sess = self.conversation_repo.get_session(session_id)
        if not sess:
            return False
        self._restore_session_workspace(sess)
        self.active_session_id = sess.session_id
        self._session_persisted = True
        self._pending_session_title = None
        self.conversation_repo.touch_session(session_id)

        # Restore bound active_task_id if present
        tasks = self.runtime.task_service.repository.list_tasks()
        if sess.active_task_id and any(t.task_id == sess.active_task_id for t in tasks):
            self.active_task_id = sess.active_task_id
        else:
            self.active_task_id = None
        # Pending actions are Task-owned durable state. A fresh process must
        # not restore them implicitly, but an explicit /resume of the owning
        # Session must make the same safety gate visible again.
        self.pending_confirmation_id = None
        self._restore_pending_confirmation()
        self._emit_event(
            "thread.resumed",
            session_id=sess.session_id,
            payload={"task_id": self.active_task_id},
        )
        return True

    def remember_explicit(self, content: str) -> Dict[str, Any]:
        """Explicitly ingests content into Task 1 long-term memory."""
        with self._memory_lock:
            res = self.long_term_memory_mgr.process_user_interaction(content, is_explicit=True)
        return res

    def _write_natural_memory(self, content: str, turn_id: str) -> Dict[str, Any]:
        """Persist a Provider-routed natural-language memory request."""
        with self._memory_lock:
            return self.long_term_memory_mgr.process_user_interaction(
                content,
                is_explicit=True,
                source_event_id=f"natural-memory:{turn_id}",
                session_id=self.active_session_id,
            )

    def _execute_memory_update_tool(self, request: ToolExecutionRequest) -> ToolCallResult:
        """Execute the Agent-selected current-turn memory update.

        The model can select the tool, but cannot supply the source text or
        provenance identifiers.  Those values come from the application turn
        context installed immediately around ``MainAgent.run``.
        """
        context = self._memory_turn_context.get()
        if context is None:
            return ToolCallResult(
                tool_call_id=request.tool_call_id,
                tool_name=request.tool_name,
                arguments=request.arguments,
                status=ToolCallStatus.FAILED,
                error_message="当前没有可绑定的用户 turn，长期记忆更新未执行。",
            )
        context.tool_call_ids.append(request.tool_call_id)
        try:
            with self._memory_lock:
                result = self.long_term_memory_mgr.update_long_term_memory_for_turn(
                    context.user_input,
                    source_event_id=f"memory-update:{context.turn_id}",
                    session_id=context.session_id,
                    message_id=context.message_id,
                )
            return ToolCallResult(
                tool_call_id=request.tool_call_id,
                tool_name=request.tool_name,
                arguments=request.arguments,
                status=ToolCallStatus.SUCCESS,
                result_content=json.dumps(
                    {
                        "schema_version": "long_term_memory_update.v1",
                        "event_id": result.get("event_id"),
                        "processed": bool(result.get("processed", True)),
                        "updated": bool(result.get("updated")),
                        "idempotent_replay": bool(result.get("idempotent_replay")),
                        "lifecycle_changes": result.get("lifecycle_changes", 0),
                        "new_memories_created": result.get("new_memories_created", 0),
                        "pending_events": result.get("pending_events", 0),
                    },
                    ensure_ascii=False,
                ),
            )
        except ProviderError:
            return ToolCallResult(
                tool_call_id=request.tool_call_id,
                tool_name=request.tool_name,
                arguments=request.arguments,
                status=ToolCallStatus.FAILED,
                error_message="长期记忆 Provider 更新失败；本轮原始事件已保留，可在后续更新中重试。",
            )
        except Exception as exc:
            metadata = log_boundary_failure(
                logger,
                "agent_memory_update_tool_failed",
                exc,
                turn_id=context.turn_id,
                session_id=context.session_id,
            )
            return ToolCallResult(
                tool_call_id=request.tool_call_id,
                tool_name=request.tool_name,
                arguments=request.arguments,
                status=ToolCallStatus.FAILED,
                error_message=(
                    "长期记忆更新失败；本轮原始事件已保留，可在后续更新中重试。"
                    f" ({metadata['error_type']})"
                ),
            )

    def get_memory_update_status(self) -> Dict[str, Any]:
        """Return diagnostics for Agent-selected synchronous memory updates."""
        return {
            "mode": "agent_tool",
            "tool_name": "update_long_term_memory",
            "pending_events": len(self.long_term_memory_mgr.sqlite_mgr.list_pending_events()),
            "update_is_agent_selected": True,
            "threshold_trigger_enabled": False,
            "time_trigger_enabled": False,
        }

    def list_memories(self) -> List[Dict[str, Any]]:
        """Lists active long-term memories from Task 1 store."""
        snapshots = self.long_term_memory_mgr.sqlite_mgr.list_current()
        res = []
        for s in snapshots:
            res.append({
                "memory_id": s.memory_id,
                "type": s.memory_type.value,
                "content": s.revision.content,
                "granularity": s.granularity.value,
            })
        return res

    @staticmethod
    def _is_profile_memory(item: MemoryItem) -> bool:
        return item.memory_type in {MemoryType.CORE_PROFILE, MemoryType.PREFERENCE_RULES}

    def _profile_memory_payload(self, item: MemoryItem) -> Dict[str, Any]:
        revision = item.current_revision
        if revision is None:
            raise MemoryNotFoundError(item.memory_id)
        refs = self.long_term_memory_mgr.get_revision_provenance(revision.revision_id)
        authorities = sorted({ref.authority.value for ref in refs})
        manually_confirmed = ProvenanceAuthority.EXPLICIT_USER.value in authorities
        return {
            "memory_id": item.memory_id,
            "type": item.memory_type.value,
            "content": revision.content,
            "granularity": item.granularity.value,
            "confidence": revision.confidence,
            "importance_score": item.importance_score,
            "retention": item.retention_disposition.value,
            "semantic_validity": revision.semantic_validity.value,
            "is_pinned": item.is_pinned,
            "auto_managed": not item.is_pinned,
            "source": "MANUAL" if manually_confirmed else "INFERRED",
            "source_authorities": authorities,
            "updated_at": item.updated_at,
        }

    def get_profile_repository(self) -> Dict[str, Any]:
        """Return the user-facing profile projection over canonical memory."""
        active = [
            self._profile_memory_payload(item)
            for item in self.long_term_memory_mgr.get_active_memories()
            if self._is_profile_memory(item) and item.current_revision is not None
        ]
        recycled = [
            self._profile_memory_payload(item)
            for item in self.long_term_memory_mgr.get_recycle_bin_memories()
            if self._is_profile_memory(item) and item.current_revision is not None
        ]
        active.sort(key=lambda item: float(item["updated_at"]), reverse=True)
        recycled.sort(key=lambda item: float(item["updated_at"]), reverse=True)
        return {
            "preferences": active,
            "recycled": recycled,
            "counts": {
                "active": len(active),
                "inferred": sum(item["source"] == "INFERRED" for item in active),
                "protected": sum(bool(item["is_pinned"]) for item in active),
                "recycled": len(recycled),
            },
            "memory_update": self.get_memory_update_status(),
        }

    def create_profile_preference(self, content: str) -> Dict[str, Any]:
        """Create or reconcile a manually confirmed profile preference."""
        result = self.remember_explicit(content)
        memory_id = str(result.get("memory_id") or "")
        if not memory_id or memory_id == "noop":
            raise MemoryNotFoundError(memory_id or "profile-preference")
        with self._memory_lock:
            self.long_term_memory_mgr.pin_memory(memory_id, True)
            item = self.long_term_memory_mgr.sqlite_mgr.get_memory(memory_id)
        if item is None or not self._is_profile_memory(item):
            raise MemoryNotFoundError(memory_id)
        return self._profile_memory_payload(item)

    def update_profile_preference(self, memory_id: str, content: str) -> Dict[str, Any]:
        """Apply a target-explicit manual correction and protect the result."""
        with self._memory_lock:
            existing = self.long_term_memory_mgr.sqlite_mgr.get_memory(memory_id)
            if existing is None or not self._is_profile_memory(existing):
                raise MemoryNotFoundError(memory_id)
            updated = self.long_term_memory_mgr.correct_memory_item(
                memory_id,
                content,
                source_event_id=f"profile-edit:{uuid.uuid4().hex}",
            )
            self.long_term_memory_mgr.pin_memory(memory_id, True)
        return self._profile_memory_payload(updated)

    def set_profile_preference_pinned(self, memory_id: str, pinned: bool) -> Dict[str, Any]:
        with self._memory_lock:
            item = self.long_term_memory_mgr.sqlite_mgr.get_memory(memory_id)
            if item is None or not self._is_profile_memory(item):
                raise MemoryNotFoundError(memory_id)
            self.long_term_memory_mgr.pin_memory(memory_id, pinned)
            updated = self.long_term_memory_mgr.sqlite_mgr.get_memory(memory_id)
        if updated is None:
            raise MemoryNotFoundError(memory_id)
        return self._profile_memory_payload(updated)

    def forget_profile_preference(self, memory_id: str) -> bool:
        item = self.long_term_memory_mgr.sqlite_mgr.get_memory(memory_id)
        if item is None or not self._is_profile_memory(item):
            return False
        return self.forget_memory(memory_id)

    def restore_profile_preference(self, memory_id: str) -> bool:
        item = self.long_term_memory_mgr.sqlite_mgr.get_memory(memory_id)
        if item is None or not self._is_profile_memory(item):
            return False
        return self.restore_memory(memory_id)

    def get_memory_detail(self, memory_id: str) -> Optional[Dict[str, Any]]:
        """Gets snapshot detail for a specific memory_id."""
        item = self.long_term_memory_mgr.sqlite_mgr.get_memory(memory_id)
        if not item or not item.current_revision:
            return None
        snapshot = self.long_term_memory_mgr.sqlite_mgr.get_current(memory_id)
        revision = item.current_revision
        return {
            "memory_id": item.memory_id,
            "type": item.memory_type.value,
            "content": revision.content,
            "granularity": item.granularity.value,
            "revision_id": revision.revision_id,
            "retention": item.retention_disposition.value,
            "confidence": revision.confidence,
            "semantic_validity": revision.semantic_validity.value,
            "provenance_count": len(snapshot.provenance) if snapshot else 0,
        }

    def get_memory_history(self, memory_id: str) -> List[Dict[str, Any]]:
        """Gets revision history for a specific memory_id."""
        revisions = self.long_term_memory_mgr.sqlite_mgr.get_history(memory_id)
        return [
            {
                "revision_id": r.revision_id,
                "content": r.content,
                "created_at": r.created_at,
            }
            for r in revisions
        ]

    def get_memory_provenance(self, memory_id: str) -> List[Dict[str, Any]]:
        """Returns revision-scoped, controlled provenance without raw credentials."""
        revisions = self.long_term_memory_mgr.get_history(memory_id)
        if not revisions:
            return []
        result: List[Dict[str, Any]] = []
        for revision in revisions:
            refs = self.long_term_memory_mgr.get_revision_provenance(revision.revision_id)
            result.append({
                "revision_id": revision.revision_id,
                "revision_no": revision.revision_no,
                "change_kind": revision.change_kind.value,
                "refs": [
                    {
                        "provenance_id": ref.provenance_id,
                        "source_event_id": ref.source_event_id,
                        "session_id": ref.session_id,
                        "message_id": ref.message_id,
                        "source_type": ref.source_type,
                        "observed_at": ref.observed_at,
                        "authority": ref.authority.value,
                        "content_hash": ref.content_hash,
                        "controlled_excerpt": ref.controlled_excerpt,
                    }
                    for ref in refs
                ],
            })
        return result

    def forget_memory(self, memory_id: str) -> bool:
        """Moves a memory to the recoverable recycle bin."""
        with self._memory_lock:
            return self.long_term_memory_mgr.soft_delete_memory(memory_id, reason="USER_DELETE")

    def restore_memory(self, memory_id: str) -> bool:
        """Restores a memory from the recoverable recycle bin."""
        with self._memory_lock:
            return self.long_term_memory_mgr.restore_memory_from_recycle_bin(memory_id) is not None

    def list_recycled_memories(self) -> List[Dict[str, Any]]:
        return [
            {
                "memory_id": item.memory_id,
                "content": item.content,
                "type": item.memory_type.value,
                "updated_at": item.updated_at,
            }
            for item in self.long_term_memory_mgr.get_recycle_bin_memories()
        ]

    def get_task_status_summary(self) -> Dict[str, Any]:
        """Gets detailed structured status summary for current active task."""
        if not self.active_task_id:
            return {
                "task_id": None,
                "status": "UNBOUND",
                "summary": "当前会话未绑定 Task",
                "goals": [],
                "constraints": [],
                "decisions": [],
                "progress": [],
                "open_questions": [],
                "checkpoint": None,
                "workspace": self.get_workspace_status(),
            }
        task_status = self.runtime.task_service.repository.get_task_status(self.active_task_id)
        working_ctx = self.runtime.task_service.context_builder.build(
            self.active_task_id,
            include_inactive=True,
        )

        goals = []
        constraints = []
        decisions = []
        progress = []
        open_questions = []
        checkpoint = None

        if working_ctx and working_ctx.structured:
            st = working_ctx.structured
            goals = [e.content for e in st.goal]
            constraints = [e.content for e in st.constraints]
            decisions = [e.content for e in st.decisions]
            progress = [e.content for e in st.progress]
            open_questions = [e.content for e in st.open_questions]
            if st.continuation:
                checkpoint = {
                    "focus": st.continuation.focus,
                    "next_action": st.continuation.next_action,
                    "world_state": st.continuation.world_state,
                    "current_step": st.continuation.current_step,
                    "continuation_note": st.continuation.continuation_note,
                }

        return {
            "task_id": self.active_task_id,
            "status": task_status.value,
            "summary": goals[0] if goals else "通用对话任务",
            "goals": goals,
            "constraints": constraints,
            "decisions": decisions,
            "progress": progress,
            "open_questions": open_questions,
            "checkpoint": checkpoint,
            "workspace": self.get_workspace_status(),
        }

    def pause_current_task(self, note: Optional[str] = None) -> None:
        self._require_no_pending_confirmation()
        task_id = self._require_active_task()
        self.runtime.task_service.pause_task(
            task_id,
            current_step=note,
            note=note,
        )

    def resume_current_task(self) -> None:
        self._require_no_pending_confirmation()
        self.runtime.task_service.resume_task(self._require_active_task())

    def complete_current_task(self) -> None:
        self._require_no_pending_confirmation()
        self.runtime.task_service.complete_task(self._require_active_task())

    def cancel_current_task(self) -> None:
        self._require_no_pending_confirmation()
        self.runtime.task_service.cancel_task(self._require_active_task())

    def get_conversation_context_status(self) -> Dict[str, Any]:
        """Returns model-aware conversation window diagnostics without transcript content."""
        if not self._session_persisted:
            return {
                "model": self.config.llm_model_name,
                "context_window_tokens": self.conversation_context_manager.policy.context_window_tokens,
                "context_window_source": self.conversation_context_manager.policy.context_window_source,
                "usable_tokens": self.conversation_context_manager.policy.usable_tokens,
                "reserve_tokens": self.conversation_context_manager.policy.reserve_tokens,
                "compaction_ratio": self.conversation_context_manager.policy.compact_at_ratio,
                "keep_recent_tokens": self.conversation_context_manager.policy.keep_recent_tokens,
                "transcript_message_count": 0,
                "projected_tail_message_count": 0,
                "estimated_projected_tokens": 0,
                "compaction_id": None,
                "summarized_message_count": 0,
                "compaction_source": "NONE",
                "compaction_degraded": False,
                "compaction_degraded_reason": None,
                "provider_last_usage": {},
            }
        return self.conversation_context_manager.describe(self.active_session_id)

    def set_output_mode(self, mode: str | OutputMode) -> OutputMode:
        """Switch CLI rendering mode for the current process."""
        self.output_mode = OutputMode.parse(mode)
        return self.output_mode

    def get_workspace_status(self) -> Dict[str, Any]:
        """Return the persistent user-managed local tool scope."""
        return self.workspace.describe()

    def get_storage_status(self) -> Dict[str, str]:
        """Return the fixed product-owned runtime storage layout."""
        return self.storage_layout.describe()

    def get_tool_catalog(self) -> List[Dict[str, Any]]:
        """Return the executable tool contract without exposing executors."""
        return [dict(item) for item in self.registry.describe()]

    def get_mcp_status(self) -> Dict[str, Any]:
        """Return MCP discovery status without exposing environment values."""
        return self.mcp_manager.describe()

    def list_pending_approvals(self) -> List[Dict[str, Any]]:
        """Return durable pending approvals for reconnecting frontends."""
        if not self.active_task_id:
            return []
        return [
            {
                "pending_action_id": action.pending_action_id,
                "agent_run_id": action.agent_run_id,
                "task_id": action.task_id,
                "agent_step_id": action.agent_step_id,
                "tool_call_id": action.tool_call_id,
                "tool_name": action.tool_name,
                "validated_arguments": self._json_safe_event_value(action.validated_arguments),
                "effect": action.effect,
                "created_at": action.created_at,
            }
            for action in self.journal.list_pending_actions(task_id=self.active_task_id)
        ]

    def subscribe_events(self, subscriber: Callable[[AgentEvent], None]) -> Callable[[], None]:
        """Subscribe to committed lifecycle events without changing turn behavior."""
        return self.event_stream.subscribe(subscriber)

    def list_events(
        self,
        *,
        after_sequence: int = 0,
        session_id: Optional[str] = None,
        turn_id: Optional[str] = None,
        limit: int = 100,
    ) -> Tuple[AgentEvent, ...]:
        """Replay durable lifecycle events for a reconnecting client."""
        return self.event_store.list_events(
            after_sequence=after_sequence,
            session_id=session_id,
            turn_id=turn_id,
            limit=limit,
        )

    def _emit_event(
        self,
        event_type: str,
        *,
        session_id: Optional[str] = None,
        turn_id: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        event_id: Optional[str] = None,
    ) -> Optional[AgentEvent]:
        """Persist telemetry without allowing a diagnostics failure to break a turn."""
        try:
            return self.event_stream.emit(
                event_type,
                session_id=session_id or getattr(self, "active_session_id", None),
                turn_id=turn_id,
                payload=payload or {},
                event_id=event_id,
            )
        except Exception as exc:
            log_boundary_failure(logger, "agent_event_persistence_failed", exc, event_type=event_type)
            return None

    @staticmethod
    def _json_safe_event_value(value: Any) -> Any:
        if isinstance(value, Enum):
            return value.value
        if is_dataclass(value):
            return PersonalAgentApp._json_safe_event_value(asdict(value))
        if isinstance(value, dict):
            return {
                str(key): PersonalAgentApp._json_safe_event_value(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [PersonalAgentApp._json_safe_event_value(item) for item in value]
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        return str(value)

    @classmethod
    def _bounded_event_value(cls, value: Any, *, depth: int = 0) -> Any:
        """Bound diagnostic values while preserving their structural shape."""
        if depth >= 5:
            return "[diagnostic value depth limit]"
        if isinstance(value, str):
            return value if len(value) <= 2000 else value[:2000] + "…[truncated]"
        if isinstance(value, dict):
            items = list(value.items())
            bounded = {
                str(key): cls._bounded_event_value(item, depth=depth + 1)
                for key, item in items[:50]
            }
            if len(items) > 50:
                bounded["[additional keys]"] = len(items) - 50
            return bounded
        if isinstance(value, (list, tuple)):
            bounded_items = [cls._bounded_event_value(item, depth=depth + 1) for item in value[:50]]
            if len(value) > 50:
                bounded_items.append(f"[additional items: {len(value) - 50}]")
            return bounded_items
        return cls._json_safe_event_value(value)

    @classmethod
    def _approval_event_payload(cls, action: Any) -> Dict[str, Any]:
        original = cls._json_safe_event_value(action.validated_arguments)
        bounded = cls._bounded_event_value(original)
        return {
            "pending_action_id": action.pending_action_id,
            "agent_run_id": action.agent_run_id,
            "task_id": action.task_id,
            "agent_step_id": action.agent_step_id,
            "tool_call_id": action.tool_call_id,
            "tool_name": action.tool_name,
            "effect": action.effect,
            "arguments": bounded,
            "arguments_truncated": bounded != original,
        }

    def _emit_tool_lifecycle_events(self, turn_id: str, result: AgentResult) -> None:
        """Project journal facts into replayable tool item lifecycle events."""
        for tool_call_id in result.executed_tool_call_ids:
            receipt = self.journal.get_receipt(tool_call_id)
            if receipt is None:
                continue
            try:
                arguments = json.loads(receipt.get("arguments_json") or "{}")
            except (TypeError, json.JSONDecodeError):
                arguments = {"_invalid_arguments_json": True}
            if not isinstance(arguments, dict):
                arguments = {"_invalid_arguments_json": True}
            original_arguments = self._json_safe_event_value(arguments)
            bounded_arguments = self._bounded_event_value(original_arguments)
            policy_snapshot = {
                "effect": receipt.get("tool_effect", "UNKNOWN"),
                "idempotency_support": receipt.get("idempotency_support", "UNKNOWN"),
                "arguments_hash": receipt.get("arguments_hash", ""),
            }
            common = {
                "tool_call_id": tool_call_id,
                "tool_name": receipt.get("tool_name", ""),
                "arguments": bounded_arguments,
                "arguments_truncated": bounded_arguments != original_arguments,
                "policy_snapshot": policy_snapshot,
                "execution_started_at": receipt.get("created_at"),
            }
            started_id = f"tool:{turn_id}:{tool_call_id}:started"
            self._emit_event(
                "tool.started",
                session_id=self.active_session_id,
                turn_id=turn_id,
                payload=common,
                event_id=started_id,
            )
            created_at = receipt.get("created_at")
            updated_at = receipt.get("updated_at")
            duration = None
            if isinstance(created_at, (int, float)) and isinstance(updated_at, (int, float)):
                duration = round(max(0.0, updated_at - created_at), 6)
            self._emit_event(
                "tool.completed",
                session_id=self.active_session_id,
                turn_id=turn_id,
                payload={
                    **common,
                    "status": receipt.get("status", "UNKNOWN"),
                    "duration_seconds": duration,
                    "result_size": len(str(receipt.get("result_json") or "")),
                    "runtime_reconciled": bool(receipt.get("runtime_reconciled")),
                },
                event_id=f"tool:{turn_id}:{tool_call_id}:completed",
            )

    @classmethod
    def _result_event_payload(cls, result: AgentResult, duration_seconds: float) -> Dict[str, Any]:

        payload: Dict[str, Any] = {
            "status": result.status.value,
            "agent_run_id": result.agent_run_id,
            "task_id": result.task_id,
            "step_count": result.step_count,
            "executed_tool_call_ids": list(result.executed_tool_call_ids),
            "warnings": list(result.warnings),
            "duration_seconds": round(duration_seconds, 6),
            "trace_metadata": cls._json_safe_event_value(result.trace_metadata),
        }
        if result.final_response is not None:
            payload["final_response"] = result.final_response
        if result.error_info is not None:
            payload["error"] = {
                "stage": result.error_info.stage,
                "category": result.error_info.category,
                "code": result.error_info.code,
                "safe_message": result.error_info.safe_message,
                "retryable": result.error_info.retryable,
                "details": result.error_info.details,
            }
        if result.pending_action is not None:
            payload["pending_action"] = cls._approval_event_payload(result.pending_action)
        return payload

    def add_workspace_root(self, path: str) -> Dict[str, Any]:
        """Add an existing folder to the Agent's visible workspace scope."""
        self._require_no_pending_confirmation()
        item = self.workspace.add_root(path)
        self._persist_active_session_workspace()
        return item

    def remove_workspace_root(self, path: str) -> bool:
        """Remove a folder from the Agent's visible workspace scope."""
        self._require_no_pending_confirmation()
        snapshot = self.workspace.snapshot()
        normalized = str(self.workspace._canonical(path))
        roots = list(snapshot.get("roots") or ())
        if normalized in roots and len(roots) == 1:
            raise ValueError("每个 session 至少需要保留一个 Agent 工作文件夹")
        removed = self.workspace.remove_root(path)
        if removed:
            self._persist_active_session_workspace()
        return removed

    def use_workspace_root(self, path: str) -> str:
        """Select an already visible folder as the default terminal cwd."""
        self._require_no_pending_confirmation()
        selected = self.workspace.use_root(path)
        self._persist_active_session_workspace()
        return selected

    def _render_session_picker(self, sessions: List[Dict[str, Any]], selected: int) -> None:
        """Render the current page of the terminal session picker."""
        print("\x1b[2J\x1b[H", end="")
        print("=== 恢复历史会话 ===")
        print("↑/↓ 选择，Enter 恢复，Esc 或 q 取消\n")
        for index, session in enumerate(sessions):
            marker = "▶" if index == selected else " "
            print(
                f"{marker} {index + 1}. {session['topic']}"
                f" | ID: {session['session_id']}"
                f" | 更新: {session['updated_at']}"
            )

    def pick_session(self) -> Optional[str]:
        """Select a session with arrow keys, falling back to numbered input."""
        sessions = self.list_sessions()
        if not sessions:
            print("[提示] 当前还没有可恢复的历史会话。")
            return None

        # Piped/non-TTY runs cannot receive single key events.  Keep a simple
        # deterministic fallback for smoke tests and redirected terminals.
        if not (getattr(sys.stdin, "isatty", lambda: False)() and getattr(sys.stdout, "isatty", lambda: False)()):
            print("\n=== 恢复历史会话 ===")
            for index, session in enumerate(sessions, start=1):
                print(
                    f" {index}. {session['topic']}"
                    f" | ID: {session['session_id']}"
                    f" | 更新: {session['updated_at']}"
                )
            choice = input("输入编号恢复，直接回车取消: ").strip()
            if not choice:
                return None
            try:
                index = int(choice) - 1
            except ValueError:
                print("[提示] 请输入会话编号。")
                return None
            if 0 <= index < len(sessions):
                return sessions[index]["session_id"]
            print("[提示] 会话编号超出范围。")
            return None

        selected = 0
        print("\x1b[?1049h", end="", flush=True)
        try:
            while True:
                self._render_session_picker(sessions, selected)
                key = _read_picker_key()
                if key == "UP":
                    selected = (selected - 1) % len(sessions)
                elif key == "DOWN":
                    selected = (selected + 1) % len(sessions)
                elif key == "ENTER":
                    return sessions[selected]["session_id"]
                elif key in {"ESC", "q"}:
                    return None
        except KeyboardInterrupt:
            return None
        finally:
            print("\x1b[?1049l", end="", flush=True)

    def _resume_and_render_session(self, session_id: str) -> None:
        """Resume a selected session and redraw its complete transcript."""
        try:
            if self.resume_session(session_id):
                print(
                    f"[系统] 成功恢复会话: {session_id}"
                    f"（当前任务已自动关联至: {self.active_task_id}）"
                )
                print(f"Agent 工作区: {self.workspace.current_root}")
                self.print_session_transcript(session_id)
            else:
                print(f"[错误] 未找到会话 ID: {session_id}")
        except ValueError as exc:
            print(f"[提示] {exc}")

    def compact_current_session(self) -> Dict[str, Any]:
        """Explicitly compact this session without hidden memory mutation."""
        if not self._session_persisted:
            # A freshly started process (or /new-session) has no durable
            # transcript yet.  Compaction must not materialize an empty
            # Conversation Session merely because a control command was used.
            return self.get_conversation_context_status()
        compact_started = time.monotonic()
        self._emit_event(
            "compaction.started",
            session_id=self.active_session_id,
            payload={"trigger": "manual"},
        )
        self.conversation_context_manager.build_for_turn(
            self.active_session_id,
            force_compact=True,
        )
        status = self.get_conversation_context_status()
        self._emit_event(
            "compaction.completed",
            session_id=self.active_session_id,
            payload={
                "trigger": "manual",
                "compaction_id": status.get("compaction_id"),
                "source": status.get("compaction_source"),
                "degraded": status.get("compaction_degraded", False),
                "duration_seconds": round(time.monotonic() - compact_started, 6),
            },
        )
        return status

    def _build_conversation_context(
        self,
        *,
        exclude_turn_id: Optional[str] = None,
        force_compact: bool = False,
    ) -> Any:
        """Build context and expose automatic compaction as a lifecycle."""
        before = self.conversation_context_manager.describe(self.active_session_id)
        should_compact = force_compact or (
            before["estimated_projected_tokens"] > before["usable_tokens"]
        )
        started_at = time.monotonic()
        if should_compact:
            self._emit_event(
                "compaction.started",
                session_id=self.active_session_id,
                payload={"trigger": "overflow" if not force_compact else "forced"},
            )
        try:
            context = self.conversation_context_manager.build_for_turn(
                self.active_session_id,
                force_compact=force_compact,
                exclude_turn_id=exclude_turn_id,
            )
        except Exception as exc:
            if should_compact:
                self._emit_event(
                    "compaction.failed",
                    session_id=self.active_session_id,
                    payload={
                        "trigger": "overflow" if not force_compact else "forced",
                        "error_type": type(exc).__name__,
                        "duration_seconds": round(time.monotonic() - started_at, 6),
                    },
                )
            raise
        after = self.conversation_context_manager.describe(self.active_session_id)
        if should_compact:
            self._emit_event(
                "compaction.completed",
                session_id=self.active_session_id,
                payload={
                    "trigger": "overflow" if not force_compact else "forced",
                    "compaction_id": after.get("compaction_id"),
                    "source": after.get("compaction_source"),
                    "degraded": after.get("compaction_degraded", False),
                    "duration_seconds": round(time.monotonic() - started_at, 6),
                    "changed": before.get("compaction_id") != after.get("compaction_id"),
                },
            )
        return context

    def process_chat_turn(self, user_input: str) -> AgentResult:
        """Executes a new chat turn via MainAgent.run."""
        # Programmatic callers and evaluation harnesses must receive the same
        # structured state that the CLI renders, rather than an exception that
        # loses the pending action details. Confirmation-required control
        # methods remain strict; only an ordinary chat turn is represented as
        # an idempotent AWAITING_CONFIRMATION result here.
        if self.pending_confirmation_id:
            pending_result = self._pending_confirmation_result()
            if pending_result is not None:
                return pending_result
            self.pending_confirmation_id = None
            self.pending_turn_id = None
        return self._process_chat_turn_with_id(
            user_input,
            f"turn_{uuid.uuid4().hex[:12]}",
            resume=False,
        )

    def _pending_confirmation_result(self) -> Optional[AgentResult]:
        """Project the durable pending action into a stable public result."""
        if not self.pending_confirmation_id:
            return None
        pending = self.journal.get_pending_action(self.pending_confirmation_id)
        if pending is None:
            return None
        run = self.journal.get_run(pending.agent_run_id) or {}
        root_turn_id = str(run.get("root_turn_id") or self.pending_turn_id or "")
        request_turn_id = str(self.pending_turn_id or root_turn_id)
        return AgentResult(
            root_turn_id=root_turn_id,
            request_turn_id=request_turn_id,
            task_id=pending.task_id,
            agent_run_id=pending.agent_run_id,
            status=AgentResultStatus.AWAITING_CONFIRMATION,
            pending_action=pending,
            warnings=("PENDING_CONFIRMATION_REQUIRES_DECISION",),
            trace_metadata={"existing_pending_confirmation": True},
        )

    def list_unfinished_turns(self, session_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return started turns that have no durable terminal event yet."""
        target_session_id = session_id or self.active_session_id
        messages = self.conversation_repo.list_messages(target_session_id)
        result: List[Dict[str, Any]] = []
        for event in self.event_store.list_unfinished_turns(session_id=target_session_id):
            user_message = next(
                (
                    message
                    for message in messages
                    if message.turn_id == event.turn_id and message.role is ConversationRole.USER
                ),
                None,
            )
            result.append(
                {
                    "turn_id": event.turn_id,
                    "session_id": event.session_id,
                    "started_at": event.created_at,
                    "input_available": user_message is not None,
                    "status": "INTERRUPTED",
                }
            )
        return result

    def resume_interrupted_turn(self, turn_id: str) -> AgentResult:
        """Resume one unfinished turn using its persisted user message.

        The original turn ID is intentionally reused. MainAgent's stable run
        and tool identities then let the execution journal reconcile a tool
        that may have completed before the process was interrupted.
        """
        self._require_no_pending_confirmation()
        unfinished = {
            item["turn_id"]: item
            for item in self.list_unfinished_turns(self.active_session_id)
        }
        if turn_id not in unfinished:
            raise ValueError(f"未找到可恢复的未完成 turn: {turn_id}")
        message = next(
            (
                item
                for item in self.conversation_repo.list_messages(self.active_session_id)
                if item.turn_id == turn_id and item.role is ConversationRole.USER
            ),
            None,
        )
        if message is None:
            raise ValueError("未完成 turn 没有可恢复的用户输入")
        return self._process_chat_turn_with_id(message.content, turn_id, resume=True)

    def _process_chat_turn_with_id(
        self,
        user_input: str,
        turn_id: str,
        *,
        resume: bool,
    ) -> AgentResult:
        if self.collaboration is None:
            return self._execute_chat_turn_with_id(user_input, turn_id, resume=resume)
        with self.llm_client.turn(self.active_session_id, turn_id, user_input) as run_id:
            result = self._execute_chat_turn_with_id(user_input, turn_id, resume=resume)
            if not self.coordination_repository.get_run(run_id)["result_json"]:
                # Explicit memory routes and runtime failures still have one
                # truthful terminal receipt, without invoking model roles.
                result = self.collaboration._finish(run_id, result)
            return result

    def _execute_chat_turn_with_id(
        self,
        user_input: str,
        turn_id: str,
        *,
        resume: bool,
    ) -> AgentResult:
        """Run a new or resumed turn through one shared lifecycle."""
        if not resume:
            self._ensure_persisted_session()
        turn_started = time.monotonic()
        self._emit_event(
            "turn.resumed" if resume else "turn.started",
            session_id=self.active_session_id,
            turn_id=turn_id,
            payload={
                "task_id": self.active_task_id,
                "input_length": len(user_input),
                "kind": "user_turn",
                "resume": resume,
            },
        )

        # 1. Build Bounded Conversation Context for PAST dialogue only
        cached_collaboration = (
            self.collaboration is not None
            and self.coordination_repository.get_run(self.llm_client.active_run_id)["result_json"] is not None
        )
        conv_ctx = None if cached_collaboration else self._build_conversation_context(
            exclude_turn_id=turn_id if resume else None,
        )

        # 2. Persist current USER message into active conversation session
        if resume:
            user_message = next(
                (
                    item
                    for item in self.conversation_repo.list_messages(self.active_session_id)
                    if item.turn_id == turn_id and item.role is ConversationRole.USER
                ),
                None,
            )
            if user_message is None:
                raise ValueError("未完成 turn 没有可恢复的用户输入")
        else:
            user_message = self.conversation_repo.append_message(
                session_id=self.active_session_id,
                turn_id=turn_id,
                role=ConversationRole.USER,
                content=user_input,
            )
            self._emit_event(
                "item.completed",
                session_id=self.active_session_id,
                turn_id=turn_id,
                payload={
                    "item_type": "user_message",
                    "message_id": user_message.message_id,
                },
            )

        # 3. Request MainAgent execution.  The internal memory tool receives
        # its source and provenance from this application-owned context, never
        # from model-authored tool arguments.
        memory_turn_context = _MemoryUpdateTurnContext(
            user_input=user_input,
            turn_id=turn_id,
            session_id=self.active_session_id,
            message_id=user_message.message_id,
        )
        memory_context_token = self._memory_turn_context.set(memory_turn_context)
        req = AgentRequest(
            turn_request=TurnRequest(
                turn_id=turn_id,
                task_id=self.active_task_id,
                user_input=user_input,
                session_id=self.active_session_id,
            ),
            conversation_context=conv_ctx,
            context_overflow_handler=lambda: self._build_conversation_context(
                force_compact=True,
                exclude_turn_id=turn_id,
            ),
        )
        try:
            res = self.agent.run(req)
        except Exception as exc:
            self._emit_event(
                "turn.failed",
                session_id=self.active_session_id,
                turn_id=turn_id,
                payload={
                    "task_id": self.active_task_id,
                    "error_type": type(exc).__name__,
                    "duration_seconds": round(time.monotonic() - turn_started, 6),
                },
            )
            raise
        finally:
            self._memory_turn_context.reset(memory_context_token)

        # The runtime's binding decision is the only source of the current
        # session's Task association. Keep the Conversation row synchronized
        # after every turn, including an explicit release to UNBOUND.
        if res.task_id:
            self.active_task_id = None if res.task_id == UNBOUND_TASK_ID else res.task_id
            self.conversation_repo.update_session_task(
                self.active_session_id,
                self.active_task_id,
            )

        memory_update_attempted = bool(memory_turn_context.tool_call_ids)
        if memory_update_attempted or res.trace_metadata.get("explicit_memory_write"):
            res = replace(
                res,
                trace_metadata={
                    **res.trace_metadata,
                    "long_term_memory_update": {
                        "attempted": True,
                        "tool_call_ids": list(memory_turn_context.tool_call_ids),
                        "route": (
                            "agent_tool"
                            if memory_update_attempted
                            else "explicit_request"
                        ),
                    },
                },
            )
        else:
            res = replace(
                res,
                trace_metadata={
                    **res.trace_metadata,
                    "long_term_memory_update": {
                        "attempted": False,
                        "tool_call_ids": [],
                        "route": "agent_tool_decision",
                    },
                },
            )
        self._persist_retrieval_trace(turn_id)

        # Tool calls/results are part of the durable conversation boundary.
        # The journal remains authoritative for execution; this transcript
        # projection keeps the paired exchange available to future turns and
        # to compaction without duplicating execution side effects.
        self._persist_tool_transcript(turn_id, res)
        self._emit_tool_lifecycle_events(turn_id, res)

        # 4. Handle Agent response & persist ASSISTANT message on SUCCESS
        if res.status == AgentResultStatus.SUCCESS and res.final_response:
            self.conversation_repo.append_message(
                session_id=self.active_session_id,
                turn_id=turn_id,
                role=ConversationRole.ASSISTANT,
                content=res.final_response,
            )

            # A selected memory tool already processed the exact user event.
            # Do not enqueue a second foreground event after the assistant
            # response; ordinary turns that the Agent judged transient do not
            # enter a hidden batch path.

        if res.status == AgentResultStatus.AWAITING_CONFIRMATION and res.pending_action:
            self.pending_confirmation_id = res.pending_action.pending_action_id
            self.pending_turn_id = turn_id
            self._emit_event(
                "approval.requested",
                session_id=self.active_session_id,
                turn_id=turn_id,
                payload=self._approval_event_payload(res.pending_action),
            )
        else:
            self.pending_confirmation_id = None

        runtime_context = getattr(self.agent, "last_turn_context", None)
        trace_groups = (
            ("runtime_stage_traces", getattr(runtime_context, "stage_traces", ()) or ()),
            ("tool_stage_traces", res.trace_metadata.get("tool_stage_traces", ()) or ()),
        )
        for trace_key, traces in trace_groups:
            for trace in traces:
                trace_payload = self._json_safe_event_value(trace)
                if not isinstance(trace_payload, dict):
                    trace_payload = {"value": trace_payload}
                self._emit_event(
                    "stage.completed",
                    session_id=self.active_session_id,
                    turn_id=turn_id,
                    payload={"source": trace_key, **trace_payload},
                )
        completed_event = self._emit_event(
            "turn.completed",
            session_id=self.active_session_id,
            turn_id=turn_id,
            payload=self._result_event_payload(res, time.monotonic() - turn_started),
        )
        if completed_event is not None:
            res = replace(
                res,
                trace_metadata={
                    **res.trace_metadata,
                    "event_sequence": completed_event.sequence,
                },
            )

        return res

    def _persist_retrieval_trace(self, turn_id: str) -> None:
        """Append a bounded, JSONL retrieval trace for local diagnostics."""
        context = getattr(self.agent, "last_turn_context", None)
        bundle = None if context is None else getattr(context, "evidence_bundle", None)
        if bundle is None:
            return
        record = {
            "turn_id": turn_id,
            "task_id": getattr(context, "task_id", None),
            "request_id": bundle.request_id,
            "trace": bundle.retrieval_trace,
            "selected_memory_ids": [item.memory_id for item in bundle.selected],
            "warnings": list(bundle.warnings),
        }
        try:
            with open(self.retrieval_trace_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        except OSError as exc:
            log_boundary_failure(logger, "retrieval_trace_write_failed", exc, turn_id=turn_id)

    def _persist_tool_transcript(self, turn_id: str, result: AgentResult) -> None:
        if not result.executed_tool_call_ids:
            return
        exchanges = []
        for tool_call_id in result.executed_tool_call_ids:
            receipt = self.journal.get_receipt(tool_call_id)
            if receipt is None:
                continue
            try:
                arguments = json.loads(receipt.get("arguments_json") or "{}")
            except (TypeError, json.JSONDecodeError):
                arguments = {"_invalid_arguments_json": True}
            try:
                outcome = json.loads(receipt.get("result_json") or "null")
            except (TypeError, json.JSONDecodeError):
                outcome = None
            exchanges.append(
                {
                    "tool_call_id": tool_call_id,
                    "tool_name": receipt.get("tool_name", ""),
                    "arguments": arguments,
                    "result": outcome,
                    "status": receipt.get("status", "UNKNOWN"),
                }
            )
            self._emit_event(
                "tool.call.completed",
                session_id=self.active_session_id,
                turn_id=turn_id,
                payload={
                    "tool_call_id": tool_call_id,
                    "tool_name": receipt.get("tool_name", ""),
                    "status": receipt.get("status", "UNKNOWN"),
                    "arguments": arguments,
                    "result_available": outcome is not None,
                },
            )
        if not exchanges:
            return
        self.conversation_repo.append_message(
            session_id=self.active_session_id,
            turn_id=turn_id,
            role=ConversationRole.TOOL,
            content=json.dumps(
                {"kind": "tool_exchange", "exchanges": exchanges},
                ensure_ascii=False,
                sort_keys=True,
            ),
        )

    def _persist_direct_tool_transcript(
        self,
        turn_id: str,
        command_text: str,
        result: ToolCallResult,
    ) -> None:
        """Keep explicit CLI tool commands available to subsequent turns."""
        self._ensure_persisted_session()
        self.conversation_repo.append_message(
            session_id=self.active_session_id,
            turn_id=turn_id,
            role=ConversationRole.USER,
            content=command_text,
        )
        self.conversation_repo.append_message(
            session_id=self.active_session_id,
            turn_id=turn_id,
            role=ConversationRole.TOOL,
            content=json.dumps(
                {
                    "kind": "tool_exchange",
                    "exchanges": [
                        {
                            "tool_call_id": result.tool_call_id,
                            "tool_name": result.tool_name,
                            "arguments": result.arguments,
                            "result": {
                                "status": result.status.value,
                                "result_content": result.result_content,
                                "error_message": result.error_message,
                            },
                            "status": result.status.value,
                        }
                    ],
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )

    def process_confirmation(self, decision: ConfirmationDecision) -> AgentResult:
        """Executes a confirmation resume turn via MainAgent.run."""
        if not self.pending_confirmation_id:
            raise ValueError("当前没有等待确认的操作。")
        self._ensure_persisted_session()

        turn_id = f"turn_conf_{uuid.uuid4().hex[:10]}"
        action_id = self.pending_confirmation_id
        self.pending_confirmation_id = None
        turn_started = time.monotonic()
        self._emit_event(
            "turn.started",
            session_id=self.active_session_id,
            turn_id=turn_id,
            payload={"kind": "confirmation", "decision": decision.value},
        )

        pending_for_event = self.journal.get_pending_action(action_id)
        if pending_for_event is not None:
            self._emit_event(
                "approval.decision",
                session_id=self.active_session_id,
                turn_id=turn_id,
                payload={
                    **self._approval_event_payload(pending_for_event),
                    "decision": decision.value,
                },
            )

        conv_ctx = self._build_conversation_context()

        self.conversation_repo.append_message(
            session_id=self.active_session_id,
            turn_id=turn_id,
            role=ConversationRole.SYSTEM,
            content=(
                "用户确认了待执行的副作用操作。"
                if decision is ConfirmationDecision.CONFIRM
                else "用户拒绝了待执行的副作用操作。"
            ),
        )

        req = AgentRequest(
            turn_request=TurnRequest(
                turn_id=turn_id,
                task_id=self.active_task_id,
                user_input=f"[系统 Confirmation: {decision.value}]",
            ),
            confirmation=ActionConfirmation(
                pending_action_id=action_id,
                decision=decision,
            ),
            conversation_context=conv_ctx,
            context_overflow_handler=lambda: self._build_conversation_context(
                force_compact=True,
                exclude_turn_id=turn_id,
            ),
        )
        try:
            res = self.agent.run(req)
        except Exception as exc:
            self._emit_event(
                "turn.failed",
                session_id=self.active_session_id,
                turn_id=turn_id,
                payload={"kind": "confirmation", "error_type": type(exc).__name__},
            )
            raise
        self._persist_tool_transcript(turn_id, res)
        self._emit_tool_lifecycle_events(turn_id, res)

        if res.status == AgentResultStatus.SUCCESS and res.final_response:
            self.conversation_repo.append_message(
                session_id=self.active_session_id,
                turn_id=turn_id,
                role=ConversationRole.ASSISTANT,
                content=res.final_response,
            )

        if res.status == AgentResultStatus.AWAITING_CONFIRMATION and res.pending_action:
            self.pending_confirmation_id = res.pending_action.pending_action_id
            self.pending_turn_id = turn_id
            self._emit_event(
                "approval.requested",
                session_id=self.active_session_id,
                turn_id=turn_id,
                payload=self._approval_event_payload(res.pending_action),
            )
        else:
            self.pending_confirmation_id = None

        if pending_for_event is not None:
            self._emit_event(
                "approval.completed",
                session_id=self.active_session_id,
                turn_id=turn_id,
                payload={
                    **self._approval_event_payload(pending_for_event),
                    "decision": decision.value,
                    "result_status": res.status.value,
                    "executed": pending_for_event.tool_call_id in res.executed_tool_call_ids,
                },
            )

        self._emit_event(
            "turn.completed",
            session_id=self.active_session_id,
            turn_id=turn_id,
            payload=self._result_event_payload(res, time.monotonic() - turn_started),
        )

        return res

    def print_welcome_banner(self) -> None:
        """Prints application startup banner."""
        print("=" * 60)
        print("Personal Agent MVP (CLI Terminal Product)")
        print(f"Provider : {self.config.llm_provider_name}")
        print(f"Model    : {self.config.llm_model_name}")
        print(f"输出模式 : {'开发模式' if self.output_mode is OutputMode.DEVELOPER else '用户模式'}")
        print(f"数据路径 : {self.data_dir}")
        print(f"当前会话 : {self.active_session_id}")
        print(f"当前任务 : {self.active_task_id or '未绑定（每轮由 Agent 判断）'}")
        print(f"Agent 工作区 : {self.workspace.current_root}")
        print("输入提示 : 看到“你 >”后可直接输入自然语言；输入 /help 查看命令。")
        if self.pending_confirmation_id:
            pending = self.journal.get_pending_action(self.pending_confirmation_id)
            if pending:
                print(
                    f"待确认操作 : {pending.tool_name} {pending.validated_arguments}；"
                    "请输入 /confirm 或 /reject。"
                )
        print("终端提示 : 如果看到“PS ...>”，说明产品未启动或已经退出。")
        print("=" * 60)

    def print_help_table(self) -> None:
        """Prints command user manual."""
        help_text = """
============================================================
个人助理命令帮助 (Commands Manual)
============================================================
对话与普通交互:
  直接输入文字        与 Personal Agent 进行对话

输入快捷键 (GNU readline):
  ↑/↓ 或 Ctrl-P/Ctrl-N  浏览输入历史
  Ctrl-R/Ctrl-S       反向/正向搜索输入历史
  Ctrl-A/Ctrl-E       跳到行首/行尾
  Ctrl-B/Ctrl-F       向左/向右移动一个字符
  Alt-B/Alt-F         向前/向后移动一个词
  Ctrl-K              删除到行尾
  Ctrl-U              删除到行首
  Ctrl-W              删除前一个词
  Ctrl-H              删除光标前一个字符；Ctrl-Y 粘回已删除内容
  Alt-D               删除光标后的一个词
  Ctrl-L              清屏（不修改会话）
  Ctrl-D              空提示符退出；有内容时删除光标后的字符
  Ctrl-C              退出 CLI；模型执行中取消当前请求
  粘贴多行文本         bracketed paste 会作为同一轮输入

会话管理 (Conversation Sessions):
  /session           显示当前活跃会话的详细信息
  /sessions          列出所有历史对话会话（含首句主题、工作区）
  /new-session [标题] 创建并切换到一个新的对话会话
  /resume            打开上下箭头会话选择器并恢复
  /resume <sess_id>  恢复并继续指定 ID 的历史会话
  /resume-turn <turn_id> 恢复当前会话中断且尚未完成的 turn
  /context           显示模型上下文窗口、压缩与完整 Transcript 状态
  /compact           手动压缩当前会话，不隐式修改长期记忆
  /events [序号]      从序号之后重放 Agent 结构化生命周期事件
  /details           展开最近一轮的开发模式运行详情

任务管理 (Task Management):
  /new [标题]        创建并切换到一个新的 Task 任务
  /tasks             列出所有已创建的任务
  /switch <task_id>  切换当前活跃的任务
  /status            显示当前活跃任务的详细状态结构
  /pause [备注]      暂停当前任务（保留历史与工作集）
  /resume-task       恢复当前已暂停任务
  /complete          将当前任务标记为已完成
  /cancel            取消当前任务（保留历史）

说明: 启动终端或 /new-session 都从未绑定 Task 的新 Session 开始；普通对话中
      Agent 每轮自行判断继续、切换、创建或释放 Task。Task 状态与长期记忆独立。

长期记忆 (Long-term Memory):
  /remember <内容>   显式将长期记忆写入 Task 1 存储 (如：/remember 我不吃香菜)
  /memories          列出当前存储的所有长期记忆
  /memory <id>       查看某条记忆的完整信息
  /memory-history <id> 查看某条记忆的修改历史
  /memory-provenance <id> 查看某条记忆每个版本的来源凭据
  /forget <id>       将记忆移入可恢复的回收站
  /restore-memory <id> 从回收站恢复记忆
  /memory-recycle-bin 列出回收站中的记忆
  长期记忆         每轮由 Agent 自主决定是否调用 update_long_term_memory 工具
  时间遗忘         默认关闭；需要时通过 MEMORY_ENABLE_FORGETTING=true 开启

安全确认 (Safety Confirmation):
  /confirm           确认执行当前等待许可的高风险 (SIDE_EFFECT) 工具操作
  /reject            拒绝并取消当前等待许可的工具操作

通用工具 (General Utilities):
  /provider          显示脱敏后的 Provider 配置状态
  /storage           显示 Agent 运行数据的固定存储布局
  /mode [user|developer] 切换用户模式/开发模式（当前默认：开发模式）
  /calc <表达式>     无需模型调用，直接使用安全计算器
  /time              无需模型调用，直接获取本地系统时间
  /search <关键词>   直接联网搜索公开网页
  /fetch <URL>       直接抓取公开网页正文
  /weather <地点>     查询当前天气和短期预报
  /tools             列出当前 Agent 可调用的工具
  /workspace         查看 Agent 可见文件夹和当前工作区
  /workspace-add <路径>    添加 Agent 可见文件夹
  /workspace-remove <路径> 删除 Agent 可见文件夹
  /workspace-use <路径>    切换终端默认工作区
  Agent 可自行选择文件读取、目录查看和终端工具；写文件/运行命令执行前需要确认
  /help              显示本帮助菜单
  /clear             清空终端屏幕 (不会修改或删除数据库)
  /quit 或 /exit     安全退出系统

注意: 每轮普通对话都由 Agent 自主判断是否调用 update_long_term_memory；调用后同步处理
     当前用户原文。Agent 未调用时不会由本地规则偷偷写入；重要事实仍可用 /remember 立即保存。
============================================================
"""
        print(help_text.strip())

    def run_interactive_loop(self) -> None:
        """Runs interactive CLI loop until /quit or EOF."""
        set_progress = getattr(self.llm_client, "set_progress_callback", None)
        if callable(set_progress):
            set_progress(self._print_model_progress)
        self.print_welcome_banner()

        try:
            while True:
                try:
                    user_raw = _normalize_cli_input(_read_cli_submission("\n你 > ")).strip()
                except (KeyboardInterrupt, EOFError):
                    print("\n[系统] 正在关闭连接并保存状态... 再见！")
                    break

                if not user_raw:
                    continue

                try:
                    user_raw.encode("utf-8")
                except UnicodeEncodeError:
                    print("[输入错误] 终端传入了无效字符。请确认终端使用 UTF-8，并在“你 >”提示符后重新输入。")
                    continue

                # Command Routing
                if user_raw.startswith("/"):
                    parts = user_raw.split(maxsplit=1)
                    cmd = parts[0].lower()
                    arg = parts[1].strip() if len(parts) > 1 else ""

                    if cmd in ("/quit", "/exit"):
                        print("[系统] 正在关闭连接并保存状态... 再见！")
                        break

                    elif cmd == "/help":
                        self.print_help_table()

                    elif cmd == "/clear":
                        os.system("clear" if os.name != "nt" else "cls")

                    elif cmd == "/provider":
                        key_status = "已配置" if self.config.llm_api_key else "未配置"
                        print("\n=== Provider 配置（已脱敏） ===")
                        print(f"Provider : {self.config.llm_provider_name}")
                        print(f"Model    : {self.config.llm_model_name}")
                        print(f"Endpoint : {self.config.llm_api_base}")
                        print(f"凭据变量 : {self.config.llm_api_key_env} ({key_status})")
                        print(f"模式     : {self.config.provider_mode.value}")

                    elif cmd == "/storage":
                        print("\n=== Agent 运行数据存储 ===")
                        for name, path in self.get_storage_status().items():
                            print(f"{name:16}: {path}")

                    elif cmd == "/mode":
                        if not arg:
                            current = "developer" if self.output_mode is OutputMode.DEVELOPER else "user"
                            print(f"[系统] 当前输出模式: {current}（可用 /mode user 或 /mode developer 切换）")
                        else:
                            try:
                                selected = self.set_output_mode(arg)
                                label = "开发模式" if selected is OutputMode.DEVELOPER else "用户模式"
                                print(f"[系统] 已切换至{label}。")
                            except ValueError as exc:
                                print(f"[错误] {exc}")

                    elif cmd == "/details":
                        if self._last_result is None:
                            print("[提示] 当前还没有可展开的运行详情。")
                        else:
                            self._render_developer_diagnostics(self._last_result)

                    elif cmd in {"/workspace", "/workspaces"}:
                        status = self.get_workspace_status()
                        print("\n=== Agent 可见工作区 ===")
                        print(f"当前终端工作区: {status['current_root']}")
                        for item in status["roots"]:
                            marker = "*" if item["current"] else " "
                            state = "可用" if item["exists"] else "不存在"
                            print(f" {marker} {item['path']} ({state})")

                    elif cmd in {"/workspace-add", "/workspace-remove", "/workspace-use"}:
                        if not arg:
                            print(f"[错误] 用法: {cmd} <文件夹路径>")
                        else:
                            try:
                                if cmd == "/workspace-add":
                                    item = self.add_workspace_root(arg)
                                    print(f"[系统] 已添加 Agent 工作区: {item['path']}")
                                elif cmd == "/workspace-remove":
                                    if self.remove_workspace_root(arg):
                                        print(f"[系统] 已移除 Agent 工作区: {arg}")
                                    else:
                                        print(f"[提示] 未找到 Agent 工作区: {arg}")
                                else:
                                    selected = self.use_workspace_root(arg)
                                    print(f"[系统] 当前终端工作区已切换为: {selected}")
                            except (ValueError, WorkspaceScopeError) as exc:
                                print(f"[错误] 工作区操作失败: {exc}")

                    elif cmd == "/calc":
                        if not arg:
                            print("[错误] 请提供算术表达式，例如: /calc 123 * 456")
                        else:
                            try:
                                result = self.run_direct_utility_tool(
                                    "calculator", {"expression": arg}
                                )
                                self._persist_direct_tool_transcript(
                                    f"turn_cli_{uuid.uuid4().hex[:12]}",
                                    user_raw,
                                    result,
                                )
                                if result.status == ToolCallStatus.SUCCESS:
                                    print(f"[工具] {result.result_content}")
                                else:
                                    print(f"[工具错误] {result.error_message}")
                            except Exception as exc:
                                self._print_unexpected_error("CALCULATOR_FAILED", exc)

                    elif cmd == "/time":
                        try:
                            result = self.run_direct_utility_tool("get_current_time", {})
                            self._persist_direct_tool_transcript(
                                f"turn_cli_{uuid.uuid4().hex[:12]}",
                                user_raw,
                                result,
                            )
                            if result.status == ToolCallStatus.SUCCESS:
                                print(f"[工具] {result.result_content}")
                            else:
                                print(f"[工具错误] {result.error_message}")
                        except Exception as exc:
                            self._print_unexpected_error("TIME_LOOKUP_FAILED", exc)

                    elif cmd == "/search":
                        if not arg:
                            print("[错误] 请提供搜索关键词，例如: /search Python 3.13 新特性")
                        else:
                            try:
                                result = self.run_direct_external_tool("web_search", {"query": arg})
                                self._persist_direct_tool_transcript(
                                    f"turn_cli_{uuid.uuid4().hex[:12]}",
                                    user_raw,
                                    result,
                                )
                                if result.status == ToolCallStatus.SUCCESS:
                                    print(f"[联网搜索] {result.result_content}")
                                else:
                                    print(f"[工具错误] {result.error_message}")
                            except Exception as exc:
                                self._print_unexpected_error("WEB_SEARCH_FAILED", exc)

                    elif cmd == "/fetch":
                        if not arg:
                            print("[错误] 请提供 URL，例如: /fetch https://example.com")
                        else:
                            try:
                                result = self.run_direct_external_tool("web_fetch", {"url": arg})
                                self._persist_direct_tool_transcript(
                                    f"turn_cli_{uuid.uuid4().hex[:12]}",
                                    user_raw,
                                    result,
                                )
                                if result.status == ToolCallStatus.SUCCESS:
                                    print(f"[网页抓取] {result.result_content}")
                                else:
                                    print(f"[工具错误] {result.error_message}")
                            except Exception as exc:
                                self._print_unexpected_error("WEB_FETCH_FAILED", exc)

                    elif cmd == "/weather":
                        if not arg:
                            print("[错误] 请提供地点，例如: /weather 上海")
                        else:
                            try:
                                result = self.run_direct_external_tool("weather", {"location": arg})
                                self._persist_direct_tool_transcript(
                                    f"turn_cli_{uuid.uuid4().hex[:12]}",
                                    user_raw,
                                    result,
                                )
                                if result.status == ToolCallStatus.SUCCESS:
                                    print(f"[天气] {result.result_content}")
                                else:
                                    print(f"[工具错误] {result.error_message}")
                            except Exception as exc:
                                self._print_unexpected_error("WEATHER_LOOKUP_FAILED", exc)

                    elif cmd == "/tools":
                        print("\n--- 当前 Agent 工具 ---")
                        for spec in self.registry.list_specs():
                            print(f" {spec.name} [{spec.effect.value}] - {spec.description}")

                    elif cmd == "/session":
                        sess = self.conversation_repo.get_session(self.active_session_id)
                        print(f"\n=== 当前会话信息 [{self.active_session_id}] ===")
                        if sess:
                            print(f"会话标题 : {sess.title}")
                            print(f"关联任务 : {sess.active_task_id}")
                            print(f"创建时间 : {datetime.datetime.fromtimestamp(sess.created_at).strftime('%Y-%m-%d %H:%M:%S')}")
                            print(f"更新时间 : {datetime.datetime.fromtimestamp(sess.updated_at).strftime('%Y-%m-%d %H:%M:%S')}")
                            print(f"当前工作区: {self.workspace.current_root}")
                            print(f"可见文件夹: {', '.join(self.workspace.snapshot()['roots'])}")
                        else:
                            print(f"会话标题 : {self._pending_session_title or '新会话（尚未开始）'}")
                            print("会话状态 : PENDING（尚未产生真实对话，不占用 Conversation 数据库记录）")
                            print("关联任务 : 未绑定（每轮由 Agent 判断）")
                            print(f"当前工作区: {self.workspace.current_root}")

                    elif cmd == "/context":
                        context_status = self.get_conversation_context_status()
                        print("\n=== 当前会话上下文状态 ===")
                        print(f"模型 : {context_status['model']}")
                        print(
                            f"上下文窗口 : {context_status['context_window_tokens']} tokens "
                            f"({context_status['context_window_source']})"
                        )
                        print(f"本轮可用预算 : {context_status['usable_tokens']} tokens")
                        print(f"完整 Transcript : {context_status['transcript_message_count']} 条消息（永久保留）")
                        print(f"当前投影尾部 : {context_status['projected_tail_message_count']} 条消息")
                        print(f"估算投影用量 : {context_status['estimated_projected_tokens']} tokens")
                        print(f"已摘要消息 : {context_status['summarized_message_count']} 条")
                        print(f"压缩检查点 : {context_status['compaction_id'] or '尚未压缩'}")
                        print(
                            f"压缩来源 : {context_status['compaction_source']}"
                            + ("（降级，摘要可能不完整）" if context_status["compaction_degraded"] else "")
                        )
                        provider_usage = context_status.get("provider_last_usage") or {}
                        if provider_usage:
                            print(f"最近 Provider 用量 : {provider_usage}")

                    elif cmd == "/events":
                        try:
                            after_sequence = int(arg) if arg else 0
                            events = self.list_events(
                                after_sequence=after_sequence,
                                session_id=self.active_session_id,
                                limit=100,
                            )
                            print(f"\n=== Agent 事件（sequence > {after_sequence}） ===")
                            if not events:
                                print("暂无事件。")
                            for event in events:
                                print(
                                    f"[{event.sequence}] {event.event_type} "
                                    f"session={event.session_id or '-'} "
                                    f"turn={event.turn_id or '-'}"
                                )
                                if event.payload:
                                    print("  " + json.dumps(event.payload, ensure_ascii=False, sort_keys=True))
                        except (TypeError, ValueError) as exc:
                            print(f"[错误] /events 序号必须是非负整数: {exc}")

                    elif cmd == "/compact":
                        context_status = self.compact_current_session()
                        print("\n=== 当前会话已执行压缩 ===")
                        print(f"压缩检查点 : {context_status['compaction_id'] or '本次没有可压缩的旧消息'}")
                        print(f"压缩来源 : {context_status['compaction_source']}")

                    elif cmd == "/sessions":
                        sessions = self.list_sessions()
                        print("\n--- 对话会话列表 (Conversation Sessions) ---")
                        for s in sessions:
                            marker = "*" if s["is_active"] else " "
                            roots = ", ".join(s["workspace_roots"]) or "未绑定工作区"
                            print(
                                f" {marker} [{s['session_id']}] {s['topic']}"
                                f" | 工作区: {roots} | 更新: {s['updated_at']}"
                            )

                    elif cmd == "/new-session":
                        try:
                            new_sid = self.create_new_session(title=arg if arg else None)
                            print(f"[系统] 已成功创建新对话会话并切换至: {new_sid}")
                        except ValueError as exc:
                            print(f"[提示] {exc}")

                    elif cmd == "/resume":
                        selected_session_id = arg or self.pick_session()
                        if selected_session_id:
                            self._resume_and_render_session(selected_session_id)

                    elif cmd == "/resume-turn":
                        if not arg:
                            print("[错误] 请提供要恢复的 turn_id，例如: /resume-turn turn_12345")
                        else:
                            try:
                                resumed = self._run_with_wait_notice(
                                    lambda: self.resume_interrupted_turn(arg)
                                )
                                self._render_agent_result(resumed)
                            except ProviderError as exc:
                                safe = exc.to_safe_projection()
                                self._print_provider_error(
                                    code=safe.get("code", "PROVIDER_ERROR"),
                                    safe_message=safe.get("safe_message", "Provider request failed."),
                                    retryable=bool(safe.get("retryable")),
                                    provider=safe.get("provider"),
                                    operation=safe.get("operation"),
                                    status_code=safe.get("status_code"),
                                )
                            except ValueError as exc:
                                print(f"[提示] 恢复 turn 失败: {exc}")

                    elif cmd == "/new":
                        try:
                            new_id = self.create_new_task(title=arg if arg else None)
                            print(f"[系统] 已成功创建新任务并切换当前任务至: {new_id}")
                        except ValueError as exc:
                            print(f"[提示] {exc}")

                    elif cmd == "/tasks":
                        tasks = self.list_tasks()
                        print("\n--- 任务列表 (Tasks) ---")
                        for t in tasks:
                            marker = "*" if t["is_active"] else " "
                            print(f" {marker} [{t['task_id']}] 状态: {t['status']} | 描述: {t['summary']}")

                    elif cmd == "/switch":
                        if not arg:
                            print("[错误] 请提供要切换的 task_id，例如: /switch task_general")
                        else:
                            try:
                                ok = self.switch_task(arg)
                                if ok:
                                    print(f"[系统] 成功切换当前任务至: {arg}")
                                else:
                                    print(f"[错误] 未找到任务 ID: {arg}")
                            except ValueError as exc:
                                print(f"[提示] {exc}")

                    elif cmd == "/status":
                        st = self.get_task_status_summary()
                        memory_status = self.get_memory_update_status()
                        print(f"\n=== 当前任务状态 [{st['task_id']}] ===")
                        print(f"运行状态 : {st['status']}")
                        print(f"概述摘要 : {st['summary']}")
                        if st.get("goals"):
                            print(f"主要目标 : {', '.join(st['goals'])}")
                        if st.get("constraints"):
                            print(f"限制条件 : {', '.join(st['constraints'])}")
                        if st.get("decisions"):
                            print(f"关键决策 : {', '.join(st['decisions'])}")
                        if st.get("progress"):
                            print(f"最新进展 : {', '.join(st['progress'])}")
                        if st.get("open_questions"):
                            print(f"待解决问题 : {', '.join(st['open_questions'])}")
                        checkpoint = st.get("checkpoint") or {}
                        if checkpoint:
                            print(f"当前焦点   : {checkpoint.get('focus') or '-'}")
                            print(f"下一动作   : {checkpoint.get('next_action') or '-'}")
                            print(f"世界状态   : {checkpoint.get('world_state') or '-'}")
                        print(
                            "长期记忆更新: Agent 自主工具模式, "
                            f"待重试事件={memory_status['pending_events']}, "
                            f"阈值触发={'开启' if memory_status['threshold_trigger_enabled'] else '关闭'}, "
                            f"时间触发={'开启' if memory_status['time_trigger_enabled'] else '关闭'}"
                        )

                    elif cmd in {"/pause", "/resume-task", "/complete", "/cancel"}:
                        try:
                            if cmd == "/pause":
                                self.pause_current_task(arg or None)
                                print("[系统] 当前任务已暂停；可用 /resume-task 继续。")
                            elif cmd == "/resume-task":
                                self.resume_current_task()
                                print("[系统] 当前任务已恢复。")
                            elif cmd == "/complete":
                                self.complete_current_task()
                                print("[系统] 当前任务已标记为完成；如需继续工作，请使用 /new 创建任务。")
                            else:
                                self.cancel_current_task()
                                print("[系统] 当前任务已取消；历史仍保留。")
                        except (KeyError, ValueError, RuntimeError) as exc:
                            print(f"[提示] 当前任务状态操作失败：{exc}")

                    elif cmd == "/remember":
                        if not arg:
                            print("[错误] 请提供要记忆的内容，例如: /remember 我喜欢用 Python 开发")
                        else:
                            try:
                                res = self.remember_explicit(arg)
                                mem_id = res.get("memory_id")
                                if not isinstance(mem_id, str) or not mem_id.strip():
                                    raise RuntimeError("memory write returned no durable memory identity")
                                print(f"[系统] 成功记下长期记忆！Memory ID: {mem_id}")
                            except Exception as exc:
                                self._print_unexpected_error("MEMORY_WRITE_FAILED", exc)

                    elif cmd == "/memories":
                        mems = self.list_memories()
                        if not mems:
                            print("[系统] 当前长期记忆存储库为空。可使用 /remember <内容> 添加记忆。")
                        else:
                            print("\n--- 长期记忆列表 (Long-term Memories) ---")
                            for m in mems:
                                content_trunc = m['content'][:60] + "..." if len(m['content']) > 60 else m['content']
                                print(f" [{m['memory_id']}] [{m['type']}] {content_trunc}")

                    elif cmd == "/memory":
                        if not arg:
                            print("[错误] 请提供 memory_id，例如: /memory mem_123")
                        else:
                            detail = self.get_memory_detail(arg)
                            if detail:
                                print(f"\n=== 记忆详情 [{detail['memory_id']}] ===")
                                print(f"类型 : {detail['type']}")
                                print(f"粒度 : {detail['granularity']}")
                                print(f"内容 : {detail['content']}")
                            else:
                                print(f"[错误] 未找到记忆 ID: {arg}")

                    elif cmd == "/memory-history":
                        if not arg:
                            print("[错误] 请提供 memory_id，例如: /memory-history mem_123")
                        else:
                            try:
                                hist = self.get_memory_history(arg)
                            except MemoryNotFoundError:
                                hist = []
                            except Exception as exc:
                                # A bad ID is normal interactive input, not a
                                # process-fatal error. Keep the CLI alive and
                                # avoid exposing a traceback to the user.
                                log_boundary_failure(logger, "memory_history_lookup_failed", exc)
                                hist = []
                            if hist:
                                print(f"\n=== 记忆修改历史 [{arg}] ===")
                                for idx, rev in enumerate(hist, 1):
                                    print(f" {idx}. Revision: {rev['revision_id']} | 时间: {rev['created_at']}")
                                    print(f"    内容: {rev['content']}")
                            else:
                                print(f"[错误] 未找到记忆历史或 ID: {arg}")

                    elif cmd == "/memory-provenance":
                        if not arg:
                            print("[错误] 请提供 memory_id，例如: /memory-provenance mem_123")
                        else:
                            try:
                                provenance = self.get_memory_provenance(arg)
                            except Exception as exc:
                                log_boundary_failure(logger, "memory_provenance_lookup_failed", exc)
                                provenance = []
                            if not provenance:
                                print(f"[错误] 未找到记忆来源或 ID: {arg}")
                            else:
                                print(f"\n=== 记忆来源 [{arg}] ===")
                                for revision in provenance:
                                    print(
                                        f" Revision {revision['revision_no']} "
                                        f"({revision['revision_id']}) "
                                        f"变更={revision['change_kind']}"
                                    )
                                    if not revision["refs"]:
                                        print("    来源: 无可用来源记录")
                                    for ref in revision["refs"]:
                                        print(
                                            f"    [{ref['authority']}] {ref['source_type']} "
                                            f"event={ref['source_event_id']} "
                                            f"session={ref['session_id'] or '-'} "
                                            f"message={ref['message_id'] or '-'}"
                                        )
                                        if ref["controlled_excerpt"]:
                                            print(f"      摘要: {ref['controlled_excerpt']}")

                    elif cmd == "/forget":
                        if not arg:
                            print("[错误] 请提供 memory_id，例如: /forget mem_123")
                        else:
                            try:
                                if self.forget_memory(arg):
                                    print("[系统] 记忆已移入回收站，可用 /restore-memory 恢复。")
                                else:
                                    print(f"[错误] 未找到可删除的活跃记忆: {arg}")
                            except Exception as exc:
                                self._print_unexpected_error("MEMORY_DELETE_FAILED", exc)

                    elif cmd == "/restore-memory":
                        if not arg:
                            print("[错误] 请提供 memory_id，例如: /restore-memory mem_123")
                        else:
                            try:
                                if self.restore_memory(arg):
                                    print("[系统] 记忆已从回收站恢复。")
                                else:
                                    print(f"[错误] 未找到可恢复的回收站记忆: {arg}")
                            except Exception as exc:
                                self._print_unexpected_error("MEMORY_RESTORE_FAILED", exc)

                    elif cmd == "/memory-recycle-bin":
                        recycled = self.list_recycled_memories()
                        if not recycled:
                            print("[系统] 回收站为空。")
                        else:
                            print("\n--- 记忆回收站 ---")
                            for item in recycled:
                                content = item["content"]
                                if len(content) > 60:
                                    content = content[:60] + "..."
                                print(f" [{item['memory_id']}] [{item['type']}] {content}")

                    elif cmd == "/confirm":
                        if not self.pending_confirmation_id:
                            print("[提示] 当前没有等待确认的操作。")
                        else:
                            print("[系统] 正在确认并继续执行 Agent 操作...")
                            try:
                                confirmation_result = self._run_with_wait_notice(
                                    lambda: self.process_confirmation(ConfirmationDecision.CONFIRM)
                                )
                                self._render_agent_result(confirmation_result)
                            except KeyboardInterrupt:
                                print("\n[已取消] 本次确认执行已由用户中断。")
                            except Exception as exc:
                                self._print_unexpected_error("CONFIRMATION_FAILED", exc)

                    elif cmd == "/reject":
                        if not self.pending_confirmation_id:
                            print("[提示] 当前没有等待确认的操作。")
                        else:
                            print("[系统] 正在拒绝并取消 Agent 操作...")
                            try:
                                rejection_result = self._run_with_wait_notice(
                                    lambda: self.process_confirmation(ConfirmationDecision.REJECT)
                                )
                                self._render_agent_result(rejection_result)
                            except KeyboardInterrupt:
                                print("\n[已取消] 本次拒绝操作已由用户中断。")
                            except Exception as exc:
                                self._print_unexpected_error("REJECTION_FAILED", exc)

                    else:
                        print(f"[错误] 未知命令: {cmd}。输入 /help 查看可用命令列表。")

                else:
                    # Normal Chat Input
                    if self.pending_confirmation_id:
                        print("[提示] 当前有等待确认的操作，请先输入 /confirm 或 /reject。")
                        continue
                    try:
                        chat_result = self._run_with_wait_notice(
                            lambda: self.process_chat_turn(user_raw)
                        )
                        self._render_agent_result(chat_result)
                    except KeyboardInterrupt:
                        print("\n[已取消] 本轮请求已由用户中断，可以继续输入新请求。")
                    except ProviderError as exc:
                        safe = exc.to_safe_projection()
                        self._print_provider_error(
                            code=safe.get("code", "PROVIDER_ERROR"),
                            safe_message=safe.get("safe_message", "Provider request failed."),
                            retryable=bool(safe.get("retryable")),
                            provider=safe.get("provider"),
                            operation=safe.get("operation"),
                            status_code=safe.get("status_code"),
                        )
                    except Exception as exc:
                        self._print_unexpected_error("TURN_EXECUTION_FAILED", exc)
        finally:
            if callable(set_progress):
                set_progress(None)
            self.close()
            _save_readline_history(self.cli_history_file)

    def _run_with_wait_notice(
        self,
        operation: Callable[[], AgentResult],
        *,
        notice_interval_seconds: float = 10.0,
        show_progress: bool = True,
    ) -> AgentResult:
        """Runs a turn, collecting timing/usage telemetry for both CLI modes."""
        started = time.monotonic()
        usage_before = self._provider_usage()
        developer_mode = self.output_mode is OutputMode.DEVELOPER
        clear_reasoning = getattr(self.llm_client, "clear_last_reasoning_content", None)
        if callable(clear_reasoning):
            clear_reasoning()
        if developer_mode and show_progress:
            print("\n[处理中] 已收到请求，正在调用模型服务...", flush=True)
        stopped = threading.Event()

        def report_wait() -> None:
            elapsed = 0.0
            while not stopped.wait(notice_interval_seconds):
                elapsed += notice_interval_seconds
                if self.output_mode is OutputMode.DEVELOPER and show_progress:
                    print(f"[处理中] 已等待 {elapsed:.0f} 秒，模型仍在响应...", flush=True)

        reporter = None
        if developer_mode and show_progress:
            reporter = threading.Thread(target=report_wait, daemon=True)
            reporter.start()
        try:
            result = operation()
        finally:
            stopped.set()
            if reporter is not None:
                reporter.join(timeout=0.2)
        if isinstance(result, AgentResult):
            observed = self._attach_observability(
                result,
                elapsed_seconds=time.monotonic() - started,
                usage_before=usage_before,
            )
            self._emit_event(
                "turn.usage",
                session_id=self.active_session_id,
                turn_id=observed.request_turn_id,
                payload={
                    "elapsed_seconds": observed.trace_metadata.get("turn_elapsed_seconds"),
                    "provider_usage_delta": observed.trace_metadata.get("provider_usage_delta", {}),
                },
            )
            return observed
        return result

    def _print_model_progress(self, schema_name: str, attempt: int, max_attempts: int) -> None:
        if self.output_mode is not OutputMode.DEVELOPER:
            return
        if schema_name.startswith("task_state"):
            stage = "正在更新任务上下文"
        elif schema_name.startswith("context_") or "selection" in schema_name:
            stage = "正在检索并整理相关记忆"
        elif schema_name == "agent_action":
            stage = "正在生成最终回答"
        else:
            stage = "正在调用模型"
        retry = f"，第 {attempt}/{max_attempts} 次尝试" if max_attempts > 1 else ""
        print(f"[处理中] {stage}{retry}...", flush=True)

    def _provider_usage(self) -> Dict[str, int]:
        raw = getattr(self.llm_client, "usage", {})
        return dict(raw) if isinstance(raw, dict) else {}

    def _attach_observability(
        self,
        result: AgentResult,
        *,
        elapsed_seconds: float,
        usage_before: Dict[str, int],
    ) -> AgentResult:
        """Attach bounded, non-secret request-local telemetry to an AgentResult."""
        usage_after = self._provider_usage()
        usage_delta = {
            key: usage_after.get(key, 0) - usage_before.get(key, 0)
            for key in ("request_count", "input_tokens", "output_tokens")
            if isinstance(usage_after.get(key, 0), int)
        }
        metadata = {
            **result.trace_metadata,
            "turn_elapsed_seconds": round(max(0.0, elapsed_seconds), 3),
            "provider_usage_delta": usage_delta,
        }
        # Make the Agent's independent memory decision visible even when it
        # chose not to call the memory tool.  This is observability only; it
        # does not create a hidden write or infer semantic content locally.
        metadata.setdefault(
            "long_term_memory_update",
            {
                "attempted": bool(result.trace_metadata.get("explicit_memory_write")),
                "tool_call_ids": [],
                "route": "explicit_request"
                if result.trace_metadata.get("explicit_memory_write")
                else "agent_tool_decision",
            },
        )

        context = getattr(self.agent, "last_turn_context", None)
        stage_traces = list(getattr(context, "stage_traces", ()) or ())
        tool_stage_traces = list(metadata.get("tool_stage_traces", ()) or ())
        metadata["runtime_stage_traces"] = tuple((*stage_traces, *tool_stage_traces))

        reasoning = getattr(self.llm_client, "last_reasoning_content", "")
        if isinstance(reasoning, str) and reasoning.strip():
            metadata["provider_reasoning_content"] = reasoning.strip()[:4000]

        tool_details = []
        for tool_call_id in result.executed_tool_call_ids:
            receipt = self.journal.get_receipt(tool_call_id)
            if receipt is None:
                continue
            try:
                arguments = json.loads(receipt.get("arguments_json") or "{}")
            except (TypeError, json.JSONDecodeError):
                arguments = {"_invalid_arguments_json": True}
            created_at = receipt.get("created_at")
            updated_at = receipt.get("updated_at")
            duration = None
            if isinstance(created_at, (int, float)) and isinstance(updated_at, (int, float)):
                duration = round(max(0.0, updated_at - created_at), 3)
            tool_details.append(
                {
                    "tool_call_id": tool_call_id,
                    "tool_name": receipt.get("tool_name", ""),
                    "arguments": arguments,
                    "status": receipt.get("status", "UNKNOWN"),
                    "duration_seconds": duration,
                }
            )
        metadata["tool_details"] = tuple(tool_details)
        return replace(result, trace_metadata=metadata)

    def _render_agent_result(self, res: AgentResult, *, include_developer_details: bool = False) -> None:
        """Renders Agent execution result cleanly in Chinese."""
        self._last_result = res
        elapsed = res.trace_metadata.get("turn_elapsed_seconds")
        reasoning = res.trace_metadata.get("provider_reasoning_content")
        if self.output_mode is OutputMode.USER:
            if isinstance(elapsed, (int, float)):
                print(f"\n思考用时: {elapsed:.3f} 秒")
            if isinstance(reasoning, str) and reasoning.strip():
                print(f"\n思考过程（Provider 返回）:\n{reasoning}")

        if self.output_mode is OutputMode.DEVELOPER:
            if include_developer_details:
                self._render_developer_diagnostics(res)
            else:
                self._render_developer_summary(res)
            print("\n" + "─" * 60)

        if res.status == AgentResultStatus.SUCCESS:
            if not res.final_response:
                print("\n[INTEGRITY_ERROR] SUCCESS result is missing final_response.")
            else:
                print(f"\nAgent > {res.final_response}")

        elif res.status == AgentResultStatus.AWAITING_CONFIRMATION:
            action = res.pending_action
            tool_name = action.tool_name if action else "未知工具"
            args_str = str(action.validated_arguments) if action else "{}"
            print("\n" + "=" * 60)
            print("⚠️  Agent 请求执行具有副作用（高风险）的操作：")
            print(f" • 工具名称: {tool_name}")
            print(f" • 验证参数: {args_str}")
            print("-" * 60)
            print(" 输入 /confirm 确认允许执行，或 /reject 拒绝取消。")
            print("=" * 60)

        elif res.status == AgentResultStatus.STEP_LIMIT_REACHED:
            print("\n[提示] 本次 Agent 未能在允许的步骤数内完成任务。")

        elif res.status == AgentResultStatus.CANCELLED:
            print(f"\n[已取消] {res.final_response or '操作已由用户拒绝。'}")

        elif res.status == AgentResultStatus.TASK_SELECTION_REQUIRED:
            error = res.error_info
            print(f"\n[需要选择任务] {error.safe_message if error else '请先选择一个活跃任务。'}")

        elif res.status == AgentResultStatus.EXECUTION_STATUS_UNKNOWN:
            error = res.error_info
            print(f"\n[执行状态未知] {error.safe_message if error else '无法安全确认工具是否已执行。'}")

        elif res.status == AgentResultStatus.FAILED:
            error = res.error_info
            if error and error.category == "PROVIDER_ERROR":
                details = error.details or {}
                self._print_provider_error(
                    code=error.code,
                    safe_message=error.safe_message,
                    retryable=error.retryable,
                    provider=details.get("provider"),
                    operation=details.get("operation"),
                    status_code=details.get("status_code"),
                )
            else:
                err_msg = error.safe_message if error else "Agent 执行遇到内部异常"
                cat = error.category if error else "AGENT_ERROR"
                print(f"\n[{cat}] {err_msg}")

    def format_agent_result(self, res: AgentResult) -> str:
        """Return the exact mode-aware result text rendered by the CLI.

        The browser demo consumes this presentation instead of maintaining a
        second set of rules about which result fields to show.  The CLI keeps
        its existing streaming/progress behavior, while both surfaces share
        the final result rendering and the selected output mode.
        """
        output = io.StringIO()
        with redirect_stdout(output):
            self._render_agent_result(res, include_developer_details=True)
        return output.getvalue().strip()

    @staticmethod
    def _trace_label(value: Any) -> str:
        return value.value if hasattr(value, "value") else str(value)

    def _render_developer_diagnostics(self, res: AgentResult) -> None:
        """Render detailed but still sanitized execution telemetry."""
        metadata = res.trace_metadata
        print("\n=== 开发模式执行详情 ===")
        elapsed = metadata.get("turn_elapsed_seconds")
        if isinstance(elapsed, (int, float)):
            print(f"总耗时: {elapsed:.3f} 秒")
        print(f"Agent 状态: {res.status.value}")
        print(f"步骤数: {res.step_count}")

        if res.error_info is not None:
            error = res.error_info
            print("错误诊断:")
            print(
                f"  stage={self._trace_label(error.stage)} category={error.category} "
                f"code={error.code} retryable={error.retryable}"
            )
            if error.details:
                print(
                    "  details="
                    + json.dumps(error.details, ensure_ascii=False, sort_keys=True)
                )

        usage = metadata.get("provider_usage_delta")
        if isinstance(usage, dict):
            print(f"Provider 本轮用量: {usage}")

        traces = metadata.get("runtime_stage_traces", ())
        if traces:
            print("阶段:")
            for trace in traces:
                stage = self._trace_label(getattr(trace, "stage", "UNKNOWN"))
                status = self._trace_label(getattr(trace, "status", "UNKNOWN"))
                duration = getattr(trace, "duration_seconds", 0.0)
                print(f"  - {stage}: {status}, {duration:.3f} 秒")
                for warning in getattr(trace, "warnings", ()):
                    print(f"    警告: {warning}")

        tools = metadata.get("tool_details", ())
        if tools:
            print("工具执行:")
            for detail in tools:
                tool_name = detail.get("tool_name", "UNKNOWN")
                status = detail.get("status", "UNKNOWN")
                call_id = detail.get("tool_call_id", "UNKNOWN")
                arguments = detail.get("arguments", {})
                duration = detail.get("duration_seconds")
                print(
                    f"  - {tool_name} [{status}] "
                    f"call_id={call_id} "
                    f"args={json.dumps(arguments, ensure_ascii=False, sort_keys=True)} "
                    f"耗时={duration} 秒"
                )
        else:
            print("工具执行: 无")

        reasoning = metadata.get("provider_reasoning_content")
        if isinstance(reasoning, str) and reasoning.strip():
            print(f"Provider 思考过程（返回字段，已截断）:\n{reasoning}")

        for warning in res.warnings:
            print(f"警告: {warning}")

    def _render_developer_summary(self, res: AgentResult) -> None:
        """Render the collapsed CLI receipt; /details expands the last run."""
        elapsed = res.trace_metadata.get("turn_elapsed_seconds")
        if isinstance(elapsed, (int, float)):
            duration = f"{elapsed:.3f} 秒"
        else:
            duration = "耗时未知"
        print(f"\nWorked for {duration} 〉（输入 /details 展开开发详情）")

    @staticmethod
    def _print_unexpected_error(code: str, exc: BaseException) -> None:
        metadata = log_boundary_failure(logger, "cli_operation_failed", exc, code=code)
        print(f"\n[{code}] 操作失败 ({metadata['error_type']})，详细诊断已写入日志。")

    @staticmethod
    def _print_provider_error(
        *,
        code: str,
        safe_message: str,
        retryable: bool,
        provider: Optional[str],
        operation: Optional[str],
        status_code: Optional[int],
    ) -> None:
        """Prints a sanitized provider failure with actionable guidance."""
        if status_code == 401:
            advice = "API Key 无效或已失效，请检查 .env.local 中的凭据。"
        elif status_code == 402:
            advice = "API 账户或 Key 额度不足，请充值或改用可用的免费模型。"
        elif status_code == 429:
            advice = "请求频率超过限制，请稍后重试。"
        elif status_code is not None and status_code >= 500:
            advice = "Provider 服务暂时不可用，请稍后重试。"
        elif retryable:
            advice = "网络或 Provider 暂时不可用，可以重试。"
        else:
            advice = "请检查 API 配置、模型名称和请求内容后重试。"

        status_text = f" HTTP {status_code}" if status_code else ""
        print(
            f"\n[API请求失败] {code}{status_text}\n"
            f"Provider: {provider or 'unknown'}\n"
            f"Operation: {operation or 'unknown'}\n"
            f"原因: {safe_message}\n"
            f"处理建议: {advice}"
        )
