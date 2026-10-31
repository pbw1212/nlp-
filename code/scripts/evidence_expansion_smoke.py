"""Real Provider evidence-window integration; uses isolated synthetic source data."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.memory.agent.models import AgentResultStatus
from src.memory.app.console import PersonalAgentApp
from src.memory.collaboration.budget import CollaborationLimits
from src.memory.conversation.models import ConversationRole
from src.memory.core.config import MemoryConfig
from src.memory.core.schema import Granularity, MemoryType, ProvenanceAuthority


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = MemoryConfig.from_env()
    if not config.llm_api_key:
        raise RuntimeError("Provider credential is not configured")
    limits = CollaborationLimits(
        max_model_calls=24,
        max_token_units=240000,
        deadline_seconds=180,
        max_completion_tokens=2048,
    )
    report = {
        "kind": "engineering_smoke",
        "requested_model": config.llm_model_name,
        "server_model_identity_retained": False,
        "fake_model_used": False,
        "source_data": "isolated_synthetic_user_record",
        "official_benchmark_score": None,
        "limits": asdict(limits),
    }
    with tempfile.TemporaryDirectory(prefix="mapas-evidence-window-") as temporary:
        app = PersonalAgentApp(
            data_dir=temporary,
            config=config,
            execution_mode="multi_fixed",
            task_lifecycle_enabled=False,
            collaboration_limits=limits,
        )
        try:
            app.conversation_repo.create_session("deadline-source")
            original = (
                "项目背景说明，仅作上下文。\n" * 450
                + "唯一批准的项目交付截止日期与时刻为2026年12月18日17:30。"
            )
            message = app.conversation_repo.append_message(
                "deadline-source", "deadline-record", ConversationRole.USER, original
            )
            event, _ = app.long_term_memory_mgr.sqlite_mgr.register_ingestion_event(
                original, session_id="deadline-source", message_id=message.message_id
            )
            revision = app.long_term_memory_mgr.sqlite_mgr.create_memory(
                content="项目交付截止日期与时刻批准记录；具体日期和时刻需要查阅原始消息末尾。",
                memory_type=MemoryType.EPISODIC_GRAPH,
                granularity=Granularity.FINE_EPISODE,
                event_ids=[event.event_id],
                authority=ProvenanceAuthority.EXPLICIT_USER,
            )
            app.create_new_task("核对批准的项目交付截止日期与时刻")
            prompt = "项目交付截止日期与时刻批准记录：请核对已批准的完整日期和时刻。摘要没有具体值，必须查阅原始消息末尾的原文窗口，用原文日期与时刻回答。仅用一个子任务，不调用文件或联网工具。"
            result = app._process_chat_turn_with_id(
                prompt, "live-evidence-window", resume=False
            )
            report["result"] = {
                "status": result.status.value,
                "answer": result.final_response,
                "model_calls": result.trace_metadata["collaboration_usage"][
                    "model_calls"
                ],
                "error": asdict(result.error_info) if result.error_info else None,
            }
            windows = [
                source
                for read in result.trace_metadata.get("source_reads", [])
                for source in read["sources"]
                if source.get("offset", 0) > 0
            ]
            report["source_length"] = len(original)
            report["window_offsets"] = [window["offset"] for window in windows]
            report["windows"] = [
                {"offset": row["offset"], "length": row.get("length")}
                for row in windows
            ]
            assert result.status is AgentResultStatus.SUCCESS
            assert all(
                value in result.final_response
                for value in ("2026", "12", "18", "17:30")
            )
            assert windows and any(
                row["offset"] + (row.get("length") or 0) > 4000 for row in windows
            )
            calls = app.coordination_repository.usage(result.agent_run_id)[
                "model_calls"
            ]
            replay = app._process_chat_turn_with_id(
                prompt, "live-evidence-window", resume=True
            )
            assert replay.status is AgentResultStatus.SUCCESS
            assert (
                app.coordination_repository.usage(result.agent_run_id)["model_calls"]
                == calls
            )
            report["checks"] = {
                "original_window_supplied": True,
                "exact_deadline": True,
                "repeat_replay_no_calls": True,
                "seeded_memory_id": revision.memory_id,
            }
            report["engineering_assertions_passed"] = True
        except BaseException as exc:
            report["engineering_assertions_passed"] = False
            report["error_type"] = type(exc).__name__
            raise
        finally:
            app.close()
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
    print(
        json.dumps({"engineering_assertions_passed": True, "output": str(args.output)})
    )


if __name__ == "__main__":
    main()
