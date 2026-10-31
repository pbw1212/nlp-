"""Limited live P1 smoke in a temporary Agent home and workspace.

No official benchmark score is produced. The optional official CitySearch
component is executed from a pinned external checkout, never vendored.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import sqlite3
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT))

from src.memory.agent.models import AgentResultStatus
from src.memory.agent.tooling import ToolEffect, ToolSpec
from src.memory.app.console import PersonalAgentApp
from src.memory.collaboration.budget import CollaborationLimits
from src.memory.collaboration.repository import CoordinationConflict
from src.memory.conversation.models import ConversationRole
from src.memory.core.config import MemoryConfig
from src.memory.runtime.models import ToolCallResult, ToolCallStatus
from src.memory.task_state.models import EntryKind


class OfficialCitiesExecutor:
    def __init__(self, checkout: Path) -> None:
        base = checkout.resolve() / "env/env_systems/travel_planner_env"
        spec = importlib.util.spec_from_file_location(
            "memoryarena_city_smoke", base / "tools/cities.py"
        )
        if spec is None or spec.loader is None:
            raise RuntimeError("CitySearch module not found")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with contextlib.redirect_stdout(io.StringIO()):
            self.tool = module.Cities(
                path=str(base / "database/background/citySet_with_states.txt")
            )

    def execute(self, request):
        result = self.tool.run(request.arguments["state"])
        if isinstance(result, Exception):
            return ToolCallResult(
                request.tool_call_id,
                request.tool_name,
                request.arguments,
                ToolCallStatus.FAILED,
                error_message="State absent from official database",
            )
        return ToolCallResult(
            request.tool_call_id,
            request.tool_name,
            request.arguments,
            ToolCallStatus.SUCCESS,
            result_content=json.dumps(result),
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Execute at most 12 provider calls per run; repair has its own bounded run",
    )
    parser.add_argument("--memoryarena-checkout", type=Path)
    parser.add_argument(
        "--task-frontier",
        action="store_true",
        help="Check a bound task and stale replay",
    )
    parser.add_argument(
        "--artifact-revocation",
        action="store_true",
        help="Check artifact revocation and stale replay",
    )
    parser.add_argument(
        "--provenance",
        action="store_true",
        help="Check conversation and receipt input identities",
    )
    parser.add_argument(
        "--history-invalidation",
        action="store_true",
        help="Check stale replay after a historical message changes",
    )
    parser.add_argument(
        "--receipt-invalidation",
        action="store_true",
        help="Check stale replay after a tool receipt changes",
    )
    parser.add_argument(
        "--contract-check",
        action="store_true",
        help="Check repair compatibility after provider configuration changes",
    )
    parser.add_argument(
        "--execute-repair",
        action="store_true",
        help="Execute the repair in a separate bounded run",
    )
    parser.add_argument(
        "--auto-repair",
        action="store_true",
        help="Restart and process a queued repair on session resume",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (
        sum(
            (
                args.task_frontier,
                args.artifact_revocation,
                args.history_invalidation,
                args.receipt_invalidation,
            )
        )
        > 1
    ):
        parser.error("choose one invalidation scenario per run")
    if (args.history_invalidation or args.receipt_invalidation) and not args.provenance:
        parser.error("history and receipt invalidation require --provenance")
    if args.contract_check and not args.artifact_revocation:
        parser.error("contract check requires --artifact-revocation")
    if args.execute_repair and not args.artifact_revocation:
        parser.error("repair execution requires --artifact-revocation")
    if args.auto_repair and (not args.artifact_revocation or args.execute_repair):
        parser.error(
            "auto repair requires --artifact-revocation and excludes --execute-repair"
        )
    if not args.live:
        parser.error(
            "Explicit --live is required; offline verification uses pytest code/tests/test_collaboration.py"
        )
    cfg = MemoryConfig.from_env()
    if not cfg.llm_api_key:
        raise RuntimeError("DeepSeek credential not configured")
    limits = CollaborationLimits(
        max_model_calls=12,
        max_token_units=120000,
        deadline_seconds=150,
        max_completion_tokens=2048,
    )
    report = {
        "kind": "engineering_smoke",
        "official_benchmark_score": None,
        "requested_model": cfg.llm_model_name,
        "limits": asdict(limits),
        "gpu_used": False,
        "fake_model_used": False,
    }
    with tempfile.TemporaryDirectory(prefix="mapas-ma-smoke-") as temporary:
        app = PersonalAgentApp(
            data_dir=str(Path(temporary) / "state"),
            config=cfg,
            execution_mode="multi_fixed",
            task_lifecycle_enabled=False,
            collaboration_limits=limits,
        )
        try:
            app.workspace.replace_scope([temporary], temporary)
            prior_message = None
            if args.history_invalidation:
                app._ensure_persisted_session()
                prior_message = app.conversation_repo.append_message(
                    app.active_session_id,
                    "live-prior-message",
                    ConversationRole.USER,
                    "This is earlier task context for the calculation.",
                )
            task_id = (
                app.create_new_task("Calculate 17 times 23")
                if args.task_frontier
                else None
            )
            source = None
            if args.memoryarena_checkout:
                source = OfficialCitiesExecutor(args.memoryarena_checkout)
                app.registry.register(
                    ToolSpec(
                        "CitySearch",
                        "Read the official offline MemoryArena travel database cities for a state.",
                        {
                            "type": "object",
                            "properties": {"state": {"type": "string"}},
                            "required": ["state"],
                            "additionalProperties": False,
                        },
                        ToolEffect.READ_ONLY,
                    ),
                    source,
                )
                user_input = "Use CitySearch on the offline MemoryArena database to list the first three cities returned for California, in the original order. Use one subtask and only this tool. Do not use web search or assume this is a live travel recommendation."
                expected = source.tool.run("California")[:3]
            else:
                user_input = (
                    (
                        "Continue the current calculation task. "
                        if args.task_frontier
                        else ""
                    )
                    + "Use the calculator to compute 17*23. Use one subtask. Report the exact result."
                )
                expected = ["391"]
            report.update(input=user_input, expected=expected)
            prepared = {}
            original_runner = app.collaboration.run_prepared
            if args.execute_repair:

                def capture_prepared(context, request, **kwargs):
                    prepared.update(context=context, request=request)
                    return original_runner(context, request, **kwargs)

                app.collaboration.run_prepared = capture_prepared
            result = app._process_chat_turn_with_id(
                user_input, "live-p1-smoke", resume=False
            )
            app.collaboration.run_prepared = original_runner
            receipts = [
                app.journal.get_receipt(key) for key in result.executed_tool_call_ids
            ]
            passed = result.status is AgentResultStatus.SUCCESS and all(
                value in (result.final_response or "") for value in expected
            )
            if args.provenance:
                graphs = app.coordination_repository.list_task_graphs(result.task_id)
                graph = next(
                    (row for row in graphs if row["run_id"] == result.agent_run_id),
                    None,
                )
                attempts = [
                    attempt
                    for node in (graph["nodes"] if graph else [])
                    for attempt in node["attempts"]
                ]
                observations = [
                    app.coordination_repository.get_attempt_observations(
                        result.agent_run_id, item["attempt_id"]
                    )
                    for item in attempts
                ]
                recorded_calls = [
                    item["tool_call_id"]
                    for group in observations
                    if group is not None
                    for item in group
                ]
                frontier = result.trace_metadata.get("conversation_frontier", {})
                source_bound = bool(frontier.get("messages")) and not frontier.get(
                    "untracked", True
                )
                receipt_bound = (
                    bool(observations)
                    and all(group is not None for group in observations)
                    and set(recorded_calls) == set(result.executed_tool_call_ids)
                )
                report["provenance_check"] = {
                    "conversation_bound": source_bound,
                    "receipt_observations_bound": receipt_bound,
                    "recorded_tool_calls": len(recorded_calls),
                }
                passed = passed and source_bound and receipt_bound
            if args.task_frontier:
                bound = result.task_id == task_id
                replay = None
                graphs = []
                if passed and bound:
                    app.runtime.task_service.repository.add_entry(
                        task_id,
                        EntryKind.CONSTRAINT,
                        "Report the result in decimal notation.",
                    )
                    replay = app._process_chat_turn_with_id(
                        user_input, "live-p1-smoke", resume=True
                    )
                    graphs = app.coordination_repository.list_task_graphs(task_id)
                stale = bool(
                    replay
                    and replay.error_info
                    and replay.error_info.code == "COLLABORATION_STALE_REPLAY"
                    and replay.final_response is None
                )
                statuses = [
                    node["status"] for graph in graphs for node in graph["nodes"]
                ]
                report["task_frontier_check"] = {
                    "bound": bound,
                    "stale_replay_blocked": stale,
                    "node_statuses": statuses,
                }
                passed = (
                    passed
                    and bound
                    and stale
                    and bool(statuses)
                    and all(status == "STALE" for status in statuses)
                )
            if args.history_invalidation:
                frontier = result.trace_metadata.get("conversation_frontier", {})
                prior_recorded = bool(
                    prior_message
                    and any(
                        ref["message_id"] == prior_message.message_id
                        for ref in frontier.get("messages", [])
                    )
                )
                stale = False
                statuses = []
                if passed and prior_recorded:
                    with contextlib.closing(
                        sqlite3.connect(app.conversation_db_path)
                    ) as db, db:
                        db.execute(
                            "UPDATE conversation_messages SET content=? WHERE message_id=?",
                            (
                                "Earlier task context was changed.",
                                prior_message.message_id,
                            ),
                        )
                    replay = app._process_chat_turn_with_id(
                        user_input, "live-p1-smoke", resume=True
                    )
                    stale = bool(
                        replay.error_info
                        and replay.error_info.code == "COLLABORATION_STALE_REPLAY"
                        and replay.final_response is None
                    )
                    graphs = app.coordination_repository.list_task_graphs(
                        result.task_id
                    )
                    statuses = [
                        node["status"]
                        for graph in graphs
                        if graph["run_id"] == result.agent_run_id
                        for node in graph["nodes"]
                    ]
                report["history_invalidation_check"] = {
                    "prior_recorded": prior_recorded,
                    "stale_replay_blocked": stale,
                    "node_statuses": statuses,
                }
                passed = (
                    passed
                    and prior_recorded
                    and stale
                    and bool(statuses)
                    and all(status == "STALE" for status in statuses)
                )
            if args.receipt_invalidation:
                stale = False
                statuses = []
                if passed and len(result.executed_tool_call_ids) == 1:
                    with contextlib.closing(
                        sqlite3.connect(app.journal_db_path)
                    ) as db, db:
                        db.execute(
                            "UPDATE tool_execution_receipts SET result_json='{}' WHERE tool_call_id=?",
                            (result.executed_tool_call_ids[0],),
                        )
                    replay = app._process_chat_turn_with_id(
                        user_input, "live-p1-smoke", resume=True
                    )
                    stale = bool(
                        replay.error_info
                        and replay.error_info.code == "COLLABORATION_STALE_REPLAY"
                        and replay.final_response is None
                    )
                    graphs = app.coordination_repository.list_task_graphs(
                        result.task_id
                    )
                    statuses = [
                        node["status"]
                        for graph in graphs
                        if graph["run_id"] == result.agent_run_id
                        for node in graph["nodes"]
                    ]
                report["receipt_invalidation_check"] = {
                    "stale_replay_blocked": stale,
                    "node_statuses": statuses,
                }
                passed = (
                    passed
                    and stale
                    and bool(statuses)
                    and all(status == "STALE" for status in statuses)
                )
            if args.artifact_revocation:
                graph_check = {
                    "artifact_found": False,
                    "stale_replay_blocked": False,
                    "node_statuses": [],
                }
                if passed:
                    graphs = app.coordination_repository.list_task_graphs(
                        result.task_id
                    )
                    graph = next(
                        (row for row in graphs if row["run_id"] == result.agent_run_id),
                        None,
                    )
                    nodes = graph["nodes"] if graph else []
                    artifacts = [
                        attempt["artifact_id"]
                        for node in nodes
                        for attempt in node["attempts"]
                        if attempt["artifact_id"]
                    ]
                    if len(artifacts) == 1:
                        app.coordination_repository.invalidate_artifact(
                            result.agent_run_id,
                            artifacts[0],
                            "live-artifact-revocation",
                        )
                        if args.auto_repair:
                            session_id = app.active_session_id
                            app.close()
                            app = PersonalAgentApp(
                                data_dir=str(Path(temporary) / "state"),
                                config=cfg,
                                execution_mode="multi_fixed",
                                task_lifecycle_enabled=False,
                                collaboration_limits=limits,
                            )
                            resumed = app.resume_session(session_id)
                            queue = app.coordination_repository.list_repairs(session_id)
                            plan_auto = app.collaboration.plan_repair(
                                result.agent_run_id, "live-artifact-revocation"
                            )
                            status_auto = app.collaboration.repair_status(
                                plan_auto["repair_id"]
                            )
                            restored = app.collaboration.execute_saved_repair(
                                plan_auto["repair_id"]
                            )
                            after = app.collaboration.repair_status(
                                plan_auto["repair_id"]
                            )
                            messages = app.conversation_repo.list_messages(session_id)
                            check_auto = {
                                "session_resumed": resumed,
                                "queue_status": (
                                    queue[0]["status"]
                                    if len(queue) == 1
                                    else "UNEXPECTED"
                                ),
                                "repair_status": status_auto["status"],
                                "answer_correct": all(
                                    value in (restored.final_response or "")
                                    for value in expected
                                ),
                                "repair_model_calls": status_auto["usage"][
                                    "model_calls"
                                ],
                                "replay_model_calls": after["usage"]["model_calls"],
                                "answer_persisted": any(
                                    m.turn_id == plan_auto["repair_id"]
                                    and m.role.value == "ASSISTANT"
                                    for m in messages
                                ),
                            }
                            report["automatic_repair_check"] = check_auto
                            passed = (
                                passed
                                and check_auto["session_resumed"]
                                and check_auto["queue_status"] == "SUCCEEDED"
                                and check_auto["repair_status"] == "SUCCEEDED"
                                and check_auto["answer_correct"]
                                and check_auto["answer_persisted"]
                                and check_auto["repair_model_calls"]
                                == check_auto["replay_model_calls"]
                            )
                        if args.contract_check:
                            candidate = app.collaboration.prepare_repair(
                                result.agent_run_id, "live-artifact-revocation"
                            )
                            repair_plan = app.collaboration.plan_repair(
                                result.agent_run_id, "live-artifact-revocation"
                            )
                            persisted = (
                                app.collaboration.get_validated_repair_plan(
                                    repair_plan["repair_id"]
                                )
                                == repair_plan
                                == app.collaboration.plan_repair(
                                    result.agent_run_id, "live-artifact-revocation"
                                )
                            )
                            provider = app.collaboration.client.client
                            old_model = provider.model_name
                            try:
                                provider.model_name = old_model + "-changed-for-test"
                                changed = app.collaboration.prepare_repair(
                                    result.agent_run_id, "live-artifact-revocation"
                                )
                                try:
                                    app.collaboration.get_validated_repair_plan(
                                        repair_plan["repair_id"]
                                    )
                                    changed_blocked = False
                                except CoordinationConflict:
                                    changed_blocked = True
                            finally:
                                provider.model_name = old_model
                            report["execution_contract_check"] = {
                                "original": candidate["execution_contract_validation"],
                                "after_model_change": changed[
                                    "execution_contract_validation"
                                ],
                                "repair_plan_persisted_idempotently": persisted,
                                "changed_plan_blocked": changed_blocked,
                                "repair_execution_status": repair_plan[
                                    "execution_status"
                                ],
                            }
                            passed = passed and report["execution_contract_check"] == {
                                "original": "PARTIAL_MATCH",
                                "after_model_change": "CHANGED",
                                "repair_plan_persisted_idempotently": True,
                                "changed_plan_blocked": True,
                                "repair_execution_status": "NOT_STARTED",
                            }
                        if args.execute_repair:
                            execution_plan = app.collaboration.plan_repair(
                                result.agent_run_id, "live-artifact-revocation"
                            )
                            repaired = app.collaboration.execute_saved_repair(
                                execution_plan["repair_id"]
                            )
                            repeated = app.collaboration.execute_saved_repair(
                                execution_plan["repair_id"]
                            )
                            repair_calls = repaired.trace_metadata.get(
                                "collaboration_usage", {}
                            ).get("model_calls", 0)
                            report["repair_execution_check"] = {
                                "status": repaired.status.value,
                                "answer_correct": all(
                                    value in (repaired.final_response or "")
                                    for value in expected
                                ),
                                "separate_run": repaired.agent_run_id
                                != result.agent_run_id,
                                "replay_same_run": repeated.agent_run_id
                                == repaired.agent_run_id,
                                "model_calls": repair_calls,
                                "replay_model_calls": repeated.trace_metadata.get(
                                    "collaboration_usage", {}
                                ).get("model_calls"),
                                "reused_subtasks": repaired.trace_metadata.get(
                                    "reused_subtasks", []
                                ),
                            }
                            check = report["repair_execution_check"]
                            passed = (
                                passed
                                and repaired.status is AgentResultStatus.SUCCESS
                                and check["answer_correct"]
                                and check["separate_run"]
                                and check["replay_same_run"]
                                and check["model_calls"] == check["replay_model_calls"]
                            )
                        replay = app._process_chat_turn_with_id(
                            user_input, "live-p1-smoke", resume=True
                        )
                        graphs = app.coordination_repository.list_task_graphs(
                            result.task_id
                        )
                        graph = next(
                            row
                            for row in graphs
                            if row["run_id"] == result.agent_run_id
                        )
                        graph_check = {
                            "artifact_found": True,
                            "stale_replay_blocked": bool(
                                replay.error_info
                                and replay.error_info.code
                                == "COLLABORATION_STALE_REPLAY"
                                and replay.final_response is None
                            ),
                            "node_statuses": [
                                node["status"] for node in graph["nodes"]
                            ],
                        }
                report["artifact_revocation_check"] = graph_check
                passed = (
                    passed
                    and graph_check["artifact_found"]
                    and graph_check["stale_replay_blocked"]
                    and graph_check["node_statuses"] == ["STALE"]
                )
            report.update(
                result=asdict(result),
                receipts=receipts,
                engineering_assertions_passed=passed,
                user_messages=sum(
                    message.role.value == "USER"
                    for message in app.conversation_repo.list_messages(
                        app.active_session_id
                    )
                ),
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(
                json.dumps(
                    {
                        "status": result.status.value,
                        "passed": passed,
                        "model_calls": result.trace_metadata.get(
                            "collaboration_usage", {}
                        ).get("model_calls"),
                        "output": str(args.output),
                    },
                    ensure_ascii=True,
                )
            )
        finally:
            app.close()
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
