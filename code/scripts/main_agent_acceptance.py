#!/usr/bin/env python3
"""100% deterministic test Acceptance Test Suite for Main Agent (Phase 4B).

Validates end-to-end execution security, durable journal recovery, tool dispatch idempotency,
confirmation boundaries, zero Task 3 AGENT event writes, and error propagation contracts.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from typing import Any, Dict, List, Tuple

# Ensure repository root is in Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.memory.agent import (
    ActionConfirmation,
    AgentAction,
    AgentActionKind,
    AgentExecutionJournal,
    AgentLoopConfig,
    AgentRequest,
    AgentResultStatus,
    ConfirmationDecision,
    IdempotencySupport,
    MainAgent,
    PendingAction,
    ToolEffect,
    ToolExecutionRequest,
    ToolRegistry,
    ToolSpec,
    derive_stable_identities,
)
from src.memory.core.config import MemoryConfig, ProviderMode
from src.memory.retrieval.evidence import EvidenceBundle, EvidenceSufficiency, EvidenceSufficiencyStatus
from src.memory.retrieval.facade import MemoryRetrievalFacade
from src.memory.runtime.agent_runtime import AgentRuntime
from src.memory.runtime.models import ToolCallResult, ToolCallStatus, TurnRequest
from src.memory.task_state.models import TaskEvent, TaskStateProposalBatch
from src.memory.task_state.repository import TaskStateRepository
from src.memory.task_state.service import TaskStateService
from src.memory.task_state.updater import TaskStateInterpreter


class FakeRetrievalFacade(MemoryRetrievalFacade):
    def retrieve_evidence(
        self, query: str, *, top_k: int = 8, query_id: str = "current-query", recent_context: Tuple[Any, ...] = ()
    ) -> EvidenceBundle:
        return EvidenceBundle(
            schema_version="v1",
            request_id=f"bundle_{query_id}",
            query=query,
            selected=(),
            sufficiency=EvidenceSufficiency(status=EvidenceSufficiencyStatus.SUFFICIENT),
        )


class FakeTaskInterpreter(TaskStateInterpreter):
    def interpret(self, event: TaskEvent, context: Any) -> TaskStateProposalBatch:
        return TaskStateProposalBatch(
            task_id=event.task_id,
            event_id=event.event_id,
            proposals=(),
        )


class ScriptFakeReasoner:
    def __init__(self, actions: List[AgentAction]) -> None:
        self.actions = list(actions)
        self.calls: List[Any] = []

    def reason(self, input_data: Any) -> AgentAction:
        self.calls.append(input_data)
        if not self.actions:
            return AgentAction(action_kind=AgentActionKind.FINAL_RESPONSE, content="Default script response")
        return self.actions.pop(0)


class ScriptMockToolExecutor:
    def __init__(self, return_content: str = "Script Tool Result") -> None:
        self.execution_count = 0
        self.last_request: Any = None
        self.return_content = return_content

    def execute(self, request: ToolExecutionRequest) -> ToolCallResult:
        self.execution_count += 1
        self.last_request = request
        return ToolCallResult(
            tool_call_id=request.tool_call_id,
            tool_name=request.tool_name,
            arguments=request.arguments,
            status=ToolCallStatus.SUCCESS,
            result_content=self.return_content,
        )


def run_acceptance_suite() -> bool:
    print("=" * 60)
    print("MAIN AGENT ACCEPTANCE TEST SUITE")
    print("=" * 60)

    results: Dict[str, str] = {}

    with tempfile.TemporaryDirectory() as tmpdir:
        t3_path = os.path.join(tmpdir, "t3_task.db")
        journal_path = os.path.join(tmpdir, "agent_journal.db")

        t3_repo = TaskStateRepository(t3_path)
        t3_service = TaskStateService(t3_repo)
        t3_interpreter = FakeTaskInterpreter()
        t2_facade = FakeRetrievalFacade()

        runtime = AgentRuntime(
            task_service=t3_service,
            retrieval_facade=t2_facade,
            task_interpreter=t3_interpreter,
        )
        journal = AgentExecutionJournal(journal_path)

        # --- Check 1: Stable Identities (SHA256, Cross-process) ---
        ids1 = derive_stable_identities("t_root_1", "task_01", step_index=0, tool_name="search", tool_arguments={"k": "v"})
        ids2 = derive_stable_identities("t_root_1", "task_01", step_index=0, tool_name="search", tool_arguments={"k": "v"})
        if ids1["agent_run_id"] == ids2["agent_run_id"] and ids1["tool_call_id"] == ids2["tool_call_id"]:
            results["Stable Identities (SHA256)"] = "PASS"
        else:
            results["Stable Identities (SHA256)"] = "FAIL"

        # --- Check 2: No-Tool Direct Final Answer ---
        registry2 = ToolRegistry()
        reasoner2 = ScriptFakeReasoner([AgentAction(action_kind=AgentActionKind.FINAL_RESPONSE, content="这是无需工具的回答。")])
        agent2 = MainAgent(runtime, journal, registry2, reasoner2)
        res2 = agent2.run(AgentRequest(turn_request=TurnRequest(turn_id="turn_acc_01", user_input="你好")))
        if res2.status == AgentResultStatus.SUCCESS and res2.final_response == "这是无需工具的回答。":
            results["No-Tool Final Response"] = "PASS"
        else:
            results["No-Tool Final Response"] = "FAIL"

        # --- Check 3: Read-Only Tool Execution Loop ---
        registry3 = ToolRegistry()
        exec3 = ScriptMockToolExecutor("天气良好，25度。")
        registry3.register(
            ToolSpec(name="get_weather", description="获取天气", parameters_schema={"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}, effect=ToolEffect.READ_ONLY),
            exec3,
        )
        reasoner3 = ScriptFakeReasoner([
            AgentAction(action_kind=AgentActionKind.TOOL_CALL, tool_name="get_weather", tool_arguments={"city": "Beijing"}),
            AgentAction(action_kind=AgentActionKind.FINAL_RESPONSE, content="北京天气良好，25度。"),
        ])
        agent3 = MainAgent(runtime, journal, registry3, reasoner3)
        res3 = agent3.run(AgentRequest(turn_request=TurnRequest(turn_id="turn_acc_02", user_input="查询北京天气")))
        if res3.status == AgentResultStatus.SUCCESS and exec3.execution_count == 1:
            results["Read-Only Tool Execution Loop"] = "PASS"
        else:
            results["Read-Only Tool Execution Loop"] = "FAIL"

        # --- Check 4: Side-Effect Confirmation Boundary ---
        registry4 = ToolRegistry()
        exec4 = ScriptMockToolExecutor("Paid")
        registry4.register(
            ToolSpec(name="pay_invoice", description="支付账单", parameters_schema={"type": "object", "properties": {"amount": {"type": "integer"}}, "required": ["amount"]}, effect=ToolEffect.SIDE_EFFECT),
            exec4,
        )
        reasoner4 = ScriptFakeReasoner([
            AgentAction(action_kind=AgentActionKind.TOOL_CALL, tool_name="pay_invoice", tool_arguments={"amount": 100}),
        ])
        agent4 = MainAgent(runtime, journal, registry4, reasoner4)
        res4 = agent4.run(AgentRequest(turn_request=TurnRequest(turn_id="turn_acc_03", user_input="支付 100 元")))
        if res4.status == AgentResultStatus.AWAITING_CONFIRMATION and res4.pending_action is not None and exec4.execution_count == 0:
            results["Side-Effect Confirmation Halt"] = "PASS"
            pending_action_id = res4.pending_action.pending_action_id
        else:
            results["Side-Effect Confirmation Halt"] = "FAIL"
            pending_action_id = ""

        # --- Check 5: Confirmed Pending Action Execution ---
        if pending_action_id:
            reasoner5 = ScriptFakeReasoner([
                AgentAction(action_kind=AgentActionKind.FINAL_RESPONSE, content="账单支付成功！"),
            ])
            agent5 = MainAgent(runtime, journal, registry4, reasoner5)
            res5 = agent5.run(
                AgentRequest(
                    turn_request=TurnRequest(turn_id="turn_acc_04", user_input="确认支付"),
                    confirmation=ActionConfirmation(pending_action_id=pending_action_id, decision=ConfirmationDecision.CONFIRM),
                )
            )
            if res5.status == AgentResultStatus.SUCCESS and exec4.execution_count == 1:
                results["Confirmed Pending Action Execution"] = "PASS"
            else:
                results["Confirmed Pending Action Execution"] = "FAIL"

        # --- Check 6: Multi-Task Confirmation Association ---
        t3_service.create_task("task_hotel")
        t3_service.create_task("task_flight")
        ids_m = derive_stable_identities("turn_multi_acc", "task_hotel", step_index=0, tool_name="pay_invoice", tool_arguments={"amount": 50})
        journal.register_run(ids_m["agent_run_id"], "turn_multi_acc", "task_hotel")
        pending_multi = PendingAction(
            pending_action_id="pending_multi_acc",
            agent_run_id=ids_m["agent_run_id"],
            task_id="task_hotel",
            agent_step_id=ids_m["agent_step_id"],
            tool_call_id=ids_m["tool_call_id"],
            tool_name="pay_invoice",
            validated_arguments={"amount": 50},
            arguments_hash=ids_m["arguments_hash"],
            idempotency_key=ids_m["idempotency_key"],
            effect="SIDE_EFFECT",
        )
        journal.add_pending_action(pending_multi)

        reasoner6 = ScriptFakeReasoner([AgentAction(action_kind=AgentActionKind.FINAL_RESPONSE, content="酒店账单支付完成")])
        agent6 = MainAgent(runtime, journal, registry4, reasoner6)
        res6 = agent6.run(
            AgentRequest(
                turn_request=TurnRequest(turn_id="turn_acc_05", user_input="确认"),
                confirmation=ActionConfirmation(pending_action_id="pending_multi_acc", decision=ConfirmationDecision.CONFIRM),
            )
        )
        if res6.status == AgentResultStatus.SUCCESS and res6.task_id == "task_hotel":
            results["Multi-Task Confirmation Resolution"] = "PASS"
        else:
            results["Multi-Task Confirmation Resolution"] = "FAIL"

        # --- Check 7: Atomic Execution Claim Concurrency ---
        journal.register_run("run_claim_acc", "turn_claim_acc", "task_claim_acc")
        claim1, s1, _ = journal.claim_execution("run_claim_acc", "step_0", "call_c1", "tool_x", "hash_x", "idemp_claim_acc", arguments_json="{}", tool_effect="READ_ONLY", idempotency_support="NONE")
        claim2, s2, _ = journal.claim_execution("run_claim_acc", "step_0", "call_c1", "tool_x", "hash_x", "idemp_claim_acc", arguments_json="{}", tool_effect="READ_ONLY", idempotency_support="NONE")
        if claim1 is True and claim2 is False and s2 == "DISPATCHING":
            results["Atomic Execution Claim (IMMEDIATE)"] = "PASS"
        else:
            results["Atomic Execution Claim (IMMEDIATE)"] = "FAIL"

        # --- Check 8: Unknown-Outcome Post-Dispatch Crash ---
        t3_service.create_task("task_crash_acc")
        ids_c = derive_stable_identities("turn_crash_acc", "task_crash_acc", step_index=0, tool_name="risky_tool", tool_arguments={})
        journal.register_run(ids_c["agent_run_id"], "turn_crash_acc", "task_crash_acc")
        journal.claim_execution(ids_c["agent_run_id"], ids_c["agent_step_id"], ids_c["tool_call_id"], "risky_tool", ids_c["arguments_hash"], ids_c["idempotency_key"], arguments_json="{}", tool_effect="READ_ONLY", idempotency_support="NONE")

        exec_risky = ScriptMockToolExecutor()
        registry8 = ToolRegistry()
        registry8.register(
            ToolSpec(name="risky_tool", description="Risky tool", parameters_schema={"type": "object"}, effect=ToolEffect.READ_ONLY, idempotency_support=IdempotencySupport.NONE),
            exec_risky,
        )
        reasoner8 = ScriptFakeReasoner([AgentAction(action_kind=AgentActionKind.TOOL_CALL, tool_name="risky_tool", tool_arguments={})])
        agent8 = MainAgent(runtime, journal, registry8, reasoner8)
        res8 = agent8.run(AgentRequest(turn_request=TurnRequest(turn_id="turn_crash_acc", user_input="重试", task_id="task_crash_acc")))
        if res8.status == AgentResultStatus.EXECUTION_STATUS_UNKNOWN and exec_risky.execution_count == 0:
            results["Unknown-Outcome Handling (No Auto Re-dispatch)"] = "PASS"
        else:
            results["Unknown-Outcome Handling (No Auto Re-dispatch)"] = "FAIL"

        # --- Check 9: Task 3 AGENT Event Writes (MUST BE ZERO) ---
        conn = t3_service.repository._connect()
        agent_events = conn.execute("SELECT * FROM task_events WHERE source_kind='AGENT'").fetchall()
        conn.close()
        if len(agent_events) == 0:
            results["Zero Task 3 AGENT Event Writes"] = "PASS"
        else:
            results["Zero Task 3 AGENT Event Writes"] = f"FAIL ({len(agent_events)} writes)"

        # --- Check 10: Bounded Step Limit Guard ---
        registry10 = ToolRegistry()
        exec10 = ScriptMockToolExecutor("Looping")
        registry10.register(
            ToolSpec(
                name="loop_tool",
                description="Looping tool",
                parameters_schema={
                    "type": "object",
                    "properties": {"iteration": {"type": "integer"}},
                    "required": ["iteration"],
                },
                effect=ToolEffect.READ_ONLY,
            ),
            exec10,
        )
        # Vary arguments so this remains a genuine step-limit test rather than
        # exercising duplicate read-only call suppression.
        infinite_actions = [
            AgentAction(
                action_kind=AgentActionKind.TOOL_CALL,
                tool_name="loop_tool",
                tool_arguments={"iteration": iteration},
            )
            for iteration in range(10)
        ]
        reasoner10 = ScriptFakeReasoner(infinite_actions)
        agent10 = MainAgent(runtime, journal, registry10, reasoner10, config=AgentLoopConfig(max_steps=3))
        res10 = agent10.run(AgentRequest(turn_request=TurnRequest(turn_id="turn_loop_acc", user_input="循环测试", task_id="task_hotel")))
        if res10.status == AgentResultStatus.STEP_LIMIT_REACHED and res10.step_count == 3:
            results["Bounded Step Limit Guard"] = "PASS"
        else:
            results["Bounded Step Limit Guard"] = "FAIL"

        # --- Check 11: Root/Request Turn Identity Distinction ---
        t3_service.create_task("task_root_diff")
        book_exec11 = ScriptMockToolExecutor("Booked")
        reg11 = ToolRegistry()
        reg11.register(
            ToolSpec(name="hotel_book", description="Book hotel", parameters_schema={"type": "object", "properties": {"id": {"type": "string"}}}, effect=ToolEffect.SIDE_EFFECT),
            book_exec11,
        )
        agent11_1 = MainAgent(runtime, journal, reg11, ScriptFakeReasoner([AgentAction(action_kind=AgentActionKind.TOOL_CALL, tool_name="hotel_book", tool_arguments={"id": "h1"})]))
        res11_1 = agent11_1.run(AgentRequest(turn_request=TurnRequest(turn_id="turn_root_orig", user_input="预订", task_id="task_root_diff")))

        agent11_2 = MainAgent(runtime, journal, reg11, ScriptFakeReasoner([AgentAction(action_kind=AgentActionKind.FINAL_RESPONSE, content="完成")]))
        res11_2 = agent11_2.run(
            AgentRequest(
                turn_request=TurnRequest(turn_id="turn_confirm_req", user_input="确认"),
                confirmation=ActionConfirmation(pending_action_id=res11_1.pending_action.pending_action_id, decision=ConfirmationDecision.CONFIRM),
            )
        )
        if res11_2.root_turn_id == "turn_root_orig" and res11_2.request_turn_id == "turn_confirm_req":
            results["Root/Request Turn Identity Distinction"] = "PASS"
        else:
            results["Root/Request Turn Identity Distinction"] = "FAIL"

        # --- Check 12: Terminal Receipt Runtime Reconciliation ---
        t3_service.create_task("task_recon")
        ids_rec = derive_stable_identities("turn_rec_01", "task_recon", step_index=0, tool_name="hotel_book", tool_arguments={"id": "h1"})
        journal.register_run(ids_rec["agent_run_id"], "turn_rec_01", "task_recon")
        journal.claim_execution(ids_rec["agent_run_id"], ids_rec["agent_step_id"], ids_rec["tool_call_id"], "hotel_book", ids_rec["arguments_hash"], ids_rec["idempotency_key"], arguments_json='{"id":"h1"}', tool_effect="SIDE_EFFECT", idempotency_support="NONE")
        journal.complete_execution(ids_rec["tool_call_id"], "SUCCEEDED", result_content="Reconciled Receipt")

        from src.memory.agent.loop import ToolExecutionIdentity
        restored_id_rec = ToolExecutionIdentity(
            agent_run_id=ids_rec["agent_run_id"],
            agent_step_id=ids_rec["agent_step_id"],
            tool_call_id=ids_rec["tool_call_id"],
            arguments_hash=ids_rec["arguments_hash"],
            idempotency_key=ids_rec["idempotency_key"],
        )

        turn_c_rec = runtime.process_user_turn(TurnRequest(turn_id="turn_rec_02", task_id="task_recon", user_input="重试")).context
        controller12 = MainAgent(runtime, journal, reg11, ScriptFakeReasoner([])).loop_controller
        _, obs12, _ = controller12._execute_tool_dispatch(turn_c_rec, ids_rec["agent_run_id"], 0, "hotel_book", {"id": "h1"}, restored_identity=restored_id_rec)

        conn12 = t3_service.repository._connect()
        found_rec_events = conn12.execute("SELECT * FROM task_events WHERE event_id=?", (f"tool:{ids_rec['tool_call_id']}:result",)).fetchall()
        conn12.close()

        if obs12.text == "Reconciled Receipt" and len(found_rec_events) == 1:
            results["Terminal Receipt Runtime Reconciliation"] = "PASS"
        else:
            results["Terminal Receipt Runtime Reconciliation"] = "FAIL"

        # --- Check 13: Fail-Closed Tool Effect & Registry Integrity ---
        try:
            ToolSpec(name="bad_tool", description="desc", parameters_schema={})
            effect_check = "FAIL"
        except TypeError:
            effect_check = "PASS"

        reg13 = ToolRegistry()
        reg13.register(ToolSpec(name="dup", description="d", parameters_schema={}, effect=ToolEffect.READ_ONLY), ScriptMockToolExecutor())
        try:
            reg13.register(ToolSpec(name="dup", description="d", parameters_schema={}, effect=ToolEffect.READ_ONLY), ScriptMockToolExecutor())
            dup_check = "FAIL"
        except ValueError:
            dup_check = "PASS"

        if effect_check == "PASS" and dup_check == "PASS":
            results["Fail-Closed Tool Effect & Registry Integrity"] = "PASS"
        else:
            results["Fail-Closed Tool Effect & Registry Integrity"] = "FAIL"

        # --- Check 14: Reconciliation Failure Preserves Observation ---
        t3_service.create_task("task_rf_acc")
        ids_rf = derive_stable_identities("turn_rf_acc", "task_rf_acc", step_index=0, tool_name="hotel_book", tool_arguments={"id": "1"})
        journal.register_run(ids_rf["agent_run_id"], "turn_rf_acc", "task_rf_acc")
        journal.claim_execution(ids_rf["agent_run_id"], ids_rf["agent_step_id"], ids_rf["tool_call_id"], "hotel_book", ids_rf["arguments_hash"], ids_rf["idempotency_key"], arguments_json='{"id":"1"}', tool_effect="SIDE_EFFECT", idempotency_support="NONE")
        journal.complete_execution(ids_rf["tool_call_id"], "SUCCEEDED", result_content="Physical Result")

        class AccFailingInterpreter:
            identity = "acc_failing"
            version = "v1"
            def interpret(self, event, context):
                raise RuntimeError("Ingestion failed")

        old_interpreter = runtime.task_interpreter
        runtime.task_interpreter = AccFailingInterpreter()
        reasoner14 = ScriptFakeReasoner([AgentAction(action_kind=AgentActionKind.FINAL_RESPONSE, content="Unreachable")])
        agent14 = MainAgent(runtime, journal, reg11, reasoner14)
        res14 = agent14.run(AgentRequest(turn_request=TurnRequest(turn_id="turn_rf_acc", task_id="task_rf_acc", user_input="继续")))
        if res14.status == AgentResultStatus.SUCCESS and len(reasoner14.calls) == 1:
            results["Reconciliation Failure Preserves Observation"] = "PASS"
        else:
            results["Reconciliation Failure Preserves Observation"] = "FAIL"

        # Reset runtime interpreter
        runtime.task_interpreter = old_interpreter

        # --- Check 15: Reconciliation Retry Without Physical Re-execution ---
        t3_service.create_task("task_rr_acc")
        exec15 = ScriptMockToolExecutor("Physical Result")
        reg15 = ToolRegistry()
        reg15.register(ToolSpec(name="hotel_book", description="Book", parameters_schema={"type": "object", "properties": {"id": {"type": "string"}}}, effect=ToolEffect.SIDE_EFFECT), exec15)
        ids_rr = derive_stable_identities("turn_rr_acc", "task_rr_acc", step_index=0, tool_name="hotel_book", tool_arguments={"id": "1"})
        journal.register_run(ids_rr["agent_run_id"], "turn_rr_acc", "task_rr_acc")
        journal.claim_execution(ids_rr["agent_run_id"], ids_rr["agent_step_id"], ids_rr["tool_call_id"], "hotel_book", ids_rr["arguments_hash"], ids_rr["idempotency_key"], arguments_json='{"id":"1"}', tool_effect="SIDE_EFFECT", idempotency_support="NONE")
        journal.complete_execution(ids_rr["tool_call_id"], "SUCCEEDED", result_content="Physical Result")

        reasoner15 = ScriptFakeReasoner([AgentAction(action_kind=AgentActionKind.FINAL_RESPONSE, content="OK")])
        agent15 = MainAgent(runtime, journal, reg15, reasoner15)
        res15 = agent15.run(AgentRequest(turn_request=TurnRequest(turn_id="turn_rr_acc", task_id="task_rr_acc", user_input="继续")))
        if res15.status == AgentResultStatus.SUCCESS and exec15.execution_count == 0 and len(reasoner15.calls) == 1:
            results["Reconciliation Retry Without Physical Re-execution"] = "PASS"
        else:
            results["Reconciliation Retry Without Physical Re-execution"] = "FAIL"

        # --- Check 16: Legacy Multi-Pending Identity Migration ---
        import tempfile as tf
        with tf.TemporaryDirectory() as tmp_m_dir:
            legacy_db = os.path.join(tmp_m_dir, "legacy_m.db")
            conn_m = sqlite3.connect(legacy_db)
            conn_m.execute("""CREATE TABLE agent_runs (agent_run_id TEXT PRIMARY KEY, root_turn_id TEXT NOT NULL, task_id TEXT NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL)""")
            conn_m.execute("""CREATE TABLE pending_actions (pending_action_id TEXT PRIMARY KEY, agent_run_id TEXT NOT NULL REFERENCES agent_runs(agent_run_id), task_id TEXT NOT NULL, tool_call_id TEXT NOT NULL, tool_name TEXT NOT NULL, arguments_json TEXT NOT NULL, effect TEXT NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL)""")
            conn_m.execute("""CREATE TABLE tool_execution_receipts (tool_call_id TEXT PRIMARY KEY, agent_run_id TEXT NOT NULL REFERENCES agent_runs(agent_run_id), tool_name TEXT NOT NULL, status TEXT NOT NULL, result_json TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL)""")
            conn_m.execute("PRAGMA user_version = 1")
            conn_m.execute("INSERT INTO agent_runs VALUES ('run_m', 'turn_m', 'task_m', 'RUNNING', 1.0, 1.0)")
            conn_m.execute("INSERT INTO pending_actions VALUES ('pa_1', 'run_m', 'task_m', 'call_A', 'pay', '{\"val\":1}', 'SIDE_EFFECT', 'PENDING', 1.0)")
            conn_m.execute("INSERT INTO pending_actions VALUES ('pa_2', 'run_m', 'task_m', 'call_B', 'pay', '{\"val\":1}', 'SIDE_EFFECT', 'PENDING', 1.0)")
            conn_m.commit()
            conn_m.close()

            journal_m = AgentExecutionJournal(legacy_db)
            p1_m = journal_m.get_pending_action("pa_1")
            p2_m = journal_m.get_pending_action("pa_2")
            if p1_m.agent_step_id != p2_m.agent_step_id and p1_m.idempotency_key != p2_m.idempotency_key:
                results["Legacy Multi-Pending Identity Migration"] = "PASS"
            else:
                results["Legacy Multi-Pending Identity Migration"] = "FAIL"

        # --- Check 17: Execution Policy Snapshot Recovery ---
        t3_service.create_task("task_ps_acc")
        ids_ps = derive_stable_identities("turn_ps_acc", "task_ps_acc", step_index=0, tool_name="hotel_book", tool_arguments={"id": "1"})
        journal.register_run(ids_ps["agent_run_id"], "turn_ps_acc", "task_ps_acc")
        journal.claim_execution(
            ids_ps["agent_run_id"], ids_ps["agent_step_id"], ids_ps["tool_call_id"], "hotel_book", ids_ps["arguments_hash"], ids_ps["idempotency_key"],
            arguments_json='{"id": "1"}', tool_effect="SIDE_EFFECT", idempotency_support="NONE"
        )
        reg17 = ToolRegistry()
        reg17.register(ToolSpec(name="hotel_book", description="Book", parameters_schema={"type": "object"}, effect=ToolEffect.READ_ONLY, idempotency_support=IdempotencySupport.NONE), ScriptMockToolExecutor())
        agent17 = MainAgent(runtime, journal, reg17, ScriptFakeReasoner([AgentAction(action_kind=AgentActionKind.TOOL_CALL, tool_name="hotel_book", tool_arguments={"id": "1"})]))
        res17 = agent17.run(AgentRequest(turn_request=TurnRequest(turn_id="turn_ps_acc", task_id="task_ps_acc", user_input="重试")))
        if res17.status == AgentResultStatus.FAILED and res17.error_info.code == "CONFIGURATION_MISMATCH":
            results["Execution Policy Snapshot Recovery"] = "PASS"
        else:
            results["Execution Policy Snapshot Recovery"] = "FAIL"

    # Summary Output
    all_passed = True
    for label, status in results.items():
        print(f"{label:<55}: {status}")
        if status != "PASS":
            all_passed = False

    print("=" * 60)
    if all_passed:
        print("ALL MAIN AGENT ACCEPTANCE CHECKS PASSED.")
        print("=" * 60)
        return True
    else:
        print("SOME MAIN AGENT ACCEPTANCE CHECKS FAILED.")
        print("=" * 60)
        return False


if __name__ == "__main__":
    success = run_acceptance_suite()
    sys.exit(0 if success else 1)
