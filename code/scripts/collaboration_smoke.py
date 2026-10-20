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
from src.memory.core.config import MemoryConfig
from src.memory.runtime.models import ToolCallResult, ToolCallStatus


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
        "--live", action="store_true", help="Execute at most 12 provider calls"
    )
    parser.add_argument("--memoryarena-checkout", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
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
            collaboration_limits=limits,
        )
        try:
            app.workspace.replace_scope([temporary], temporary)
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
                user_input = "Use the calculator to compute 17*23. Use one subtask. Report the exact result."
                expected = ["391"]
            report.update(input=user_input, expected=expected)
            result = app._process_chat_turn_with_id(
                user_input, "live-p1-smoke", resume=False
            )
            receipts = [
                app.journal.get_receipt(key) for key in result.executed_tool_call_ids
            ]
            passed = result.status is AgentResultStatus.SUCCESS and all(
                value in (result.final_response or "") for value in expected
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
