"""Live Provider check of native evidence handoff to downstream verifiers."""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", required=True, action="store_true")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    cfg = MemoryConfig.from_env()
    if not cfg.llm_api_key:
        raise RuntimeError("Provider credential is not configured")
    report = {
        "kind": "native_verifier_evidence_engineering_smoke",
        "requested_model": cfg.llm_model_name,
        "server_model_identity_retained": False,
        "fake_model_used": False,
        "official_benchmark_score": None,
        "checks": {},
        "verifier_handoffs": [],
    }
    names = [
        "agent/loop.py",
        "app/mcp.py",
        "collaboration/lifecycle.py",
        "collaboration/coordinator.py",
        "collaboration/repository.py",
        "collaboration/roles.py",
    ]
    report["source_sha256"] = {
        "code/src/memory/" + name: hashlib.sha256(
            (CODE_ROOT / "src/memory" / name).read_bytes()
        ).hexdigest()
        for name in names
    }
    with tempfile.TemporaryDirectory(prefix="mapas-native-verifier-") as temporary:
        base = Path(temporary)
        workspace = base / "workspace"
        workspace.mkdir()
        tokens = {
            key: key.upper() + "_" + secrets.token_hex(5) for key in ("left", "right")
        }
        for key, value in tokens.items():
            (workspace / (key + ".txt")).write_text(
                key + "_token=" + value + "\n", encoding="utf-8"
            )
        limits = CollaborationLimits(
            max_model_calls=24,
            max_token_units=240000,
            deadline_seconds=180,
            max_completion_tokens=4096,
        )
        app = PersonalAgentApp(
            data_dir=str(base / "state"),
            config=cfg,
            execution_mode="multi_fixed",
            collaboration_limits=limits,
            task_lifecycle_enabled=False,
        )
        # Isolate graph verification; use the same bounded format correction as
        # the default production multi_fixed lifecycle mode.
        app.llm_client.structured_recovery = True
        report["limits_per_run"] = asdict(limits)
        original = app.collaboration.roles.run

        def observe(role, inputs):
            output = original(role, inputs)
            if role == "Verifier":
                support = inputs.get("supporting_evidence", {})
                report["verifier_handoffs"].append(
                    {
                        "scope": inputs.get("verification_scope", "subgoal"),
                        "subgoal": inputs.get("subgoal", {}).get("key"),
                        "direct_receipt_count": len(inputs.get("receipts", [])),
                        "supporting_receipts": support.get("receipts", []),
                        "source_packet_count": len(support.get("source_packets", [])),
                        "verdict": output["verdict"],
                        "evidence_ids": output["evidence_ids"],
                    }
                )
            return output

        app.collaboration.roles.run = observe
        try:
            app.workspace.replace_scope([str(workspace)], str(workspace))
            result = app.process_chat_turn(
                "Read left.txt and right.txt using read_file and report both exact left_token and right_token. "
                "Use exactly three subgoals named left, right, merge: left and right are independent file reads; "
                "merge has no tools and depends on both. In merge inspect the host-supplied native dependency "
                "evidence and combine both observed tokens. Preserve literal values; do not guess or merely state that files were read."
            )
            report["result"] = {
                "status": result.status.value,
                "answer": result.final_response,
                "error": asdict(result.error_info) if result.error_info else None,
                "executed_tool_call_ids": list(result.executed_tool_call_ids),
            }
            report["usage"] = result.trace_metadata.get("collaboration_usage")
            assert result.status is AgentResultStatus.SUCCESS, (
                "graph verification failed"
            )
            report["checks"]["real_file_graph_succeeds"] = True
            assert all(value in result.final_response for value in tokens.values())
            report["checks"]["both_random_native_tokens_preserved"] = True
            for scope in ("merge", "whole_user_goal"):
                entry = next(
                    row
                    for row in report["verifier_handoffs"]
                    if row["subgoal"] == scope or row["scope"] == scope
                )
                receipts = entry["supporting_receipts"]
                assert len(receipts) == 2 and entry["verdict"] == "PASS"
                assert {row["evidence_id"] for row in receipts} == set(
                    result.executed_tool_call_ids
                )
                for row in receipts:
                    native = app.journal.get_receipt(row["evidence_id"])
                    assert row["result"] == json.loads(native["result_json"])
                report["checks"][scope + "_receives_native_receipts"] = True
            merge = next(
                row for row in report["verifier_handoffs"] if row["subgoal"] == "merge"
            )
            assert merge["direct_receipt_count"] == 0
            report["checks"]["dependency_only_merge_needs_no_duplicate_read"] = True
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
