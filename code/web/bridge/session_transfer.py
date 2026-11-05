#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""会话导出 / 导入（桥接层适配；后端暂未提供对应 API）。

## 为什么会有这个模块（边界说明）

后端自 commit `53484dc` 起已提供官方的会话重命名 / 删除（`PersonalAgentApp.rename_session` /
`delete_session`），桥接已改用官方接口，不再直写数据库。

**导出 / 导入仍没有后端 API**，所以这里保留对 `conversation.db` 的受控读写：
导出只读；导入写入新会话（生成新 session_id、绝不覆盖已有会话）。

## 约定

- 只动三张表：`conversation_sessions` / `conversation_messages` / `conversation_compactions`；
  单事务提交，失败即回滚。
- **不做隐式级联**：`agent_events` / `agent_journal` / `task_state` 是追加型日志/独立所有权，
  这里不删（删会话不等于删除历史事件，符合项目"历史可追溯"的口径）。
- 若后端将来提供正式接口（如 `thread/rename` / `thread/delete`），
  应删除本模块并改调接口，避免长期直连数据库。
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid

MAX_TITLE_LENGTH = 120
MAX_IMPORT_MESSAGES = 5000
EXPORT_FORMAT = "mapas.session/v1"


def _connect(db_path: str) -> sqlite3.Connection:
    if not db_path or not os.path.exists(db_path):
        raise FileNotFoundError(f"会话数据库不存在：{db_path}")
    connection = sqlite3.connect(db_path, timeout=30.0)
    connection.row_factory = sqlite3.Row
    return connection


def export_session(db_path: str, session_id: str) -> dict:
    """把一个会话导出成可移植 JSON（会话元信息 + 全部消息）。

    只读 conversation.db；事件流（agent_events）不在导出范围内，
    因此导入后时间线只显示消息、不显示当时的工具/阶段过程（前端会说明这一点）。
    """
    connection = _connect(db_path)
    try:
        session = connection.execute(
            "SELECT session_id, title, active_task_id, created_at, updated_at, status "
            "FROM conversation_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if session is None:
            raise LookupError(f"会话不存在：{session_id}")
        messages = [
            {
                "turnId": row["turn_id"],
                "role": row["role"],
                "content": row["content"],
                "createdAt": row["created_at"],
            }
            for row in connection.execute(
                "SELECT turn_id, role, content, created_at FROM conversation_messages "
                "WHERE session_id = ? ORDER BY created_at",
                (session_id,),
            )
        ]
    finally:
        connection.close()

    return {
        "format": EXPORT_FORMAT,
        "exportedAt": time.time(),
        "thread": {
            "title": session["title"],
            "activeTaskId": session["active_task_id"] or None,
            "createdAt": session["created_at"],
            "updatedAt": session["updated_at"],
            "status": session["status"],
        },
        "messages": messages,
    }


def _validate_import_payload(payload: object) -> tuple[str, list[dict]]:
    if not isinstance(payload, dict):
        raise ValueError("导入内容必须是一个 JSON 对象")
    if payload.get("format") != EXPORT_FORMAT:
        raise ValueError(f"不支持的导入格式（需要 {EXPORT_FORMAT}）")
    thread = payload.get("thread")
    if not isinstance(thread, dict):
        raise ValueError("缺少 thread 字段")
    title = str(thread.get("title") or "").strip()
    if title == "":
        raise ValueError("会话标题不能为空")
    raw_messages = payload.get("messages")
    if not isinstance(raw_messages, list):
        raise ValueError("缺少 messages 字段（应为数组）")
    if len(raw_messages) > MAX_IMPORT_MESSAGES:
        raise ValueError(f"消息过多（上限 {MAX_IMPORT_MESSAGES} 条）")

    messages: list[dict] = []
    for index, item in enumerate(raw_messages):
        if not isinstance(item, dict):
            raise ValueError(f"第 {index + 1} 条消息格式不正确")
        role = str(item.get("role") or "").upper()
        if role not in {"USER", "ASSISTANT", "TOOL", "SYSTEM"}:
            raise ValueError(f"第 {index + 1} 条消息的角色不合法：{role or '(空)'}")
        content = item.get("content")
        if not isinstance(content, str):
            raise ValueError(f"第 {index + 1} 条消息的内容必须是字符串")
        created_at = item.get("createdAt")
        if not isinstance(created_at, (int, float)):
            created_at = time.time() + index / 1000.0
        messages.append(
            {
                "turn_id": str(item.get("turnId") or f"turn_import_{uuid.uuid4().hex[:10]}"),
                "role": role,
                "content": content,
                "created_at": float(created_at),
            }
        )
    return title, messages


def import_session(db_path: str, payload: object) -> dict:
    """把导出的 JSON 导入为新会话（生成新 session_id，绝不覆盖已有会话）。"""
    title, messages = _validate_import_payload(payload)
    session_id = f"session_{uuid.uuid4().hex[:12]}"
    now = time.time()
    created = messages[0]["created_at"] if messages else now
    updated = messages[-1]["created_at"] if messages else now
    import_title = f"{title}（导入）"[:MAX_TITLE_LENGTH]

    connection = _connect(db_path)
    try:
        connection.execute(
            """
            INSERT INTO conversation_sessions
            (session_id, title, active_task_id, created_at, updated_at, status,
             workspace_roots_json, current_workspace_root)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (session_id, import_title, "", created, max(updated, created), "ACTIVE", "[]", None),
        )
        for message in messages:
            connection.execute(
                """
                INSERT INTO conversation_messages
                (message_id, session_id, turn_id, role, content, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    f"msg_{uuid.uuid4().hex[:16]}",
                    session_id,
                    message["turn_id"],
                    message["role"],
                    message["content"],
                    message["created_at"],
                ),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return {
        "sessionId": session_id,
        "title": import_title,
        "messageCount": len(messages),
    }
