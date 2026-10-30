"""Bounded model-proposed evidence expansion with durable stage replay."""

from dataclasses import asdict, replace
from typing import Any

from src.memory.collaboration.models import MEMORY_SCHEMA, SEARCH_SCHEMA, digest
from src.memory.collaboration.repository import CoordinationConflict
from src.memory.retrieval.sources import SourceDescriptor


def complete_evidence(
    host: Any,
    run_id: str,
    key: str,
    inputs: dict,
    selection: dict,
    reads: list,
    refs: list,
) -> tuple[dict, dict, dict]:
    preparation = host._prepare_evidence(run_id, key, inputs, selection, reads)
    needs = {row["need_id"]: row for row in selection["evidence_needs"]}
    history = []
    extra_reads = 0
    for round_index in range(2):
        check = preparation["evidence_check"]
        unmet = (
            [row for row in check["assessments"] if row["status"] != "SUPPORTED"]
            if check
            else []
        )
        if not unmet:
            break
        proposal_input = {
            **inputs,
            **preparation,
            "evidence_needs": list(needs.values()),
            "unmet": unmet,
            "previous_searches": history,
            "remaining_rounds": 2 - round_index,
        }
        stage = f"{key}:expand:{round_index}"
        proposal = host._stage(
            run_id,
            stage,
            "EvidenceSearch",
            proposal_input,
            lambda proposal_input=proposal_input: host.roles.run(
                "EvidenceSearch", proposal_input
            ),
            SEARCH_SCHEMA,
        )
        catalog = {
            row["source_ref"]: SourceDescriptor(**row)
            for row in inputs["source_catalog"]
        }
        for request in proposal["queries"] + proposal["source_windows"]:
            if not request["need_ids"] or not set(request["need_ids"]) <= needs.keys():
                raise CoordinationConflict("evidence expansion invented a need")
        if any(digest(proposal) == digest(previous) for previous in history):
            break
        if not proposal["queries"] and not proposal["source_windows"]:
            break
        history.append(proposal)
        memories = {row["memory_id"]: row for row in inputs["memories"]}
        for query_index, query in enumerate(proposal["queries"]):
            retrieval = host.runtime.retrieval_facade
            if retrieval is None:
                continue

            def retrieve(
                query=query, retrieval=retrieval, stage=stage, query_index=query_index
            ):
                with host.client.role("Retrieval"):
                    bundle = retrieval.retrieve_evidence(
                        query["query"],
                        top_k=8,
                        query_id=f"{run_id}:{stage}:{query_index}",
                    )
                return bundle.to_dict()

            output = host._stage(
                run_id, f"{stage}:query:{query_index}", "Retrieval", query, retrieve
            )
            for item in output["selected"]:
                if len(memories) >= 24 and item["memory_id"] not in memories:
                    continue
                snapshot = host.memory_repository.get_current(item["memory_id"])
                row = {
                    "memory_id": item["memory_id"],
                    "revision_id": item["revision_id"],
                    "content": item["text"],
                    "provenance_refs": item["provenance_refs"],
                    "content_hash": (
                        digest(snapshot.revision.content) if snapshot else "MISSING"
                    ),
                }
                host._validate_refs([row])
                memories[row["memory_id"]] = row
        inputs = {
            **inputs,
            "memories": list(memories.values()),
            "required_evidence_needs": list(needs.values()),
            "search_history": history,
            "previous_preparation": preparation,
        }
        known_refs = {row["memory_id"] for row in refs}
        refs.extend(
            {k: row[k] for k in ("memory_id", "revision_id", "content_hash")}
            for row in memories.values()
            if row["memory_id"] not in known_refs
        )
        tool_ids = [
            source.source_id
            for source in catalog.values()
            if source.kind == "TOOL_RECEIPT"
        ]
        catalog.update(
            {
                row.source_ref: row
                for row in host.sources.catalog(
                    inputs["memories"], tool_ids, inputs["task_id"]
                )
            }
        )
        requested_windows = []
        for window in proposal["source_windows"]:
            original = catalog.get(window["source_ref"])
            if original is None:
                raise CoordinationConflict(
                    "evidence expansion requested an ungranted source"
                )
            projected = replace(
                original,
                source_ref=original.source_ref.split("#window:")[0]
                + "#window:"
                + digest([window["offset"], window["length"]])[:16],
                offset=window["offset"],
                length=window["length"],
            )
            catalog[projected.source_ref] = projected
            requested_windows.append(
                {"source_ref": projected.source_ref, "need_ids": window["need_ids"]}
            )
        inputs["source_catalog"] = [asdict(row) for row in list(catalog.values())[:40]]
        selection = host._stage(
            run_id,
            stage + ":memory",
            "Memory",
            inputs,
            lambda inputs=inputs: host.roles.run("Memory", inputs),
            MEMORY_SCHEMA,
        )
        if {row["need_id"]: row for row in selection["evidence_needs"]} != needs:
            raise CoordinationConflict(
                "expanded selection changed evidence requirements"
            )
        if not set(selection["selected_memory_ids"]) <= memories.keys():
            raise CoordinationConflict("expanded selection invented a memory")
        selection = {
            **selection,
            "source_requests": list(selection["source_requests"]),
            "selected_memory_ids": list(selection["selected_memory_ids"]),
        }
        for request in requested_windows:
            if request["source_ref"] not in {
                row["source_ref"] for row in inputs["source_catalog"]
            }:
                raise CoordinationConflict("source window exceeds catalog budget")
            if request["source_ref"] not in {
                row["source_ref"] for row in selection["source_requests"]
            }:
                selection["source_requests"].append(request)
            owner = catalog[request["source_ref"]].memory_id
            if owner and owner not in selection["selected_memory_ids"]:
                selection["selected_memory_ids"].append(owner)
        if (
            len(selection["source_requests"]) > 4
            or extra_reads + len(selection["source_requests"]) > 4
        ):
            raise CoordinationConflict("evidence expansion read budget exceeded")
        extra_reads += len(selection["source_requests"])
        preparation = host._prepare_evidence(run_id, stage, inputs, selection, reads)
    return inputs, selection, preparation
