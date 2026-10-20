import json

from src.memory.agent.tooling import ToolExecutionRequest
from src.memory.app.tools import ApplyPatchExecutor, FileSearchExecutor, build_product_tool_registry
from src.memory.app.workspace import WorkspaceManager
from src.memory.runtime.models import ToolCallStatus


def _request(name, arguments):
    return ToolExecutionRequest(
        tool_call_id=f"call_{name}",
        tool_name=name,
        arguments=arguments,
        idempotency_key=f"idemp_{name}",
        agent_run_id=f"run_{name}",
        agent_step_id=f"step_{name}",
    )


def test_file_search_is_scoped_and_returns_line_evidence(tmp_path):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "notes.txt").write_text("Alpha\nimportant detail\n", encoding="utf-8")
    (workspace_root / "binary.bin").write_bytes(b"important\x00detail")
    manager = WorkspaceManager(str(tmp_path / "agent"), default_root=str(workspace_root))

    result = FileSearchExecutor(manager).execute(_request("search_files", {"query": "IMPORTANT"}))

    assert result.status is ToolCallStatus.SUCCESS
    payload = json.loads(result.result_content)
    assert payload["matches"] == [{
        "path": str(workspace_root / "notes.txt"),
        "line": 2,
        "column": 1,
        "text": "important detail",
    }]
    assert payload["files_scanned"] == 1
    assert payload["files_skipped"] == 1


def test_apply_patch_supports_add_update_delete_without_shell(tmp_path):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    target = workspace_root / "notes.txt"
    target.write_text("one\ntwo\n", encoding="utf-8")
    manager = WorkspaceManager(str(tmp_path / "agent"), default_root=str(workspace_root))
    patch = """*** Begin Patch
*** Update File: notes.txt
@@
 one
-two
+changed
*** Add File: added.txt
+new file
*** End Patch"""

    result = ApplyPatchExecutor(manager).execute(_request("apply_patch", {"patch": patch}))

    assert result.status is ToolCallStatus.SUCCESS
    assert target.read_text(encoding="utf-8") == "one\nchanged\n"
    assert (workspace_root / "added.txt").read_text(encoding="utf-8") == "new file\n"

    delete = """*** Begin Patch
*** Delete File: added.txt
*** End Patch"""
    deleted = ApplyPatchExecutor(manager).execute(_request("apply_patch", {"patch": delete}))
    assert deleted.status is ToolCallStatus.SUCCESS
    assert not (workspace_root / "added.txt").exists()


def test_apply_patch_rejects_paths_outside_workspace(tmp_path):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    manager = WorkspaceManager(str(tmp_path / "agent"), default_root=str(workspace_root))
    patch = """*** Begin Patch
*** Add File: ../outside.txt
+must not write
*** End Patch"""

    result = ApplyPatchExecutor(manager).execute(_request("apply_patch", {"patch": patch}))

    assert result.status is ToolCallStatus.FAILED
    assert not (tmp_path / "outside.txt").exists()


def test_product_registry_exposes_search_and_patch_contracts():
    registry = build_product_tool_registry()
    search = registry.get_spec("search_files")
    patch = registry.get_spec("apply_patch")
    assert search is not None and search.effect.value == "READ_ONLY"
    assert patch is not None and patch.effect.value == "SIDE_EFFECT"
