#!/usr/bin/env python3
"""MAPAS 本地 HTTP + SSE 桥接进程（P2 最小可用面）。

本文件是一个**薄适配层**：它把 ``PersonalAgentApp`` 已有的能力翻译成浏览器
可用的 HTTP/JSON + SSE 接口，不新增业务逻辑，也不直接写业务数据库。

并发模型
--------
``PersonalAgentApp`` 不是线程安全的，因此所有会读写其状态的调用都投递到
**单个 worker 线程**串行执行；HTTP 线程只负责解析请求、排队、取结果。
唯一的例外是事件读取：``AgentEventStore`` / ``AgentEventStream`` 内部自带锁，
并且每次调用都独立打开 SQLite 连接，本身就是为多客户端订阅设计的
（见 ``src/memory/runtime/events.py``），因此 ``GET /api/events`` 与 SSE 的补发、
增量轮询直接读事件存储，避免一个长 turn 把实时推送和前端游标也堵住。
事件是 append-only 的，读取不会观察到未提交的中间状态。

仅使用 Python 标准库。
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import re
import secrets
import sqlite3
import signal
import sys
import threading
import urllib.error
import urllib.request
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional

CODE_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if CODE_ROOT not in sys.path:
    sys.path.insert(0, CODE_ROOT)

from src.memory.agent.models import AgentResult, AgentResultStatus, ConfirmationDecision
from src.memory.app.console import PersonalAgentApp
from src.memory.app.serialization import _serialize_result

# 同目录的会话导出/导入适配。直接以脚本方式运行时脚本目录就是 sys.path[0]，
# 这里再显式插入一次，避免以模块方式导入时找不到。
_BRIDGE_DIR = os.path.dirname(os.path.abspath(__file__))
if _BRIDGE_DIR not in sys.path:
    sys.path.insert(0, _BRIDGE_DIR)
from session_transfer import export_session as _transfer_export_session  # noqa: E402
from session_transfer import import_session as _transfer_import_session  # noqa: E402
from src.memory.collaboration.repository import CoordinationConflict
from src.memory.llm.errors import ProviderError

PROTOCOL_VERSION = "mapas-web-bridge/1"

# 未配置 API Key 时的统一用户提示（前端/CLI 共用同一句话）
API_KEY_MISSING_MESSAGE = "您还未设置 DeepSeek API Key，请点击右上角「API Key」填入密钥。"


class ApiKeyMissing(RuntimeError):
    """未配置 API Key：桥接进程仍在，但 Agent 能力不可用（降级模式）。"""
ALLOWED_ORIGINS = frozenset({"http://localhost:5173", "http://localhost:4173"})
MAX_REQUEST_BYTES = 1024 * 1024
SSE_PING_SECONDS = 15.0
SSE_POLL_SECONDS = 5.0
SSE_BACKFILL_PAGE = 500
SSE_BACKFILL_MAX_PAGES = 20
JOB_HISTORY_LIMIT = 500


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def _safe_json(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _contains_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _int_query(query: Dict[str, List[str]], name: str, default: int) -> int:
    values = query.get(name)
    if not values:
        return default
    raw = values[-1].strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是整数") from exc


def _str_query(query: Dict[str, List[str]], name: str) -> Optional[str]:
    values = query.get(name)
    if not values:
        return None
    value = values[-1].strip()
    return value or None


def _body_str(body: Dict[str, Any], name: str) -> str:
    """读取必填字符串字段；缺失或空白按参数错误处理。"""
    value = body.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} 必须是非空字符串")
    return value.strip()


def _optional_body_str(body: Dict[str, Any], name: str) -> Optional[str]:
    """读取可选字符串字段：缺省与空串都视为未提供。"""
    value = body.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} 必须是字符串")
    text = value.strip()
    return text or None


class CollaborationRunNotFound(Exception):
    """协作运行不存在（前端据此显示 404，而不是 400 参数错误）。"""


class ThreadNotFound(Exception):
    """请求的会话在持久化存储中不存在。"""

    def __init__(self, thread_id: str) -> None:
        super().__init__(f"未找到会话：{thread_id}")
        self.thread_id = thread_id


class RequestTooLarge(Exception):
    """请求体超过桥接允许的上限。"""


class MethodNotAllowed(Exception):
    """路径存在但不支持当前 HTTP 方法。"""


class CollaborationUnavailable(ValueError):
    """当前执行模式不具备协作 / 修复 / 程序性经验能力（single 模式）。"""

    def __init__(self) -> None:
        super().__init__(
            "当前执行模式未启用多 Agent 协作，请用 --execution-mode multi_fixed 启动桥接"
        )


# 协作层 ``CoordinationConflict`` 的原始原因只放进 detail；HTTP 的 error
# 必须是可展示的中文，并区分「目标不存在」与「状态冲突」。
COORDINATION_ERROR_MESSAGES = {
    "unknown run": (404, "未找到协作运行"),
    "unknown repair plan": (404, "未找到修复计划"),
    "unknown experience": (404, "未找到程序性经验"),
}


# ---------------------------------------------------------------------------
# 单 worker 串行执行 PersonalAgentApp 调用
# ---------------------------------------------------------------------------


class SerialWorker:
    """单个 daemon 线程按提交顺序串行执行任务。"""

    def __init__(self, name: str = "mapas-bridge-worker") -> None:
        self._queue: "queue.Queue[Optional[Callable[[], Any]]]" = queue.Queue()
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._stopped = False
        self._thread.start()

    def submit(self, task: Callable[[], Any]) -> None:
        if self._stopped:
            raise RuntimeError("桥接 worker 已停止")
        self._queue.put(task)

    def call(self, task: Callable[[], Any], timeout: Optional[float] = None) -> Any:
        """提交任务并等待结果；异常在调用线程重新抛出。"""
        box: Dict[str, Any] = {}
        done = threading.Event()

        def wrapper() -> None:
            try:
                box["result"] = task()
            except BaseException as exc:  # noqa: BLE001 - 转发给调用线程
                box["error"] = exc
            finally:
                done.set()

        self.submit(wrapper)
        if not done.wait(timeout):
            raise RuntimeError("桥接正忙：等待 worker 超时")
        if "error" in box:
            raise box["error"]
        return box.get("result")

    def _run(self) -> None:
        while True:
            task = self._queue.get()
            if task is None:
                return
            try:
                task()
            except BaseException as exc:  # noqa: BLE001 - worker 不能因单个任务崩溃
                sys.stderr.write(
                    f"[bridge] worker 任务异常：{type(exc).__name__}: {exc}\n"
                )

    def stop(self, timeout: float = 10.0) -> None:
        self._stopped = True
        self._queue.put(None)
        self._thread.join(timeout)


# ---------------------------------------------------------------------------
# 后台作业
# ---------------------------------------------------------------------------


class _Job:
    """一个后台长任务的内存句柄（不落盘，进程退出即失）。"""

    def __init__(
        self, kind: str, thread_id: Optional[str] = None, turn_id: Optional[str] = None
    ) -> None:
        self.job_id = "job_" + secrets.token_hex(8)
        self.kind = kind
        self.thread_id = thread_id
        self.turn_id = turn_id
        self.accepted_at = time.time()
        self._lock = threading.Lock()
        self._state = "QUEUED"
        self._result: Optional[Dict[str, Any]] = None
        self._error: Optional[Dict[str, str]] = None

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def mark_running(self) -> None:
        with self._lock:
            if self._state == "QUEUED":
                self._state = "RUNNING"

    def succeed(self, result: Optional[Dict[str, Any]]) -> None:
        with self._lock:
            self._result = result
            self._state = "SUCCEEDED"

    def fail(
        self,
        code: str,
        message: str,
        result: Optional[Dict[str, Any]] = None,
    ) -> None:
        with self._lock:
            self._result = result
            self._error = {"code": code, "message": message}
            self._state = "FAILED"

    def handle(self) -> Dict[str, Any]:
        """POST 返回的 JobHandle（与 http.ts 的 JobHandle 对齐）。"""
        payload: Dict[str, Any] = {
            "jobId": self.job_id,
            "kind": self.kind,
            "acceptedAt": self.accepted_at,
        }
        if self.thread_id is not None:
            payload["threadId"] = self.thread_id
        if self.turn_id is not None:
            payload["turnId"] = self.turn_id
        return payload

    def snapshot(self) -> Dict[str, Any]:
        """GET /api/jobs/:jobId 的响应体（与 http.ts 的 JobResult 对齐）。"""
        with self._lock:
            payload: Dict[str, Any] = {"jobId": self.job_id, "state": self._state}
            if self._result is not None:
                payload["result"] = self._result
            if self._error is not None:
                payload["error"] = dict(self._error)
            return payload


# ---------------------------------------------------------------------------
# 桥接核心
# ---------------------------------------------------------------------------


class WebBridge:
    """把 PersonalAgentApp 的调用串行化，并维护后台作业登记表。"""

    def __init__(
        self,
        app: Optional[PersonalAgentApp],
        *,
        token: Optional[str],
        data_dir: Optional[str] = None,
        execution_mode: Optional[str] = None,
    ) -> None:
        self._app = app
        # 记下数据目录与模式：未配置 Key 时也要能对外说话（降级启动 + 供前端填 Key）
        self._data_dir: str = str(app.data_dir) if app is not None else str(data_dir or "")
        self._mode: str = (
            app.execution_mode if app is not None else (execution_mode or "single")
        )
        # 凭据配置文件（仓库之外）：API Key 的只写管理用
        self._config_path: str = (
            str(app.storage_layout.config_env_path)
            if app is not None
            else os.path.join(self._data_dir, "config", ".env.local")
        )
        # 探活缓存：避免每次打开页面都等一次外网请求（默认 30s 内复用）
        self._key_probe_cache: Optional[Dict[str, Any]] = None
        self._key_probe_ttl: float = 30.0
        self.token = token
        self.worker = SerialWorker()
        self._jobs: Dict[str, _Job] = {}
        self._jobs_lock = threading.Lock()

    @property
    def app(self) -> PersonalAgentApp:
        if self._app is None:
            raise ApiKeyMissing(API_KEY_MISSING_MESSAGE)
        return self._app

    @app.setter
    def app(self, value: PersonalAgentApp) -> None:
        self._app = value

    # ---- 输出降噪 ------------------------------------------------------
    #
    # 宿主在"未绑定任务"的普通轮次会附带一条技术提醒（"This turn has no Task 3 binding."）。
    # 它是宿主实现细节，不是用户需要处理的警告；桥接作为协议适配层统一降噪，
    # 前端因此不需要（也不应该）用关键词去猜哪些警告该隐藏。
    BENIGN_WARNINGS = frozenset({"This turn has no Task 3 binding."})

    @classmethod
    def _drop_benign_warnings(cls, warnings: Any) -> Any:
        if not isinstance(warnings, list):
            return warnings
        return [item for item in warnings if item not in cls.BENIGN_WARNINGS]

    def clean_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """对事件做协议层降噪（当前只处理 turn.completed 的 warnings）。"""
        if event.get("event_type") == "turn.completed":
            payload = event.get("payload")
            if isinstance(payload, dict) and "warnings" in payload:
                payload["warnings"] = self._drop_benign_warnings(payload["warnings"])
        return event

    def clean_serialized_result(self, serialized: Dict[str, Any]) -> Dict[str, Any]:
        """对作业结果做同样的降噪（result.warnings 与 developer_details.warnings）。"""
        if "warnings" in serialized:
            serialized["warnings"] = self._drop_benign_warnings(serialized["warnings"])
        details = serialized.get("developer_details")
        if isinstance(details, dict) and "warnings" in details:
            details["warnings"] = self._drop_benign_warnings(details["warnings"])
        return serialized

    # ---- 执行模式热切换 ------------------------------------------------

    def has_active_jobs(self) -> bool:
        """是否有排队/运行中的作业（供 HTTP 线程在入队前快速判断）。"""
        with self._jobs_lock:
            return any(
                item.state in {"QUEUED", "RUNNING"} for item in self._jobs.values()
            )

    def switch_mode(self, mode: str) -> Dict[str, Any]:
        """在同一个数据目录上重建 PersonalAgentApp 以切换执行模式。

        为什么是重建：`execution_mode` 是 PersonalAgentApp 的构造参数
        （见 code/src/memory/app/console.py），后端没有运行时切换接口。
        重建期间不丢数据——会话/记忆/任务都在同一个 data_dir 的 SQLite 里。

        该函数只会在 worker 线程里被调用，因此不会与正在执行的应用调用并发。
        """
        if mode not in {"single", "multi_fixed"}:
            raise ValueError("mode 必须是 single 或 multi_fixed")
        if self._app is None:
            raise ApiKeyMissing(API_KEY_MISSING_MESSAGE)
        if mode == self.app.execution_mode:
            return self.meta()
        if self.has_active_jobs():
            raise RuntimeError("当前还有排队或执行中的作业，等它结束再切换模式")

        old_app = self.app
        old_mode = old_app.execution_mode
        try:
            old_app.close()
        except Exception:  # noqa: BLE001 - 关闭失败不应该阻止切换
            pass

        try:
            new_app = PersonalAgentApp(data_dir=self._data_dir, execution_mode=mode)
        except Exception:
            # 回滚：尽量把旧模式重新拉起来，避免桥接整体不可用
            try:
                self.app = PersonalAgentApp(data_dir=self._data_dir, execution_mode=old_mode)
            except Exception:  # noqa: BLE001
                self.app = old_app
            raise

        self.app = new_app
        return self.meta()

    # ---- API Key 只写管理 ----------------------------------------------
    #
    # 安全约定（用户要求）：**只写不读**。
    # - 状态接口不返回 key 的任何片段（连后 4 位都不给）；
    # - 更新/删除只接受"新值/无值"，从不回显；
    # - 可用性用 DeepSeek 的 GET /models 探活：不发推理请求、不消耗 token。
    # - 文件写入是原子替换，保留 UTF-8 无 BOM，只改 DEEPSEEK_API_KEY 一行。

    ENV_KEY_NAME = "DEEPSEEK_API_KEY"
    ENV_PROBE_URL = "https://api.deepseek.com/models"

    @classmethod
    def _read_env_lines(cls, path: str) -> List[str]:
        if not os.path.exists(path):
            return []
        with open(path, "r", encoding="utf-8-sig") as handle:
            return handle.read().splitlines()

    @classmethod
    def _env_key_from_lines(cls, lines: List[str]) -> Optional[str]:
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            name, _, value = stripped.partition("=")
            if name.strip().lstrip("export ").strip() == cls.ENV_KEY_NAME:
                return value.strip().strip('"').strip("'")
        return None

    # 注释行里可能残留手工粘贴的旧 key（真实发生过），写入时统一抹掉，避免"删除后仍留副本"
    KEY_TOKEN = re.compile(r"sk-[A-Za-z0-9]{10,}")

    @classmethod
    def _scrub_comment_line(cls, line: str) -> str:
        if line.lstrip().startswith("#"):
            return cls.KEY_TOKEN.sub("sk-<removed>", line)
        return line

    @classmethod
    def _rewrite_env_lines(cls, lines: List[str], key: Optional[str]) -> List[str]:
        """替换/新增/删除 DEEPSEEK_API_KEY 一行，保留其它内容与顺序，并清理注释里的 key 残留。"""
        result: List[str] = []
        replaced = False
        for line in lines:
            stripped = line.strip()
            name = stripped.partition("=")[0].strip().lstrip("export ").strip()
            if stripped and not stripped.startswith("#") and name == cls.ENV_KEY_NAME:
                if key is not None and not replaced:
                    result.append(f"{cls.ENV_KEY_NAME}={key}")
                    replaced = True
                continue
            result.append(cls._scrub_comment_line(line))
        if key is not None and not replaced:
            if result and result[-1].strip() != "":
                result.append("")
            result.append(f"{cls.ENV_KEY_NAME}={key}")
        return result

    @staticmethod
    def _atomic_write(path: str, text: str) -> None:
        directory = os.path.dirname(path) or "."
        os.makedirs(directory, exist_ok=True)
        temp = path + ".tmp"
        with open(temp, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(temp, path)

    @staticmethod
    def _probe_key(key: str) -> tuple[Optional[bool], Optional[str]]:
        """GET /models 探活：不产生推理 token 消耗。"""
        request = urllib.request.Request(
            WebBridge.ENV_PROBE_URL,
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                return response.status == 200, None
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                return False, f"HTTP {exc.code}（凭据被拒绝）"
            return None, f"HTTP {exc.code}"
        except Exception as exc:  # noqa: BLE001 - 网络问题不等于 key 无效
            return None, f"无法连接校验服务（{type(exc).__name__}）"

    def api_key_status(
        self, *, probe: bool = True, refresh: bool = False
    ) -> Dict[str, Any]:
        lines = self._read_env_lines(self._config_path)
        key = self._env_key_from_lines(lines)
        configured = bool(key)
        valid: Optional[bool] = None
        probe_error: Optional[str] = None
        probe_cached = False
        probe_age = 0.0
        if configured and probe:
            now = time.time()
            cache = self._key_probe_cache
            fresh = (
                cache is not None
                and not refresh
                and (now - float(cache.get("at", 0.0))) < self._key_probe_ttl
            )
            if fresh:
                valid = cache.get("valid")
                probe_error = cache.get("error")
                probe_cached = True
                probe_age = now - float(cache.get("at", 0.0))
            else:
                valid, probe_error = self._probe_key(key)
                self._key_probe_cache = {
                    "valid": valid,
                    "error": probe_error,
                    "at": now,
                }
        updated_at: Optional[float] = None
        try:
            updated_at = os.path.getmtime(self._config_path)
        except OSError:
            updated_at = None
        return {
            "configured": configured,
            "valid": valid,
            "probeError": probe_error,
            "updatedAt": updated_at,
            "mode": self._mode,
            "appReady": self._app is not None,
            "configPath": self._config_path,
            "probeCached": probe_cached,
            "probeAgeSeconds": round(probe_age, 1),
        }

    def update_api_key(self, key: str) -> Dict[str, Any]:
        cleaned = (key or "").strip()
        if cleaned == "":
            raise ValueError("API Key 不能为空")
        if any(character.isspace() for character in cleaned):
            raise ValueError("API Key 不能包含空白字符")
        if not cleaned.startswith("sk-"):
            raise ValueError("DeepSeek API Key 应以 sk- 开头")
        if len(cleaned) < 20 or len(cleaned) > 200:
            raise ValueError("API Key 长度异常，请确认是否复制完整")
        lines = self._read_env_lines(self._config_path)
        self._atomic_write(self._config_path, "\n".join(self._rewrite_env_lines(lines, cleaned)) + "\n")

        # 让新 key 立刻生效：用同一数据目录重建 app（模式不变）
        reloaded = False
        reload_error: Optional[str] = None
        try:
            self.reload_app()
            reloaded = True
        except Exception as exc:  # noqa: BLE001 - 文件已写入，重载失败要如实上报
            reload_error = f"{type(exc).__name__}: {exc}"
        self._key_probe_cache = None
        status = self.api_key_status(refresh=True)
        status["appReloaded"] = reloaded
        status["reloadError"] = reload_error
        return status

    def delete_api_key(self) -> Dict[str, Any]:
        lines = self._read_env_lines(self._config_path)
        self._atomic_write(self._config_path, "\n".join(self._rewrite_env_lines(lines, None)) + "\n")
        # 立即关闭 Agent：删除要立刻生效，不能靠"进程内存里还留着旧 key"
        if self._app is not None:
            try:
                self._app.close()
            except Exception:  # noqa: BLE001
                pass
            self._app = None
        self._key_probe_cache = {"valid": None, "error": "未配置", "at": time.time()}
        status = self.api_key_status(probe=False)
        status.update(
            {
                "deleted": True,
                "appReloaded": False,
                "reloadError": None,
                "note": (
                    "已删除并立即停用 Agent。现在对话会被拒绝，"
                    "填入新 Key 后自动恢复。"
                ),
            }
        )
        return status

    def reload_app(self) -> Dict[str, Any]:
        """用同一数据目录、同一模式重建 app（让新的 API Key 生效；也可从降级态恢复）。"""
        old_app = self._app
        mode = self._mode
        if old_app is not None:
            try:
                old_app.close()
            except Exception:  # noqa: BLE001
                pass
        try:
            new_app = PersonalAgentApp(data_dir=self._data_dir, execution_mode=mode)
        except Exception:
            self._app = old_app
            raise
        self._app = new_app
        return self.meta()


    # ---- 作业 ---------------------------------------------------------

    def _register_job(self, job: _Job) -> None:
        with self._jobs_lock:
            self._jobs[job.job_id] = job
            if len(self._jobs) > JOB_HISTORY_LIMIT:
                finished = [
                    item
                    for item in self._jobs.values()
                    if item.state in {"SUCCEEDED", "FAILED"}
                ]
                for stale in finished[: len(self._jobs) - JOB_HISTORY_LIMIT]:
                    self._jobs.pop(stale.job_id, None)

    def get_job(self, job_id: str) -> Optional[_Job]:
        with self._jobs_lock:
            return self._jobs.get(job_id)

    def _start_job(
        self,
        kind: str,
        operation: Callable[[], Any],
        *,
        thread_id: Optional[str] = None,
        turn_id: Optional[str] = None,
    ) -> _Job:
        job = _Job(kind, thread_id=thread_id, turn_id=turn_id)

        def task() -> None:
            job.mark_running()
            try:
                result = operation()
            except BaseException as exc:  # noqa: BLE001 - 作业失败必须如实上报
                job.fail(
                    code=type(exc).__name__,
                    message=str(exc).strip() or "后台任务执行失败",
                )
                return
            try:
                payload = None
                if isinstance(result, AgentResult):
                    payload = self.clean_serialized_result(
                        self.app._json_safe_event_value(_serialize_result(result))
                    )
                elif result is not None:
                    # 非 turn 类后台作业（例如上下文压缩）直接把结果当作结果体。
                    payload = self.app._json_safe_event_value(result)
                if (
                    isinstance(result, AgentResult)
                    and result.status is AgentResultStatus.FAILED
                ):
                    info = result.error_info
                    job.fail(
                        code=(info.code if info is not None else "TURN_FAILED"),
                        message=(
                            (info.safe_message if info is not None else "")
                            or "本轮执行失败（宿主未返回具体原因）"
                        ),
                        result=payload,
                    )
                    return
                job.succeed(payload)
            except BaseException as exc:  # noqa: BLE001 - 序列化失败也是作业失败
                job.fail(
                    code="SERIALIZATION_FAILED",
                    message=f"结果序列化失败：{type(exc).__name__}: {exc}",
                )

        self._register_job(job)
        self.worker.submit(task)
        return job

    def start_turn_job(self, thread_id: str, user_input: str) -> _Job:
        app = self.app

        def operation() -> AgentResult:
            if app.active_session_id != thread_id:
                if not app.resume_session(thread_id):
                    raise ThreadNotFound(thread_id)
            return app._run_with_wait_notice(
                lambda: app.process_chat_turn(user_input),
                show_progress=False,
            )

        return self._start_job("turn", operation, thread_id=thread_id)

    def start_turn_resume_job(self, thread_id: str, turn_id: str) -> _Job:
        app = self.app

        def operation() -> AgentResult:
            if app.active_session_id != thread_id:
                if not app.resume_session(thread_id):
                    raise ThreadNotFound(thread_id)
            return app._run_with_wait_notice(
                lambda: app.resume_interrupted_turn(turn_id),
                show_progress=False,
            )

        return self._start_job(
            "turnResume", operation, thread_id=thread_id, turn_id=turn_id
        )

    def start_confirmation_job(
        self,
        pending_action_id: str,
        decision: ConfirmationDecision,
        thread_id: Optional[str],
    ) -> _Job:
        app = self.app

        def operation() -> AgentResult:
            if thread_id is not None and app.active_session_id != thread_id:
                if not app.resume_session(thread_id):
                    raise ThreadNotFound(thread_id)
            if app.pending_confirmation_id != pending_action_id:
                raise ValueError(
                    f"待确认操作 {pending_action_id} 不存在或已经处理"
                )
            return app._run_with_wait_notice(
                lambda: app.process_confirmation(decision),
                show_progress=False,
            )

        return self._start_job(
            "confirmation", operation, thread_id=thread_id
        )

    def start_compact_job(self) -> _Job:
        """显式压缩当前会话；作业体与 http.ts 的 compact() 对齐。"""

        return self._start_job("compact", self.app.compact_current_session)

    # ---- 以下方法只会在 worker 线程里被调用 ----------------------------

    def _thread_payload(self, thread_id: str) -> Dict[str, Any]:
        session = self.app.conversation_repo.get_session(thread_id)
        if session is None:
            raise ThreadNotFound(thread_id)
        return {
            "threadId": session.session_id,
            "title": session.title,
            "taskId": session.active_task_id,
            "status": session.status,
            "createdAt": session.created_at,
            "updatedAt": session.updated_at,
        }

    def list_threads(self) -> List[Dict[str, Any]]:
        threads: List[Dict[str, Any]] = []
        for entry in self.app.list_sessions():
            session_id = str(entry.get("session_id") or "")
            if not session_id:
                continue
            session = self.app.conversation_repo.get_session(session_id)
            if session is None:
                continue
            threads.append(
                {
                    "threadId": session.session_id,
                    "title": session.title,
                    "taskId": session.active_task_id,
                    "status": session.status,
                    "createdAt": session.created_at,
                    "updatedAt": session.updated_at,
                }
            )
        return threads

    def create_thread(self, title: Optional[str]) -> Dict[str, Any]:
        # 后端自 53484dc 起支持 create_new_session(title, persist=True)：
        # 新建即落库，会话从诞生那一刻就是一等公民（可重命名/删除/随时切回），
        # 不再需要桥接调用私有的 _ensure_persisted_session。
        self.app.create_new_session(title, persist=True)
        return self._thread_payload(self.app.active_session_id)

    def resume_thread(self, thread_id: str) -> Dict[str, Any]:
        if not self.app.resume_session(thread_id):
            raise ThreadNotFound(thread_id)
        return self._thread_payload(thread_id)

    def _require_persisted_thread(self, thread_id: str) -> None:
        """重命名/删除只对已落库的会话生效。"""
        if self.app.conversation_repo.get_session(thread_id) is None:
            raise ThreadNotFound(thread_id)

    def rename_thread(self, thread_id: str, title: str) -> Dict[str, Any]:
        self._require_persisted_thread(thread_id)
        try:
            self.app.rename_session(thread_id, title)
        except ValueError as exc:
            text = str(exc)
            if "thread not found" in text:
                raise ThreadNotFound(thread_id) from None
            if "characters" in text:
                raise ValueError("标题长度需在 1–256 个字符之间") from None
            raise
        return self._thread_payload(thread_id)

    def export_thread(self, thread_id: str) -> Dict[str, Any]:
        self._require_persisted_thread(thread_id)
        return self.app._json_safe_event_value(
            _transfer_export_session(self.app.conversation_db_path, thread_id)
        )

    def import_thread(self, payload: object) -> Dict[str, Any]:
        result = _transfer_import_session(self.app.conversation_db_path, payload)
        return self.app._json_safe_event_value(result)

    def delete_thread(self, thread_id: str) -> Dict[str, Any]:
        self._require_persisted_thread(thread_id)
        was_active = thread_id == self.app.active_session_id
        try:
            self.app.delete_session(thread_id)
        except ValueError as exc:
            text = str(exc)
            if "thread not found" in text:
                raise ThreadNotFound(thread_id) from None
            if "resolve pending approvals" in text:
                raise ValueError("该会话还有待处理审批或未完成的轮次，请先处理后再删除") from None
            if "thread changed before deletion" in text:
                raise ValueError("会话刚刚有更新，请刷新后重试删除") from None
            raise
        return {"ok": True, "deletedId": thread_id, "wasActive": was_active}

    def _require_thread(self, thread_id: str) -> None:
        if thread_id == self.app.active_session_id:
            return
        if self.app.conversation_repo.get_session(thread_id) is None:
            raise ThreadNotFound(thread_id)

    def read_messages(self, thread_id: str) -> List[Dict[str, Any]]:
        self._require_thread(thread_id)
        return [
            {
                "messageId": message.message_id,
                "turnId": message.turn_id,
                "role": message.role.value,
                "content": message.content,
                "createdAt": message.created_at,
            }
            for message in self.app.conversation_repo.list_messages(thread_id)
        ]

    def read_unfinished(self, thread_id: str) -> List[Dict[str, Any]]:
        self._require_thread(thread_id)
        return [
            self.app._json_safe_event_value(item)
            for item in self.app.list_unfinished_turns(thread_id)
        ]

    def read_events(
        self,
        *,
        after_sequence: int,
        session_id: Optional[str],
        turn_id: Optional[str],
        limit: int,
    ) -> List[Dict[str, Any]]:
        if after_sequence < 0:
            raise ValueError("afterSequence 不能为负数")
        if limit <= 0 or limit > 1000:
            raise ValueError("limit 必须位于 1 到 1000 之间")
        # 事件读取直接走 AgentEventStore：它内部自带锁、每次调用独立连接，
        # 且事件 append-only。前端需要在长 turn 运行期间持续按游标增量读事件，
        # 若排队等 worker 会一直阻塞到 turn 结束（并导致重复追加）。SSE 用的是
        # 同一条数据通道，见模块头部的并发说明。
        events = self.app.event_store.list_events(
            after_sequence=after_sequence,
            session_id=session_id,
            turn_id=turn_id,
            limit=limit,
        )
        return [
            self.clean_event(self.app._json_safe_event_value(event.to_dict()))
            for event in events
        ]

    def status_snapshot(self) -> Dict[str, Any]:
        app = self.app
        payload = {
            "taskLifecycle": app.get_task_lifecycle_status(),
            "status": app.get_task_status_summary(),
            "memory": app.get_memory_update_status(),
            "unfinishedTurns": app.list_unfinished_turns(),
            "pendingApprovals": app.list_pending_approvals(),
        }
        return app._json_safe_event_value(payload)

    def list_approvals(self, thread_id: Optional[str] = None) -> List[Dict[str, Any]]:
        # 53484dc 起审批按会话隔离；未指定会话时取当前活动会话。
        try:
            approvals = self.app.list_pending_approvals(thread_id)
        except ValueError as exc:
            if "thread not found" in str(exc):
                raise ThreadNotFound(thread_id or "") from None
            raise
        return self.app._json_safe_event_value(approvals)

    def tool_catalog(self) -> List[Dict[str, Any]]:
        return self.app._json_safe_event_value(self.app.get_tool_catalog())

    def context_status(self) -> Dict[str, Any]:
        return self.app._json_safe_event_value(
            self.app.get_conversation_context_status()
        )

    def mcp_status(self) -> Dict[str, Any]:
        return self.app._json_safe_event_value(self.app.get_mcp_status())

    def workspace_status(self) -> Dict[str, Any]:
        return self.app._json_safe_event_value(self.app.get_workspace_status())

    def storage_status(self) -> Dict[str, str]:
        return self.app.get_storage_status()

    def collaboration_runs(
        self, limit: int = 20, all_sessions: bool = False
    ) -> List[Dict[str, Any]]:
        """协作运行列表（默认当前会话；all_sessions 时跨会话按时间倒序）。"""
        self._require_collaboration()
        limit = max(1, min(int(limit), 100))
        if not all_sessions:
            return self.app._json_safe_event_value(
                self.app.coordination_repository.list_session_graphs(
                    self.app.active_session_id, limit=limit
                )
            )

        path = str(getattr(self.app.coordination_repository, "db_path", ""))
        if not path or not os.path.exists(path):
            raise ValueError("找不到协作数据库 coordination.db")
        connection = sqlite3.connect(
            "file:%s?mode=ro" % path.replace("\\", "/"), uri=True
        )
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                "SELECT g.graph_id, g.run_id, g.task_id, r.status AS run_status, "
                "r.session_id, r.turn_id, r.started "
                "FROM task_graphs g JOIN runs r ON r.run_id = g.run_id "
                "ORDER BY r.started DESC LIMIT ?",
                (limit,),
            ).fetchall()
            graphs: List[Dict[str, Any]] = []
            for row in rows:
                graph = dict(row)
                graph["nodes"] = [
                    dict(node)
                    for node in connection.execute(
                        "SELECT n.node_key, n.status, a.artifact_id "
                        "FROM task_nodes n LEFT JOIN task_attempts a "
                        "ON a.node_id = n.node_id AND a.run_id = ? "
                        "WHERE n.graph_id = ? ORDER BY n.rowid",
                        (graph["run_id"], graph["graph_id"]),
                    )
                ]
                graphs.append(graph)
        finally:
            connection.close()
        return self.app._json_safe_event_value(graphs)

    def _graph_plan(self, graph_id: str) -> Dict[str, Any]:
        """plan_json 还没进入官方投影（get_run_details 只给图节点），这里保留一小段只读 SQL。

        其余协作数据全部走 `CoordinationRepository` 的公开方法，不再直连数据库。
        """
        repository = self.app.coordination_repository
        path = str(getattr(repository, "db_path", ""))
        if not graph_id or not path or not os.path.exists(path):
            return {"subtasks": []}
        connection = sqlite3.connect(
            "file:%s?mode=ro" % path.replace("\\", "/"), uri=True
        )
        connection.row_factory = sqlite3.Row
        try:
            row = connection.execute(
                "SELECT plan_json FROM task_graphs WHERE graph_id=?", (graph_id,)
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return {"subtasks": []}
        try:
            parsed = json.loads(row["plan_json"] or "{}")
        except json.JSONDecodeError:
            return {"subtasks": []}
        return parsed if isinstance(parsed, dict) else {"subtasks": []}

    def collaboration_run_detail(self, run_id: str) -> Dict[str, Any]:
        """协作运行只读快照：运行 + 阶段 + 图节点 + 预算用量 + 结果。

        数据来源（commit 53484dc 起）：
        - `CoordinationRepository.get_run()` / `get_run_details()` —— 官方公开投影，
          含 stages（角色/状态/交接接收方/产物核验）、graph.nodes、usage（含 usage_source）；
        - `runs.result_json` —— 失败原因/警告/最终回答/核验子任务；
        - `task_graphs.plan_json` —— 目前仍是只读 SQL（未进公开投影）。
        """
        self._require_collaboration()
        repository = self.app.coordination_repository
        try:
            run = repository.get_run(run_id)
        except CoordinationConflict:
            raise CollaborationRunNotFound(run_id) from None
        session_id = str(run.get("session_id") or "")
        try:
            details = repository.get_run_details(session_id, run_id)
        except CoordinationConflict:
            raise CollaborationRunNotFound(run_id) from None

        graph = details.get("graph") or {}
        graph_id = str(graph.get("graph_id") or "")
        nodes = [
            {
                "node_key": node.get("node_key"),
                "status": node.get("status"),
                "node_id": node.get("attempt_id") or "",
                "artifact_id": node.get("artifact_id"),
            }
            for node in (graph.get("nodes") or [])
        ]
        stages = details.get("stages") or []
        roles = [
            {
                "stage_key": stage.get("stage_key"),
                "role": stage.get("role"),
                "status": stage.get("status"),
            }
            for stage in stages
        ]
        artifacts = [
            {
                "artifact_id": stage.get("artifact_id"),
                "stage_key": stage.get("stage_key"),
                "verification_status": stage.get("verification_status") or "",
            }
            for stage in stages
            if stage.get("artifact_id")
        ]
        handoffs = [
            {
                "stage_key": stage.get("stage_key"),
                "sender": stage.get("role"),
                "receiver": stage.get("receiver") or "",
            }
            for stage in stages
            if stage.get("message_id")
        ]

        usage = details.get("usage") or {}
        calls: List[Dict[str, Any]] = []
        by_role: Dict[str, Dict[str, Any]] = {}
        failed_with_estimate = 0
        for call in usage.get("calls") or []:
            role = str(call.get("role") or "unknown")
            call_usage = call.get("usage") or {}
            charged = int(call.get("charged_units") or 0)
            view = {
                "call_id": call.get("call_id"),
                "role": role,
                "operation": call.get("operation"),
                "status": call.get("status"),
                "charged_units": charged,
                "usage": call_usage,
                "usage_source": call.get("usage_source"),
                "elapsed": call.get("elapsed"),
            }
            calls.append(view)
            bucket = by_role.setdefault(
                role,
                {
                    "role": role,
                    "calls": 0,
                    "failed": 0,
                    "chargedUnits": 0,
                    "inputTokens": 0,
                    "outputTokens": 0,
                    "estimatedCalls": 0,
                },
            )
            bucket["calls"] += 1
            bucket["chargedUnits"] += charged
            if view["status"] == "FAILED":
                bucket["failed"] += 1
            if view["usage_source"] == "reservation":
                bucket["estimatedCalls"] += 1
                if view["status"] == "FAILED":
                    failed_with_estimate += 1
            bucket["inputTokens"] += int(
                call_usage.get("input_tokens") or call_usage.get("prompt_tokens") or 0
            )
            bucket["outputTokens"] += int(
                call_usage.get("output_tokens") or call_usage.get("completion_tokens") or 0
            )

        try:
            result = json.loads(run.get("result_json") or "{}")
        except json.JSONDecodeError:
            result = {}
        trace_metadata = result.get("trace_metadata") or {}

        return self.app._json_safe_event_value(
            {
                "runId": run.get("run_id"),
                "sessionId": session_id,
                "turnId": run.get("turn_id"),
                "taskId": run.get("task_id"),
                "status": run.get("status"),
                "started": run.get("started"),
                "limits": details.get("limits") or {},
                "graphId": graph_id,
                "plan": self._graph_plan(graph_id),
                "nodes": nodes,
                "roles": roles,
                "artifacts": artifacts,
                "handoffs": handoffs,
                "modelCalls": {
                    "total": len(calls),
                    "failed": sum(1 for call in calls if call.get("status") == "FAILED"),
                    "chargedUnits": sum(call["charged_units"] for call in calls),
                    "failedWithEmptyUsage": failed_with_estimate,
                    "byRole": sorted(by_role.values(), key=lambda item: item["role"]),
                    "list": calls,
                },
                "usage": {
                    "modelCalls": usage.get("model_calls"),
                    "chargedTokenUnits": usage.get("charged_token_units"),
                    "providerReportedTokenUnits": usage.get("provider_reported_token_units"),
                    "estimatedTokenUnits": usage.get("estimated_token_units"),
                },
                "result": {
                    "status": result.get("status"),
                    "errorInfo": result.get("error_info"),
                    "warnings": result.get("warnings") or [],
                    "finalResponse": result.get("final_response") or "",
                    "executedToolCalls": result.get("executed_tool_call_ids") or [],
                    "verifiedSubtasks": trace_metadata.get("verified_subtasks") or [],
                    "sourceReads": trace_metadata.get("source_reads") or [],
                    "collaborationUsage": trace_metadata.get("collaboration_usage"),
                    "executionMode": trace_metadata.get("execution_mode"),
                },
            }
        )

    def retrieval_traces(self, limit: int = 50) -> List[Dict[str, Any]]:
        """读取真实落盘的检索 trace（只读文件，不改任何运行状态）。

        `execution/retrieval-traces.jsonl` 是后端每次检索后追加的**聚合**记录：
        只有 candidate_counts / stages / query_variants / applicability 计数与 selected_memory_ids，
        **没有**逐条排名、分数、适用性四态、冲突与充分性。
        前端据此只能做「检索过程概览」，界面必须如实标注缺失字段。
        """
        path = getattr(self.app, "retrieval_trace_path", None)
        if not path or not os.path.exists(path):
            return []
        rows: List[Dict[str, Any]] = []
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    text = line.strip()
                    if not text:
                        continue
                    try:
                        parsed = json.loads(text)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(parsed, dict):
                        rows.append(parsed)
        except OSError:
            return []
        rows.reverse()
        return self.app._json_safe_event_value(rows[: max(1, min(limit, 500))])

    def retrieval_trace(self, turn_id: str) -> Optional[Dict[str, Any]]:
        for row in self.retrieval_traces(500):
            if row.get("turn_id") == turn_id or row.get("request_id") == turn_id:
                return row
        return None

    # ---- 长期记忆（只调用 PersonalAgentApp 的公开方法） -----------------

    def list_memories(self) -> List[Dict[str, Any]]:
        return self.app._json_safe_event_value(self.app.list_memories())

    def list_recycled_memories(self) -> List[Dict[str, Any]]:
        return self.app._json_safe_event_value(
            self.app.list_recycled_memories()
        )

    def memory_profile(self) -> Dict[str, Any]:
        return self.app._json_safe_event_value(
            self.app.get_profile_repository()
        )

    def memory_detail(self, memory_id: str) -> Optional[Dict[str, Any]]:
        """返回记忆详情；不存在时保留 None，由路由层返回 200 + null。"""
        detail = self.app.get_memory_detail(memory_id)
        if detail is None:
            return None
        return self.app._json_safe_event_value(detail)

    def memory_history(self, memory_id: str) -> List[Dict[str, Any]]:
        return self.app._json_safe_event_value(
            self.app.get_memory_history(memory_id)
        )

    def memory_provenance(self, memory_id: str) -> List[Dict[str, Any]]:
        return self.app._json_safe_event_value(
            self.app.get_memory_provenance(memory_id)
        )

    def remember(self, content: str) -> Dict[str, Any]:
        """显式写入长期记忆；失败时异常原样上抛给 HTTP 层。"""
        return self.app._json_safe_event_value(
            self.app.remember_explicit(content)
        )

    def forget_memory(self, memory_id: str) -> bool:
        return bool(self.app.forget_memory(memory_id))

    def restore_memory(self, memory_id: str) -> bool:
        return bool(self.app.restore_memory(memory_id))

    # ---- 任务 ---------------------------------------------------------

    def list_tasks(self) -> List[Dict[str, Any]]:
        return self.app._json_safe_event_value(self.app.list_tasks())

    def create_task(self, title: Optional[str]) -> str:
        return self.app.create_new_task(title)

    def switch_task(self, task_id: str) -> bool:
        return bool(self.app.switch_task(task_id))

    def task_status_summary(self) -> Dict[str, Any]:
        return self.app._json_safe_event_value(
            self.app.get_task_status_summary()
        )

    def pause_current_task(self, note: Optional[str]) -> None:
        self.app.pause_current_task(note)

    def resume_current_task(self) -> None:
        self.app.resume_current_task()

    def complete_current_task(self) -> None:
        self.app.complete_current_task()

    def cancel_current_task(self) -> None:
        self.app.cancel_current_task()

    def task_lifecycle(self, task_id: Optional[str]) -> Dict[str, Any]:
        return self.app._json_safe_event_value(
            self.app.get_task_lifecycle_status(task_id)
        )

    def drain_task_lifecycle(self) -> Dict[str, Any]:
        return self.app._json_safe_event_value(self.app.drain_task_lifecycle())

    # ---- 协作修复与程序性经验（只调用 PersonalAgentApp 的公开方法） ----

    def _require_collaboration(self) -> None:
        if self.app.collaboration is None:
            raise CollaborationUnavailable()

    def list_repairs(self) -> List[Dict[str, Any]]:
        self._require_collaboration()
        rows = self.app.coordination_repository.list_repairs(
            self.app.active_session_id
        )
        return self.app._json_safe_event_value(rows)

    def list_session_graphs(self) -> List[Dict[str, Any]]:
        self._require_collaboration()
        rows = self.app.coordination_repository.list_session_graphs(
            self.app.active_session_id
        )
        return self.app._json_safe_event_value(rows)

    def repair_plan(self, repair_id: str) -> Dict[str, Any]:
        """返回顶层的修复计划对象；不存在时由路由层转成 404。"""
        self._require_collaboration()
        plan = self.app.coordination_repository.get_repair_plan(repair_id)
        return self.app._json_safe_event_value(plan)

    def list_experiences(self) -> List[Dict[str, Any]]:
        self._require_collaboration()
        return self.app._json_safe_event_value(self.app.list_experiences())

    def learn_experience(
        self, run_id: str, revision_of: Optional[str]
    ) -> Optional[Dict[str, Any]]:
        self._require_collaboration()
        experience = self.app.learn_experience(run_id, revision_of=revision_of)
        if experience is None:
            return None
        return self.app._json_safe_event_value(experience)

    def review_experience(
        self, experience_id: str, confirmation: str
    ) -> Dict[str, Any]:
        self._require_collaboration()
        return self.app._json_safe_event_value(
            self.app.review_experience(experience_id, confirmation)
        )

    def revoke_experience(
        self, experience_id: str, event_id: str
    ) -> Dict[str, Any]:
        self._require_collaboration()
        return self.app._json_safe_event_value(
            self.app.revoke_experience(experience_id, event_id)
        )

    def feedback_experience(
        self, experience_id: str, run_id: str, event_id: str
    ) -> Dict[str, Any]:
        self._require_collaboration()
        return self.app._json_safe_event_value(
            self.app.feedback_experience(experience_id, run_id, event_id)
        )

    def start_repair_job(self, run_id: str, event_id: str) -> _Job:
        # single 模式必须在受理前拒绝，否则会先返回 202 再在后台失败。
        self._require_collaboration()
        app = self.app
        return self._start_job(
            "repair",
            lambda: app.repair_collaboration(run_id, event_id),
        )

    def start_revoke_repair_job(
        self, run_id: str, artifact_id: str, event_id: str
    ) -> _Job:
        self._require_collaboration()
        app = self.app
        return self._start_job(
            "repair",
            lambda: app.revoke_and_repair_collaboration(
                run_id, artifact_id, event_id
            ),
        )

    def start_drain_repairs_job(self) -> _Job:
        self._require_collaboration()
        app = self.app
        return self._start_job(
            "drain",
            lambda: app.process_pending_collaboration_repairs(),
        )

    def meta(self) -> Dict[str, Any]:
        if self._app is None:
            return {
                "protocolVersion": PROTOCOL_VERSION,
                "executionMode": self._mode,
                "agentHome": self._data_dir,
                # 前端据此显示"未配置密钥"面板（而不是一屏红色报错）
                "needsApiKey": True,
                "capabilities": {
                    "collaborationRepair": False,
                    "proceduralExperiences": False,
                    "taskLifecycle": False,
                    "eventReplay": False,
                    "contextDiagnostics": False,
                    "approvals": False,
                    "toolCatalog": False,
                    "interrupt": False,
                },
            }
        app = self.app
        collaboration = app.collaboration
        lifecycle = getattr(collaboration, "lifecycle", None)
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "executionMode": app.execution_mode,
            "agentHome": app.data_dir,
            "needsApiKey": False,
            "capabilities": {
                "collaborationRepair": collaboration is not None,
                "proceduralExperiences": collaboration is not None,
                "taskLifecycle": collaboration is not None and lifecycle is not None,
                "eventReplay": True,
                "contextDiagnostics": True,
                "approvals": True,
                "toolCatalog": True,
                "interrupt": False,
            },
        }


# ---------------------------------------------------------------------------
# HTTP 处理器
# ---------------------------------------------------------------------------


class BridgeRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "MAPASWebBridge/1"
    sys_version = ""

    # ---- 基础响应 -----------------------------------------------------

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        if getattr(self.server, "quiet", False):
            return
        sys.stderr.write(
            "[bridge] %s - %s\n" % (self.address_string(), format % args)
        )

    def log_error(self, format: str, *args: Any) -> None:  # noqa: A002
        sys.stderr.write(
            "[bridge] %s - %s\n" % (self.address_string(), format % args)
        )

    def _write_cors_headers(self) -> None:
        origin = self.headers.get("Origin")
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def _send_json(
        self,
        status: int,
        payload: Dict[str, Any],
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> None:
        data = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        self.send_response(status)
        self._write_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def _try_send_json(self, status: int, payload: Dict[str, Any]) -> None:
        try:
            self._send_json(status, payload)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass

    def _send_options(self) -> None:
        self.send_response(204)
        self._write_cors_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers", "Content-Type, X-MAPAS-Token, Accept"
        )
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _method_not_allowed(self, allowed: str) -> None:
        self._try_send_json(
            405,
            {"error": "该路径不支持此 HTTP 方法", "detail": f"允许的方法：{allowed}"},
        )

    # ---- 请求入口 -----------------------------------------------------

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send_options()

    def do_GET(self) -> None:  # noqa: N802
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._handle("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle("DELETE")

    def do_PATCH(self) -> None:  # noqa: N802
        self._handle("PATCH")

    def _authorized(self, path: str = "") -> bool:
        # 只有这个探针接口免鉴权：它不返回任何数据，只回答"本桥接是否要求口令"，
        # 否则前端无法在"未填口令"时知道自己该不该显示输入框（只能看到一个 401）。
        if path.rstrip("/") == "/api/auth":
            return True
        expected = getattr(self.server, "bridge").token
        if expected is None:
            return True
        provided = self.headers.get("X-MAPAS-Token")
        if not provided:
            values = self._query.get("token") or []
            if values:
                provided = values[-1]
        if not provided:
            return False
        return secrets.compare_digest(str(provided), expected)

    def _read_json_body(self) -> Dict[str, Any]:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None or not raw_length.strip():
            return {}
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ValueError("Content-Length 无效") from exc
        if length < 0:
            raise ValueError("Content-Length 无效")
        if length > MAX_REQUEST_BYTES:
            raise RequestTooLarge()
        data = self.rfile.read(length) if length else b""
        if not data.strip():
            return {}
        try:
            body = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("请求体不是合法的 UTF-8 JSON") from exc
        if not isinstance(body, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return body

    def _handle(self, method: str) -> None:
        try:
            parsed = urllib.parse.urlsplit(self.path)
            self._query = urllib.parse.parse_qs(
                parsed.query, keep_blank_values=True
            )
            path = urllib.parse.unquote(parsed.path or "/")
        except Exception:  # noqa: BLE001
            self._try_send_json(400, {"error": "请求地址无法解析"})
            return

        if method == "OPTIONS":
            self._send_options()
            return

        if not self._authorized(path):
            self._try_send_json(
                401,
                {
                    "error": "缺少或无效的桥接令牌（X-MAPAS-Token）",
                    "detail": "请使用启动时打印的 token，或启动时加 --no-token",
                },
            )
            return

        try:
            body = self._read_json_body() if method == "POST" else None
            self._route(method, path, body)
        except RequestTooLarge:
            self._try_send_json(413, {"error": "请求体过大"})
        except ThreadNotFound as exc:
            self._try_send_json(404, {"error": f"未找到会话：{exc.thread_id}"})
        except CollaborationRunNotFound as exc:
            self._try_send_json(404, {"error": f"未找到协作运行：{exc}"})
        except MethodNotAllowed as exc:
            self._method_not_allowed(str(exc))
        except CoordinationConflict as exc:
            # CoordinationConflict 是 ValueError 子类，必须先于 ValueError 匹配。
            reason = str(exc).strip()
            status_code, message = COORDINATION_ERROR_MESSAGES.get(
                reason, (400, "协作状态冲突")
            )
            payload: Dict[str, Any] = {"error": message}
            if reason:
                payload["detail"] = reason
            self._try_send_json(status_code, payload)
        except ValueError as exc:
            text = str(exc).strip() or "请求处理失败"
            if _contains_cjk(text):
                self._try_send_json(400, {"error": text})
            else:
                self._try_send_json(
                    400, {"error": "请求参数无效", "detail": text}
                )
        except ApiKeyMissing:
            self._try_send_json(
                503,
                {
                    "error": API_KEY_MISSING_MESSAGE,
                    "code": "API_KEY_MISSING",
                    "detail": "请在右上角「API Key」中填入密钥；填入后会立即生效。",
                },
            )
        except ProviderError as exc:
            # Provider 失败必须带上真实原因（例如 HTTP 401），不能伪装成功。
            reason = str(exc).strip() or "Provider 未返回具体原因"
            status_code = getattr(exc, "status_code", None)
            prefix = (
                f"Provider 调用失败（HTTP {status_code}）"
                if status_code
                else "Provider 调用失败"
            )
            self._try_send_json(
                502, {"error": f"{prefix}：{reason}", "detail": reason}
            )
        except RuntimeError as exc:
            text = str(exc).strip() or "桥接运行时错误"
            if _contains_cjk(text):
                self._try_send_json(500, {"error": text})
            else:
                self._try_send_json(
                    500, {"error": "桥接运行时错误", "detail": text}
                )
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return
        except Exception as exc:  # noqa: BLE001 - 不吞异常，如实返回 500
            traceback.print_exc()
            self._try_send_json(
                500,
                {
                    "error": "桥接内部错误",
                    "detail": f"{type(exc).__name__}: {exc}",
                },
            )

    # ---- 路由 ---------------------------------------------------------

    def _route(
        self,
        method: str,
        path: str,
        body: Optional[Dict[str, Any]],
    ) -> None:
        parts = [part for part in path.strip("/").split("/") if part]
        if len(parts) < 2 or parts[0] != "api":
            self._try_send_json(404, {"error": f"未知的接口路径：{path}"})
            return
        rest = parts[1:]
        bridge: WebBridge = self.server.bridge

        if rest == ["meta"]:
            if method != "GET":
                self._method_not_allowed("GET")
                return
            self._send_json(200, bridge.meta())
            return

        # 鉴权探针：前端据此区分"本机免校验"与"需要填写口令"
        if rest == ["auth"]:
            if method != "GET":
                self._method_not_allowed("GET")
                return
            self._send_json(
                200,
                {
                    "tokenRequired": bridge.token is not None,
                    "protocolVersion": PROTOCOL_VERSION,
                },
            )
            return

        # API Key：只写不读（状态接口不含 key 内容）
        if rest == ["api-key"]:
            if method == "GET":
                raw_refresh = (self._query.get("refresh") or ["0"])[0]
                force = str(raw_refresh).lower() in {"1", "true", "yes"}
                self._send_json(
                    200,
                    bridge.worker.call(
                        lambda: bridge.api_key_status(probe=True, refresh=force)
                    ),
                )
                return
            if method == "POST":
                payload = body or {}
                key = payload.get("key")
                if not isinstance(key, str):
                    raise ValueError("key 必须是字符串")
                self._send_json(200, bridge.worker.call(lambda: bridge.update_api_key(key)))
                return
            if method == "DELETE":
                self._send_json(200, bridge.worker.call(bridge.delete_api_key))
                return
            self._method_not_allowed("GET, POST, DELETE")
            return

        # 执行模式热切换：single ⇄ multi_fixed（每轮之间可切，同一数据目录）
        if rest == ["mode"]:
            if method == "GET":
                self._send_json(200, {"mode": bridge.app.execution_mode})
                return
            if method != "POST":
                self._method_not_allowed("GET, POST")
                return
            payload = body or {}
            mode = payload.get("mode")
            if not isinstance(mode, str) or mode not in {"single", "multi_fixed"}:
                raise ValueError("mode 必须是 single 或 multi_fixed")
            if bridge.has_active_jobs():
                self._try_send_json(
                    409,
                    {"error": "当前还有排队或执行中的作业，等它结束再切换模式"},
                )
                return
            meta = bridge.worker.call(lambda: bridge.switch_mode(mode))
            self._send_json(200, {"mode": meta.get("executionMode"), "meta": meta})
            return

        if rest == ["threads"]:
            if method == "GET":
                threads = bridge.worker.call(bridge.list_threads)
                self._send_json(200, {"threads": threads})
                return
            if method == "POST":
                payload = body or {}
                title = payload.get("title")
                if title is not None and not isinstance(title, str):
                    raise ValueError("title 必须是字符串")
                thread = bridge.worker.call(
                    lambda: bridge.create_thread(title)
                )
                self._send_json(200, {"thread": thread})
                return
            self._method_not_allowed("GET, POST")
            return

        if rest == ["threads", "import"]:
            if method != "POST":
                self._method_not_allowed("POST")
                return
            payload = body or {}
            result = bridge.worker.call(lambda: bridge.import_thread(payload))
            self._send_json(200, result)
            return

        if len(rest) == 3 and rest[0] == "threads":
            thread_id, action = rest[1], rest[2]
            if action == "resume":
                if method != "POST":
                    self._method_not_allowed("POST")
                    return
                thread = bridge.worker.call(
                    lambda: bridge.resume_thread(thread_id)
                )
                self._send_json(200, {"thread": thread})
                return
            if action == "export":
                if method != "GET":
                    self._method_not_allowed("GET")
                    return
                payload = bridge.worker.call(
                    lambda: bridge.export_thread(thread_id)
                )
                self._send_json(200, payload)
                return
            if action == "rename":
                if method != "POST":
                    self._method_not_allowed("POST")
                    return
                payload = body or {}
                title = payload.get("title")
                if not isinstance(title, str):
                    raise ValueError("title 必须是字符串")
                thread = bridge.worker.call(
                    lambda: bridge.rename_thread(thread_id, title)
                )
                self._send_json(200, {"thread": thread})
                return
            if action == "delete":
                if method != "POST":
                    self._method_not_allowed("POST")
                    return
                result = bridge.worker.call(
                    lambda: bridge.delete_thread(thread_id)
                )
                self._send_json(200, result)
                return
            if action == "messages":
                if method != "GET":
                    self._method_not_allowed("GET")
                    return
                messages = bridge.worker.call(
                    lambda: bridge.read_messages(thread_id)
                )
                self._send_json(200, {"messages": messages})
                return
            if action == "unfinished":
                if method != "GET":
                    self._method_not_allowed("GET")
                    return
                unfinished = bridge.worker.call(
                    lambda: bridge.read_unfinished(thread_id)
                )
                self._send_json(200, {"unfinishedTurns": unfinished})
                return
            if action == "turns":
                if method != "POST":
                    self._method_not_allowed("POST")
                    return
                payload = body or {}
                user_input = payload.get("input")
                if not isinstance(user_input, str) or not user_input.strip():
                    raise ValueError("input 必须是非空字符串")
                job = bridge.start_turn_job(thread_id, user_input)
                self._send_json(202, job.handle())
                return
            self._try_send_json(404, {"error": f"未知的接口路径：{path}"})
            return

        if (
            len(rest) == 5
            and rest[0] == "threads"
            and rest[2] == "turns"
            and rest[4] == "resume"
        ):
            if method != "POST":
                self._method_not_allowed("POST")
                return
            job = bridge.start_turn_resume_job(rest[1], rest[3])
            self._send_json(202, job.handle())
            return

        if len(rest) == 2 and rest[0] == "jobs":
            if method != "GET":
                self._method_not_allowed("GET")
                return
            job = bridge.get_job(rest[1])
            if job is None:
                self._try_send_json(404, {"error": "未找到该作业"})
                return
            self._send_json(200, job.snapshot())
            return

        if rest == ["events"]:
            if method != "GET":
                self._method_not_allowed("GET")
                return
            after = _int_query(self._query, "afterSequence", 0)
            limit = _int_query(self._query, "limit", 100)
            thread_id = _str_query(self._query, "threadId")
            turn_id = _str_query(self._query, "turnId")
            events = bridge.read_events(
                after_sequence=after,
                session_id=thread_id,
                turn_id=turn_id,
                limit=limit,
            )
            next_sequence = (
                int(events[-1]["sequence"]) if events else after
            )
            self._send_json(
                200, {"events": events, "nextSequence": next_sequence}
            )
            return

        if rest == ["events", "stream"]:
            if method != "GET":
                self._method_not_allowed("GET")
                return
            self._handle_stream(bridge)
            return

        if rest == ["status"]:
            if method != "GET":
                self._method_not_allowed("GET")
                return
            payload = bridge.worker.call(bridge.status_snapshot)
            self._send_json(200, payload)
            return

        if rest == ["approvals"]:
            if method != "GET":
                self._method_not_allowed("GET")
                return
            raw_thread = (self._query.get("threadId") or [None])[0]
            thread_id = raw_thread if isinstance(raw_thread, str) and raw_thread else None
            approvals = bridge.worker.call(
                lambda: bridge.list_approvals(thread_id)
            )
            self._send_json(200, {"approvals": approvals})
            return

        if (
            len(rest) == 3
            and rest[0] == "approvals"
            and rest[2] == "decision"
        ):
            if method != "POST":
                self._method_not_allowed("POST")
                return
            payload = body or {}
            raw_decision = payload.get("decision")
            if raw_decision not in {"CONFIRM", "REJECT"}:
                raise ValueError("decision 必须是 CONFIRM 或 REJECT")
            try:
                decision = ConfirmationDecision(str(raw_decision))
            except ValueError as exc:
                raise ValueError("decision 必须是 CONFIRM 或 REJECT") from exc
            thread_id = payload.get("threadId")
            if thread_id is not None and not isinstance(thread_id, str):
                raise ValueError("threadId 必须是字符串")
            if not thread_id:
                thread_id = _str_query(self._query, "threadId")
            job = bridge.start_confirmation_job(rest[1], decision, thread_id)
            self._send_json(202, job.handle())
            return

        if rest == ["tools"]:
            if method != "GET":
                self._method_not_allowed("GET")
                return
            tools = bridge.worker.call(bridge.tool_catalog)
            self._send_json(200, {"tools": tools})
            return

        if rest == ["context"]:
            if method != "GET":
                self._method_not_allowed("GET")
                return
            self._send_json(200, bridge.worker.call(bridge.context_status))
            return

        if rest == ["context", "compact"]:
            if method != "POST":
                self._method_not_allowed("POST")
                return
            job = bridge.start_compact_job()
            self._send_json(202, job.handle())
            return

        if rest == ["mcp"]:
            if method != "GET":
                self._method_not_allowed("GET")
                return
            self._send_json(200, bridge.worker.call(bridge.mcp_status))
            return

        if rest == ["workspace"]:
            if method != "GET":
                self._method_not_allowed("GET")
                return
            self._send_json(200, bridge.worker.call(bridge.workspace_status))
            return

        if rest == ["storage"]:
            if method != "GET":
                self._method_not_allowed("GET")
                return
            self._send_json(200, bridge.worker.call(bridge.storage_status))
            return

        # ---- 长期记忆 -------------------------------------------------

        if rest and rest[0] == "memories":
            if rest == ["memories"]:
                if method == "GET":
                    memories = bridge.worker.call(bridge.list_memories)
                    self._send_json(200, {"memories": memories})
                    return
                if method == "POST":
                    payload = body or {}
                    content = payload.get("content")
                    if not isinstance(content, str) or not content.strip():
                        raise ValueError("content 必须是非空字符串")
                    result = bridge.worker.call(
                        lambda: bridge.remember(content)
                    )
                    self._send_json(200, result)
                    return
                self._method_not_allowed("GET, POST")
                return

            # 固定路径必须先于 /api/memories/:id 匹配。
            if rest == ["memories", "recycle-bin"]:
                if method != "GET":
                    self._method_not_allowed("GET")
                    return
                memories = bridge.worker.call(
                    bridge.list_recycled_memories
                )
                self._send_json(200, {"memories": memories})
                return

            if rest == ["memories", "profile"]:
                if method != "GET":
                    self._method_not_allowed("GET")
                    return
                profile = bridge.worker.call(bridge.memory_profile)
                self._send_json(200, profile)
                return

            if len(rest) == 2:
                if method != "GET":
                    self._method_not_allowed("GET")
                    return
                memory = bridge.worker.call(
                    lambda: bridge.memory_detail(rest[1])
                )
                self._send_json(200, {"memory": memory})
                return

            if len(rest) == 3:
                memory_id, action = rest[1], rest[2]
                if action == "history":
                    if method != "GET":
                        self._method_not_allowed("GET")
                        return
                    revisions = bridge.worker.call(
                        lambda: bridge.memory_history(memory_id)
                    )
                    self._send_json(200, {"revisions": revisions})
                    return
                if action == "provenance":
                    if method != "GET":
                        self._method_not_allowed("GET")
                        return
                    provenance = bridge.worker.call(
                        lambda: bridge.memory_provenance(memory_id)
                    )
                    self._send_json(200, {"provenance": provenance})
                    return
                if action == "forget":
                    if method != "POST":
                        self._method_not_allowed("POST")
                        return
                    ok = bridge.worker.call(
                        lambda: bridge.forget_memory(memory_id)
                    )
                    self._send_json(200, {"ok": bool(ok)})
                    return
                if action == "restore":
                    if method != "POST":
                        self._method_not_allowed("POST")
                        return
                    ok = bridge.worker.call(
                        lambda: bridge.restore_memory(memory_id)
                    )
                    self._send_json(200, {"ok": bool(ok)})
                    return

            self._try_send_json(404, {"error": f"未知的接口路径：{path}"})
            return

        # ---- 任务 -----------------------------------------------------

        if rest and rest[0] == "tasks":
            if rest == ["tasks"]:
                if method == "GET":
                    tasks = bridge.worker.call(bridge.list_tasks)
                    self._send_json(200, {"tasks": tasks})
                    return
                if method == "POST":
                    payload = body or {}
                    title = payload.get("title")
                    if title is not None and not isinstance(title, str):
                        raise ValueError("title 必须是字符串")
                    task_id = bridge.worker.call(
                        lambda: bridge.create_task(title)
                    )
                    self._send_json(200, {"taskId": task_id})
                    return
                self._method_not_allowed("GET, POST")
                return

            # /api/tasks/status 与 /api/tasks/current/* 必须先于 :id/switch。
            if rest == ["tasks", "status"]:
                if method != "GET":
                    self._method_not_allowed("GET")
                    return
                status = bridge.worker.call(bridge.task_status_summary)
                self._send_json(200, {"status": status})
                return

            if len(rest) == 3 and rest[1] == "current":
                action = rest[2]
                if method != "POST":
                    self._method_not_allowed("POST")
                    return
                if action == "pause":
                    payload = body or {}
                    note = payload.get("note")
                    if note is not None and not isinstance(note, str):
                        raise ValueError("note 必须是字符串")
                    bridge.worker.call(
                        lambda: bridge.pause_current_task(note)
                    )
                    self._send_json(200, {"ok": True})
                    return
                if action == "resume":
                    bridge.worker.call(bridge.resume_current_task)
                    self._send_json(200, {"ok": True})
                    return
                if action == "complete":
                    bridge.worker.call(bridge.complete_current_task)
                    self._send_json(200, {"ok": True})
                    return
                if action == "cancel":
                    bridge.worker.call(bridge.cancel_current_task)
                    self._send_json(200, {"ok": True})
                    return

            if len(rest) == 3 and rest[2] == "switch":
                if method != "POST":
                    self._method_not_allowed("POST")
                    return
                ok = bridge.worker.call(
                    lambda: bridge.switch_task(rest[1])
                )
                self._send_json(200, {"ok": bool(ok)})
                return

            self._try_send_json(404, {"error": f"未知的接口路径：{path}"})
            return

        # ---- 检索 trace（降级证据视图，只读文件）-----------------------

        if rest == ["traces"]:
            if method != "GET":
                self._method_not_allowed("GET")
                return
            limit = _int_query(self._query, "limit", 50)
            self._send_json(200, {"traces": bridge.retrieval_traces(limit)})
            return

        if len(rest) == 3 and rest[0] == "turns" and rest[2] == "trace":
            if method != "GET":
                self._method_not_allowed("GET")
                return
            row = bridge.retrieval_trace(rest[1])
            if row is None:
                self._try_send_json(
                    404,
                    {
                        "error": "未找到该轮次的检索 trace",
                        "detail": "no persisted retrieval trace for this turn",
                    },
                )
                return
            self._send_json(200, row)
            return

        # ---- 任务闭环 -------------------------------------------------

        if rest == ["task-lifecycle"]:
            if method != "GET":
                self._method_not_allowed("GET")
                return
            task_id = _str_query(self._query, "taskId")
            lifecycle = bridge.worker.call(
                lambda: bridge.task_lifecycle(task_id)
            )
            self._send_json(200, lifecycle)
            return

        if rest == ["task-lifecycle", "drain"]:
            if method != "POST":
                self._method_not_allowed("POST")
                return
            drained = bridge.worker.call(bridge.drain_task_lifecycle)
            self._send_json(200, drained)
            return

        # ---- 协作修复 -------------------------------------------------

        if rest == ["collaboration", "runs"]:
            if method != "GET":
                self._method_not_allowed("GET")
                return
            raw_all = (self._query.get("all") or ["0"])[0]
            include_all = str(raw_all).lower() in {"1", "true", "yes"}
            limit = _int_query(self._query, "limit", 20)
            graphs = bridge.worker.call(
                lambda: bridge.collaboration_runs(limit, include_all)
            )
            self._send_json(200, {"graphs": graphs})
            return

        if len(rest) == 3 and rest[0] == "collaboration" and rest[1] == "runs":
            if method != "GET":
                self._method_not_allowed("GET")
                return
            detail = bridge.worker.call(
                lambda: bridge.collaboration_run_detail(rest[2])
            )
            self._send_json(200, detail)
            return

        if rest and rest[0] == "repairs":
            if rest == ["repairs"]:
                if method != "GET":
                    self._method_not_allowed("GET")
                    return
                repairs = bridge.worker.call(bridge.list_repairs)
                self._send_json(200, {"repairs": repairs})
                return

            # 固定子路径必须先于 /api/repairs/:repairId 匹配。
            if rest == ["repairs", "revoke"]:
                if method != "POST":
                    self._method_not_allowed("POST")
                    return
                payload = body or {}
                run_id = _body_str(payload, "runId")
                artifact_id = _body_str(payload, "artifactId")
                event_id = _body_str(payload, "eventId")
                job = bridge.start_revoke_repair_job(
                    run_id, artifact_id, event_id
                )
                self._send_json(202, job.handle())
                return

            if rest == ["repairs", "drain"]:
                if method != "POST":
                    self._method_not_allowed("POST")
                    return
                job = bridge.start_drain_repairs_job()
                self._send_json(202, job.handle())
                return

            if len(rest) == 4 and rest[3] == "run":
                if method != "POST":
                    self._method_not_allowed("POST")
                    return
                job = bridge.start_repair_job(rest[1], rest[2])
                self._send_json(202, job.handle())
                return

            if len(rest) == 2:
                if method != "GET":
                    self._method_not_allowed("GET")
                    return
                plan = bridge.worker.call(
                    lambda: bridge.repair_plan(rest[1])
                )
                self._send_json(200, plan)
                return

            self._try_send_json(404, {"error": f"未知的接口路径：{path}"})
            return

        # ---- 程序性经验 -------------------------------------------------

        if rest and rest[0] == "experiences":
            if rest == ["experiences"]:
                if method != "GET":
                    self._method_not_allowed("GET")
                    return
                experiences = bridge.worker.call(bridge.list_experiences)
                self._send_json(200, {"experiences": experiences})
                return

            if len(rest) == 2 and rest[1] in {
                "learn",
                "review",
                "revoke",
                "feedback",
            }:
                if method != "POST":
                    self._method_not_allowed("POST")
                    return
                payload = body or {}
                action = rest[1]
                if action == "learn":
                    run_id = _body_str(payload, "runId")
                    revision_of = _optional_body_str(payload, "revisionOf")
                    experience = bridge.worker.call(
                        lambda: bridge.learn_experience(run_id, revision_of)
                    )
                    self._send_json(200, {"experience": experience})
                    return

                experience_id = _body_str(payload, "experienceId")
                if action == "review":
                    confirmation = _body_str(payload, "confirmation")
                    experience = bridge.worker.call(
                        lambda: bridge.review_experience(
                            experience_id, confirmation
                        )
                    )
                elif action == "revoke":
                    event_id = _body_str(payload, "eventId")
                    experience = bridge.worker.call(
                        lambda: bridge.revoke_experience(
                            experience_id, event_id
                        )
                    )
                else:
                    run_id = _body_str(payload, "runId")
                    event_id = _body_str(payload, "eventId")
                    experience = bridge.worker.call(
                        lambda: bridge.feedback_experience(
                            experience_id, run_id, event_id
                        )
                    )
                self._send_json(200, {"experience": experience})
                return

            self._try_send_json(404, {"error": f"未知的接口路径：{path}"})
            return

        self._try_send_json(404, {"error": f"未知的接口路径：{path}"})

    # ---- SSE ----------------------------------------------------------

    def _write_sse_event(self, event: Any) -> None:
        payload = self.server.bridge.app._json_safe_event_value(event.to_dict())
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        self.wfile.write(("data: " + data + "\n\n").encode("utf-8"))
        self.wfile.flush()

    def _handle_stream(self, bridge: WebBridge) -> None:
        app = bridge.app
        after = _int_query(self._query, "afterSequence", 0)
        if after < 0:
            raise ValueError("afterSequence 不能为负数")
        thread_id = _str_query(self._query, "threadId")
        if thread_id is not None and thread_id != app.active_session_id:
            if app.conversation_repo.get_session(thread_id) is None:
                raise ThreadNotFound(thread_id)

        pending: List[Any] = []
        condition = threading.Condition()

        def on_event(event: Any) -> None:
            with condition:
                pending.append(event)
                condition.notify_all()

        unsubscribe = app.subscribe_events(on_event)
        try:
            # 先订阅再补发，避免两者之间的空窗；补发失败必须在响应头之前暴露。
            first_batch = app.list_events(
                after_sequence=after,
                session_id=thread_id,
                limit=SSE_BACKFILL_PAGE,
            )

            self.send_response(200)
            self._write_cors_headers()
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self.close_connection = True

            last_sequence = after
            page = first_batch
            pages = 0
            while page:
                for event in page:
                    if event.sequence <= last_sequence:
                        continue
                    self._write_sse_event(event)
                    last_sequence = event.sequence
                pages += 1
                if len(page) < SSE_BACKFILL_PAGE or pages >= SSE_BACKFILL_MAX_PAGES:
                    break
                page = app.list_events(
                    after_sequence=last_sequence,
                    session_id=thread_id,
                    limit=SSE_BACKFILL_PAGE,
                )

            last_ping = time.monotonic()
            next_poll = last_ping + SSE_POLL_SECONDS
            while not self.server.stopping.is_set():
                with condition:
                    if not pending:
                        condition.wait(timeout=1.0)
                    batch = list(pending)
                    pending.clear()
                for event in batch:
                    if event.sequence <= last_sequence:
                        continue
                    last_sequence = event.sequence
                    if thread_id is not None and event.session_id != thread_id:
                        continue
                    self._write_sse_event(event)
                now = time.monotonic()
                if now >= next_poll:
                    next_poll = now + SSE_POLL_SECONDS
                    try:
                        missed = app.list_events(
                            after_sequence=last_sequence,
                            session_id=thread_id,
                            limit=200,
                        )
                        for event in missed:
                            if event.sequence <= last_sequence:
                                continue
                            last_sequence = event.sequence
                            self._write_sse_event(event)
                    except Exception as exc:  # noqa: BLE001
                        # 流已经建立，无法再改 HTTP 状态码；记录真实原因并断开，
                        # 让 EventSource 重连，而不是静默假装还在推送。
                        sys.stderr.write(
                            "[bridge] SSE 轮询失败：%s: %s\n"
                            % (type(exc).__name__, exc)
                        )
                        break
                if now - last_ping >= SSE_PING_SECONDS:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    last_ping = now
        except (
            BrokenPipeError,
            ConnectionResetError,
            ConnectionAbortedError,
            TimeoutError,
            OSError,
        ):
            # 浏览器关闭页面属于正常生命周期，不是桥接故障。
            pass
        except Exception as exc:  # noqa: BLE001
            # 响应头已经发出，无法再改成 HTTP 错误；记录真实原因并断开连接，
            # 由 EventSource 重连，而不是静默继续。
            sys.stderr.write(
                "[bridge] SSE 推送异常：%s: %s\n" % (type(exc).__name__, exc)
            )
        finally:
            try:
                unsubscribe()
            except Exception:  # noqa: BLE001
                pass


class BridgeHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: Any, bridge: WebBridge) -> None:
        self.bridge = bridge
        self.stopping = threading.Event()
        self.quiet = False
        super().__init__(address, BridgeRequestHandler)

    def handle_error(self, request: Any, client_address: Any) -> None:
        """客户端断开是正常生命周期，不打印 traceback；其他错误照常上报。"""
        exc_type, exc, _traceback = sys.exc_info()
        if isinstance(
            exc,
            (
                ConnectionResetError,
                ConnectionAbortedError,
                BrokenPipeError,
                TimeoutError,
            ),
        ):
            return
        super().handle_error(request, client_address)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="MAPAS 本地 HTTP+SSE 桥接进程",
    )
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（仅限本机回环）")
    parser.add_argument("--port", type=int, default=8787, help="监听端口")
    parser.add_argument(
        "--execution-mode",
        choices=("single", "multi_fixed"),
        default="multi_fixed",
        help="Agent 执行模式；协作/修复/经验/任务闭环需要 multi_fixed",
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        help="隔离的 Agent 数据目录；默认使用全局 Agent home",
    )
    parser.add_argument(
        "--no-token", action="store_true", help="关闭一次性本地 token 校验"
    )
    return parser


def _install_shutdown_signals() -> None:
    """让 Ctrl+C（SIGINT）与 Ctrl+Break（SIGBREAK）都走 KeyboardInterrupt。

    Windows 上 SIGBREAK 的默认处置是直接终止进程，不会执行 ``finally``；
    显式安装 Python 的默认中断处理器后，关闭流程才能执行 ``app.close()``
    并停止 worker。安装失败（例如非主线程）时保持原状但不影响启动。
    """
    try:
        signal.signal(signal.SIGINT, signal.default_int_handler)
    except (ValueError, OSError, AttributeError):
        pass
    sigbreak = getattr(signal, "SIGBREAK", None)
    if sigbreak is not None:
        try:
            signal.signal(sigbreak, signal.default_int_handler)
        except (ValueError, OSError, AttributeError):
            pass


def _resolve_data_dir(argument: Optional[str]) -> str:
    """与 AgentStorageLayout 一致的数据目录解析（AGENT_DATA_DIR > AGENT_HOME > ~/.agent_data）。"""
    if argument:
        return str(argument)
    for name in ("AGENT_DATA_DIR", "AGENT_HOME"):
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return os.path.join(os.path.expanduser("~"), ".agent_data")


def _env_file_key_configured(data_dir: str) -> bool:
    if os.environ.get("DEEPSEEK_API_KEY", "").strip():
        return True
    path = os.path.join(data_dir, "config", ".env.local")
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            text = handle.read()
    except OSError:
        return False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        if name.strip().lstrip("export ").strip() == "DEEPSEEK_API_KEY" and value.strip():
            return True
    return False


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        sys.stderr.write(
            "启动失败：桥接只允许监听本机回环地址（127.0.0.1/localhost/::1），"
            f"收到 --host {args.host}\n"
        )
        return 2

    resolved_data_dir = _resolve_data_dir(args.data_dir)
    app: Optional[PersonalAgentApp] = None
    try:
        app = PersonalAgentApp(
            data_dir=args.data_dir, execution_mode=args.execution_mode
        )
    except Exception as exc:  # noqa: BLE001
        if not _env_file_key_configured(resolved_data_dir):
            # 没有 Key 不应该让桥接直接退出：降级启动后前端才能填 Key
            sys.stderr.write(
                "注意：未配置 DeepSeek API Key，桥接以降级模式启动。"
                f"（{type(exc).__name__}）\n"
                f"请在前端右上角「API Key」填入，或写入 "
                f"{os.path.join(resolved_data_dir, 'config', '.env.local')}\n"
            )
        else:
            sys.stderr.write(
                "启动失败：无法初始化 PersonalAgentApp。"
                f"（{type(exc).__name__}: {exc}）\n"
            )
            return 1

    token = None if args.no_token else secrets.token_urlsafe(24)
    bridge = WebBridge(
        app,
        token=token,
        data_dir=resolved_data_dir,
        execution_mode=args.execution_mode,
    )

    try:
        server = BridgeHTTPServer((args.host, args.port), bridge)
    except OSError as exc:
        sys.stderr.write(
            f"启动失败：无法监听 {args.host}:{args.port}（{exc}）\n"
        )
        bridge.worker.stop(timeout=5.0)
        if app is not None:
            try:
                app.close()
            except Exception:  # noqa: BLE001
                pass
        return 1

    print("MAPAS Web Bridge", flush=True)
    print(f"  address: http://{args.host}:{args.port}", flush=True)
    print(f"  executionMode: {bridge._mode}", flush=True)
    print(f"  data dir: {bridge._data_dir}", flush=True)
    if app is None:
        print("  needsApiKey: true（降级模式：填 Key 后自动恢复）", flush=True)
    if token is None:
        print("token=<disabled>", flush=True)
    else:
        print(f"token={token}", flush=True)

    _install_shutdown_signals()

    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        sys.stderr.write("\n收到中断信号，正在关闭桥接进程...\n")
    finally:
        server.stopping.set()
        try:
            server.server_close()
        except Exception:  # noqa: BLE001
            pass
        bridge.worker.stop(timeout=10.0)
        try:
            if app is not None:
                app.close()
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(
                f"[bridge] 关闭 PersonalAgentApp 时出错：{type(exc).__name__}: {exc}\n"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
