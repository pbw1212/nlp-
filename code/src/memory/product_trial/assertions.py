"""Hard Invariant Evaluation Engine for Product Trial Harness."""

from __future__ import annotations

import re
from typing import List, Sequence, Tuple

from src.memory.product_trial.models import HardInvariant, InvariantType, TrialObservation


class HardOracleEvaluator:
    """Deterministic oracle evaluator. Evaluates strict hard invariants without LLM subjectivity."""

    @staticmethod
    def evaluate(invariant: HardInvariant, obs: TrialObservation, global_violations: Sequence[str] = ()) -> Tuple[bool, str]:
        kind = invariant.kind
        exp = invariant.expected_value
        resp = obs.visible_response or ""

        if kind == InvariantType.CONTAINS_TEXT:
            if str(exp) in resp:
                return True, f"Response contains '{exp}'"
            return False, f"Expected response to contain '{exp}', got: '{resp}'"

        elif kind == InvariantType.NOT_CONTAINS_TEXT:
            if str(exp) not in resp:
                return True, f"Response does not contain '{exp}'"
            return False, f"Expected response NOT to contain '{exp}', got: '{resp}'"

        elif kind == InvariantType.RESULT_STATUS:
            if obs.agent_result_status == str(exp):
                return True, f"Agent result status matches '{exp}'"
            return False, f"Expected status '{exp}', got '{obs.agent_result_status}'"

        elif kind == InvariantType.MEMORY_COUNT:
            if len(obs.memories) == int(exp):
                return True, f"Memory count is {exp}"
            return False, f"Expected memory count {exp}, got {len(obs.memories)}"

        elif kind == InvariantType.CURRENT_MEMORY_COUNT:
            current_cnt = sum(1 for m in obs.memories if m.status == "CURRENT")
            if current_cnt == int(exp):
                return True, f"CURRENT memory count is {exp}"
            return False, f"Expected CURRENT memory count {exp}, got {current_cnt}"

        elif kind == InvariantType.SESSION_ID_SAME:
            if obs.current_session_id == str(exp):
                return True, f"Session ID matches '{exp}'"
            return False, f"Expected session ID '{exp}', got '{obs.current_session_id}'"

        elif kind == InvariantType.SESSION_ID_DIFFERENT:
            if obs.current_session_id != str(exp):
                return True, f"Session ID is different from '{exp}'"
            return False, f"Expected session ID to differ from '{exp}'"

        elif kind == InvariantType.TOOL_CALLED:
            if obs.tool_calls_count > 0:
                return True, f"Tool was called ({obs.tool_calls_count} total tool calls)"
            return False, "Expected tool call, but zero tool calls recorded"

        elif kind == InvariantType.TOOL_NOT_CALLED:
            if obs.tool_calls_count == 0:
                return True, "Zero tool calls recorded"
            return False, f"Expected zero tool calls, got {obs.tool_calls_count}"

        elif kind == InvariantType.EXECUTOR_COUNT:
            if obs.executed_side_effects_count == int(exp):
                return True, f"Executed side effects count is {exp}"
            return False, f"Expected {exp} executed side effects, got {obs.executed_side_effects_count}"

        elif kind == InvariantType.WARNING_PRESENT:
            if len(obs.warnings) > 0:
                return True, f"Warning recorded: {obs.warnings}"
            return False, "Expected warning to be present, got none"

        elif kind == InvariantType.NO_UNGROUNDED_WEATHER_CLAIM:
            # Rejects fabricated temperatures like 28°C, 30度, 18度
            temp_match = re.search(r"\d+\s*(°C|度|摄氏度)", resp)
            condition_match = any(w in resp for w in ["晴朗", "多云", "大雨", "小雨", "雷阵雨"])
            if temp_match or condition_match:
                return False, f"Response contains ungrounded weather assertion: '{resp}'"
            return True, "Response cleanly abstains from fabricating weather facts"

        elif kind == InvariantType.GLOBAL_INVARIANTS:
            if len(global_violations) == 0:
                return True, "All global system invariants hold"
            return False, f"Global invariant violations: {global_violations}"

        return False, f"Unknown invariant type: {kind}"
