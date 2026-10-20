from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.memory.agent.tooling import ToolExecutionRequest
from src.memory.app.tools import DirectoryListExecutor, FileReadExecutor, FileWriteExecutor, TerminalCommandExecutor
from src.memory.app.workspace import WorkspaceManager, WorkspaceScopeError
from src.memory.app.storage import AgentStorageLayout, default_agent_data_dir, migrate_legacy_config, migrate_legacy_project_data
from src.memory.conversation.repository import ConversationRepository
from src.memory.conversation.models import ConversationRole
from src.memory.runtime.models import ToolCallStatus


def _request(name: str, arguments: dict) -> ToolExecutionRequest:
    return ToolExecutionRequest(
        tool_call_id=f"call-{name}",
        tool_name=name,
        arguments=arguments,
        idempotency_key=f"idemp-{name}",
        agent_run_id="run-workspace-test",
        agent_step_id=f"step-{name}",
    )


def test_workspace_roots_are_persistent_and_user_selectable(tmp_path):
    data_dir = tmp_path / "agent-data"
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()

    manager = WorkspaceManager(str(data_dir), default_root=str(first))
    assert manager.current_root == str(first.resolve())
    manager.add_root(str(second))
    manager.use_root(str(second))
    assert manager.remove_root(str(first)) is True

    restored = WorkspaceManager(str(data_dir), default_root=str(first))
    assert restored.current_root == str(second.resolve())
    assert [item["path"] for item in restored.list_roots()] == [str(second.resolve())]
    state = json.loads((data_dir / "workspaces" / "workspace_scope.json").read_text(encoding="utf-8"))
    assert state["current_root"] == str(second.resolve())

    assert restored.remove_root(str(second)) is True
    assert restored.describe()["current_root"] is None
    restored.add_root(str(first))
    assert restored.current_root == str(first.resolve())


def test_agent_storage_layout_separates_product_data_and_migrates_flat_files(tmp_path):
    data_dir = tmp_path / "agent-data"
    data_dir.mkdir()
    legacy_memory = data_dir / "memory.db"
    legacy_memory.write_text("legacy-memory", encoding="utf-8")

    layout = AgentStorageLayout(str(data_dir))

    assert layout.memory_db_path == str(data_dir / "long_term_memory" / "memory.db")
    assert not legacy_memory.exists()
    assert (data_dir / "long_term_memory" / "memory.db").read_text(encoding="utf-8") == "legacy-memory"
    assert Path(layout.conversation_db_path) == data_dir / "conversations" / "conversation.db"
    assert Path(layout.task_db_path) == data_dir / "task_state" / "task_state.db"
    assert Path(layout.journal_db_path) == data_dir / "execution" / "agent_journal.db"
    assert Path(layout.cli_history_path) == data_dir / "cli" / "cli_history"
    assert Path(layout.workspace_scope_path) == data_dir / "workspaces" / "workspace_scope.json"
    described = layout.describe()
    assert described["memory_db"] == layout.memory_db_path
    assert described["conversation_store"].endswith("conversations")
    assert Path(described["config_env"]).parts[-2:] == ("config", ".env.local")
    assert default_agent_data_dir({"AGENT_HOME": str(tmp_path / "global-agent")}) == str((tmp_path / "global-agent").resolve())


def test_default_agent_data_dir_uses_global_agent_data_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    assert default_agent_data_dir({}) == str((tmp_path / ".agent_data").resolve())


def test_legacy_project_runtime_directory_moves_to_global_agent_home(tmp_path):
    legacy = tmp_path / "project" / ".agent_data"
    target = tmp_path / "home" / ".agent_data"
    legacy.mkdir(parents=True)
    (legacy / "memory.db").write_text("old", encoding="utf-8")

    assert migrate_legacy_project_data(str(legacy), str(target)) is True
    assert not legacy.exists()
    assert (target / "memory.db").read_text(encoding="utf-8") == "old"


def test_global_agent_config_is_loaded_from_agent_home(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "global-agent"))
    monkeypatch.setenv("MEMORY_LOAD_LOCAL_ENV", "1")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    config_path = tmp_path / "global-agent" / "config" / ".env.local"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("DEEPSEEK_API_KEY=global-test-key\n", encoding="utf-8")

    from src.memory.core.config import MemoryConfig

    config = MemoryConfig.from_env()
    assert config.llm_api_key == "global-test-key"


