"""Product Tool Registry and safe local tools for Personal Agent App."""

from __future__ import annotations

import ast
import datetime
import html
import http.client
import ipaddress
import json
import os
import subprocess
import socket
import shutil
import sys
import tempfile
import zlib
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import parse_qs, quote_plus, urljoin, urlparse, urlunparse
from urllib.request import HTTPHandler, HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

from src.memory.runtime.models import ToolCallResult, ToolCallStatus
from src.memory.app.workspace import WorkspaceManager, WorkspaceScopeError
from src.memory.agent.tooling import (
    IdempotencySupport,
    ToolEffect,
    ToolExecutionRequest,
    ToolExecutor,
    ToolRegistry,
    ToolSpec,
)


class TimeLookupExecutor(ToolExecutor):
    """Safe read-only tool executor to return local system date and time."""

    def execute(self, request: ToolExecutionRequest) -> ToolCallResult:
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S %A")
        return ToolCallResult(
            tool_call_id=request.tool_call_id,
            tool_name=request.tool_name,
            arguments=request.arguments,
            status=ToolCallStatus.SUCCESS,
            result_content=f"当前系统时间: {now_str}",
        )


class LongTermMemoryUpdateExecutor(ToolExecutor):
    """Dispatch the current user turn to the app-owned memory update boundary.

    The Agent chooses whether to call this tool.  The executor deliberately
    receives no candidate memory text from the model: the app callback binds
    the exact current user message and its provenance before invoking the
    Provider-owned lifecycle proposer.
    """

    def __init__(self, callback: Callable[[ToolExecutionRequest], ToolCallResult]) -> None:
        self._callback = callback

    def execute(self, request: ToolExecutionRequest) -> ToolCallResult:
        return self._callback(request)


def _safe_eval_ast(node: ast.AST) -> float | int:
    """Recursively evaluates AST node using explicit operator whitelist."""
    if isinstance(node, ast.Expression):
        return _safe_eval_ast(node.body)
    elif isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    elif isinstance(node, ast.UnaryOp):
        operand = _safe_eval_ast(node.operand)
        if isinstance(node.op, ast.UAdd):
            return +operand
        elif isinstance(node.op, ast.USub):
            return -operand
        else:
            raise ValueError(f"不支持的一元运算符: {type(node.op).__name__}")
    elif isinstance(node, ast.BinOp):
        left = _safe_eval_ast(node.left)
        right = _safe_eval_ast(node.right)
        if isinstance(node.op, ast.Add):
            return left + right
        elif isinstance(node.op, ast.Sub):
            return left - right
        elif isinstance(node.op, ast.Mult):
            return left * right
        elif isinstance(node.op, ast.Div):
            if right == 0:
                raise ZeroDivisionError("除数不能为 0")
            return left / right
        elif isinstance(node.op, ast.FloorDiv):
            if right == 0:
                raise ZeroDivisionError("除数不能为 0")
            return left // right
        elif isinstance(node.op, ast.Mod):
            return left % right
        elif isinstance(node.op, ast.Pow):
            if abs(right) > 50 or abs(left) > 10000:
                raise ValueError("幂指数或基数超出安全范围上限 (exponent <= 50)")
            return left ** right
        else:
            raise ValueError(f"不支持的二元运算符: {type(node.op).__name__}")
    else:
        raise ValueError(f"算术表达式包含非白名单语法节点: {type(node).__name__}")


class SafeCalculatorExecutor(ToolExecutor):
    """Safe read-only tool executor using AST white-listed arithmetic evaluation."""

    def execute(self, request: ToolExecutionRequest) -> ToolCallResult:
        expr = request.arguments.get("expression", "").strip()
        if not expr:
            return ToolCallResult(
                tool_call_id=request.tool_call_id,
                tool_name=request.tool_name,
                arguments=request.arguments,
                status=ToolCallStatus.FAILED,
                error_message="错误: 未提供算术表达式",
            )

        try:
            parsed_ast = ast.parse(expr, mode="eval")
            res = _safe_eval_ast(parsed_ast)
            return ToolCallResult(
                tool_call_id=request.tool_call_id,
                tool_name=request.tool_name,
                arguments=request.arguments,
                status=ToolCallStatus.SUCCESS,
                result_content=f"计算结果: {res}",
            )
        except Exception as exc:
            return ToolCallResult(
                tool_call_id=request.tool_call_id,
                tool_name=request.tool_name,
                arguments=request.arguments,
                status=ToolCallStatus.FAILED,
                error_message=f"计算错误: {exc}",
            )


_LOCAL_FILE_RESULT_CHARS = 20_000
_TERMINAL_OUTPUT_CHARS = 20_000
_FILE_SEARCH_MAX_BYTES = 2 * 1024 * 1024
_FILE_SEARCH_MAX_RESULTS = 200
_PATCH_MAX_CHARS = 200_000


class FileWriteExecutor(ToolExecutor):
    """Writes a confirmed text file inside the user-managed workspace scope."""

    def __init__(self, workspace: WorkspaceManager | str) -> None:
        self.workspace = workspace if isinstance(workspace, WorkspaceManager) else WorkspaceManager(workspace, default_root=workspace)

    def execute(self, request: ToolExecutionRequest) -> ToolCallResult:
        filename = request.arguments.get("filename")
        content = request.arguments.get("content")
        if not isinstance(filename, str) or not filename.strip():
            return _safe_failure(request, "文件名不能为空")
        if not isinstance(content, str):
            return _safe_failure(request, "文件内容必须是字符串")

        try:
            target = self.workspace.resolve_path(filename)
        except WorkspaceScopeError as exc:
            return _safe_failure(request, str(exc))
        if target.exists() and target.is_dir():
            return _safe_failure(request, "目标路径是文件夹，不能写入文本文件")

        temporary_path = None
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=target.parent,
                prefix=".agent-write-",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = temporary.name
            os.replace(temporary_path, target)
            return ToolCallResult(
                tool_call_id=request.tool_call_id,
                tool_name=request.tool_name,
                arguments=request.arguments,
                status=ToolCallStatus.SUCCESS,
                result_content=json.dumps(
                    {"path": str(target), "bytes": len(content.encode("utf-8"))},
                    ensure_ascii=False,
                ),
            )
        except Exception as exc:
            if temporary_path:
                try:
                    os.unlink(temporary_path)
                except OSError:
                    pass
            return _safe_failure(request, f"文件写入失败 ({type(exc).__name__})")


