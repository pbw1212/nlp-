"""Observer for Autonomous Personal Agent Product Trial Harness."""

from __future__ import annotations

import sqlite3
from contextlib import closing
import os
from typing import List, Optional

from src.memory.app.console import PersonalAgentApp
from src.memory.agent.models import AgentResult
from src.memory.product_trial.models import MemoryItemSnapshot, TrialObservation


class TrialObservationError(RuntimeError):
    """Raised when trial evidence cannot be observed reliably."""


class ProductTrialObserver:
    """Observes SUT (System Under Test) internal and external states without modifying them."""

    @staticmethod
    def capture_observation(app: PersonalAgentApp, last_result: Optional[AgentResult] = None) -> TrialObservation:
        visible_resp = last_result.final_response if last_result else None
        res_status = str(last_result.status) if last_result else "NONE"
        warnings = tuple(last_result.warnings) if last_result and last_result.warnings else ()
        errors = (
            (f"{last_result.error_info.category}:{last_result.error_info.code}",)
            if last_result and last_result.error_info
            else ()
        )

        # 1. Inspect Memory DB
        memories = ProductTrialObserver._observe_memories(app.memory_db_path)

        # 2. Inspect Task State DB
        task_entries_cnt = ProductTrialObserver._count_rows(app.task_db_path, "task_entries")

        # 3. Inspect Journal DB
        tool_calls_cnt = ProductTrialObserver._count_rows(app.journal_db_path, "tool_execution_receipts")

        # 4. Side-effect executions count
        side_effects_cnt = ProductTrialObserver._count_side_effects(app)

        return TrialObservation(
            visible_response=visible_resp,
            agent_result_status=res_status,
            current_session_id=app.active_session_id,
            current_task_id=app.active_task_id,
            memories=tuple(memories),
            task_entries_count=task_entries_cnt,
            tool_calls_count=tool_calls_cnt,
            executed_side_effects_count=side_effects_cnt,
            warnings=warnings,
            errors=errors,
        )

    @staticmethod
    def check_global_invariants(app: PersonalAgentApp) -> List[str]:
        """Evaluates strict deterministic system invariants across all SQLite DBs."""
        violations = []

        # Invariant 1: Session ID != Task ID
        if app.active_session_id == app.active_task_id:
            violations.append(f"Global Invariant Violation: active_session_id '{app.active_session_id}' equals active_task_id")

        # Invariant 2: At most 1 CURRENT revision per memory item
        if os.path.exists(app.memory_db_path):
            with closing(sqlite3.connect(app.memory_db_path)) as conn:
                rows = conn.execute(
                    """
                    SELECT memory_id, COUNT(*) as cnt 
                    FROM memory_revisions 
                    WHERE semantic_validity = 'CURRENT' 
                    GROUP BY memory_id 
                    HAVING cnt > 1
                    """
                ).fetchall()
                for r in rows:
                    violations.append(f"Global Invariant Violation: memory_id '{r[0]}' has {r[1]} CURRENT revisions")

        # Invariant 3: Conversation messages belong to valid session
        if os.path.exists(app.conversation_db_path):
            with closing(sqlite3.connect(app.conversation_db_path)) as conn:
                orphan_cnt = conn.execute(
                    """
                    SELECT COUNT(*) FROM conversation_messages 
                    WHERE session_id NOT IN (SELECT session_id FROM conversation_sessions)
                    """
                ).fetchone()[0]
                if orphan_cnt > 0:
                    violations.append(f"Global Invariant Violation: {orphan_cnt} conversation messages reference invalid session")

        return violations

    @staticmethod
    def _observe_memories(db_path: str) -> List[MemoryItemSnapshot]:
        if not os.path.exists(db_path):
            return []
        snapshots = []
        with closing(sqlite3.connect(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            items = conn.execute("SELECT memory_id, memory_type FROM memory_items").fetchall()
            for item in items:
                mid = item["memory_id"]
                mtype = item["memory_type"]
                revs = conn.execute(
                    "SELECT content, semantic_validity FROM memory_revisions WHERE memory_id = ? ORDER BY created_at DESC", (mid,)
                ).fetchall()
                rev_cnt = len(revs)
                curr_content = revs[0]["content"] if revs else ""
                curr_status = revs[0]["semantic_validity"] if revs else "UNKNOWN"
                snapshots.append(
                    MemoryItemSnapshot(
                        memory_id=mid,
                        type=mtype,
                        status=curr_status,
                        revision_count=rev_cnt,
                        content=curr_content,
                    )
                )
        return snapshots

    @staticmethod
    def _count_rows(db_path: str, table_name: str) -> int:
        if not os.path.exists(db_path):
            return 0
        try:
            with closing(sqlite3.connect(db_path)) as conn:
                cnt = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
                return int(cnt)
        except Exception as exc:
            raise TrialObservationError(
                f"cannot count rows in existing trial database table '{table_name}'"
            ) from exc

    @staticmethod
    def _count_side_effects(app: PersonalAgentApp) -> int:
        if not os.path.exists(app.journal_db_path):
            return 0
        try:
            with closing(sqlite3.connect(app.journal_db_path)) as conn:
                cnt = conn.execute(
                    "SELECT COUNT(*) FROM tool_execution_receipts WHERE tool_effect = 'SIDE_EFFECT'"
                ).fetchone()[0]
                return int(cnt)
        except Exception as exc:
            raise TrialObservationError(
                "cannot count side-effect receipts in existing trial journal"
            ) from exc
