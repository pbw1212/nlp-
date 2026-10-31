"""Bounded real-Provider integration in an isolated temporary Agent home.

No benchmark score or comparative performance claim is produced.
"""

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
from src.memory.core.config import MemoryConfig


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
        "official_benchmark_score": None,
        "limits_per_run": asdict(limits),
        "checks": {},
    }
    with tempfile.TemporaryDirectory(prefix="mapas-file-lifecycle-") as temporary:
        base = Path(temporary)
        workspace = base / "workspace"
        workspace.mkdir()
        (workspace / "left.txt").write_text("left_value=17\n", encoding="utf-8")
        (workspace / "right.txt").write_text("right_value=23\n", encoding="utf-8")
        app = PersonalAgentApp(
            data_dir=str(base / "state"),
            config=config,
            execution_mode="multi_fixed",
            task_lifecycle_enabled=False,
            collaboration_limits=limits,
        )
        try:
            app.workspace.replace_scope([str(workspace)], str(workspace))
            app.create_new_task(
                "Read two independent source files, verify each, and merge their values"
            )
            prompt = "Read left.txt and right.txt using read_file. Use exactly three subgoals: left (read left.txt), right (read right.txt), and merge (depends on left and right). Report both exact values using the literal field names left_value and right_value. Preserve the task and its criteria."
            result = app.process_chat_turn(prompt)
            report["initial"] = {
                "status": result.status.value,
                "answer": result.final_response,
                "model_calls": result.trace_metadata["collaboration_usage"][
                    "model_calls"
                ],
                "error": asdict(result.error_info) if result.error_info else None,
            }
            print("Initial task: " + result.status.value, flush=True)
            assert result.status is AgentResultStatus.SUCCESS
            assert all(
                text in result.final_response
                for text in ("left_value", "right_value", "17", "23")
            )
            assert result.task_id != "__unbound__"
            session, task = app.active_session_id, result.task_id
            graphs = app.coordination_repository.list_task_graphs(task)
            assert len(graphs[0]["nodes"]) == 3
            report["checks"]["bound_three_node_task"] = True
            (workspace / "left.txt").write_text("left_value=91\n", encoding="utf-8")
            app.close()
            app = PersonalAgentApp(
                data_dir=str(base / "state"),
                config=config,
                execution_mode="multi_fixed",
                task_lifecycle_enabled=False,
                collaboration_limits=limits,
            )
            assert app.resume_session(session)
            queues = app.coordination_repository.list_repairs(session)
            assert queues and queues[0]["status"] == "SUCCEEDED", queues
            graph = app.coordination_repository.list_task_graphs(task)[-1]
            repaired = json.loads(
                app.coordination_repository.get_run(graph["run_id"])["result_json"]
            )
            report["repair"] = {
                "status": repaired["status"],
                "answer": repaired["final_response"],
                "model_calls": repaired["trace_metadata"]["collaboration_usage"][
                    "model_calls"
                ],
                "reused_subtasks": repaired["trace_metadata"]["reused_subtasks"],
                "executed_tool_calls": len(repaired["executed_tool_call_ids"]),
            }
            print("Restart repair: " + repaired["status"], flush=True)
            assert (
                "91" in repaired["final_response"]
                and "23" in repaired["final_response"]
            )
            assert all(
                name in repaired["final_response"]
                for name in ("left_value", "right_value")
            )
            assert repaired["trace_metadata"]["reused_subtasks"] == ["right"]
            assert len(repaired["executed_tool_call_ids"]) == 1
            report["checks"]["restart_selective_repair"] = True
            usage = app.coordination_repository.usage(graph["run_id"])
            assert app.resume_session(session)
            assert app.coordination_repository.usage(graph["run_id"]) == usage
            report["checks"]["repeat_resume_no_calls"] = True
            lesson = app.learn_experience(graph["run_id"])
            report["experience"] = {
                "state": lesson.get("state"),
                "decision": lesson.get("decision"),
                "experience_id": lesson.get("experience_id"),
            }
            assert lesson.get("state") == "ACTIVE", report["experience"]
            report["checks"]["experience_admitted"] = True
            consumer = app.process_chat_turn(prompt)
            report["consumer"] = {
                "status": consumer.status.value,
                "answer": consumer.final_response,
                "experience_inputs": consumer.trace_metadata.get(
                    "experience_inputs", []
                ),
                "model_calls": consumer.trace_metadata["collaboration_usage"][
                    "model_calls"
                ],
                "error": asdict(consumer.error_info) if consumer.error_info else None,
                "operations": [
                    row["operation"]
                    for row in consumer.trace_metadata["collaboration_usage"]["calls"]
                ],
            }
            assert consumer.status is AgentResultStatus.SUCCESS
            assert any(
                ref["experience_id"] == lesson["experience_id"]
                for ref in consumer.trace_metadata["experience_inputs"]
            )
            report["checks"]["experience_applied"] = True
            feedback = app.feedback_experience(
                lesson["experience_id"], consumer.agent_run_id, "live-consumer-feedback"
            )
            assert any(event["kind"] == "FEEDBACK" for event in feedback["events"])
            app.revoke_experience(lesson["experience_id"], "live-lesson-revocation")
            replay = app._process_chat_turn_with_id(
                prompt, consumer.request_turn_id, resume=True
            )
            assert (
                replay.status is AgentResultStatus.FAILED
                and replay.final_response is None
            )
            report["checks"]["experience_feedback_and_revocation"] = True
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