def test_legacy_project_env_moves_into_global_config(tmp_path):
    legacy = tmp_path / "project.env.local"
    target = tmp_path / "agent" / "config" / ".env.local"
    legacy.write_text("DEEPSEEK_API_KEY=old-key\n", encoding="utf-8")

    assert migrate_legacy_config(str(legacy), str(target)) is True
    assert not legacy.exists()
    assert target.read_text(encoding="utf-8") == "DEEPSEEK_API_KEY=old-key\n"


def test_conversation_sessions_use_one_authoritative_store(tmp_path):
    conversations = tmp_path / "conversations"
    repo = ConversationRepository(str(conversations / "conversation.db"))
    session = repo.create_session(session_id="session_storage_test")
    message = repo.append_message(session.session_id, "turn_1", ConversationRole.USER, "短期上下文")
    repo.save_compaction(session.session_id, message.message_id, "压缩检查点", 1)

    assert repo.list_messages(session.session_id)[0].content == "短期上下文"
    assert repo.get_latest_compaction(session.session_id).summary == "压缩检查点"
    assert not (conversations / "sessions").exists()


def test_conversation_repository_default_uses_global_agent_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "global-agent"))

    repo = ConversationRepository()

    assert repo.db_path == str(tmp_path / "global-agent" / "conversations" / "conversation.db")
    assert repo.sessions_dir == str(tmp_path / "global-agent" / "conversations")


def test_workspace_scope_rejects_paths_outside_all_roots(tmp_path):
    root = tmp_path / "visible"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    manager = WorkspaceManager(str(tmp_path / "data"), default_root=str(root))

    with pytest.raises(WorkspaceScopeError):
        manager.resolve_path(str(outside / "secret.txt"))
    with pytest.raises(WorkspaceScopeError):
        manager.add_root(str(outside / "missing"))


def test_file_directory_and_terminal_tools_share_workspace_scope(tmp_path):
    root = tmp_path / "visible"
    root.mkdir()
    manager = WorkspaceManager(str(tmp_path / "data"), default_root=str(root))
    writer = FileWriteExecutor(manager)
    reader = FileReadExecutor(manager)
    listing = DirectoryListExecutor(manager)
    terminal = TerminalCommandExecutor(manager)

    written = writer.execute(_request("create_file", {"filename": "notes/today.txt", "content": "核心能力"}))
    assert written.status is ToolCallStatus.SUCCESS
    read = reader.execute(_request("read_file", {"filename": "notes/today.txt"}))
    assert read.status is ToolCallStatus.SUCCESS
    assert "核心能力" in read.result_content
    listed = listing.execute(_request("list_directory", {"path": "notes"}))
    assert listed.status is ToolCallStatus.SUCCESS
    assert "today.txt" in listed.result_content

    ran = terminal.execute(_request("run_terminal_command", {
        "command": "pwd",
        "cwd": None,
        "timeout_seconds": 10,
    }))
    import sys
    import shutil
    sandbox_available = sys.platform.startswith("linux") and shutil.which("bwrap")
    if sandbox_available:
        assert ran.status is ToolCallStatus.SUCCESS
        assert str(root.resolve()) in json.loads(ran.result_content)["stdout"]
    else:
        assert ran.status is ToolCallStatus.FAILED
    python_ran = terminal.execute(_request("run_terminal_command", {
        "command": "python -c 'print(\"terminal-ok\")'",
        "cwd": None,
        "timeout_seconds": 10,
    }))
    if sandbox_available:
        assert python_ran.status is ToolCallStatus.SUCCESS
        assert "terminal-ok" in json.loads(python_ran.result_content)["stdout"]
    else:
        assert python_ran.status is ToolCallStatus.FAILED

    rejected = reader.execute(_request("read_file", {"filename": str(tmp_path / "secret.txt")}))
    assert rejected.status is ToolCallStatus.FAILED
    assert "超出" in rejected.error_message


def test_terminal_sandbox_fails_closed_on_unsupported_platform(tmp_path, monkeypatch):
    root = tmp_path / "visible"
    root.mkdir()
    monkeypatch.setattr("src.memory.app.tools.sys.platform", "win32")

    with pytest.raises(WorkspaceScopeError, match="暂未提供.*终端沙箱"):
        TerminalCommandExecutor._sandbox_command("dir", root, [root])
