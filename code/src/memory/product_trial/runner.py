"""Runner for Autonomous Personal Agent Product Trial Harness."""

from __future__ import annotations

import json
import random
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.memory.app.console import PersonalAgentApp
from src.memory.core.config import MemoryConfig, ProviderMode
from src.memory.llm.llm_client import LLMClientBase
from src.memory.agent.models import AgentResult, AgentResultStatus, ConfirmationDecision
from src.memory.product_trial.assertions import HardOracleEvaluator
from src.memory.product_trial.models import (
    ScenarioResult,
    StepResult,
    TrialActionKind,
    TrialScenario,
)
from src.memory.product_trial.observer import ProductTrialObserver
from src.memory.product_trial.scenarios import get_all_scenarios


class DeterministicTrialLLMClient(LLMClientBase):
    """Deterministic deterministic test mock LLM client for harness execution without network dependencies."""

    def __init__(self) -> None:
        self.recorded_prompts: List[Dict[str, str]] = []

    def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        response_schema: Dict[str, Any] | None = None,
        schema_name: str = "structured_output",
    ) -> Dict[str, Any]:
        self.recorded_prompts.append({"system_prompt": system_prompt, "user_prompt": user_prompt, "schema_name": schema_name})

        if schema_name == "task_binding_decision_v1":
            payload = json.loads(user_prompt)
            current_task_id = payload.get("current_task_id")
            if current_task_id:
                return {"action": "EXISTING", "task_id": current_task_id, "title": None}
            return {"action": "NEW", "task_id": None, "title": "试验任务"}

        if "partition them into semantically coherent" in system_prompt:
            return {"episodes": []}
        if "Prediction Error Auditor" in system_prompt:
            return {
                "predicted_content": "",
                "prediction_error_residual": "",
                "is_novel_info": False,
                "novelty_score": 0.0,
            }
        if ("task_state" in schema_name or "proposal" in schema_name) and schema_name != "explicit_proposal":
            return {"proposals": [], "warnings": []}
        if "selection" in schema_name:
            return {"schema_version": 1, "selected": [], "warnings": []}
        if "context" in schema_name:
            return {"schema_version": 1, "values": [], "warnings": []}
        if "scope" in schema_name:
            payload = json.loads(user_prompt)
            memory_id = payload["input"]["memory_id"]
            return {"schema_version": 1, "memory_id": memory_id, "rules": [], "warnings": []}

        # Task 1 Explicit Proposal Schema (Test Proposer Double)
        if schema_name == "explicit_proposal":
            import re
            target_id = None
            m = re.search(r"mem_[a-z0-9]{12}", user_prompt)
            if m:
                target_id = m.group(0)

            cand_text = user_prompt.split("Candidate Statement:\n")[-1].strip() if "Candidate Statement:\n" in user_prompt else user_prompt

            # Harness Scenarios
            if cand_text == "我不吃香菜" and target_id:
                return {"operation": "NOOP", "target_memory_id": target_id, "content": "", "reason": "already represented"}
            if cand_text == "我爱吃香菜" and target_id:
                return {"operation": "CORRECT", "target_memory_id": target_id, "content": "我爱吃香菜", "reason": "newer preference supersedes the old one"}
            if "机器人" in cand_text and target_id:
                return {"operation": "UPDATE", "target_memory_id": target_id, "content": "我喜欢Python，尤其喜欢机器人开发", "reason": "adds the user's domain detail"}

            # Unseen Domain Scenarios
            if "每天" in cand_text and target_id:
                return {"operation": "CORRECT", "target_memory_id": target_id, "content": "我现在每天都会喝咖啡", "reason": "newer preference supersedes the old one"}
            if "Neovim" in cand_text and target_id:
                return {"operation": "CORRECT", "target_memory_id": target_id, "content": "我现在主要使用 Neovim", "reason": "newer preference supersedes the old one"}
            if "macOS" in cand_text and target_id:
                return {"operation": "CORRECT", "target_memory_id": target_id, "content": "我现在主要用 macOS", "reason": "newer preference supersedes the old one"}
            if "牛奶我不喝" in cand_text and target_id:
                return {"operation": "NOOP", "target_memory_id": target_id, "content": "", "reason": "already represented"}
            if "系统工具" in cand_text and target_id:
                return {"operation": "UPDATE", "target_memory_id": target_id, "content": "我喜欢 Rust，尤其用于写系统工具", "reason": "adds the user's domain detail"}

            return {"operation": "ADD", "target_memory_id": None, "content": cand_text, "reason": "new independent user statement"}

        # Agent Action Schema
        if schema_name == "agent_action":
            # Tool Observation Response (Step 1 after Tool Execution)
            if "<tool_observation" in user_prompt or "TOOL OBSERVATIONS" in user_prompt:
                if "calculator" in user_prompt or "2 + 2" in user_prompt or "4.0" in user_prompt or "4" in user_prompt:
                    return {"action_kind": "FINAL_RESPONSE", "content": "计算结果是 4。"}
                if "create_file" in user_prompt:
                    return {"action_kind": "FINAL_RESPONSE", "content": "文件已成功写入。"}

            # Tool call matching (Step 0)
            if "算一下" in user_prompt or "2+2" in user_prompt or "calculator" in user_prompt:
                return {
                    "action_kind": "TOOL_CALL",
                    "tool_name": "calculator",
                    "tool_arguments": {"expression": "2 + 2"},
                }
            if "写文件" in user_prompt or "create_file" in user_prompt:
                return {
                    "action_kind": "TOOL_CALL",
                    "tool_name": "create_file",
                    "tool_arguments": {"filename": "test.txt", "content": "hello"},
                }

            # Response for previous question recall (C001)
            if "我刚刚问了什么" in user_prompt:
                return {
                    "action_kind": "FINAL_RESPONSE",
                    "content": "您刚才问了我：现在几点了。",
                }

            # Response for temp code (C002)
            if "代号是蓝鲸" in user_prompt:
                return {
                    "action_kind": "FINAL_RESPONSE",
                    "content": "好的，我已经知道您在本次对话中的临时代号是蓝鲸。",
                }
            if "代号是什么" in user_prompt or "临时代号" in user_prompt:
                if "蓝鲸" in user_prompt or "<recent_conversation>" in user_prompt and "蓝鲸" in user_prompt:
                    return {
                        "action_kind": "FINAL_RESPONSE",
                        "content": "根据我们刚才的对话，您在这场会话里的临时代号是蓝鲸。",
                    }
                return {
                    "action_kind": "FINAL_RESPONSE",
                    "content": "在这场全新的对话中，您尚未告诉我任何临时代号。",
                }

            # Default Final Response
            return {
                "action_kind": "FINAL_RESPONSE",
                "content": "你好！我是你的 Personal Agent 个人智能助理。",
            }

        return {"action_kind": "FINAL_RESPONSE", "content": "完成"}

    def generate_chat_json(self, messages: List[Dict[str, str]], enable_web_search: bool = False) -> Dict[str, Any]:
        return self.generate_json("", "")


