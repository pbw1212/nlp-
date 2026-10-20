from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence, Tuple

from src.memory.retrieval.evidence import DistilledEvidence, EvidenceBundle
from src.memory.task_state.models import (
    ActiveWorkingContext,
    ContinuationCheckpoint,
    EntryKind,
    EntryStatus,
    ExternalObservation,
    LedgerEntry,
    Priority,
    StructuredTaskContext,
    TaskContextCompaction,
    TaskEvent,
    TaskStatus,
)
from src.memory.task_state.repository import TaskStateRepository


@dataclass(frozen=True)
class TaskContextBudget:
    max_entries: int = 20
    max_chars: int = 5000
    max_chars_per_entry: int = 600
    max_evidence_items: int = 8
    max_recent_events: int = 4
    max_compaction_chars: int = 600

    @classmethod
    def from_model_window(
        cls,
        context_window_tokens: int,
        *,
        reserve_tokens: int = 8192,
        compact_at_ratio: float = 0.78,
    ) -> "TaskContextBudget":
        """Allocate the task projection from the resolved model budget.

        Task 3 remains structurally bounded because it shares the prompt with
        the transcript, retrieved evidence, tools and the answer. The values
        scale with the resolved model window instead of imposing a universal
        20-entry/5000-character product limit.
        """
        if context_window_tokens <= reserve_tokens:
            raise ValueError("context window must exceed reserve_tokens")
        usable_tokens = max(1, min(
            int(context_window_tokens * compact_at_ratio) - reserve_tokens,
            context_window_tokens - reserve_tokens,
        ))
        task_tokens = max(512, usable_tokens // 8)
        max_chars = task_tokens * 3
        max_entries = max(4, task_tokens // 128)
        max_chars_per_entry = max(200, max_chars // max_entries)
        max_evidence_items = max(4, min(32, task_tokens // 256))
        max_recent_events = max(2, min(16, task_tokens // 384))
        max_compaction_chars = max(200, max_chars // 5)
        return cls(
            max_entries=max_entries,
            max_chars=max_chars,
            max_chars_per_entry=max_chars_per_entry,
            max_evidence_items=max_evidence_items,
            max_recent_events=max_recent_events,
            max_compaction_chars=max_compaction_chars,
        )

    def __post_init__(self) -> None:
        if min(self.max_entries, self.max_chars, self.max_chars_per_entry, self.max_evidence_items, self.max_recent_events, self.max_compaction_chars) <= 0:
            raise ValueError("context budgets must be positive")


@dataclass(frozen=True)
class WorkingContextPolicy:
    version: str = "working-context-v1"
    meaningful_event_threshold: int = 3

    def tier(self, entry: LedgerEntry, referenced: set[str]) -> int:
        if entry.entry_id in referenced or entry.kind is EntryKind.GOAL or (
            entry.kind is EntryKind.CONSTRAINT and entry.priority >= Priority.CRITICAL
        ):
            return 0
        if entry.kind in {EntryKind.CONSTRAINT, EntryKind.DECISION, EntryKind.OPEN_QUESTION, EntryKind.PROGRESS}:
            return 1
        if entry.kind is EntryKind.DEPENDENCY:
            return 2
        return 3


class DeterministicTaskCompactor:
    """Extractive, bounded compactor for omitted entries only."""

    def __init__(self, policy: WorkingContextPolicy) -> None:
        self.policy = policy

    def compact(
        self,
        entries: Sequence[LedgerEntry],
        *,
        max_chars: int,
        checkpoint_id: Optional[str] = None,
    ) -> Optional[TaskContextCompaction]:
        if not entries or max_chars <= 0:
            return None
        grouped: dict[EntryKind, list[str]] = {}
        for entry in entries:
            text = " ".join(entry.content.split())
            if text:
                grouped.setdefault(entry.kind, []).append(text)
        labels = {
            EntryKind.GOAL: "goal",
            EntryKind.CONSTRAINT: "constraints",
            EntryKind.DECISION: "decisions",
            EntryKind.PROGRESS: "progress",
            EntryKind.OPEN_QUESTION: "open_questions",
            EntryKind.DEPENDENCY: "dependencies",
            EntryKind.BACKGROUND: "background",
        }
        sections: dict[str, str] = {}
        remaining = max_chars
        for kind in (EntryKind.GOAL, EntryKind.CONSTRAINT, EntryKind.DECISION, EntryKind.PROGRESS,
                     EntryKind.OPEN_QUESTION, EntryKind.DEPENDENCY, EntryKind.BACKGROUND):
            values = grouped.get(kind, [])
            if not values or remaining <= 0:
                continue
            text = "; ".join(values)
            bounded = text[:remaining]
            sections[labels[kind]] = bounded
            remaining -= len(bounded)
        return TaskContextCompaction(
            policy_version=self.policy.version,
            source_entry_ids=tuple(entry.entry_id for entry in entries),
            sections=sections,
            generated_at=time.time(),
            source_checkpoint_id=checkpoint_id,
            warnings=("compaction_lossy_extractive",) if remaining < max_chars else (),
        )


class TaskContextBuilder:
    """Builds a bounded derived context; it never mutates the ledger."""

    _SECTION_ORDER = (
        EntryKind.GOAL,
        EntryKind.CONSTRAINT,
        EntryKind.DECISION,
        EntryKind.PROGRESS,
        EntryKind.OPEN_QUESTION,
        EntryKind.DEPENDENCY,
        EntryKind.BACKGROUND,
    )

    def __init__(self, repository: TaskStateRepository, budget: TaskContextBudget | None = None, policy: WorkingContextPolicy | None = None) -> None:
        self.repository = repository
        self.budget = budget or TaskContextBudget()
        self.policy = policy or WorkingContextPolicy()
        self.compactor = DeterministicTaskCompactor(self.policy)

    def build(
        self,
        task_id: str,
        *,
        checkpoint: Optional[ContinuationCheckpoint] = None,
        current_turn: str = "",
        evidence_bundle: Optional[EvidenceBundle] = None,
        external_observations: Sequence[ExternalObservation] = (),
        recent_events: Optional[Sequence[TaskEvent]] = None,
        include_inactive: bool = False,
    ) -> ActiveWorkingContext:
        ledger = self.repository.get_ledger(task_id)
        checkpoint = checkpoint or self.repository.get_latest_checkpoint(task_id)
        active = [entry for entry in ledger.entries if entry.status is EntryStatus.ACTIVE]
        warnings: list[str] = []
        if ledger.status is not TaskStatus.ACTIVE and not include_inactive:
            active = []
            warnings.append(f"task_not_active:{ledger.status.value}")
        referenced = self._valid_checkpoint_refs(checkpoint, active)
        selected: dict[EntryKind, list[LedgerEntry]] = {kind: [] for kind in self._SECTION_ORDER}
        omitted: list[str] = []
        evidence_candidates = () if evidence_bundle is None else evidence_bundle.selected[: self.budget.max_evidence_items]
        reserved_evidence_entries = min(self.budget.max_entries, len(evidence_candidates))
        reserved_evidence_chars = min(
            self.budget.max_chars,
            sum(min(len(item.text), self.budget.max_chars_per_entry) for item in evidence_candidates),
        )
        compaction_budget = min(self.budget.max_compaction_chars, max(1, self.budget.max_chars // 5))
        remaining_chars = max(0, self.budget.max_chars - reserved_evidence_chars - compaction_budget)
        remaining_entries = self.budget.max_entries - reserved_evidence_entries

        candidates = sorted(active, key=lambda entry: self._sort_key(entry, referenced, self.policy))
        for entry in candidates:
            kind = entry.kind
            if remaining_entries <= 0 or remaining_chars <= 0:
                omitted.append(entry.entry_id)
                continue
            bounded = " ".join(entry.content.split())[: min(self.budget.max_chars_per_entry, remaining_chars)]
            if not bounded:
                omitted.append(entry.entry_id)
                continue
            if len(bounded) < len(" ".join(entry.content.split())):
                warnings.append(f"entry_truncated:{entry.entry_id}")
            selected[kind].append(
                LedgerEntry(
                    entry_id=entry.entry_id,
                    task_id=entry.task_id,
                    kind=entry.kind,
                    content=bounded,
                    status=entry.status,
                    created_at=entry.created_at,
                    updated_at=entry.updated_at,
                    priority=entry.priority,
                    source_kind=entry.source_kind,
                    basis=entry.basis,
                    source_ref=entry.source_ref,
                    evidence_refs=entry.evidence_refs,
                    supersedes_entry_id=entry.supersedes_entry_id,
                )
            )
            remaining_entries -= 1
            remaining_chars -= len(bounded)

        compacted = None
        if omitted:
            omitted_entries = [entry for entry in active if entry.entry_id in set(omitted)]
            compacted = self.compactor.compact(
                omitted_entries,
                max_chars=compaction_budget,
                checkpoint_id=None if checkpoint is None else checkpoint.checkpoint_id,
            )
            if compacted is not None:
                warnings.append("entries_compacted_for_budget")

        evidence: Tuple[DistilledEvidence, ...] = ()
        if evidence_bundle is not None and reserved_evidence_entries > 0 and reserved_evidence_chars > 0:
            evidence_budget = reserved_evidence_chars
            selected_evidence = []
            for item in evidence_candidates:
                if len(item.text) <= evidence_budget:
                    selected_evidence.append(item)
                    evidence_budget -= len(item.text)
            evidence = tuple(selected_evidence)
            if len(evidence) < len(evidence_bundle.selected):
                warnings.append("evidence_budget_truncated")

        if omitted:
            warnings.append("ledger_entries_omitted_for_budget")
        event_slice = tuple(recent_events) if recent_events is not None else self.repository.get_recent_events(task_id, self.budget.max_recent_events)
        event_slice = tuple(event_slice[-self.budget.max_recent_events:])
        fresh_observations = self._fresh_observations(external_observations)
        structured = StructuredTaskContext(
            task_id=task_id,
            current_turn=current_turn[: self.budget.max_chars_per_entry],
            goal=tuple(selected[EntryKind.GOAL]),
            constraints=tuple(selected[EntryKind.CONSTRAINT]),
            decisions=tuple(selected[EntryKind.DECISION]),
            progress=tuple(selected[EntryKind.PROGRESS]),
            open_questions=tuple(selected[EntryKind.OPEN_QUESTION]),
            dependencies=tuple(selected[EntryKind.DEPENDENCY]),
            relevant_background=tuple(selected[EntryKind.BACKGROUND]),
            retrieved_evidence=evidence,
            continuation=checkpoint,
            external_observations=fresh_observations,
            recent_events=event_slice,
            compaction=compacted,
            policy_version=self.policy.version,
            warnings=tuple(dict.fromkeys(warnings)),
        )
        return ActiveWorkingContext(task_id, structured, tuple(omitted), tuple(dict.fromkeys(warnings)))

    @staticmethod
    def _checkpoint_refs(checkpoint: Optional[ContinuationCheckpoint]) -> set[str]:
        if checkpoint is None:
            return set()
        return {
            *checkpoint.goal_entry_ids,
            *checkpoint.critical_constraint_ids,
            *checkpoint.open_question_ids,
            *checkpoint.decision_ids,
        }

    @classmethod
    def _valid_checkpoint_refs(cls, checkpoint: Optional[ContinuationCheckpoint], active: Sequence[LedgerEntry]) -> set[str]:
        valid = {entry.entry_id for entry in active}
        return cls._checkpoint_refs(checkpoint).intersection(valid)

    @staticmethod
    def _fresh_observations(observations: Sequence[ExternalObservation]) -> Tuple[ExternalObservation, ...]:
        latest: dict[str, ExternalObservation] = {}
        for observation in observations:
            previous = latest.get(observation.source_ref)
            if previous is None or observation.observed_at >= previous.observed_at:
                latest[observation.source_ref] = observation
        return tuple(sorted(latest.values(), key=lambda item: (item.source_type, item.source_ref)))

    @staticmethod
    def _sort_key(entry: LedgerEntry, referenced: set[str], policy: WorkingContextPolicy) -> tuple[int, int, int, float, str]:
        return (
            policy.tier(entry, referenced),
            0 if entry.entry_id in referenced else 1,
            -int(entry.priority),
            -entry.updated_at,
            entry.entry_id,
        )