class FileReadExecutor(ToolExecutor):
    """Reads a bounded UTF-8 text file inside the workspace scope."""

    def __init__(self, workspace: WorkspaceManager) -> None:
        self.workspace = workspace

    def execute(self, request: ToolExecutionRequest) -> ToolCallResult:
        filename = request.arguments.get("filename")
        if not isinstance(filename, str) or not filename.strip():
            return _safe_failure(request, "文件路径不能为空")
        try:
            target = self.workspace.resolve_path(filename, must_exist=True)
            if not target.is_file():
                return _safe_failure(request, "目标路径不是文件")
            content = target.read_text(encoding="utf-8")
            truncated = len(content) > _LOCAL_FILE_RESULT_CHARS
            if truncated:
                content = content[:_LOCAL_FILE_RESULT_CHARS]
            suffix = "\n[输出已截断]" if truncated else ""
            return ToolCallResult(
                tool_call_id=request.tool_call_id,
                tool_name=request.tool_name,
                arguments=request.arguments,
                status=ToolCallStatus.SUCCESS,
                result_content=f"文件: {target}\n{content}{suffix}",
            )
        except (WorkspaceScopeError, OSError, UnicodeError) as exc:
            return _safe_failure(request, f"文件读取失败: {exc}")


class DirectoryListExecutor(ToolExecutor):
    """Lists a bounded directory view inside the workspace scope."""

    def __init__(self, workspace: WorkspaceManager) -> None:
        self.workspace = workspace

    def execute(self, request: ToolExecutionRequest) -> ToolCallResult:
        path = request.arguments.get("path") or "."
        if not isinstance(path, str):
            return _safe_failure(request, "目录路径必须是字符串")
        try:
            target = self.workspace.resolve_path(path, must_exist=True, directory=True)
            entries = []
            for item in sorted(target.iterdir(), key=lambda value: (not value.is_dir(), value.name.casefold())):
                entries.append({"name": item.name, "type": "directory" if item.is_dir() else "file"})
                if len(entries) >= 500:
                    break
            return ToolCallResult(
                tool_call_id=request.tool_call_id,
                tool_name=request.tool_name,
                arguments=request.arguments,
                status=ToolCallStatus.SUCCESS,
                result_content=json.dumps({"path": str(target), "entries": entries}, ensure_ascii=False),
            )
        except (WorkspaceScopeError, OSError) as exc:
            return _safe_failure(request, f"目录读取失败: {exc}")


class FileSearchExecutor(ToolExecutor):
    """Search literal text in bounded UTF-8 files within the workspace."""

    def __init__(self, workspace: WorkspaceManager) -> None:
        self.workspace = workspace

    def execute(self, request: ToolExecutionRequest) -> ToolCallResult:
        query = request.arguments.get("query")
        path = request.arguments.get("path") or "."
        case_sensitive = request.arguments.get("case_sensitive", False)
        max_results = request.arguments.get("max_results", 50)
        if not isinstance(query, str) or not query.strip():
            return _safe_failure(request, "搜索内容不能为空")
        if len(query) > 300:
            return _safe_failure(request, "搜索内容过长")
        if not isinstance(path, str) or not isinstance(case_sensitive, bool):
            return _safe_failure(request, "搜索路径和大小写选项格式无效")
        if isinstance(max_results, bool) or not isinstance(max_results, int) or not 1 <= max_results <= _FILE_SEARCH_MAX_RESULTS:
            return _safe_failure(request, f"max_results 必须是 1 到 {_FILE_SEARCH_MAX_RESULTS} 之间的整数")
        try:
            root = self.workspace.resolve_path(path, must_exist=True, directory=True)
        except WorkspaceScopeError as exc:
            return _safe_failure(request, str(exc))

        needle = query if case_sensitive else query.casefold()
        matches: List[Dict[str, Any]] = []
        files_scanned = 0
        files_skipped = 0
        try:
            for directory, dir_names, file_names in os.walk(root, topdown=True, followlinks=False):
                dir_path = Path(directory)
                dir_names[:] = [name for name in dir_names if not (dir_path / name).is_symlink()]
                for filename in sorted(file_names, key=str.casefold):
                    target = dir_path / filename
                    if target.is_symlink():
                        files_skipped += 1
                        continue
                    try:
                        if target.stat().st_size > _FILE_SEARCH_MAX_BYTES:
                            files_skipped += 1
                            continue
                        raw = target.read_bytes()
                        if b"\x00" in raw:
                            files_skipped += 1
                            continue
                        text = raw.decode("utf-8")
                    except (OSError, UnicodeError):
                        files_skipped += 1
                        continue
                    files_scanned += 1
                    lines = text.splitlines()
                    for line_number, line in enumerate(lines, start=1):
                        haystack = line if case_sensitive else line.casefold()
                        column = haystack.find(needle)
                        if column < 0:
                            continue
                        matches.append({
                            "path": str(target),
                            "line": line_number,
                            "column": column + 1,
                            "text": line[:1000],
                        })
                        if len(matches) >= max_results:
                            break
                    if len(matches) >= max_results:
                        break
                if len(matches) >= max_results:
                    break
        except OSError as exc:
            return _safe_failure(request, f"文件搜索失败: {exc}")
        payload = {
            "query": query,
            "path": str(root),
            "case_sensitive": case_sensitive,
            "matches": matches,
            "truncated": len(matches) >= max_results,
            "files_scanned": files_scanned,
            "files_skipped": files_skipped,
        }
        return ToolCallResult(
            tool_call_id=request.tool_call_id,
            tool_name=request.tool_name,
            arguments=request.arguments,
            status=ToolCallStatus.SUCCESS,
            result_content=json.dumps(payload, ensure_ascii=False),
        )


