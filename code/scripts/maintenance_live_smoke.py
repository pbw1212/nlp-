"""Bounded real-provider engineering smoke. Key comes only from process env.

Run from the repository: python code/scripts/maintenance_live_smoke.py
All product data is isolated in TemporaryDirectory; only sanitized evidence is saved.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
from contextlib import closing

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.memory.api.memory_manager import LongTermMemoryManager
from src.memory.app.console import PersonalAgentApp
from src.memory.core.config import MemoryConfig
from src.memory.llm.llm_client import OpenAILLMClient
from src.memory.llm.errors import ProviderError


class BudgetClient(OpenAILLMClient):
    def _read_response_with_deadline(self, req):
        if self.usage["request_count"] > 30:
            raise RuntimeError("live request budget exhausted")
        return super()._read_response_with_deadline(req)


def main():
    out = Path(__file__).resolve().parents[2] / "docs/maintenance/2026-10-14/live-smoke.json"
    client = BudgetClient(os.environ["DEEPSEEK_API_KEY"], max_retries=1,
                          request_timeout_seconds=45, max_completion_tokens=4096,
                          reasoning_effort="low")
    records = []

    def case(name, input_text, expected, run):
        started = time.monotonic()
        before = client.usage["request_count"]
        try:
            actual = run()
            records.append({"case": name, "input": input_text, "expected": expected,
                            "status": "PASS", "actual": actual})
        except Exception as exc:
            actual = exc.to_safe_projection() if isinstance(exc, ProviderError) else {"error_type": type(exc).__name__}
            records.append({"case": name, "input": input_text, "expected": expected,
                            "status": "FAIL", "actual": actual})
        records[-1].update(seconds=round(time.monotonic()-started, 3), requests=client.usage["request_count"]-before)
        out.write_text(json.dumps({"date": "2026-10-14", "request_budget": 30,
                                  "usage": client.usage, "cases": records},
                                 ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"case": name, "status": records[-1]["status"], "requests": records[-1]["requests"]}), flush=True)

    with tempfile.TemporaryDirectory(prefix="mapas-maintenance-") as temp:
        root = Path(temp)
        config = MemoryConfig(sqlite_db_path=str(root / "memory.db"), llm_api_key=client.api_key,
                              llm_max_retries=1, llm_reasoning_effort="low", llm_max_completion_tokens=4096)
        manager = LongTermMemoryManager(config=config, llm_client=client)

        def explicit(text, event_id):
            result = manager.process_user_interaction(text, is_explicit=True, source_event_id=event_id)
            return {"result": result, "current": [{"id": s.memory_id, "content": s.revision.content} for s in manager.list_current()]}

        def add():
            result = explicit("我长期喜欢使用Python进行数据分析", "live-add")
            assert len(manager.list_current()) == 1
            assert "Python" in manager.list_current()[0].revision.content
            assert manager.vector_store.search("Python")
            return result
        case("memory_add", "我长期喜欢使用Python进行数据分析", "数据库和索引都有一条记忆", add)

        def duplicate():
            result = explicit("我长期喜欢使用Python进行数据分析", "live-duplicate")
            assert len(manager.list_current()) == 1
            assert len(manager.get_history(manager.list_current()[0].memory_id)) == 1
            return result
        case("memory_deduplicate", "相同偏好再次输入", "不增加身份或版本", duplicate)

        def correct():
            result = explicit("更正之前的数据分析偏好：我以后长期使用Julia，替代Python", "live-correct")
            assert len(manager.list_current()) == 1
            item = manager.list_current()[0]
            assert "Julia" in item.revision.content
            assert len(manager.get_history(item.memory_id)) == 2
            return result
        case("memory_correct", "Julia替代Python", "同一身份出现更正版本", correct)

        def restart():
            restarted = LongTermMemoryManager(config=config, llm_client=client)
            assert [(s.memory_id, s.revision.content) for s in restarted.list_current()] == [(s.memory_id, s.revision.content) for s in manager.list_current()]
            return {"current_count": len(restarted.list_current()), "index_count": len(restarted.vector_store.vectors)}
        case("restart_consistency", "重启存储管理器", "读取当前版本一致", restart)

        def remove():
            result = explicit("请删除之前记录的数据分析语言偏好，以后不要记录这个偏好", "live-remove")
            assert manager.list_current() == []
            assert manager.vector_store.search("Julia") == []
            assert manager.get_recycle_bin_memories()
            return result
        case("memory_remove", "以后不要记录这个偏好", "有效读取与索引均排除，回收站保留", remove)

        def transient():
            manager.update_long_term_memory_for_turn("今天路上堵了十分钟，这只是今天的临时情况", source_event_id="live-transient")
            assert manager.list_current() == []
            return {"current_count": 0, "pending": len(manager.sqlite_mgr.list_pending_events())}
        case("transient_filter", "今天堵车的临时情况", "不产生长期记忆", transient)

        def inferred():
            result = manager.update_long_term_memory_for_turn("我多年一直喜欢清晰简洁的工作汇报，今后也希望如此", source_event_id="live-inferred")
            assert manager.list_current()
            assert all(s.provenance for s in manager.list_current())
            return {"result": result, "current_count": len(manager.list_current())}
        case("inferred_memory", "长期汇报偏好", "类型化提案落库且有来源", inferred)

        # Five full application turns, no synthetic reasoner or task router.
        app_config = MemoryConfig(sqlite_db_path=str(root / "app/long_term_memory/memory.db"), llm_api_key=client.api_key,
                                  llm_max_retries=1, llm_reasoning_effort="low", llm_max_completion_tokens=4096)
        app = PersonalAgentApp(data_dir=str(root / "app"), config=app_config, llm_client=client)
        prompts = ["我们先聊一个临时练习，代号为青石。无需创建持续任务或长期记忆。",
                   "这个临时练习有两个方案，A三步，B五步。请简短复述。",
                   "刚才方案A有几步？",
                   "请把方案A增加一步，仍然只在本会话中讨论。",
                   "请复述这段临时练习的代号和修改后的方案A步数。"]
        for index, prompt in enumerate(prompts, 1):
            def turn(prompt=prompt):
                result = app.process_chat_turn(prompt)
                assert result.status.value == "SUCCESS"
                assert len(app.conversation_repo.list_messages(app.active_session_id)) >= 2
                return {"status": result.status.value, "response": result.final_response,
                        "message_count": len(app.conversation_repo.list_messages(app.active_session_id)),
                        "memory_count": len(app.long_term_memory_mgr.list_current())}
            case(f"conversation_turn_{index}", prompt, "完整应用轮次成功且历史持久化", turn)

        def integrity():
            with closing(sqlite3.connect(config.sqlite_db_path)) as conn:
                check = conn.execute("PRAGMA integrity_check").fetchone()[0]
                assert check == "ok"
                assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
            return {"integrity_check": check}
        case("sqlite_integrity", "最终一致性检查", "SQLite完整性与外键通过", integrity)

    out.write_text(json.dumps({"date": "2026-10-14", "request_budget": 30, "usage": client.usage,
                               "cases": records, "note": "真实API工程小样本；PASS仅代表记录中的断言，不代表模型语义质量全面合格。"}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(out), "usage": client.usage, "failures": sum(r["status"] != "PASS" for r in records)}))


if __name__ == "__main__":
    main()
