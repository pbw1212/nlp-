"""Real Provider task lifecycle: wait, repair, approve, seal, learn and reuse."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT))

from src.memory.agent.models import AgentResultStatus
from src.memory.app.console import PersonalAgentApp
from src.memory.collaboration.budget import CollaborationLimits
from src.memory.core.config import MemoryConfig
from src.memory.llm.errors import ProviderError
from src.memory.task_state.models import TaskStatus


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", required=True, action="store_true")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    cfg = MemoryConfig.from_env()
    if not cfg.llm_api_key:
        raise RuntimeError("Provider credential is not configured")
    limits = CollaborationLimits(
        max_model_calls=24,
        max_token_units=240000,
        deadline_seconds=180,
        max_completion_tokens=4096,
    )
    report = {
        "kind": "engineering_smoke",
        "requested_model": cfg.llm_model_name,
        "server_model_identity_retained": False,
        "fake_model_used": False,
        "official_benchmark_score": None,
        "limits_per_run": asdict(limits),
        "checks": {},
        "structured_failures": [],
    }
    source_names = [
        "src/memory/collaboration/lifecycle.py",
        "src/memory/collaboration/coordinator.py",
        "src/memory/collaboration/experience.py",
        "src/memory/collaboration/roles.py",
        "src/memory/task_state/repository.py",
        "src/memory/app/console.py",
    ]
    report["source_sha256"] = {
        name: hashlib.sha256((CODE_ROOT / name).read_bytes()).hexdigest()
        for name in source_names
    }
    with tempfile.TemporaryDirectory(prefix="mapas-task-cycle-") as temporary:
        base = Path(temporary)
        workspace = base / "workspace"
        workspace.mkdir()
        (workspace / "left.txt").write_text("left_value=17\n", encoding="utf-8")
        (workspace / "right.txt").write_text("right_value=23\n", encoding="utf-8")

        def open_app():
            application = PersonalAgentApp(
                data_dir=str(base / "state"),
                config=cfg,
                execution_mode="multi_fixed",
                collaboration_limits=limits,
            )
            provider = application.llm_client.client
            for method_name in ("generate_json", "generate_reasoned_json"):
                original_method = getattr(provider, method_name)

                def observed(*args, original_method=original_method, **kwargs):
                    try:
                        return original_method(*args, **kwargs)
                    except ProviderError as exc:
                        cause = exc.__cause__
                        report["structured_failures"].append(
                            {
                                "operation": kwargs.get("schema_name"),
                                "error_type": type(exc).__name__,
                                "cause_type": type(cause).__name__ if cause else None,
                                "schema_path": list(
                                    getattr(cause, "absolute_schema_path", [])
                                ),
                                "json_position": getattr(cause, "pos", None),
                            }
                        )
                        raise

                setattr(provider, method_name, observed)
            return application

        app = open_app()

        def record_result(name, result):
            report[name] = {
                "status": result.status.value,
                "answer": result.final_response,
                "task_id": result.task_id,
                "graph_model_calls": result.trace_metadata.get(
                    "collaboration_usage", {}
                ).get("model_calls"),
                "error": asdict(result.error_info) if result.error_info else None,
                "lifecycle": app.get_task_lifecycle_status(result.task_id)["tasks"],
            }

        try:
            app.workspace.replace_scope([str(workspace)], str(workspace))
            first_task = app.create_new_task(
                "Read left.txt and right.txt, verify both values and merge them; complete only after the user explicitly approves the final values"
            )
            prompt = "For the tracked task read left.txt and right.txt with read_file. Use exactly three subgoals left, right, merge; merge depends on left and right. Report literal fields left_value and right_value. I have NOT approved completion yet: keep the task open until I explicitly approve the final values."
            first = app.process_chat_turn(prompt)
            record_result("first_turn", first)
            assert first.status is AgentResultStatus.SUCCESS
            assert (
                app.runtime.task_service.get_task(first_task).status
                is TaskStatus.ACTIVE
            )
            assert (
                app.get_task_lifecycle_status(first_task)["tasks"][0]["state"]
                == "WAITING_USER"
            )
            report["checks"]["waits_for_actual_user_approval"] = True
            print("Initial task awaits user approval", flush=True)
            session = app.active_session_id
            (workspace / "left.txt").write_text("left_value=91\n", encoding="utf-8")
            app.close()
            app = open_app()
            assert app.resume_session(session)
            repairs = app.coordination_repository.list_repairs(session)
            report["repairs"] = repairs
            assert repairs and all(row["status"] == "SUCCEEDED" for row in repairs)
            assert (
                app.runtime.task_service.get_task(first_task).status
                is TaskStatus.ACTIVE
            )
            assert (
                app.get_task_lifecycle_status(first_task)["tasks"][0]["state"]
                == "WAITING_USER"
            )
            report["checks"]["restart_repairs_without_inventing_approval"] = True
            print(
                "Source change repaired after restart; task still awaits approval",
                flush=True,
            )
            approval = "I explicitly approve the current final values and completion of this tracked task. Reread left.txt and right.txt using read_file and report both literal fields left_value and right_value. In the merged report also describe the reusable procedure demonstrated here: re-observe changing sources, combine independently verified values, and keep task closure separate from user approval. All task requirements are now satisfied once this verification succeeds. Use left, right, merge subgoals with merge depending on the other two."
            accepted = app.process_chat_turn(approval)
            record_result("approved_turn", accepted)
            assert accepted.status is AgentResultStatus.SUCCESS
            assert accepted.task_id == first_task
            assert (
                app.runtime.task_service.get_task(first_task).status
                is TaskStatus.COMPLETED
            )
            seal = app.runtime.task_service.repository.get_completion_receipt(
                first_task
            )
            assert seal and seal["source_run_id"] == accepted.agent_run_id
            lessons = app.list_experiences()
            report["first_experience"] = [
                {"experience_id": row["experience_id"], "state": row["state"]}
                for row in lessons
            ]
            assert lessons and lessons[0]["state"] == "ACTIVE"
            first_experience = lessons[0]["experience_id"]
            report["checks"]["automatic_completion_seal_and_learning"] = True
            usage_before = app.get_task_lifecycle_status(first_task)["tasks"][0][
                "usage"
            ]
            app.drain_task_lifecycle()
            assert (
                app.get_task_lifecycle_status(first_task)["tasks"][0]["usage"]
                == usage_before
            )
            report["checks"]["repeated_drain_no_new_calls"] = True
            app.close()
            app = open_app()
            assert app.resume_session(session)
            assert (
                app.get_task_lifecycle_status(first_task)["tasks"][0]["usage"]
                == usage_before
            )
            app.create_new_task(
                "Read and verify both source files and report the merged current values; completion is approved once the report is verified"
            )
            followup = "For this new task completion is approved once the report is verified. Read left.txt and right.txt using read_file and report exact fields left_value and right_value. Use left, right, merge subgoals; merge depends on both reads."
            second = app.process_chat_turn(followup)
            record_result("second_task", second)
            assert second.status is AgentResultStatus.SUCCESS
            assert second.task_id != first_task
            assert first_experience in {
                row["experience_id"]
                for row in second.trace_metadata["experience_inputs"]
            }
            assert (
                app.runtime.task_service.get_task(second.task_id).status
                is TaskStatus.COMPLETED
            )
            report["checks"][
                "sealed_experience_survives_restart_and_new_task_reuses_it"
            ] = True
            app.revoke_experience(first_experience, "live-task-cycle-revocation")
            replay = app._process_chat_turn_with_id(
                followup, second.request_turn_id, resume=True
            )
            assert (
                replay.status is AgentResultStatus.FAILED
                and replay.final_response is None
            )
            report["checks"]["revoked_experience_blocks_old_consumer_answer"] = True
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