def _parse_apply_patch(patch: str) -> List[tuple[str, str, List[str]]]:
    """Parse the bounded Codex-style patch envelope without executing a shell."""
    lines = patch.splitlines()
    if not lines or lines[0].strip() != "*** Begin Patch" or lines[-1].strip() != "*** End Patch":
        raise ValueError("patch 必须使用 *** Begin Patch / *** End Patch 包裹")
    operations: List[tuple[str, str, List[str]]] = []
    index = 1
    while index < len(lines) - 1:
        header = lines[index]
        index += 1
        matched = next((prefix for prefix in ("*** Add File: ", "*** Update File: ", "*** Delete File: ") if header.startswith(prefix)), None)
        if matched is None:
            raise ValueError(f"不支持的 patch 文件头: {header}")
        path = header[len(matched):].strip()
        if not path:
            raise ValueError("patch 文件路径不能为空")
        action = "add" if matched.startswith("*** Add") else "update" if matched.startswith("*** Update") else "delete"
        body: List[str] = []
        while index < len(lines) - 1 and not lines[index].startswith("*** "):
            body.append(lines[index])
            index += 1
        operations.append((action, path, body))
    return operations


def _apply_update_hunks(original: str, body: List[str]) -> str:
    """Apply exact context hunks from the patch format to one text file."""
    current = original.splitlines()
    had_trailing_newline = original.endswith("\n")
    hunks: List[List[str]] = []
    active: Optional[List[str]] = None
    for line in body:
        if line.startswith("@@"):
            active = []
            hunks.append(active)
        elif active is None:
            raise ValueError("Update File 缺少 @@ hunk 标记")
        elif not line or line[0] not in " +-":
            raise ValueError("patch hunk 含有无效行")
        else:
            active.append(line)
    if not hunks:
        raise ValueError("Update File 至少需要一个 @@ hunk")
    offset = 0
    for hunk in hunks:
        old_lines = [line[1:] for line in hunk if line[0] in " -"]
        new_lines = [line[1:] for line in hunk if line[0] in " +"]
        start = next((position for position in range(offset, len(current) - len(old_lines) + 1) if current[position:position + len(old_lines)] == old_lines), None)
        if start is None:
            raise ValueError("patch 上下文与目标文件不匹配")
        current[start:start + len(old_lines)] = new_lines
        offset = start + len(new_lines)
    return "\n".join(current) + ("\n" if had_trailing_newline else "")


class ApplyPatchExecutor(ToolExecutor):
    """Apply a confirmed multi-file text patch within authorized roots."""

    def __init__(self, workspace: WorkspaceManager) -> None:
        self.workspace = workspace

    def execute(self, request: ToolExecutionRequest) -> ToolCallResult:
        patch = request.arguments.get("patch")
        if not isinstance(patch, str) or not patch.strip():
            return _safe_failure(request, "patch 不能为空")
        if len(patch) > _PATCH_MAX_CHARS:
            return _safe_failure(request, "patch 过大")
        try:
            operations = _parse_apply_patch(patch)
            prepared: List[tuple[str, Path, Optional[str]]] = []
            seen: set[Path] = set()
            for action, filename, body in operations:
                target = self.workspace.resolve_path(filename)
                if target in seen:
                    raise ValueError(f"patch 重复操作同一路径: {filename}")
                seen.add(target)
                if action == "add":
                    if target.exists():
                        raise ValueError(f"待新增文件已存在: {filename}")
                    content_lines = []
                    for line in body:
                        if not line.startswith("+"):
                            raise ValueError("Add File 的每一行都必须以 + 开头")
                        content_lines.append(line[1:])
                    prepared.append((action, target, "\n".join(content_lines) + ("\n" if content_lines else "")))
                elif action == "update":
                    if not target.is_file():
                        raise ValueError(f"待更新文件不存在: {filename}")
                    prepared.append((action, target, _apply_update_hunks(target.read_text(encoding="utf-8"), body)))
                else:
                    if not target.is_file():
                        raise ValueError(f"待删除文件不存在: {filename}")
                    if body:
                        raise ValueError("Delete File 不应包含 patch 内容")
                    prepared.append((action, target, None))

            for action, target, content in prepared:
                if action == "delete":
                    target.unlink()
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary_path = None
                try:
                    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent, prefix=".agent-patch-", suffix=".tmp", delete=False) as temporary:
                        temporary.write(content or "")
                        temporary.flush()
                        os.fsync(temporary.fileno())
                        temporary_path = temporary.name
                    os.replace(temporary_path, target)
                finally:
                    if temporary_path and os.path.exists(temporary_path):
                        try:
                            os.unlink(temporary_path)
                        except OSError:
                            pass
            return ToolCallResult(
                tool_call_id=request.tool_call_id,
                tool_name=request.tool_name,
                arguments=request.arguments,
                status=ToolCallStatus.SUCCESS,
                result_content=json.dumps({"files": [str(target) for _, target, _ in prepared]}, ensure_ascii=False),
            )
        except (WorkspaceScopeError, OSError, UnicodeError, ValueError) as exc:
            return _safe_failure(request, f"patch 应用失败: {exc}")


