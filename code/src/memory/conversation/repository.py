"""SQLite-backed Repository for Conversation Sessions and Dialogue Messages."""

from __future__ import annotations

import datetime
import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from typing import Iterable, Iterator, List, Optional, Tuple

from src.memory.conversation.models import (
    ConversationCompaction,
    ConversationContext,
    ConversationMessage,
    ConversationMessageView,
    ConversationRole,
    ConversationSession,
)
from src.memory.core.paths import default_agent_data_dir
from src.memory.storage.sqlite_utils import ClosingSQLiteConnection


class ConversationRepository:
    """Persistent SQLite repository managing conversation sessions and transcripts."""

    def __init__(self, db_path: Optional[str] = None, sessions_dir: Optional[str] = None) -> None:
        default_db_path = os.path.join(default_agent_data_dir(), "conversations", "conversation.db")
        self.db_path = os.path.abspath(db_path or default_db_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        # SQLite is the single authoritative conversation store. Keep the
        # legacy attribute for callers that inspect the old layout, but do not
        # create or write one directory per Session anymore.
        self.sessions_dir = os.path.abspath(sessions_dir or os.path.dirname(self.db_path))
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.db_path,
            timeout=30.0,
            factory=ClosingSQLiteConnection,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = self._get_connection()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS conversation_sessions (
                    session_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    active_task_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    status TEXT NOT NULL,
                    workspace_roots_json TEXT NOT NULL DEFAULT '[]',
                    current_workspace_root TEXT
                );
            """)
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(conversation_sessions)").fetchall()
            }
            if "workspace_roots_json" not in columns:
                conn.execute(
                    "ALTER TABLE conversation_sessions ADD COLUMN workspace_roots_json TEXT NOT NULL DEFAULT '[]'"
                )
            if "current_workspace_root" not in columns:
                conn.execute(
                    "ALTER TABLE conversation_sessions ADD COLUMN current_workspace_root TEXT"
                )
            conn.execute("""
                CREATE TABLE IF NOT EXISTS conversation_messages (
                    message_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    turn_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES conversation_sessions(session_id) ON DELETE CASCADE
                );
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_messages_session_time 
                ON conversation_messages(session_id, created_at);
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_sessions_updated 
                ON conversation_sessions(updated_at);
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS conversation_compactions (
                    compaction_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    boundary_message_id TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    summarized_message_count INTEGER NOT NULL,
                    source TEXT NOT NULL DEFAULT 'MODEL',
                    degraded_reason TEXT,
                    created_at REAL NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES conversation_sessions(session_id) ON DELETE CASCADE
                );
            """)
            # Existing runtime databases predate compaction diagnostics. Keep
            # the transcript and checkpoints intact while adding the fields in
            # place; this is intentionally a small forward-only migration.
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(conversation_compactions)").fetchall()
            }
            if "source" not in columns:
                conn.execute(
                    "ALTER TABLE conversation_compactions ADD COLUMN source TEXT NOT NULL DEFAULT 'MODEL'"
                )
            if "degraded_reason" not in columns:
                conn.execute(
                    "ALTER TABLE conversation_compactions ADD COLUMN degraded_reason TEXT"
                )
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_compactions_session_time
                ON conversation_compactions(session_id, created_at);
            """)
            conn.commit()

    def create_session(
        self,
        session_id: Optional[str] = None,
        title: Optional[str] = None,
        active_task_id: Optional[str] = None,
        workspace_roots: Iterable[str] = (),
        current_workspace_root: Optional[str] = None,
    ) -> ConversationSession:
        now = time.time()
        sid = session_id or f"session_{uuid.uuid4().hex[:12]}"
        if not title:
            dt_str = datetime.datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M")
            t_str = f"Session {dt_str}"
        else:
            t_str = title

        roots = tuple(dict.fromkeys(str(root) for root in workspace_roots if str(root).strip()))
        if current_workspace_root is not None:
            current_workspace_root = str(current_workspace_root)
            if current_workspace_root not in roots:
                roots = (*roots, current_workspace_root)

        session = ConversationSession(
            session_id=sid,
            title=t_str,
            active_task_id=active_task_id,
            created_at=now,
            updated_at=now,
            status="ACTIVE",
            workspace_roots=roots,
            current_workspace_root=current_workspace_root,
        )

        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO conversation_sessions
                (session_id, title, active_task_id, created_at, updated_at, status,
                 workspace_roots_json, current_workspace_root)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (session.session_id, session.title, session.active_task_id or "",
                 session.created_at, session.updated_at, session.status,
                 json.dumps(session.workspace_roots, ensure_ascii=False),
                 session.current_workspace_root),
            )
            conn.commit()
        return session

    def get_session(self, session_id: str) -> Optional[ConversationSession]:
        with self._connection() as conn:
            row = conn.execute(
                """SELECT session_id, title, active_task_id, created_at, updated_at, status,
                          workspace_roots_json, current_workspace_root
                   FROM conversation_sessions WHERE session_id = ?""",
                (session_id,),
            ).fetchone()
            if not row:
                return None
            return ConversationSession(
                session_id=row["session_id"],
                title=row["title"],
                active_task_id=row["active_task_id"] or None,
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                status=row["status"],
                workspace_roots=self._workspace_roots_from_row(row),
                current_workspace_root=row["current_workspace_root"],
            )

    @staticmethod
    def _workspace_roots_from_row(row: sqlite3.Row) -> Tuple[str, ...]:
        raw = row["workspace_roots_json"]
        if not raw:
            return ()
        try:
            values = json.loads(raw)
        except (TypeError, ValueError):
            return ()
        if not isinstance(values, list):
            return ()
        return tuple(str(value) for value in values if isinstance(value, str) and value.strip())

    def list_sessions(self) -> List[ConversationSession]:
        with self._connection() as conn:
            rows = conn.execute(
                """SELECT session_id, title, active_task_id, created_at, updated_at, status,
                          workspace_roots_json, current_workspace_root
                   FROM conversation_sessions ORDER BY updated_at DESC"""
            ).fetchall()
            return [
                ConversationSession(
                    session_id=r["session_id"],
                    title=r["title"],
                    active_task_id=r["active_task_id"] or None,
                    created_at=r["created_at"],
                    updated_at=r["updated_at"],
                    status=r["status"],
                    workspace_roots=self._workspace_roots_from_row(r),
                    current_workspace_root=r["current_workspace_root"],
                )
                for r in rows
            ]

    def get_latest_active_session(self) -> Optional[ConversationSession]:
        sessions = [session for session in self.list_sessions() if session.status == "ACTIVE"]
        return sessions[0] if sessions else None

    _UNSET_TASK_BINDING = object()

    def touch_session(
        self,
        session_id: str,
        active_task_id: object = _UNSET_TASK_BINDING,
    ) -> None:
        now = time.time()
        with self._connection() as conn:
            if active_task_id is not self._UNSET_TASK_BINDING:
                cursor = conn.execute(
                    "UPDATE conversation_sessions SET updated_at = ?, active_task_id = ? WHERE session_id = ?",
                    (now, str(active_task_id or ""), session_id),
                )
            else:
                cursor = conn.execute(
                    "UPDATE conversation_sessions SET updated_at = ? WHERE session_id = ?",
                    (now, session_id),
                )
            if cursor.rowcount != 1:
                raise KeyError(f"conversation session not found: {session_id}")
            conn.commit()

    def update_session_task(self, session_id: str, active_task_id: Optional[str]) -> None:
        self.touch_session(session_id, active_task_id=active_task_id)

    def update_session_workspace(
        self,
        session_id: str,
        workspace_roots: Iterable[str],
        current_workspace_root: Optional[str],
    ) -> None:
        roots = tuple(dict.fromkeys(str(root) for root in workspace_roots if str(root).strip()))
        if current_workspace_root is not None:
            current_workspace_root = str(current_workspace_root)
            if current_workspace_root not in roots:
                roots = (*roots, current_workspace_root)
        now = time.time()
        with self._connection() as conn:
            cursor = conn.execute(
                """UPDATE conversation_sessions
                   SET updated_at=?, workspace_roots_json=?, current_workspace_root=?
                   WHERE session_id=?""",
                (now, json.dumps(roots, ensure_ascii=False), current_workspace_root, session_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"conversation session not found: {session_id}")
            conn.commit()

    def append_message(
        self,
        session_id: str,
        turn_id: str,
        role: ConversationRole,
        content: str,
        created_at: Optional[float] = None,
    ) -> ConversationMessage:
        now = created_at if created_at is not None else time.time()
        if not isinstance(content, str) or not content:
            raise ValueError("conversation message content 不能为空")

        # Serialize the idempotency check and insert. A read in one transaction
        # followed by an insert in another leaves a race in multi-process use.
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                """
                SELECT message_id, session_id, turn_id, role, content, created_at
                FROM conversation_messages
                WHERE session_id = ? AND turn_id = ? AND role = ?
                """,
                (session_id, turn_id, role.value),
            ).fetchone()
            if existing:
                if existing["content"] != content:
                    raise ValueError(
                        "同一 session_id/turn_id/role 已绑定不同 message content"
                    )
                return ConversationMessage(
                    message_id=existing["message_id"],
                    session_id=existing["session_id"],
                    turn_id=existing["turn_id"],
                    role=ConversationRole(existing["role"]),
                    content=existing["content"],
                    created_at=existing["created_at"],
                )

            mid = f"msg_{uuid.uuid4().hex[:12]}"
            msg = ConversationMessage(
                message_id=mid,
                session_id=session_id,
                turn_id=turn_id,
                role=role,
                content=content,
                created_at=now,
            )
            conn.execute(
                """
                INSERT INTO conversation_messages (message_id, session_id, turn_id, role, content, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (msg.message_id, msg.session_id, msg.turn_id, msg.role.value, msg.content, msg.created_at),
            )
            cursor = conn.execute(
                "UPDATE conversation_sessions SET updated_at = ? WHERE session_id = ?",
                (now, session_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"conversation session not found: {session_id}")
            conn.commit()
            return msg

    def list_recent_messages(self, session_id: str, limit: Optional[int] = None) -> Tuple[ConversationMessage, ...]:
        if limit is not None and limit < 0:
            raise ValueError("message limit 不能为负数")
        with self._connection() as conn:
            query = """
                SELECT message_id, session_id, turn_id, role, content, created_at 
                FROM conversation_messages 
                WHERE session_id = ? 
                ORDER BY created_at DESC, rowid DESC
            """
            params: Tuple[object, ...] = (session_id,)
            if limit is not None:
                query += " LIMIT ?"
                params += (limit,)
            rows = conn.execute(query, params).fetchall()
            messages = [
                ConversationMessage(
                    message_id=r["message_id"],
                    session_id=r["session_id"],
                    turn_id=r["turn_id"],
                    role=ConversationRole(r["role"]),
                    content=r["content"],
                    created_at=r["created_at"],
                )
                for r in reversed(rows)
            ]
            return tuple(messages)

    def list_messages(self, session_id: str) -> Tuple[ConversationMessage, ...]:
        """Return the complete durable transcript in insertion order."""
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT message_id, session_id, turn_id, role, content, created_at
                FROM conversation_messages
                WHERE session_id = ?
                ORDER BY rowid
                """,
                (session_id,),
            ).fetchall()
        return tuple(
            ConversationMessage(
                message_id=row["message_id"], session_id=row["session_id"],
                turn_id=row["turn_id"], role=ConversationRole(row["role"]),
                content=row["content"], created_at=row["created_at"],
            )
            for row in rows
        )

    def get_latest_compaction(self, session_id: str) -> Optional[ConversationCompaction]:
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT compaction_id, session_id, boundary_message_id, summary,
                       summarized_message_count, source, degraded_reason, created_at
                FROM conversation_compactions
                WHERE session_id = ?
                ORDER BY created_at DESC, rowid DESC
                LIMIT 1
                """,
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return ConversationCompaction(
            compaction_id=row["compaction_id"], session_id=row["session_id"],
            boundary_message_id=row["boundary_message_id"], summary=row["summary"],
            summarized_message_count=row["summarized_message_count"],
            source=row["source"] or "MODEL", degraded_reason=row["degraded_reason"],
            created_at=row["created_at"],
        )

    def save_compaction(
        self,
        session_id: str,
        boundary_message_id: str,
        summary: str,
        summarized_message_count: int,
        *,
        compaction_id: Optional[str] = None,
        source: str = "MODEL",
        degraded_reason: Optional[str] = None,
    ) -> ConversationCompaction:
        if not summary.strip():
            raise ValueError("compaction summary 不能为空")
        if summarized_message_count <= 0:
            raise ValueError("summarized_message_count 必须大于 0")
        if source not in {"MODEL", "FALLBACK"}:
            raise ValueError("compaction source 必须是 MODEL 或 FALLBACK")
        with self._connection() as conn:
            boundary = conn.execute(
                "SELECT 1 FROM conversation_messages WHERE message_id=? AND session_id=?",
                (boundary_message_id, session_id),
            ).fetchone()
            if boundary is None:
                raise KeyError(f"conversation boundary not found: {boundary_message_id}")
            existing = conn.execute(
                """
                SELECT compaction_id, session_id, boundary_message_id, summary,
                       summarized_message_count, source, degraded_reason, created_at
                FROM conversation_compactions
                WHERE session_id=? AND boundary_message_id=?
                ORDER BY created_at DESC LIMIT 1
                """,
                (session_id, boundary_message_id),
            ).fetchone()
            if existing is not None and existing["summary"] == summary:
                return ConversationCompaction(
                    compaction_id=existing["compaction_id"], session_id=existing["session_id"],
                    boundary_message_id=existing["boundary_message_id"], summary=existing["summary"],
                    summarized_message_count=existing["summarized_message_count"],
                    source=existing["source"] or "MODEL",
                    degraded_reason=existing["degraded_reason"],
                    created_at=existing["created_at"],
                )
            now = time.time()
            item = ConversationCompaction(
                compaction_id=compaction_id or f"compact_{uuid.uuid4().hex[:12]}",
                session_id=session_id, boundary_message_id=boundary_message_id,
                summary=summary, summarized_message_count=summarized_message_count,
                source=source, degraded_reason=degraded_reason, created_at=now,
            )
            conn.execute(
                """
                INSERT INTO conversation_compactions
                (compaction_id, session_id, boundary_message_id, summary,
                 summarized_message_count, source, degraded_reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (item.compaction_id, item.session_id, item.boundary_message_id,
                 item.summary, item.summarized_message_count, item.source,
                 item.degraded_reason, item.created_at),
            )
            conn.commit()
            return item

    def list_messages_after(self, session_id: str, boundary_message_id: str) -> Tuple[ConversationMessage, ...]:
        """Return the intact transcript tail after a compaction boundary."""
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT m.message_id, m.session_id, m.turn_id, m.role, m.content, m.created_at
                FROM conversation_messages AS m
                WHERE m.session_id=?
                  AND m.rowid > (
                      SELECT boundary.rowid FROM conversation_messages AS boundary
                      WHERE boundary.message_id=? AND boundary.session_id=?
                  )
                ORDER BY m.rowid
                """,
                (session_id, boundary_message_id, session_id),
            ).fetchall()
        return tuple(
            ConversationMessage(
                message_id=row["message_id"], session_id=row["session_id"],
                turn_id=row["turn_id"], role=ConversationRole(row["role"]),
                content=row["content"], created_at=row["created_at"],
            )
            for row in rows
        )

    def build_conversation_context(
        self,
        session_id: str,
        max_messages: Optional[int] = None,
        max_chars: Optional[int] = None,
        *,
        summary: Optional[str] = None,
        summarized_message_count: int = 0,
        exclude_turn_id: Optional[str] = None,
    ) -> ConversationContext:
        """Build a model-window projection; raw transcript is never deleted."""
        if max_messages is not None and max_messages < 0:
            raise ValueError("conversation context budgets 不能为负数")
        if max_chars is not None and max_chars < 0:
            raise ValueError("conversation context budgets 不能为负数")
        with self._connection() as conn:
            # Count total messages in session
            total_count_row = conn.execute(
                "SELECT COUNT(*) as cnt FROM conversation_messages WHERE session_id = ?"
                + (" AND turn_id != ?" if exclude_turn_id is not None else ""),
                (session_id,) if exclude_turn_id is None else (session_id, exclude_turn_id),
            ).fetchone()
            total_count = total_count_row["cnt"] if total_count_row else 0

        latest_compaction = self.get_latest_compaction(session_id)
        if summary is None and latest_compaction is not None:
            summary = latest_compaction.summary
            summarized_message_count = latest_compaction.summarized_message_count
            recent_msgs = self.list_messages_after(session_id, latest_compaction.boundary_message_id)
        else:
            recent_msgs = self.list_recent_messages(session_id, limit=max_messages or 0) if max_messages is not None else self.list_messages(session_id)
        if exclude_turn_id is not None:
            recent_msgs = tuple(message for message in recent_msgs if message.turn_id != exclude_turn_id)

        compaction_source = latest_compaction.source if latest_compaction is not None else "NONE"
        compaction_degraded = compaction_source == "FALLBACK"

        if max_messages == 0 or max_chars == 0:
            return ConversationContext(
                session_id=session_id,
                summary=summary,
                recent_messages=(),
                omitted_message_count=total_count,
                summarized_message_count=summarized_message_count,
                compaction_source=compaction_source,
                compaction_degraded=compaction_degraded,
            )
        if not recent_msgs:
            return ConversationContext(
                session_id=session_id, summary=summary, recent_messages=(),
                omitted_message_count=max(0, total_count - summarized_message_count),
                summarized_message_count=summarized_message_count,
                compaction_source=compaction_source,
                compaction_degraded=compaction_degraded,
            )

        # Truncate by total character budget if needed, keeping most recent
        selected_views: List[ConversationMessageView] = []
        accumulated_chars = 0

        for msg in reversed(recent_msgs):
            content_text = msg.content
            remaining = (max_chars - accumulated_chars) if max_chars is not None else None
            if remaining is not None and remaining <= 0:
                break
            if remaining is not None and len(content_text) > remaining:
                if selected_views:
                    break
                marker = "\n[内容已截断]"
                if remaining <= len(marker):
                    content_text = content_text[:remaining]
                else:
                    content_text = content_text[: remaining - len(marker)] + marker
            
            selected_views.insert(0, ConversationMessageView(role=msg.role, content=content_text))
            accumulated_chars += len(content_text)

        omitted_count = max(0, total_count - len(selected_views))
        return ConversationContext(
            session_id=session_id,
            summary=summary,
            recent_messages=tuple(selected_views),
            omitted_message_count=max(0, total_count - summarized_message_count - len(selected_views)),
            summarized_message_count=summarized_message_count,
            compaction_source=compaction_source,
            compaction_degraded=compaction_degraded,
        )
