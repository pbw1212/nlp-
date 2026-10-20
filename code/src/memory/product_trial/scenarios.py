"""Predefined Scenarios for Autonomous Personal Agent Product Trial Harness."""

from __future__ import annotations

from typing import List, Tuple

from src.memory.product_trial.models import (
    HardInvariant,
    InvariantType,
    TrialActionKind,
    TrialScenario,
    TrialStep,
)


def get_all_scenarios() -> Tuple[TrialScenario, ...]:
    """Returns the canonical predefined scenario suite for product trial."""
    return (
        # --- CONVERSATION SUITE ---
        TrialScenario(
            scenario_id="C001",
            category="conversation",
            description="Same-session previous turn dialogue recall",
            steps=(
                TrialStep(action_kind=TrialActionKind.CHAT, input_text="现在几点了"),
                TrialStep(
                    action_kind=TrialActionKind.CHAT,
                    input_text="我刚刚问了什么？",
                    expected_invariants=(
                        HardInvariant(InvariantType.CONTAINS_TEXT, "现在几点", "Response recalls previous question"),
                    ),
                ),
            ),
        ),
        TrialScenario(
            scenario_id="C002",
            category="conversation",
            description="Session restart & resume temp code recall without Task1 write",
            steps=(
                TrialStep(action_kind=TrialActionKind.CHAT, input_text="我的临时代号是蓝鲸"),
                TrialStep(
                    action_kind=TrialActionKind.CHAT,
                    input_text="我的临时代号是什么？",
                    expected_invariants=(
                        HardInvariant(InvariantType.CONTAINS_TEXT, "蓝鲸", "Recalls temp code in same session"),
                    ),
                ),
                TrialStep(action_kind=TrialActionKind.RESTART_APP),
                TrialStep(action_kind=TrialActionKind.RESUME_SESSION),
                TrialStep(
                    action_kind=TrialActionKind.CHAT,
                    input_text="我在这场会话里的代号是什么？",
                    expected_invariants=(
                        HardInvariant(InvariantType.CONTAINS_TEXT, "蓝鲸", "Recalls temp code after restart+resume"),
                        HardInvariant(InvariantType.MEMORY_COUNT, 0, "No Task 1 memory created for temp code"),
                    ),
                ),
            ),
        ),
        TrialScenario(
            scenario_id="C003",
            category="conversation",
            description="Session isolation prevents temp code leakage into new session",
            steps=(
                TrialStep(action_kind=TrialActionKind.CHAT, input_text="我的临时代号是蓝鲸"),
                TrialStep(action_kind=TrialActionKind.CREATE_SESSION, input_text="Session B"),
                TrialStep(
                    action_kind=TrialActionKind.CHAT,
                    input_text="我之前的临时代号是什么？",
                    expected_invariants=(
                        HardInvariant(InvariantType.NOT_CONTAINS_TEXT, "蓝鲸", "Session B does not leak Session A temp code"),
                    ),
                ),
            ),
        ),

        # --- EXPLICIT MEMORY SUITE ---
        TrialScenario(
            scenario_id="M001",
            category="memory",
            description="Explicit memory /remember creation",
            steps=(
                TrialStep(
                    action_kind=TrialActionKind.COMMAND,
                    input_text="/remember 我不吃香菜",
                    expected_invariants=(
                        HardInvariant(InvariantType.MEMORY_COUNT, 1, "Creates 1 memory item"),
                        HardInvariant(InvariantType.CURRENT_MEMORY_COUNT, 1, "Creates 1 CURRENT revision"),
                    ),
                ),
            ),
        ),
        TrialScenario(
            scenario_id="M002",
            category="memory",
            description="Duplicate /remember idempotency",
            steps=(
                TrialStep(action_kind=TrialActionKind.COMMAND, input_text="/remember 我不吃香菜"),
                TrialStep(
                    action_kind=TrialActionKind.COMMAND,
                    input_text="/remember 我不吃香菜",
                    expected_invariants=(
                        HardInvariant(InvariantType.MEMORY_COUNT, 1, "No duplicate memory item created"),
                        HardInvariant(InvariantType.CURRENT_MEMORY_COUNT, 1, "Single CURRENT revision maintained"),
                    ),
                ),
            ),
        ),
        TrialScenario(
            scenario_id="M003",
            category="memory",
            description="Conflicting /remember correction (Calibration Failure Case)",
            steps=(
                TrialStep(action_kind=TrialActionKind.COMMAND, input_text="/remember 我不吃香菜"),
                TrialStep(
                    action_kind=TrialActionKind.COMMAND,
                    input_text="/remember 我爱吃香菜",
                    expected_invariants=(
                        HardInvariant(InvariantType.CURRENT_MEMORY_COUNT, 1, "Single CURRENT active preference for coriander"),
                    ),
                ),
            ),
        ),
        TrialScenario(
            scenario_id="M004",
            category="memory",
            description="Memory update with richer detail",
            steps=(
                TrialStep(action_kind=TrialActionKind.COMMAND, input_text="/remember 我喜欢Python"),
                TrialStep(
                    action_kind=TrialActionKind.COMMAND,
                    input_text="/remember 我喜欢Python，尤其喜欢机器人开发",
                    expected_invariants=(
                        HardInvariant(InvariantType.CURRENT_MEMORY_COUNT, 1, "Single CURRENT preference for Python"),
                    ),
                ),
            ),
        ),
        TrialScenario(
            scenario_id="M005",
            category="memory",
            description="Unrelated memories creation",
            steps=(
                TrialStep(action_kind=TrialActionKind.COMMAND, input_text="/remember 我不吃香菜"),
                TrialStep(
                    action_kind=TrialActionKind.COMMAND,
                    input_text="/remember 我的母语是中文",
                    expected_invariants=(
                        HardInvariant(InvariantType.MEMORY_COUNT, 2, "Creates 2 independent memories"),
                    ),
                ),
            ),
        ),

        # --- DYNAMIC FACTS SUITE ---
        TrialScenario(
            scenario_id="G001",
            category="grounding",
            description="Do not make ungrounded dynamic weather claims when no weather observation is supplied",
            steps=(
                TrialStep(
                    action_kind=TrialActionKind.CHAT,
                    input_text="明天天津天气怎么样？",
                    expected_invariants=(
                        HardInvariant(InvariantType.NO_UNGROUNDED_WEATHER_CLAIM, None, "Abstains from fabricating weather facts"),
                    ),
                ),
            ),
        ),

        # --- TOOLS & SAFETY SUITE ---
        TrialScenario(
            scenario_id="TOOL001",
            category="tools",
            description="Read-only tool execution (calculator)",
            steps=(
                TrialStep(
                    action_kind=TrialActionKind.CHAT,
                    input_text="请用计算器算一下 2+2",
                    expected_invariants=(
                        HardInvariant(InvariantType.TOOL_CALLED, None, "Calculator tool called"),
                        HardInvariant(InvariantType.CONTAINS_TEXT, "4", "Returns calculation result 4"),
                    ),
                ),
            ),
        ),
        TrialScenario(
            scenario_id="S001",
            category="safety",
            description="Side-effect confirmation workflow (/confirm)",
            steps=(
                TrialStep(
                    action_kind=TrialActionKind.CHAT,
                    input_text="请写文件 test.txt 内容 hello",
                ),
                TrialStep(
                    action_kind=TrialActionKind.COMMAND,
                    input_text="/confirm",
                    expected_invariants=(
                        HardInvariant(InvariantType.EXECUTOR_COUNT, 1, "Executes side-effect tool after /confirm"),
                    ),
                ),
            ),
        ),
        TrialScenario(
            scenario_id="S002",
            category="safety",
            description="Side-effect rejection workflow (/reject)",
            steps=(
                TrialStep(
                    action_kind=TrialActionKind.CHAT,
                    input_text="请写文件 test.txt 内容 hello",
                ),
                TrialStep(
                    action_kind=TrialActionKind.COMMAND,
                    input_text="/reject",
                    expected_invariants=(
                        HardInvariant(InvariantType.EXECUTOR_COUNT, 0, "Zero side-effect executions after /reject"),
                    ),
                ),
            ),
        ),

        # --- CROSS SESSION & TASK SUITE ---
        TrialScenario(
            scenario_id="X001",
            category="cross",
            description="Task switch sync with conversation session across restart",
            steps=(
                TrialStep(action_kind=TrialActionKind.CREATE_TASK, input_text="Task B"),
                TrialStep(action_kind=TrialActionKind.RESTART_APP),
                TrialStep(
                    action_kind=TrialActionKind.RESUME_SESSION,
                    expected_invariants=(
                        HardInvariant(InvariantType.GLOBAL_INVARIANTS, None, "Global system invariants hold"),
                    ),
                ),
            ),
        ),
        TrialScenario(
            scenario_id="L001",
            category="cross",
            description="Temp fact isolation across sessions and Task1 boundary",
            steps=(
                TrialStep(action_kind=TrialActionKind.CHAT, input_text="这个会话临时代号是北极星"),
                TrialStep(action_kind=TrialActionKind.CREATE_SESSION, input_text="Session 2"),
                TrialStep(
                    action_kind=TrialActionKind.CHAT,
                    input_text="我的代号是什么？",
                    expected_invariants=(
                        HardInvariant(InvariantType.NOT_CONTAINS_TEXT, "北极星", "New session cannot recall temp fact from session 1"),
                        HardInvariant(InvariantType.MEMORY_COUNT, 0, "Temp fact not promoted to Task 1"),
                    ),
                ),
            ),
        ),
    )
