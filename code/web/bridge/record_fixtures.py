#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把桥接运行时的真实响应录制成前端 fixture（P5）。

用途：录一次真实运行，之后前端可以 `?src=fixture` 离线回放——
答辩现场即使没有 API 密钥、没有额度、桥接没启动，界面里也全是**真实录下来的数据**。

原则（重要）：本工具只**原样搬运** bridge 的响应，不做任何加工或补造。

用法：
    # 1) 先启动桥接（建议同一台机器、默认数据目录）
    python code/web/bridge/web_bridge.py --execution-mode multi_fixed --port 8787 --no-token

    # 2) 录制（默认写到 code/web/src/datasource/fixtures）
    python code/web/bridge/record_fixtures.py --bridge http://127.0.0.1:8787

    # 需要 token 时：
    python code/web/bridge/record_fixtures.py --token <bridge-token>

    # 3) 前端用离线模式打开
    #    http://localhost:5173/?src=fixture

注意：fixtures 里会包含你的测试会话、记忆正文等数据。录之前确认里面没有隐私内容。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
WEB_ROOT = os.path.dirname(HERE)
DEFAULT_OUT = os.path.join(WEB_ROOT, "src", "datasource", "fixtures")


class Recorder:
    def __init__(self, base: str, token: Optional[str]) -> None:
        self.base = base.rstrip("/")
        self.token = token
        self.written: List[str] = []

    def get(self, path: str) -> Any:
        request = urllib.request.Request(self.base + path, method="GET")
        if self.token:
            request.add_header("X-MAPAS-Token", self.token)
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))

    def write(self, key: str, payload: Any, out_dir: str) -> None:
        path = os.path.join(out_dir, key + ".json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        self.written.append(key)


def record(base: str, token: Optional[str], out_dir: str) -> List[str]:
    rec = Recorder(base, token)

    # ---- 元信息与全局状态 ----
    rec.write("meta", rec.get("/api/meta"), out_dir)
    rec.write("status", rec.get("/api/status"), out_dir)
    rec.write("context", rec.get("/api/context"), out_dir)
    rec.write("tools", rec.get("/api/tools")["tools"], out_dir)
    rec.write("mcp", rec.get("/api/mcp"), out_dir)
    rec.write("workspace", rec.get("/api/workspace"), out_dir)
    rec.write("storage", rec.get("/api/storage"), out_dir)

    # ---- 会话与事件 ----
    threads: List[Dict[str, Any]] = rec.get("/api/threads")["threads"]
    rec.write("threads", threads, out_dir)
    for thread in threads:
        tid = thread["threadId"]
        rec.write(f"thread.{tid}", thread, out_dir)
        rec.write(f"messages.{tid}", rec.get(f"/api/threads/{tid}/messages")["messages"], out_dir)
        rec.write(
            f"unfinished.{tid}",
            rec.get(f"/api/threads/{tid}/unfinished")["unfinishedTurns"],
            out_dir,
        )
        events = rec.get(f"/api/events?afterSequence=0&threadId={tid}&limit=1000")
        rec.write(f"events.{tid}", events["events"], out_dir)

    # ---- 长期记忆 ----
    memories: List[Dict[str, Any]] = rec.get("/api/memories")["memories"]
    rec.write("memories", memories, out_dir)
    rec.write("recycle-bin", rec.get("/api/memories/recycle-bin")["memories"], out_dir)
    rec.write("profile", rec.get("/api/memories/profile"), out_dir)
    for memory in memories:
        mid = memory.get("memory_id")
        if not mid:
            continue
        try:
            rec.write(f"memory.{mid}", rec.get(f"/api/memories/{mid}")["memory"], out_dir)
        except Exception:  # noqa: BLE001 - 单条缺失不应中断整次录制
            pass
        try:
            rec.write(f"memory-history.{mid}", rec.get(f"/api/memories/{mid}/history")["revisions"], out_dir)
        except Exception:  # noqa: BLE001
            pass
        try:
            rec.write(
                f"memory-provenance.{mid}",
                rec.get(f"/api/memories/{mid}/provenance")["provenance"],
                out_dir,
            )
        except Exception:  # noqa: BLE001
            pass

    # ---- 任务与闭环 ----
    rec.write("tasks", rec.get("/api/tasks")["tasks"], out_dir)
    rec.write("task-summary", rec.get("/api/tasks/status")["status"], out_dir)
    rec.write("task-lifecycle", rec.get("/api/task-lifecycle"), out_dir)
    rec.write("approvals", rec.get("/api/approvals")["approvals"], out_dir)

    # ---- 检索 trace（降级证据视图）----
    try:
        traces: List[Dict[str, Any]] = rec.get("/api/traces?limit=100")["traces"]
        rec.write("traces", traces, out_dir)
        for row in traces[:20]:
            turn_id = row.get("turn_id") or row.get("request_id")
            if not turn_id:
                continue
            try:
                rec.write(f"turn-trace.{turn_id}", rec.get(f"/api/turns/{turn_id}/trace"), out_dir)
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass

    # ---- 协作 / 修复 / 经验 ----
    rec.write("graphs", rec.get("/api/collaboration/runs")["graphs"], out_dir)
    rec.write("repairs", rec.get("/api/repairs")["repairs"], out_dir)
    rec.write("experiences", rec.get("/api/experiences")["experiences"], out_dir)
    return rec.written


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="MAPAS bridge → frontend fixture recorder")
    parser.add_argument("--bridge", default="http://127.0.0.1:8787", help="桥接地址")
    parser.add_argument("--token", default=None, help="桥接 token（若未用 --no-token 启动）")
    parser.add_argument("--out", default=DEFAULT_OUT, help="输出目录（默认 src/datasource/fixtures）")
    args = parser.parse_args(argv)

    out_dir = os.path.abspath(args.out)
    try:
        written = record(args.bridge, args.token, out_dir)
    except urllib.error.URLError as exc:  # type: ignore[attr-defined]
        print(f"录制失败：连不上桥接（{args.bridge}）：{exc}", file=sys.stderr)
        print("请先启动：python code/web/bridge/web_bridge.py --port 8787 --no-token", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"录制失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    total = sum(
        os.path.getsize(os.path.join(out_dir, name + ".json"))
        for name in written
        if os.path.exists(os.path.join(out_dir, name + ".json"))
    )
    print(f"已录制 {len(written)} 个 fixture → {out_dir}（合计 {total / 1024:.1f} KB）")
    for name in sorted(written):
        print(f"  - {name}.json")
    print()
    print("离线预览：cd code/web && pnpm dev  →  http://localhost:5173/?src=fixture")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
