"""Bounded original-source reads; semantic source selection belongs to the Provider.

Only current memory provenance and explicitly granted same-task tool receipts
are addressable. Missing or unverifiable originals never fall back to excerpts.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

from src.memory.conversation.models import ConversationRole
from src.memory.core.schema import (
    MemorySnapshot,
    RetentionDisposition,
    SemanticValidity,
    payload_hash,
)


class SourceAccessError(ValueError):
    """A proposed source is outside its granted scope or has changed identity."""


@dataclass(frozen=True)
class SourceDescriptor:
    source_ref: str
    kind: str
    source_id: str
    authority: str
    memory_id: str | None = None
    revision_id: str | None = None
    provenance_id: str | None = None
    session_id: str | None = None
    message_id: str | None = None
    expected_hash: str | None = None
    offset: int = 0
    length: int | None = None


@dataclass(frozen=True)
class SourcePacket:
    source: SourceDescriptor
    status: str
    text: str = ""
    content_hash: str | None = None
    truncated: bool = False
    source_length: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SourceResolver:
    MAX_CATALOG_ITEMS = 32
    MAX_READS = 4
    MAX_SOURCE_CHARS = 4000
    MAX_TOTAL_CHARS = 12000

    def __init__(
        self, memory_repository: Any, conversation_repository: Any, journal: Any
    ) -> None:
        self.memories = memory_repository
        self.conversations = conversation_repository
        self.journal = journal

    def _current(self, ref: dict[str, Any]) -> MemorySnapshot:
        snapshot = self.memories.get_current(ref["memory_id"])
        if (
            snapshot is None
            or snapshot.revision.revision_id != ref["revision_id"]
            or snapshot.retention_disposition is not RetentionDisposition.ACTIVE
            or snapshot.revision.semantic_validity is not SemanticValidity.CURRENT
            or snapshot.memory_id != snapshot.revision.memory_id
        ):
            raise SourceAccessError("source memory is stale or withdrawn")
        return snapshot

    def catalog(
        self,
        memory_inputs: Sequence[dict[str, Any]],
        tool_call_ids: Sequence[str],
        task_id: str,
    ) -> tuple[SourceDescriptor, ...]:
        rows = []
        for ref in memory_inputs:
            snapshot = self._current(ref)
            for provenance in snapshot.provenance:
                parts = [
                    snapshot.memory_id,
                    snapshot.revision.revision_id,
                    provenance.provenance_id,
                ]
                source_ref = "source:" + payload_hash(json.dumps(parts))[:32]
                rows.append(
                    SourceDescriptor(
                        source_ref=source_ref,
                        kind="MEMORY_PROVENANCE",
                        source_id=provenance.source_event_id,
                        authority=provenance.authority.value,
                        memory_id=snapshot.memory_id,
                        revision_id=snapshot.revision.revision_id,
                        provenance_id=provenance.provenance_id,
                        session_id=provenance.session_id,
                        message_id=provenance.message_id,
                        expected_hash=provenance.content_hash,
                    )
                )
                if len(rows) == self.MAX_CATALOG_ITEMS:
                    return tuple(rows)
        for call_id in dict.fromkeys(tool_call_ids):
            receipt = self.journal.get_receipt(call_id)
            run = self.journal.get_run(receipt["agent_run_id"]) if receipt else None
            if receipt is None or run is None or run["task_id"] != task_id:
                raise SourceAccessError("tool source does not belong to task")
            rows.append(
                SourceDescriptor(
                    source_ref="receipt:" + call_id,
                    kind="TOOL_RECEIPT",
                    source_id=call_id,
                    authority="SYSTEM_OR_TOOL",
                    expected_hash=payload_hash(receipt["result_json"] or ""),
                )
            )
            if len(rows) == self.MAX_CATALOG_ITEMS:
                break
        return tuple(rows)

    def resolve(
        self,
        requested_refs: Sequence[str],
        catalog: Sequence[SourceDescriptor],
        memory_inputs: Sequence[dict[str, Any]],
        task_id: str,
    ) -> tuple[SourcePacket, ...]:
        if len(requested_refs) > self.MAX_READS or len(set(requested_refs)) != len(
            requested_refs
        ):
            raise SourceAccessError("source read limit or duplicate source")
        allowed = {item.source_ref: item for item in catalog}
        memory_refs = {item["memory_id"]: item for item in memory_inputs}
        packets = []
        remaining = self.MAX_TOTAL_CHARS
        for source_ref in requested_refs:
            source = allowed.get(source_ref)
            if source is None:
                raise SourceAccessError("source was not granted")
            if source.kind == "MEMORY_PROVENANCE":
                if source.memory_id not in memory_refs:
                    raise SourceAccessError("source memory was not granted")
                current = self._current(memory_refs[source.memory_id])
                provenance = next(
                    (
                        p
                        for p in current.provenance
                        if p.provenance_id == source.provenance_id
                    ),
                    None,
                )
                if (
                    provenance is None
                    or current.revision.revision_id != source.revision_id
                    or provenance.source_event_id != source.source_id
                    or provenance.session_id != source.session_id
                    or provenance.message_id != source.message_id
                    or provenance.content_hash != source.expected_hash
                    or provenance.authority.value != source.authority
                ):
                    raise SourceAccessError("source provenance changed")
                if provenance.source_type != "INGESTION_EVENT":
                    packets.append(SourcePacket(source, "UNSUPPORTED_SOURCE"))
                    continue
                if not source.session_id or not source.message_id:
                    packets.append(SourcePacket(source, "SOURCE_UNAVAILABLE"))
                    continue
                message = self.conversations.get_message(
                    source.session_id, source.message_id
                )
                if message is None:
                    packets.append(SourcePacket(source, "SOURCE_UNAVAILABLE"))
                    continue
                if message.role is not ConversationRole.USER:
                    packets.append(SourcePacket(source, "SOURCE_ROLE_MISMATCH"))
                    continue
                text = message.content
                content_hash = payload_hash(text)
                if content_hash != source.expected_hash:
                    packets.append(SourcePacket(source, "SOURCE_HASH_MISMATCH"))
                    continue
            elif source.kind == "TOOL_RECEIPT":
                receipt = self.journal.get_receipt(source.source_id)
                run = self.journal.get_run(receipt["agent_run_id"]) if receipt else None
                if receipt is None or run is None or run["task_id"] != task_id:
                    raise SourceAccessError("tool source does not belong to task")
                if payload_hash(receipt["result_json"] or "") != source.expected_hash:
                    raise SourceAccessError("tool source changed")
                if receipt["status"] != "SUCCEEDED":
                    packets.append(SourcePacket(source, "SOURCE_NOT_SUCCESSFUL"))
                    continue
                # This is the authoritative persisted receipt, not a claim to
                # reconstruct an external response already bounded by the Journal.
                text = receipt["result_json"] or ""
                content_hash = payload_hash(text)
            else:
                raise SourceAccessError("unsupported source kind")
            limit = min(self.MAX_SOURCE_CHARS, remaining)
            if (
                type(source.offset) is not int
                or source.offset < 0
                or source.offset > len(text)
            ):
                raise SourceAccessError("invalid source window offset")
            if source.length is not None and (
                type(source.length) is not int
                or not 1 <= source.length <= self.MAX_SOURCE_CHARS
            ):
                raise SourceAccessError("invalid source window length")
            source_length = len(text)
            window = (
                text[source.offset : source.offset + source.length]
                if source.length is not None
                else text[source.offset :]
            )
            truncated = len(window) > limit
            packets.append(
                SourcePacket(
                    source,
                    "TRUNCATED" if truncated else "RESOLVED",
                    window[:limit],
                    content_hash,
                    truncated,
                    source_length,
                )
            )
            remaining -= min(len(window), limit)
        return tuple(packets)