class ProductTrialRunner:
    """Orchestrates scenario execution in 100% isolated temporary data directories."""

    def __init__(self, llm_client: Optional[LLMClientBase] = None) -> None:
        self.llm_client = llm_client or DeterministicTrialLLMClient()

    def _build_isolated_app(self, tmp_dir: str, cfg: MemoryConfig) -> PersonalAgentApp:
        workspace = Path(tmp_dir) / "workspace"
        workspace.mkdir(exist_ok=True)
        app = PersonalAgentApp(data_dir=tmp_dir, config=cfg, llm_client=self.llm_client)
        app.workspace.replace_scope([str(workspace)], str(workspace))
        return app

    def run_scenario(self, scenario: TrialScenario) -> ScenarioResult:
        with tempfile.TemporaryDirectory() as tmp_dir:
            cfg = MemoryConfig(
                sqlite_db_path=f"{tmp_dir}/memory.db",
                provider_mode=ProviderMode.REQUIRED,
            )
            app = self._build_isolated_app(tmp_dir, cfg)

            # Register mock SIDE_EFFECT tool for safety scenarios
            from src.memory.agent.tooling import ToolEffect, ToolExecutor, ToolSpec
            from src.memory.runtime.models import ToolCallResult, ToolCallStatus
            class MockSideEffectExecutor(ToolExecutor):
                def execute(self, request):
                    return ToolCallResult(
                        tool_call_id=request.tool_call_id,
                        tool_name=request.tool_name,
                        arguments=request.arguments,
                        status=ToolCallStatus.SUCCESS,
                        result_content="文件写入成功",
                    )
            if app.registry.get_spec("create_file") is None:
                app.registry.register(
                    ToolSpec(name="create_file", description="写文件", parameters_schema={"type": "object"}, effect=ToolEffect.SIDE_EFFECT),
                    MockSideEffectExecutor(),
                )

            step_results: List[StepResult] = []
            failed_reasons: List[str] = []
            last_result: Optional[AgentResult] = None
            last_session_id = app.active_session_id

            for idx, step in enumerate(scenario.steps):
                # 1. Execute Action
                if step.action_kind == TrialActionKind.CHAT:
                    last_result = app.process_chat_turn(step.input_text)

                elif step.action_kind == TrialActionKind.COMMAND:
                    cmd_text = step.input_text.strip()
                    if cmd_text == "/confirm":
                        last_result = app.process_confirmation(ConfirmationDecision.CONFIRM)
                    elif cmd_text == "/reject":
                        last_result = app.process_confirmation(ConfirmationDecision.REJECT)
                    elif cmd_text.startswith("/remember "):
                        text = cmd_text[10:].strip()
                        app.remember_explicit(text)
                        last_result = None
                elif step.action_kind == TrialActionKind.RESTART_APP:
                    last_session_id = app.active_session_id
                    app = self._build_isolated_app(tmp_dir, cfg)
                    last_result = None

                elif step.action_kind == TrialActionKind.RESUME_SESSION:
                    sid = step.input_text or last_session_id
                    app.resume_session(sid)
                    last_result = None

                elif step.action_kind == TrialActionKind.CREATE_SESSION:
                    sid = app.create_new_session(title=step.input_text or None)
                    last_session_id = sid
                    last_result = None

                elif step.action_kind == TrialActionKind.CREATE_TASK:
                    app.create_new_task(title=step.input_text or None)
                    last_result = None

                elif step.action_kind == TrialActionKind.SWITCH_TASK:
                    app.switch_task(step.input_text)
                    last_result = None

                # 2. Capture Observation & Check Invariants
                obs = ProductTrialObserver.capture_observation(app, last_result)
                global_violations = ProductTrialObserver.check_global_invariants(app)

                step_failed_reasons = []
                if last_result is not None and last_result.status not in {
                    AgentResultStatus.SUCCESS,
                    AgentResultStatus.AWAITING_CONFIRMATION,
                    AgentResultStatus.CANCELLED,
                }:
                    error_code = (
                        last_result.error_info.code
                        if last_result.error_info is not None
                        else "MISSING_ERROR_INFO"
                    )
                    step_failed_reasons.append(
                        f"Step {idx} unexpected AgentResult status "
                        f"{last_result.status.value} ({error_code})"
                    )
                # Check Global Invariants
                if global_violations:
                    step_failed_reasons.extend(global_violations)

                # Check Step Invariants
                for inv in step.expected_invariants:
                    passed, msg = HardOracleEvaluator.evaluate(inv, obs, global_violations)
                    if not passed:
                        step_failed_reasons.append(f"Step {idx} [{inv.kind.value}]: {msg}")

                step_passed = len(step_failed_reasons) == 0
                step_results.append(
                    StepResult(
                        step_index=idx,
                        action_kind=step.action_kind.value,
                        input_text=step.input_text,
                        passed=step_passed,
                        observation=obs,
                        failed_invariants=tuple(step_failed_reasons),
                    )
                )

                if not step_passed:
                    failed_reasons.extend(step_failed_reasons)

            overall_status = "PASS" if len(failed_reasons) == 0 else "FAIL"
            return ScenarioResult(
                scenario_id=scenario.scenario_id,
                category=scenario.category,
                description=scenario.description,
                status=overall_status,
                step_results=tuple(step_results),
                failed_reasons=tuple(failed_reasons),
            )

    def run_suite(self, category_filter: Optional[str] = None) -> List[ScenarioResult]:
        scenarios = get_all_scenarios()
        results = []
        for sc in scenarios:
            if category_filter and sc.category != category_filter:
                continue
            res = self.run_scenario(sc)
            results.append(res)
        return results

    def run_seeded_fuzz(self, seed: int = 42, steps_count: int = 50) -> Tuple[bool, List[str]]:
        """Executes deterministic seeded fuzzing over user actions and validates global invariants."""
        rnd = random.Random(seed)
        violations: List[str] = []

        with tempfile.TemporaryDirectory() as tmp_dir:
            cfg = MemoryConfig(
                sqlite_db_path=f"{tmp_dir}/memory.db",
                provider_mode=ProviderMode.REQUIRED,
            )
            app = self._build_isolated_app(tmp_dir, cfg)

            for step in range(steps_count):
                action_choice = rnd.choice([
                    "CHAT", "REMEMBER", "CREATE_SESSION", "CREATE_TASK", "SWITCH_TASK", "RESTART"
                ])

                if action_choice == "CHAT":
                    result = app.process_chat_turn(f"Fuzz test input {step}")
                    if result.status is not AgentResultStatus.SUCCESS:
                        code = result.error_info.code if result.error_info else "MISSING_ERROR_INFO"
                        violations.append(
                            f"Fuzz Step {step}: unexpected AgentResult status "
                            f"{result.status.value} ({code})"
                        )
                        break
                elif action_choice == "REMEMBER":
                    app.remember_explicit(f"Fuzz memory content {step}")
                elif action_choice == "CREATE_SESSION":
                    app.create_new_session(f"Session {step}")
                elif action_choice == "CREATE_TASK":
                    app.create_new_task(f"Task {step}")
                elif action_choice == "RESTART":
                    last_sid = app.active_session_id
                    app = self._build_isolated_app(tmp_dir, cfg)
                    app.resume_session(last_sid)

                # Check Global Invariants after every fuzz step
                step_violations = ProductTrialObserver.check_global_invariants(app)
                if step_violations:
                    violations.extend([f"Fuzz Step {step}: {v}" for v in step_violations])
                    break

        return (len(violations) == 0, violations)