class TerminalCommandExecutor(ToolExecutor):
    """Runs a shell command with a workspace-selected working directory.

    The command is deliberately a SIDE_EFFECT tool because arbitrary shell
    commands cannot be classified safely from their text.  The application
    confirmation boundary therefore remains in force for every command.
    """

    def __init__(self, workspace: WorkspaceManager) -> None:
        self.workspace = workspace

    @staticmethod
    def _sandbox_command(command: str, cwd: Path, roots: List[Path]) -> List[str]:
        """Build a Linux sandbox command with only authorized roots writable."""
        if not sys.platform.startswith("linux"):
            raise WorkspaceScopeError(
                "当前平台暂未提供受工作区限制的终端沙箱；为避免越过 Agent 授权范围，终端命令未执行"
            )
        bwrap = shutil.which("bwrap")
        if not bwrap:
            raise WorkspaceScopeError(
                "当前 Linux 系统没有 bubblewrap，无法安全启动受工作区限制的终端；"
                "请安装 bubblewrap 后重试"
            )
        runtime_bin = str(Path(sys.executable).resolve().parent)
        runtime_prefix = Path(runtime_bin).parents[2] if len(Path(runtime_bin).parents) > 2 else None
        path_value = f"{runtime_bin}:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        args = [
            bwrap, "--die-with-parent", "--new-session", "--unshare-pid", "--unshare-net",
            "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--tmpfs", "/run",
            "--clearenv", "--setenv", "PATH", path_value,
            "--setenv", "HOME", "/tmp", "--setenv", "LANG", "C.UTF-8",
        ]
        # Keep normal system executables and libraries available read-only.
        for system_path in ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc", "/sys"):
            if os.path.exists(system_path):
                args.extend(["--ro-bind", system_path, system_path])
        created: set[str] = set()
        if runtime_prefix is not None and runtime_prefix.is_dir() and str(runtime_prefix) not in {"/usr", "/bin", "/lib"}:
            current = Path(runtime_prefix.anchor or "/")
            for part in runtime_prefix.parts[1:]:
                current = current / part
                current_text = str(current)
                if current_text not in created:
                    args.extend(["--dir", str(current)])
                    created.add(current_text)
            args.extend(["--ro-bind", str(runtime_prefix), str(runtime_prefix)])

        # The namespace starts empty. Create parents before binding each
        # authorized folder back at its original absolute path.
        for root in sorted((item for item in roots if item.is_dir()), key=lambda item: len(item.parts)):
            current = Path(root.anchor or "/")
            for part in root.parts[1:]:
                current = current / part
                current_text = str(current)
                if current_text not in created:
                    args.extend(["--dir", current_text])
                    created.add(current_text)
            args.extend(["--bind", str(root), str(root)])
        args.extend(["--chdir", str(cwd), "--", "/bin/bash", "-lc", command])
        return args

    def execute(self, request: ToolExecutionRequest) -> ToolCallResult:
        command = request.arguments.get("command")
        cwd_arg = request.arguments.get("cwd") or self.workspace.current_root
        timeout = request.arguments.get("timeout_seconds", 60)
        if not isinstance(command, str) or not command.strip():
            return _safe_failure(request, "终端命令不能为空")
        try:
            cwd = self.workspace.resolve_path(cwd_arg, must_exist=True, directory=True)
            roots = [Path(item["path"]) for item in self.workspace.list_roots() if item["exists"]]
            completed = subprocess.run(
                self._sandbox_command(command, cwd, roots),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=float(timeout),
                check=False,
            )
            stdout = completed.stdout[-_TERMINAL_OUTPUT_CHARS:]
            stderr = completed.stderr[-_TERMINAL_OUTPUT_CHARS:]
            payload = {
                "cwd": str(cwd),
                "exit_code": completed.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "output_truncated": len(completed.stdout) > _TERMINAL_OUTPUT_CHARS or len(completed.stderr) > _TERMINAL_OUTPUT_CHARS,
            }
            status = ToolCallStatus.SUCCESS if completed.returncode == 0 else ToolCallStatus.FAILED
            return ToolCallResult(
                tool_call_id=request.tool_call_id,
                tool_name=request.tool_name,
                arguments=request.arguments,
                status=status,
                result_content=json.dumps(payload, ensure_ascii=False),
                error_message="" if status is ToolCallStatus.SUCCESS else f"命令退出码: {completed.returncode}",
            )
        except WorkspaceScopeError as exc:
            return _safe_failure(request, str(exc))
        except subprocess.TimeoutExpired:
            return _safe_failure(request, "终端命令超时")
        except (OSError, ValueError) as exc:
            return _safe_failure(request, f"终端命令执行失败: {exc}")


_WEB_USER_AGENT = "PersonalAgent/1.0 (+https://example.invalid/personal-agent)"
_WEB_TIMEOUT_SECONDS = 12.0
_WEB_MAX_RESPONSE_BYTES = 1_000_000
_WEB_MAX_RESULT_CHARS = 8_000
_NETWORK_TRANSLATION_RANGE = ipaddress.ip_network("198.18.0.0/15")
_WEATHER_MAX_LOCATION_CHARS = 120


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class _DuckDuckGoParser(HTMLParser):
    """Small dependency-free parser for DuckDuckGo's HTML result page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: List[Dict[str, str]] = []
        self._current: Optional[Dict[str, str]] = None
        self._capture: Optional[str] = None
        self._buffer: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[tuple[str, Optional[str]]]) -> None:
        attrs_map = dict(attrs)
        classes = set((attrs_map.get("class") or "").split())
        if tag == "a" and {"result__a", "result-link"}.intersection(classes):
            self._current = {"title": "", "url": html.unescape(attrs_map.get("href") or "")}
            self._capture = "title"
            self._buffer = []
        elif self._current is not None and tag in {"a", "div", "td"} and {"result__snippet", "result-snippet"}.intersection(classes):
            self._capture = "snippet"
            self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._capture == "title" and tag == "a" and self._current is not None:
            self._current["title"] = " ".join("".join(self._buffer).split())
            self._capture = None
            self._buffer = []
        elif self._capture == "snippet" and tag in {"a", "div", "td"} and self._current is not None:
            self._current["snippet"] = " ".join("".join(self._buffer).split())
            self.results.append(self._current)
            self._current = None
            self._capture = None
            self._buffer = []


class _ReadableHTMLParser(HTMLParser):
    """Extracts bounded readable text while skipping executable/page chrome."""

    _SKIP_TAGS = {"script", "style", "noscript", "svg", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self._skip_depth = 0
        self._parts: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[tuple[str, Optional[str]]]) -> None:
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skip_depth:
            cleaned = " ".join(data.split())
            if cleaned:
                self._parts.append(cleaned)

    @property
    def text(self) -> str:
        return " ".join(self._parts)


def _safe_failure(request: ToolExecutionRequest, message: str) -> ToolCallResult:
    return ToolCallResult(
        tool_call_id=request.tool_call_id,
        tool_name=request.tool_name,
        arguments=request.arguments,
        status=ToolCallStatus.FAILED,
        error_message=message,
    )


def _unwrap_search_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.path == "/l/":
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        if target:
            return target
    return urljoin("https://html.duckduckgo.com", url)


def _resolved_public_addresses(hostname: str, port: int, *, allow_network_translation: bool) -> List[tuple[Any, ...]]:
    """Resolve a host and retain only addresses safe for direct public egress.

    Some DNS environments return an unusable transition IPv6 record alongside
    a valid public IPv4 record.  Rejecting the whole hostname in that case
    breaks legitimate requests.  Returning only validated records lets the
    connection layer pin the socket to a public address without weakening the
    SSRF boundary or trusting a second, potentially different DNS lookup.
    """
    try:
        resolved = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError("无法解析目标主机") from exc

    permitted: List[tuple[Any, ...]] = []
    seen = set()
    for family, socktype, proto, canonname, sockaddr in resolved:
        address = sockaddr[0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            continue
        translated_public = allow_network_translation and ip in _NETWORK_TRANSLATION_RANGE
        if not (ip.is_global or translated_public):
            continue
        identity = (family, socktype, proto, sockaddr)
        if identity in seen:
            continue
        seen.add(identity)
        permitted.append((family, socktype, proto, canonname, sockaddr))
    if not permitted:
        raise ValueError("出于安全原因不访问本地或保留地址")
    return permitted


def _validated_web_target(url: str) -> tuple[str, int, List[tuple[Any, ...]]]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("仅支持 http/https URL")
    if parsed.username or parsed.password:
        raise ValueError("URL 不得包含账号或密码")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".local"):
        raise ValueError("出于安全原因不访问本地主机名")
    try:
        literal_ip = ipaddress.ip_address(hostname)
    except ValueError:
        literal_ip = None
    if literal_ip is not None and literal_ip in _NETWORK_TRANSLATION_RANGE:
        raise ValueError("出于安全原因不直接访问网络转换地址")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    addresses = _resolved_public_addresses(
        hostname,
        port,
        allow_network_translation=literal_ip is None,
    )
    return hostname, port, addresses


def _reject_private_host(url: str) -> None:
    _validated_web_target(url)


def _connect_public_host(
    hostname: str,
    port: int,
    timeout: Any,
    source_address: Optional[tuple[str, int]] = None,
) -> socket.socket:
    """Connect only to an address validated by the public-egress policy."""
    try:
        literal_ip = ipaddress.ip_address(hostname.rstrip(".").lower())
    except ValueError:
        literal_ip = None
    addresses = _resolved_public_addresses(
        hostname,
        port,
        allow_network_translation=literal_ip is None,
    )
    last_error: Optional[OSError] = None
    for family, socktype, proto, _canonname, sockaddr in addresses:
        connection = socket.socket(family, socktype, proto)
        try:
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                connection.settimeout(timeout)
            if source_address:
                connection.bind(source_address)
            connection.connect(sockaddr)
            return connection
        except OSError as exc:
            last_error = exc
            connection.close()
    raise OSError("无法连接目标主机的公网地址") from last_error


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """HTTP connection whose socket cannot drift to an unvalidated DNS result."""

    def connect(self) -> None:
        self.sock = _connect_public_host(self.host, self.port, self.timeout, self.source_address)
        if self._tunnel_host:
            self._tunnel()


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS variant retaining hostname verification and SNI after IP pinning."""

    def connect(self) -> None:
        self.sock = _connect_public_host(self.host, self.port, self.timeout, self.source_address)
        if self._tunnel_host:
            self._tunnel()
            server_hostname = self._tunnel_host
        else:
            server_hostname = self.host
        self.sock = self._context.wrap_socket(self.sock, server_hostname=server_hostname)


