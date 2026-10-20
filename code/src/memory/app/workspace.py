"""Persistent, user-managed workspace scope for local Agent tools."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional


class WorkspaceScopeError(ValueError):
    """Raised when a workspace or a path is outside the configured scope."""


class WorkspaceManager:
    """Owns the durable list of folders visible to local Agent tools.

    The manager is intentionally independent of natural-language intent.  It
    only performs path normalization, persistence, and containment checks.
    Relative tool paths are resolved from the selected current workspace.
    """

    def __init__(self, data_dir: str, *, default_root: Optional[str] = None) -> None:
        self.data_dir = Path(data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.workspace_dir = self.data_dir / "workspaces"
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.workspace_dir / "workspace_scope.json"
        legacy_state_path = self.data_dir / "workspace_scope.json"
        if not self.state_path.exists() and legacy_state_path.exists():
            try:
                os.replace(legacy_state_path, self.state_path)
            except OSError:
                pass
        self._lock = threading.RLock()
        initial = default_root or os.getcwd()
        self._roots: List[str] = []
        self._current_root: Optional[str] = None
        self._load(initial)

    @staticmethod
    def _canonical(path: str | os.PathLike[str], *, base: Optional[Path] = None) -> Path:
        raw = Path(path).expanduser()
        if not raw.is_absolute():
            raw = (base or Path.cwd()) / raw
        return raw.resolve(strict=False)

    @staticmethod
    def _ensure_directory(path: Path) -> None:
        if not path.exists() or not path.is_dir():
            raise WorkspaceScopeError(f"工作区必须是已存在的文件夹: {path}")
        if path == Path(path.anchor):
            raise WorkspaceScopeError("不能把整个文件系统根目录设为 Agent 工作区")

    def _load(self, default_root: str) -> None:
        with self._lock:
            loaded: Dict[str, Any] = {}
            try:
                if self.state_path.exists():
                    candidate = json.loads(self.state_path.read_text(encoding="utf-8"))
                    if isinstance(candidate, dict):
                        loaded = candidate
            except (OSError, ValueError):
                loaded = {}

            roots = loaded.get("roots")
            if isinstance(roots, list):
                for item in roots:
                    if isinstance(item, str) and item.strip():
                        normalized = str(self._canonical(item))
                        if normalized not in self._roots:
                            self._roots.append(normalized)
            if not self._roots:
                normalized = str(self._canonical(default_root))
                self._roots = [normalized]
            current = loaded.get("current_root")
            if isinstance(current, str) and current.strip():
                current_normalized = str(self._canonical(current))
                if current_normalized in self._roots:
                    self._current_root = current_normalized
            if self._current_root is None:
                self._current_root = self._roots[0]
            if not loaded:
                self._persist()

    def _persist(self) -> None:
        payload = {"schema_version": 1, "roots": self._roots, "current_root": self._current_root}
        temporary_path: Optional[str] = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.data_dir,
                prefix=".workspace-scope-", suffix=".tmp", delete=False,
            ) as temporary:
                json.dump(payload, temporary, ensure_ascii=False, indent=2)
                temporary.write("\n")
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = temporary.name
            os.replace(temporary_path, self.state_path)
        finally:
            if temporary_path and os.path.exists(temporary_path):
                try:
                    os.unlink(temporary_path)
                except OSError:
                    pass

    def list_roots(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [
                {"path": path, "current": path == self._current_root, "exists": Path(path).is_dir()}
                for path in self._roots
            ]

    @property
    def current_root(self) -> Optional[str]:
        with self._lock:
            return self._current_root

    def snapshot(self) -> Dict[str, Any]:
        """Return a serializable snapshot of the current authorized scope."""
        with self._lock:
            return {
                "roots": list(self._roots),
                "current_root": self._current_root,
            }

    def replace_scope(self, roots: List[str], current_root: Optional[str]) -> None:
        """Restore a session's folder scope.

        Roots are allowed to be temporarily missing because a folder can be
        renamed or disconnected between sessions.  The path remains
        authorized, while normal tool execution still reports that it is not
        currently available.  This preserves the session's intent without
        silently falling back to another session's folder.
        """
        normalized_roots: List[str] = []
        for item in roots:
            if not isinstance(item, str) or not item.strip():
                continue
            normalized = str(self._canonical(item))
            if normalized not in normalized_roots:
                normalized_roots.append(normalized)

        normalized_current = None
        if isinstance(current_root, str) and current_root.strip():
            normalized_current = str(self._canonical(current_root))
            if normalized_current not in normalized_roots:
                normalized_roots.append(normalized_current)

        with self._lock:
            self._roots = normalized_roots
            self._current_root = normalized_current
            if self._current_root is None and self._roots:
                self._current_root = self._roots[0]
            self._persist()

    def add_root(self, path: str) -> Dict[str, Any]:
        normalized = self._canonical(path)
        self._ensure_directory(normalized)
        value = str(normalized)
        with self._lock:
            if value not in self._roots:
                self._roots.append(value)
            if self._current_root is None:
                self._current_root = value
            self._persist()
            return next(item for item in self.list_roots() if item["path"] == value)

    def remove_root(self, path: str) -> bool:
        normalized = str(self._canonical(path))
        with self._lock:
            if normalized not in self._roots:
                return False
            self._roots.remove(normalized)
            self._current_root = self._roots[0] if normalized == self._current_root and self._roots else self._current_root
            if self._current_root not in self._roots:
                self._current_root = self._roots[0] if self._roots else None
            self._persist()
            return True

    def use_root(self, path: str) -> str:
        normalized = str(self._canonical(path))
        with self._lock:
            if normalized not in self._roots:
                raise WorkspaceScopeError("该文件夹不在 Agent 可见范围内，请先使用 /workspace-add 添加")
            if not Path(normalized).is_dir():
                raise WorkspaceScopeError(f"当前工作区不存在或不是文件夹: {normalized}")
            self._current_root = normalized
            self._persist()
            return normalized

    def resolve_path(self, path: str, *, must_exist: bool = False, directory: bool = False) -> Path:
        if not isinstance(path, str) or not path.strip():
            raise WorkspaceScopeError("路径不能为空")
        with self._lock:
            roots = [Path(item) for item in self._roots]
            if self._current_root is None:
                raise WorkspaceScopeError("尚未配置工作区，请先添加文件夹")
            base = Path(self._current_root)
        candidate = self._canonical(path, base=base)
        for root in roots:
            try:
                candidate.relative_to(root)
            except ValueError:
                continue
            if must_exist and not candidate.exists():
                raise WorkspaceScopeError(f"工作区内不存在该路径: {candidate}")
            if directory and candidate.exists() and not candidate.is_dir():
                raise WorkspaceScopeError(f"路径不是文件夹: {candidate}")
            return candidate
        raise WorkspaceScopeError("路径超出 Agent 当前可见的工作区范围")

    def describe(self) -> Dict[str, Any]:
        with self._lock:
            return {"current_root": self._current_root, "roots": self.list_roots()}
