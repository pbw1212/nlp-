"""Data models for Autonomous Personal Agent Product Trial Harness."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union


class TrialActionKind(str, Enum):
    CHAT = "CHAT"
    COMMAND = "COMMAND"
    RESTART_APP = "RESTART_APP"
    RESUME_SESSION = "RESUME_SESSION"
    CREATE_SESSION = "CREATE_SESSION"
    CREATE_TASK = "CREATE_TASK"
    SWITCH_TASK = "SWITCH_TASK"
    REMEMBER = "REMEMBER"
    MEMORY_AUTO_ON = "MEMORY_AUTO_ON"
    MEMORY_AUTO_OFF = "MEMORY_AUTO_OFF"
    CONFIRM = "CONFIRM"
    REJECT = "REJECT"


class InvariantType(str, Enum):
    CONTAINS_TEXT = "CONTAINS_TEXT"
    NOT_CONTAINS_TEXT = "NOT_CONTAINS_TEXT"
    RESULT_STATUS = "RESULT_STATUS"
    MEMORY_COUNT = "MEMORY_COUNT"
    CURRENT_MEMORY_COUNT = "CURRENT_MEMORY_COUNT"
    SESSION_ID_SAME = "SESSION_ID_SAME"
    SESSION_ID_DIFFERENT = "SESSION_ID_DIFFERENT"
    TOOL_CALLED = "TOOL_CALLED"
    TOOL_NOT_CALLED = "TOOL_NOT_CALLED"
    EXECUTOR_COUNT = "EXECUTOR_COUNT"
    WARNING_PRESENT = "WARNING_PRESENT"
    NO_UNGROUNDED_WEATHER_CLAIM = "NO_UNGROUNDED_WEATHER_CLAIM"
    GLOBAL_INVARIANTS = "GLOBAL_INVARIANTS"


@dataclass(frozen=True)
class HardInvariant:
    kind: InvariantType
    expected_value: Any = None
    description: str = ""


@dataclass(frozen=True)
class TrialStep:
    action_kind: TrialActionKind
    input_text: str = ""
    target_id: Optional[str] = None
    expected_invariants: Tuple[HardInvariant, ...] = ()
    description: str = ""


@dataclass(frozen=True)
class TrialScenario:
    scenario_id: str
    category: str
    description: str
    steps: Tuple[TrialStep, ...]
    invariants: Tuple[HardInvariant, ...] = ()


@dataclass(frozen=True)
class MemoryItemSnapshot:
    memory_id: str
    type: str
    status: str
    revision_count: int
    content: str


@dataclass(frozen=True)
class TrialObservation:
    visible_response: Optional[str]
    agent_result_status: str
    current_session_id: str
    current_task_id: str
    memories: Tuple[MemoryItemSnapshot, ...]
    task_entries_count: int
    tool_calls_count: int
    executed_side_effects_count: int
    warnings: Tuple[str, ...]
    errors: Tuple[str, ...]


@dataclass(frozen=True)
class StepResult:
    step_index: int
    action_kind: str
    input_text: str
    passed: bool
    observation: TrialObservation
    failed_invariants: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ScenarioResult:
    scenario_id: str
    category: str
    description: str
    status: str  # "PASS", "FAIL", "UNCERTAIN"
    step_results: Tuple[StepResult, ...]
    failed_reasons: Tuple[str, ...] = ()