def _connect_configured_proxy(
    hostname: str,
    port: int,
    timeout: Any,
    source_address: Optional[tuple[str, int]] = None,
) -> socket.socket:
    """Connect to the user-configured proxy, which may intentionally be local."""
    return socket.create_connection(
        (hostname, port),
        timeout,
        source_address,
    )


class _ProxyHTTPConnection(http.client.HTTPConnection):
    def connect(self) -> None:
        self.sock = _connect_configured_proxy(self.host, self.port, self.timeout, self.source_address)


class _PinnedProxyHTTPSConnection(http.client.HTTPSConnection):
    """Tunnel through a configured proxy to a pre-resolved public target IP."""

    def __init__(
        self,
        proxy_host: str,
        *,
        target_hostname: str,
        target_port: int,
        target_address: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(proxy_host, **kwargs)
        self._target_hostname = target_hostname
        self._target_port = target_port
        self._target_address = target_address

    def set_tunnel(self, _host: str, port: Optional[int] = None, headers: Optional[Dict[str, str]] = None) -> None:
        # ``urllib`` supplies the original hostname here.  Send CONNECT with
        # the already validated numeric address instead, while retaining the
        # original hostname below for TLS SNI and certificate verification.
        super().set_tunnel(self._target_address, self._target_port, headers)

    def connect(self) -> None:
        self.sock = _connect_configured_proxy(self.host, self.port, self.timeout, self.source_address)
        self._tunnel()
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self._target_hostname)


class _PinnedHTTPHandler(HTTPHandler):
    def http_open(self, req: Request) -> Any:
        if req.has_proxy():
            parsed = urlparse(req.full_url)
            hostname, port, addresses = _validated_web_target(req.full_url)
            target_address = addresses[0][4][0]
            authority = f"[{target_address}]" if ":" in target_address else target_address
            authority = f"{authority}:{port}"
            req.selector = urlunparse(parsed._replace(netloc=authority))
            req.add_unredirected_header("Host", parsed.netloc)
            return self.do_open(_ProxyHTTPConnection, req)
        return self.do_open(_PinnedHTTPConnection, req)


class _PinnedHTTPSHandler(HTTPSHandler):
    def https_open(self, req: Request) -> Any:
        if req._tunnel_host:
            original_url = req.full_url
            hostname, port, addresses = _validated_web_target(original_url)
            target_address = addresses[0][4][0]

            def connection_factory(proxy_host: str, **kwargs: Any) -> _PinnedProxyHTTPSConnection:
                return _PinnedProxyHTTPSConnection(
                    proxy_host,
                    target_hostname=hostname,
                    target_port=port,
                    target_address=target_address,
                    **kwargs,
                )

            return self.do_open(connection_factory, req, context=self._context)
        return self.do_open(_PinnedHTTPSConnection, req, context=self._context)


class _SafeRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Optional[Request]:
        absolute = urljoin(req.full_url, newurl)
        _reject_private_host(absolute)
        return super().redirect_request(req, fp, code, msg, headers, absolute)


def _decode_http_payload(payload: bytes, content_encoding: str, *, max_bytes: int) -> tuple[bytes, bool]:
    """Decode bounded HTTP content without allowing compression bombs.

    ``urllib`` exposes the response body as received and does not transparently
    decode Content-Encoding. Decode the standard encodings we advertise, in
    reverse order, while retaining the same decompressed size limit used for
    uncompressed responses. Unknown encodings fail closed instead of leaking
    binary data into the user-facing fetch result.
    """
    encodings = [item.strip().lower() for item in content_encoding.split(",") if item.strip()]
    decoded = payload
    truncated = False
    for encoding in reversed(encodings):
        if encoding in {"identity", ""}:
            continue
        if encoding not in {"gzip", "x-gzip", "deflate"}:
            raise ValueError(f"不支持的 HTTP 内容编码: {encoding}")
        window_bits = zlib.MAX_WBITS | 16 if encoding in {"gzip", "x-gzip"} else zlib.MAX_WBITS
        decompressor = zlib.decompressobj(window_bits)
        decoded = decompressor.decompress(decoded, max_bytes + 1)
        if len(decoded) > max_bytes or decompressor.unconsumed_tail:
            truncated = True
            decoded = decoded[:max_bytes]
            break
        decoded += decompressor.flush(max_bytes + 1 - len(decoded))
        if len(decoded) > max_bytes:
            truncated = True
            decoded = decoded[:max_bytes]
            break
    return decoded, truncated


