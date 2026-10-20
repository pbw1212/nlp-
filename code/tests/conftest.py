import sys
import os
import json
from typing import Any, Dict, List

# The project code now lives under code/; add that directory before importing
# production modules so pytest works both from the repository root and from a
# standalone checkout of code/.
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from src.memory.llm.llm_client import LLMClientBase


class TestProviderClient(LLMClientBase):
    """Explicit test double; production code always constructs a real API client."""

    def generate_json(self, system_prompt: str, user_prompt: str, *, response_schema: Dict[str, Any] | None = None, schema_name: str = "structured_output") -> Dict[str, Any]:
        if schema_name == "task_binding_decision_v1":
            payload = json.loads(user_prompt)
            current_task_id = payload.get("current_task_id")
            if current_task_id:
                return {"action": "EXISTING", "task_id": current_task_id, "title": None}
            return {"action": "NEW", "task_id": None, "title": "测试任务"}
        if schema_name == "explicit_proposal":
            return {
                "operation": "ADD",
                "target_memory_id": None,
                "content": user_prompt.split("Candidate Statement:\n")[-1].strip(),
                "reason": "test provider explicit lifecycle proposal",
            }
        if "task_state" in schema_name or "proposal" in schema_name:
            return {"proposals": [], "warnings": []}
        if "selection" in schema_name:
            return {"schema_version": 1, "selected": [], "warnings": []}
        if "context" in schema_name:
            return {"schema_version": 1, "values": [], "warnings": []}
        if "scope" in schema_name:
            return {"schema_version": 1, "memory_id": "mem_test", "rules": [], "warnings": []}
        if "Dialogue Window:" in user_prompt:
            return {"episodes": []}
        if "Existing Memories S_in:" in user_prompt:
            return {"predicted_content": "", "prediction_error_residual": "", "is_novel_info": False, "novelty_score": 0.0}
        if "Cognitive Synthesis Engine" in system_prompt:
            return {"abstract_insight": "", "granularity": "HIGH_ABSTRACT"}
        if schema_name == "agent_action":
            return {"action_kind": "FINAL_RESPONSE", "content": "test provider response"}
        return {"response": "test provider response"}

    def generate_chat_json(self, messages: List[Dict[str, str]], enable_web_search: bool = False) -> Dict[str, Any]:
        return {"response": "test provider response"}

def pytest_configure(config):
    client = TestProviderClient()
    import src.memory.api.memory_manager as memory_manager
    memory_manager.build_llm_client = lambda _: client
