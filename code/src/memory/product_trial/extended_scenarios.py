"""Extended Scenarios for Autonomous Personal Agent Product Trial Harness.

Drop-in extension: place this file next to scenarios.py under
code/src/memory/product_trial/ and run via personal_agent_trial_extended.py.

All scenarios are designed to work with the stock DeterministicTrialLLMClient
(including its pre-seeded "Unseen Domain Scenarios" branches), so no changes
to runner.py are required.
"""

from __future__ import annotations

from typing import Tuple

from src.memory.product_trial.models import (
    HardInvariant,
    InvariantType,
    TrialActionKind,
    TrialScenario,
    TrialStep,
)


def get_extended_scenarios() -> Tuple[TrialScenario, ...]:
    """Returns the extended scenario suite (18 scenarios)."""
    return (
        # ================= MEMORY: WRITE & COUNT =================
        TrialScenario(
            scenario_id="M101",
            category="memory",
            description="Three independent memories accumulate correctly",
            steps=(
                TrialStep(action_kind=TrialActionKind.COMMAND, input_text="/remember 我不吃香菜"),
                TrialStep(action_kind=TrialActionKind.COMMAND, input_text="/remember 我的母语是中文"),
                TrialStep(
                    action_kind=TrialActionKind.COMMAND,
                    input_text="/remember 我住在天津",
                    expected_invariants=(
                        HardInvariant(InvariantType.MEMORY_COUNT, 3, "3 independent memory items"),
                        HardInvariant(InvariantType.CURRENT_MEMORY_COUNT, 3, "3 CURRENT revisions"),
                    ),
                ),
            ),
        ),
        TrialScenario(
            scenario_id="M102",
            category="memory",
            description="Preference correction: occasional coffee -> daily coffee (CORRECT)",
            steps=(
                TrialStep(action_kind=TrialActionKind.COMMAND, input_text="/remember 我偶尔喝咖啡"),
                TrialStep(
                    action_kind=TrialActionKind.COMMAND,
                    input_text="/remember 我现在每天都会喝咖啡",
                    expected_invariants=(
                        HardInvariant(InvariantType.MEMORY_COUNT, 1, "Correction keeps single memory item"),
                        HardInvariant(InvariantType.CURRENT_MEMORY_COUNT, 1, "Single CURRENT revision after CORRECT"),
                    ),
                ),
            ),
        ),
        TrialScenario(
            scenario_id="M103",
            category="memory",
            description="Tool preference migration: VSCode -> Neovim (CORRECT)",
            steps=(
                TrialStep(action_kind=TrialActionKind.COMMAND, input_text="/remember 我主要用 VSCode 写代码"),
                TrialStep(
                    action_kind=TrialActionKind.COMMAND,
                    input_text="/remember 我现在主要使用 Neovim",
                    expected_invariants=(
                        HardInvariant(InvariantType.CURRENT_MEMORY_COUNT, 1, "Single CURRENT editor preference"),
                    ),
                ),
            ),
        ),
        TrialScenario(
            scenario_id="M104",
            category="memory",
            description="OS preference migration: Windows -> macOS (CORRECT)",
            steps=(
                TrialStep(action_kind=TrialActionKind.COMMAND, input_text="/remember 我用 Windows 办公"),
                TrialStep(
                    action_kind=TrialActionKind.COMMAND,
                    input_text="/remember 我现在主要用 macOS",
                    expected_invariants=(
                        HardInvariant(InvariantType.CURRENT_MEMORY_COUNT, 1, "Single CURRENT OS preference"),
                    ),
                ),
            ),
        ),
        TrialScenario(
            scenario_id="M105",
            category="memory",
            description="Paraphrase duplicate is idempotent (NOOP)",
            steps=(
                TrialStep(action_kind=TrialActionKind.COMMAND, input_text="/remember 我不喝牛奶"),
                TrialStep(
                    action_kind=TrialActionKind.COMMAND,
                    input_text="/remember 牛奶我不喝",
                    expected_invariants=(
                        HardInvariant(InvariantType.MEMORY_COUNT, 1, "Paraphrase does not duplicate memory"),
                        HardInvariant(InvariantType.CURRENT_MEMORY_COUNT, 1, "Single CURRENT revision maintained"),
                    ),
                ),
            ),
        ),
        TrialScenario(
            scenario_id="M106",
            category="memory",
            description="Detail enrichment: Rust -> Rust for system tools (UPDATE)",
            steps=(
                TrialStep(action_kind=TrialActionKind.COMMAND, input_text="/remember 我喜欢 Rust"),
                TrialStep(
                    action_kind=TrialActionKind.COMMAND,
                    input_text="/remember 我喜欢 Rust，尤其用于写系统工具",
                    expected_invariants=(
                        HardInvariant(InvariantType.MEMORY_COUNT, 1, "Enrichment keeps single memory item"),
                        HardInvariant(InvariantType.CURRENT_MEMORY_COUNT, 1, "Single CURRENT revision after UPDATE"),
                    ),
                ),
            ),
        ),
        TrialScenario(
            scenario_id="M107",
            category="memory",
            description="Correction then new unrelated memory keeps both invariants",
            steps=(
                TrialStep(action_kind=TrialActionKind.COMMAND, input_text="/remember 我不吃香菜"),
                TrialStep(action_kind=TrialActionKind.COMMAND, input_text="/remember 我爱吃香菜"),
                TrialStep(
                    action_kind=TrialActionKind.COMMAND,
                    input_text="/remember 我对花生过敏",
                    expected_invariants=(
                        HardInvariant(InvariantType.MEMORY_COUNT, 2, "Corrected item + new allergy item"),
                        HardInvariant(InvariantType.CURRENT_MEMORY_COUNT, 2, "Both memories CURRENT"),
                    ),
                ),
            ),
        ),
        TrialScenario(
            scenario_id="M108",
            category="memory",
            description="Transient turns do not fabricate long-term memories when the Agent skips its memory tool",
            steps=(
                TrialStep(action_kind=TrialActionKind.CHAT, input_text="今天开了一下午会，有点累"),
                TrialStep(
                    action_kind=TrialActionKind.CHAT,
                    input_text="晚饭随便吃点吧",
                    expected_invariants=(
                        HardInvariant(InvariantType.MEMORY_COUNT, 0, "Casual chat not promoted to long-term memory"),
                    ),
                ),
            ),
        ),

        # ================= CONVERSATION =================
        TrialScenario(
            scenario_id="C101",
            category="conversation",
            description="Multi-turn same-session recall across three exchanges",
            steps=(
                TrialStep(action_kind=TrialActionKind.CHAT, input_text="我的临时代号是蓝鲸"),
                TrialStep(action_kind=TrialActionKind.CHAT, input_text="今天天气不错"),
                TrialStep(
                    action_kind=TrialActionKind.CHAT,
                    input_text="我的临时代号是什么？",
                    expected_invariants=(
                        HardInvariant(InvariantType.CONTAINS_TEXT, "蓝鲸", "Recall survives intervening chit-chat"),
                    ),
                ),
            ),
        ),
        TrialScenario(
            scenario_id="C102",
            category="conversation",
            description="Restart without resume keeps new session clean",
            steps=(
                TrialStep(action_kind=TrialActionKind.CHAT, input_text="我的临时代号是蓝鲸"),
                TrialStep(action_kind=TrialActionKind.RESTART_APP),
                TrialStep(
                    action_kind=TrialActionKind.CHAT,
                    input_text="我的代号是什么？",
                    expected_invariants=(
                        HardInvariant(InvariantType.NOT_CONTAINS_TEXT, "蓝鲸", "No resume means no leakage"),
                    ),
                ),
            ),
        ),
        TrialScenario(
            scenario_id="C103",
            category="conversation",
            description="Three-session isolation chain",
            steps=(
                TrialStep(action_kind=TrialActionKind.CHAT, input_text="我的临时代号是蓝鲸"),
                TrialStep(action_kind=TrialActionKind.CREATE_SESSION, input_text="Session B"),
                TrialStep(action_kind=TrialActionKind.CREATE_SESSION, input_text="Session C"),
                TrialStep(
                    action_kind=TrialActionKind.CHAT,
                    input_text="我之前的临时代号是什么？",
                    expected_invariants=(
                        HardInvariant(InvariantType.NOT_CONTAINS_TEXT, "蓝鲸", "Session C isolated from Session A"),
                    ),
                ),
            ),
        ),

        # ================= SAFETY =================
        TrialScenario(
            scenario_id="S101",
            category="safety",
            description="Side-effect tool is NOT executed without explicit confirmation",
            steps=(
                TrialStep(
                    action_kind=TrialActionKind.CHAT,
                    input_text="请写文件 draft.txt 内容 hello",
                    expected_invariants=(
                        HardInvariant(InvariantType.EXECUTOR_COUNT, 0, "Zero side effects before /confirm"),
                    ),
                ),
            ),
        ),
        TrialScenario(
            scenario_id="S102",
            category="safety",
            description="Reject then re-request then confirm executes exactly once",
            steps=(
                TrialStep(action_kind=TrialActionKind.CHAT, input_text="请写文件 test.txt 内容 hello"),
                TrialStep(
                    action_kind=TrialActionKind.COMMAND,
                    input_text="/reject",
                    expected_invariants=(
                        HardInvariant(InvariantType.EXECUTOR_COUNT, 0, "Rejected: nothing executed"),
                    ),
                ),
                TrialStep(action_kind=TrialActionKind.CHAT, input_text="请写文件 test.txt 内容 hello"),
                TrialStep(
                    action_kind=TrialActionKind.COMMAND,
                    input_text="/confirm",
                    expected_invariants=(
                        HardInvariant(InvariantType.EXECUTOR_COUNT, 1, "Confirmed: executed exactly once"),
                    ),
                ),
            ),
        ),

        # ================= TASK & CROSS =================
        TrialScenario(
            scenario_id="T101",
            category="task",
            description="Task creation persists across restart and resume",
            steps=(
                TrialStep(action_kind=TrialActionKind.CREATE_TASK, input_text="写开题报告"),
                TrialStep(action_kind=TrialActionKind.RESTART_APP),
                TrialStep(
                    action_kind=TrialActionKind.RESUME_SESSION,
                    expected_invariants=(
                        HardInvariant(InvariantType.GLOBAL_INVARIANTS, None, "Global invariants hold after task+restart"),
                    ),
                ),
            ),
        ),
        TrialScenario(
            scenario_id="T102",
            category="task",
            description="Multiple tasks then restart keeps system consistent",
            steps=(
                TrialStep(action_kind=TrialActionKind.CREATE_TASK, input_text="任务甲"),
                TrialStep(action_kind=TrialActionKind.CREATE_TASK, input_text="任务乙"),
                TrialStep(action_kind=TrialActionKind.CREATE_TASK, input_text="任务丙"),
                TrialStep(action_kind=TrialActionKind.RESTART_APP),
                TrialStep(
                    action_kind=TrialActionKind.RESUME_SESSION,
                    expected_invariants=(
                        HardInvariant(InvariantType.GLOBAL_INVARIANTS, None, "Global invariants hold with 3 tasks"),
                    ),
                ),
            ),
        ),
        TrialScenario(
            scenario_id="X101",
            category="cross",
            description="Explicit memory survives restart (long-term persistence)",
            steps=(
                TrialStep(action_kind=TrialActionKind.COMMAND, input_text="/remember 我对花生过敏"),
                TrialStep(action_kind=TrialActionKind.RESTART_APP),
                TrialStep(
                    action_kind=TrialActionKind.RESUME_SESSION,
                    expected_invariants=(
                        HardInvariant(InvariantType.MEMORY_COUNT, 1, "Memory persists across restart"),
                        HardInvariant(InvariantType.CURRENT_MEMORY_COUNT, 1, "CURRENT revision persists"),
                    ),
                ),
            ),
        ),
        TrialScenario(
            scenario_id="X102",
            category="cross",
            description="Memory written in Session A does not duplicate in new Session B",
            steps=(
                TrialStep(action_kind=TrialActionKind.COMMAND, input_text="/remember 我不吃香菜"),
                TrialStep(action_kind=TrialActionKind.CREATE_SESSION, input_text="Session B"),
                TrialStep(
                    action_kind=TrialActionKind.CHAT,
                    input_text="我们重新开始聊",
                    expected_invariants=(
                        HardInvariant(InvariantType.MEMORY_COUNT, 1, "Global memory store unchanged by session switch"),
                    ),
                ),
            ),
        ),
        TrialScenario(
            scenario_id="X103",
            category="cross",
            description="Full lifecycle: remember + task + session + restart + resume",
            steps=(
                TrialStep(action_kind=TrialActionKind.COMMAND, input_text="/remember 我的研究方向是操作系统"),
                TrialStep(action_kind=TrialActionKind.CREATE_TASK, input_text="读 kernel fuzzing 综述"),
                TrialStep(action_kind=TrialActionKind.CHAT, input_text="帮我整理一下思路"),
                TrialStep(action_kind=TrialActionKind.RESTART_APP),
                TrialStep(
                    action_kind=TrialActionKind.RESUME_SESSION,
                    expected_invariants=(
                        HardInvariant(InvariantType.MEMORY_COUNT, 1, "Memory intact after full lifecycle"),
                        HardInvariant(InvariantType.GLOBAL_INVARIANTS, None, "Global invariants hold"),
                    ),
                ),
            ),
        ),
    )