def _bounded_json_text(payload: Dict[str, Any], text_field: str, *, max_chars: int) -> str:
    """Serialize valid JSON while fitting a potentially large text field."""
    text = str(payload.get(text_field, ""))
    candidate = dict(payload)
    encoded = json.dumps(candidate, ensure_ascii=False)
    if len(encoded) <= max_chars:
        return encoded

    low = 0
    high = len(text)
    best = ""
    while low <= high:
        midpoint = (low + high) // 2
        candidate[text_field] = text[:midpoint]
        candidate["truncated"] = True
        encoded = json.dumps(candidate, ensure_ascii=False)
        if len(encoded) <= max_chars:
            best = encoded
            low = midpoint + 1
        else:
            high = midpoint - 1
    if not best:
        candidate[text_field] = ""
        candidate["truncated"] = True
        best = json.dumps(candidate, ensure_ascii=False)
    return best


def _open_web_url(url: str, *, accept: str) -> tuple[str, str, bool, str]:
    _reject_private_host(url)
    request = Request(
        url,
        headers={
            "User-Agent": _WEB_USER_AGENT,
            "Accept": accept,
            "Accept-Encoding": "gzip, deflate",
        },
    )
    # Each redirect is validated again.  Direct sockets are pinned to filtered
    # public addresses; configured proxies receive a numeric CONNECT target so
    # they cannot resolve the hostname to a different private destination.
    opener = build_opener(
        ProxyHandler(),
        _SafeRedirectHandler(),
        _PinnedHTTPHandler(),
        _PinnedHTTPSHandler(),
    )
    with opener.open(request, timeout=_WEB_TIMEOUT_SECONDS) as response:
        final_url = response.geturl()
        _reject_private_host(final_url)
        content_type = response.headers.get_content_type().lower()
        payload = response.read(_WEB_MAX_RESPONSE_BYTES + 1)
        decoded_payload, encoding_truncated = _decode_http_payload(
            payload,
            response.headers.get("Content-Encoding", ""),
            max_bytes=_WEB_MAX_RESPONSE_BYTES,
        )
        truncated = encoding_truncated or len(payload) > _WEB_MAX_RESPONSE_BYTES
        charset = response.headers.get_content_charset() or "utf-8"
        return final_url, content_type, truncated, decoded_payload.decode(charset, errors="replace")


class WebSearchExecutor(ToolExecutor):
    """Read-only web search using a bounded public HTML endpoint."""

    def execute(self, request: ToolExecutionRequest) -> ToolCallResult:
        query = str(request.arguments.get("query", "")).strip()
        if not query:
            return _safe_failure(request, "搜索词不能为空")
        if len(query) > 300:
            return _safe_failure(request, "搜索词过长")
        try:
            endpoints = (
                f"https://lite.duckduckgo.com/lite/?q={quote_plus(query)}",
                f"https://html.duckduckgo.com/html/?q={quote_plus(query)}",
            )
            parser = _DuckDuckGoParser()
            for endpoint in endpoints:
                _, _, _, body = _open_web_url(endpoint, accept="text/html")
                parser = _DuckDuckGoParser()
                parser.feed(body)
                if parser.results:
                    break
            results = []
            for item in parser.results:
                title = item.get("title", "").strip()
                if not title or title.casefold() == "more info":
                    continue
                url = _unwrap_search_url(item.get("url", ""))
                if not url.startswith(("http://", "https://")):
                    continue
                results.append({
                    "title": title[:300],
                    "url": url[:1000],
                    "snippet": item.get("snippet", "")[:600],
                    "citation": {"url": url[:1000], "title": title[:300]},
                })
                if len(results) >= 5:
                    break
            fetched_at = _utc_now_iso()
            payload = {
                "schema_version": "external_observation.v1",
                "kind": "web_search",
                "query": query,
                "source": "DuckDuckGo",
                "source_url": endpoint,
                "fetched_at": fetched_at,
                "results": results,
                "results_truncated": False,
            }
            content = json.dumps(payload, ensure_ascii=False)
            while len(content) > _WEB_MAX_RESULT_CHARS and payload["results"]:
                payload["results"].pop()
                payload["results_truncated"] = True
                content = json.dumps(payload, ensure_ascii=False)
            return ToolCallResult(
                tool_call_id=request.tool_call_id,
                tool_name=request.tool_name,
                arguments=request.arguments,
                status=ToolCallStatus.SUCCESS,
                result_content=content,
            )
        except Exception as exc:
            return _safe_failure(request, f"联网搜索失败 ({type(exc).__name__})")


class WebFetchExecutor(ToolExecutor):
    """Read-only bounded URL fetcher with SSRF and content-size safeguards."""

    def execute(self, request: ToolExecutionRequest) -> ToolCallResult:
        url = str(request.arguments.get("url", "")).strip()
        if not url:
            return _safe_failure(request, "URL 不能为空")
        try:
            final_url, content_type, truncated, body = _open_web_url(
                url, accept="text/html,text/plain,application/json;q=0.9,*/*;q=0.1"
            )
            if content_type == "text/html" or "html" in content_type:
                parser = _ReadableHTMLParser()
                parser.feed(body)
                title = " ".join(parser.title.split())[:300]
                text = parser.text
            else:
                title = ""
                text = body
            result = {
                "schema_version": "external_observation.v1",
                "kind": "web_fetch",
                "url": final_url,
                "source": "HTTP",
                "source_url": final_url,
                "fetched_at": _utc_now_iso(),
                "content_type": content_type,
                "title": title,
                "content": text[:_WEB_MAX_RESULT_CHARS],
                "truncated": truncated or len(text) > _WEB_MAX_RESULT_CHARS,
            }
            content = _bounded_json_text(result, "content", max_chars=_WEB_MAX_RESULT_CHARS)
            return ToolCallResult(
                tool_call_id=request.tool_call_id,
                tool_name=request.tool_name,
                arguments=request.arguments,
                status=ToolCallStatus.SUCCESS,
                result_content=content,
            )
        except Exception as exc:
            return _safe_failure(request, f"网页抓取失败 ({type(exc).__name__})")


_WEATHER_CODE_DESCRIPTIONS = {
    0: "晴", 1: "大致晴", 2: "局部多云", 3: "阴", 45: "雾", 48: "雾凇",
    51: "小毛毛雨", 53: "毛毛雨", 55: "大毛毛雨", 56: "冻毛毛雨", 57: "强冻毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨", 66: "冻雨", 67: "强冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒", 80: "小阵雨",
    81: "中阵雨", 82: "强阵雨", 85: "小阵雪", 86: "强阵雪", 95: "雷暴",
    96: "雷暴伴小冰雹", 99: "雷暴伴大冰雹",
}


class WeatherExecutor(ToolExecutor):
    """Read-only current weather and short forecast via Open-Meteo."""

    def execute(self, request: ToolExecutionRequest) -> ToolCallResult:
        location = str(request.arguments.get("location", "")).strip()
        forecast_days = request.arguments.get("forecast_days", 3)
        if not location:
            return _safe_failure(request, "天气查询地点不能为空")
        if len(location) > _WEATHER_MAX_LOCATION_CHARS:
            return _safe_failure(request, "天气查询地点过长")
        if not isinstance(forecast_days, int) or isinstance(forecast_days, bool) or not 1 <= forecast_days <= 7:
            return _safe_failure(request, "forecast_days 必须是 1 到 7 之间的整数")
        try:
            geocode_url = (
                "https://geocoding-api.open-meteo.com/v1/search?"
                f"name={quote_plus(location)}&count=1&language=zh&format=json"
            )
            _, _, _, geocode_body = _open_web_url(geocode_url, accept="application/json")
            geocode = json.loads(geocode_body)
            matches = geocode.get("results") if isinstance(geocode, dict) else None
            if not isinstance(matches, list) or not matches or not isinstance(matches[0], dict):
                return _safe_failure(request, f"找不到天气地点: {location}")
            place = matches[0]
            latitude = float(place["latitude"])
            longitude = float(place["longitude"])
            effective_forecast_days = max(2, forecast_days)
            forecast_url = (
                "https://api.open-meteo.com/v1/forecast?"
                f"latitude={latitude}&longitude={longitude}"
                "&current=temperature_2m,relative_humidity_2m,apparent_temperature,"
                "precipitation,weather_code,wind_speed_10m"
                "&daily=weather_code,temperature_2m_max,temperature_2m_min,"
                "precipitation_probability_max&timezone=auto"
                f"&forecast_days={effective_forecast_days}"
            )
            _, _, _, forecast_body = _open_web_url(forecast_url, accept="application/json")
            forecast = json.loads(forecast_body)
            current = forecast.get("current", {}) if isinstance(forecast, dict) else {}
            daily = forecast.get("daily", {}) if isinstance(forecast, dict) else {}
            current_view = dict(current)
            current_code = current.get("weather_code")
            current_view["weather_description"] = _WEATHER_CODE_DESCRIPTIONS.get(current_code, "未知")
            daily_view = []
            dates = daily.get("time", [])
            codes = daily.get("weather_code", [])
            highs = daily.get("temperature_2m_max", [])
            lows = daily.get("temperature_2m_min", [])
            rain = daily.get("precipitation_probability_max", [])
            for index, date in enumerate(dates):
                code = codes[index] if index < len(codes) else None
                daily_view.append({
                    "date": date,
                    "weather_code": code,
                    "weather_description": _WEATHER_CODE_DESCRIPTIONS.get(code, "未知"),
                    "temperature_max": highs[index] if index < len(highs) else None,
                    "temperature_min": lows[index] if index < len(lows) else None,
                    "precipitation_probability_max": rain[index] if index < len(rain) else None,
                })
            fetched_at = _utc_now_iso()
            result = {
                "schema_version": "external_observation.v1",
                "kind": "weather",
                "location": place.get("name", location),
                "country": place.get("country"),
                "latitude": latitude,
                "longitude": longitude,
                "timezone": forecast.get("timezone"),
                "fetched_at": fetched_at,
                "source": "Open-Meteo",
                "source_urls": [geocode_url, forecast_url],
                "current": current_view,
                "requested_forecast_days": forecast_days,
                "daily": daily_view,
            }
            return ToolCallResult(
                tool_call_id=request.tool_call_id,
                tool_name=request.tool_name,
                arguments=request.arguments,
                status=ToolCallStatus.SUCCESS,
                result_content=json.dumps(result, ensure_ascii=False)[:_WEB_MAX_RESULT_CHARS],
            )
        except Exception as exc:
            return _safe_failure(request, f"天气查询失败 ({type(exc).__name__})")


def build_product_tool_registry(
    *,
    file_root: Optional[str] = None,
    workspace: Optional[WorkspaceManager] = None,
    memory_update_callback: Optional[Callable[[ToolExecutionRequest], ToolCallResult]] = None,
) -> ToolRegistry:
    """Builds clean product tool registry separate from evaluation fixtures."""
    registry = ToolRegistry()
    if workspace is None:
        # Compatibility for callers that used the old file_root-only factory.
        # Product applications pass their persistent WorkspaceManager.
        root = file_root or os.getcwd()
        state_dir = file_root or tempfile.mkdtemp(prefix="personal-agent-tool-scope-")
        workspace = WorkspaceManager(state_dir, default_root=root)

    time_spec = ToolSpec(
        name="get_current_time",
        description="获取当前系统本地日期与时间。无参数。",
        parameters_schema={"type": "object", "properties": {}},
        effect=ToolEffect.READ_ONLY,
        returns_user_facing_result=True,
    )
    registry.register(time_spec, TimeLookupExecutor())

    calc_spec = ToolSpec(
        name="calculator",
        description="计算算术表达式。参数：expression (str, 必需)。例如：'12.5 * 4 + 100'。",
        parameters_schema={
            "type": "object",
            "properties": {"expression": {"type": "string"}},
            "required": ["expression"],
        },
        effect=ToolEffect.READ_ONLY,
        returns_user_facing_result=True,
    )
    registry.register(calc_spec, SafeCalculatorExecutor())

    if memory_update_callback is not None:
        registry.register(
            ToolSpec(
                name="update_long_term_memory",
                description=(
                    "评估当前用户 turn 是否包含应跨会话保留的事实、偏好、身份、关系、习惯、"
                    "项目背景或更正；需要持久化时调用此工具。工具会把当前用户原文交给长期记忆"
                    "Provider 做分段和生命周期判断，不要在参数中复述或改写记忆内容。对于纯问题、"
                    "临时闲聊、助手建议或外部工具结果，不要调用。每个 turn 最多调用一次。"
                ),
                parameters_schema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                effect=ToolEffect.INTERNAL,
                idempotency_support=IdempotencySupport.SUPPORTED,
            ),
            LongTermMemoryUpdateExecutor(memory_update_callback),
        )

    registry.register(
        ToolSpec(
            name="web_search",
            description="联网搜索公开网页并返回少量标题、链接和摘要；用于新闻、资料、实时信息等需要外部证据的问题。",
            parameters_schema={
                "type": "object",
                "properties": {"query": {"type": "string", "minLength": 1, "maxLength": 300}},
                "required": ["query"],
                "additionalProperties": False,
            },
            effect=ToolEffect.READ_ONLY,
        ),
        WebSearchExecutor(),
    )
    registry.register(
        ToolSpec(
            name="web_fetch",
            description="抓取一个公开 http/https 网页并提取有界可读文本；应在搜索结果需要核验或用户给出 URL 时使用。",
            parameters_schema={
                "type": "object",
                "properties": {"url": {"type": "string", "format": "uri", "maxLength": 2000}},
                "required": ["url"],
                "additionalProperties": False,
            },
            effect=ToolEffect.READ_ONLY,
        ),
        WebFetchExecutor(),
    )
    registry.register(
        ToolSpec(
            name="weather",
            description=(
                "查询指定地点的当前天气和按实际日期排列的预报；daily 数据通常包含今天，"
                "需要未来日期时至少请求 2 天；结果包含数据来源与抓取时间。"
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "location": {"type": "string", "minLength": 1, "maxLength": _WEATHER_MAX_LOCATION_CHARS},
                    "forecast_days": {"type": "integer", "minimum": 1, "maximum": 7},
                },
                "required": ["location"],
                "additionalProperties": False,
            },
            effect=ToolEffect.READ_ONLY,
        ),
        WeatherExecutor(),
    )

    registry.register(
        ToolSpec(
            name="create_file",
            description=(
                "在用户配置的 Agent 工作区范围内创建或覆盖一个文本文件。此工具具有副作用，"
                "必须先经过用户确认；filename 可以是当前工作区内的相对路径。仅当用户明确要求创建、"
                "写入或导出外部工作区文件时使用；内部 Task 状态、任务历史和 checkpoint 由应用的结构化"
                " Task State 管理，不要用文件模拟。"
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "minLength": 1, "maxLength": 255},
                    "content": {"type": "string", "maxLength": 1_000_000},
                },
                "required": ["filename", "content"],
                "additionalProperties": False,
            },
            effect=ToolEffect.SIDE_EFFECT,
            idempotency_support=IdempotencySupport.SUPPORTED,
        ),
        FileWriteExecutor(workspace),
    )

    registry.register(
        ToolSpec(
            name="read_file",
            description="读取用户配置的 Agent 工作区范围内的 UTF-8 文本文件；filename 可为相对路径或范围内绝对路径。",
            parameters_schema={
                "type": "object",
                "properties": {"filename": {"type": "string", "minLength": 1, "maxLength": 4096}},
                "required": ["filename"],
                "additionalProperties": False,
            },
            effect=ToolEffect.READ_ONLY,
            returns_user_facing_result=True,
        ),
        FileReadExecutor(workspace),
    )
    registry.register(
        ToolSpec(
            name="list_directory",
            description="列出用户配置的 Agent 工作区范围内的目录内容；path 省略时列出当前工作区。",
            parameters_schema={
                "type": "object",
                "properties": {"path": {"type": "string", "maxLength": 4096}},
                "additionalProperties": False,
            },
            effect=ToolEffect.READ_ONLY,
            returns_user_facing_result=True,
        ),
        DirectoryListExecutor(workspace),
    )
    registry.register(
        ToolSpec(
            name="search_files",
            description=(
                "在用户配置的 Agent 工作区内搜索文件中的文字，返回文件路径、行号和匹配行；"
                "只读，不修改文件。"
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1, "maxLength": 300},
                    "path": {"type": "string", "maxLength": 4096},
                    "case_sensitive": {"type": "boolean"},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": _FILE_SEARCH_MAX_RESULTS},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            effect=ToolEffect.READ_ONLY,
        ),
        FileSearchExecutor(workspace),
    )
    registry.register(
        ToolSpec(
            name="apply_patch",
            description=(
                "在用户配置的 Agent 工作区内应用 Codex 风格的多文件文本 patch；"
                "该操作会修改文件，必须先经过用户确认。"
            ),
            parameters_schema={
                "type": "object",
                "properties": {"patch": {"type": "string", "minLength": 1, "maxLength": _PATCH_MAX_CHARS}},
                "required": ["patch"],
                "additionalProperties": False,
            },
            effect=ToolEffect.SIDE_EFFECT,
            idempotency_support=IdempotencySupport.NONE,
        ),
        ApplyPatchExecutor(workspace),
    )
    registry.register(
        ToolSpec(
            name="run_terminal_command",
            description=(
                "在用户配置的 Agent 工作区内运行本地 bash 终端命令；默认 cwd 是当前工作区，"
                "可用 cwd 选择另一个已授权工作区。该工具有副作用，必须先经过用户确认。"
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "minLength": 1, "maxLength": 10000},
                    "cwd": {"type": ["string", "null"], "maxLength": 4096},
                    "timeout_seconds": {"type": "number", "minimum": 1, "maximum": 120},
                },
                "required": ["command", "cwd", "timeout_seconds"],
                "additionalProperties": False,
            },
            effect=ToolEffect.SIDE_EFFECT,
            idempotency_support=IdempotencySupport.NONE,
        ),
        TerminalCommandExecutor(workspace),
    )

    return registry
