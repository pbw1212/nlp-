from __future__ import annotations

import json
from dataclasses import replace
from typing import Any, Mapping, Optional

from src.memory.core.config import MemoryConfig, ProviderMode
from src.memory.llm.errors import ProviderConfigurationError, ProviderError
from src.memory.llm.llm_client import LLMClientBase, build_llm_client
from src.memory.task_state.models import (
    ActiveWorkingContext,
    EntryKind,
    EvidenceRef,
    Priority,
    ProposalAction,
    ProposalBasis,
    SourceKind,
    TaskStateScope,
    TaskEvent,
    TaskAnalysis,
    TaskIntent,
    TaskRelation,
    TaskStateProposal,
    TaskStateProposalBatch,
    RelationKind,
)
from src.memory.task_state.updater import TaskStateInterpreterError


TASK_STATE_INTERPRETER_VERSION = "task_state_interpretation_v2"


def _proposal_schema(source_kind: SourceKind = SourceKind.USER) -> dict[str, Any]:
    analysis = {
        "type": ["object", "null"],
        "properties": {
            # The parser treats task_stage as a required non-empty semantic
            # label. Keep that invariant in the provider contract as well so
            # a syntactically valid but unusable response is rejected by the
            # provider schema before it reaches durable-state processing.
            "task_stage": {"type": "string", "minLength": 1},
            "query_intent": {"type": "string", "enum": [item.value for item in TaskIntent]},
            "memory_write_requested": {"type": "boolean"},
            "information_needs": {"type": "array", "items": {"type": "string"}},
            "retrieval_needed": {"type": "boolean"},
            "important_kinds": {"type": "array", "items": {"type": "string", "enum": [item.value for item in EntryKind]}},
            "focus_entry_ids": {"type": "array", "items": {"type": "string"}},
            "search_query": {"type": "string"},
            "warnings": {"type": "array", "items": {"type": "string"}},
            "memory_content": {"type": ["string", "null"]},
            "memory_support_span": {"type": ["string", "null"]},
        },
        "required": [
            "task_stage", "query_intent", "memory_write_requested", "information_needs", "retrieval_needed",
            "important_kinds", "focus_entry_ids", "search_query", "warnings",
            "memory_content", "memory_support_span",
        ],
        "additionalProperties": False,
    }
    relation = {
        "type": ["object", "null"],
        "properties": {
            "task_id": {"type": "string"},
            "from_entry_id": {"type": "string"},
            "to_entry_id": {"type": "string"},
            "kind": {"type": "string", "enum": [item.value for item in RelationKind]},
        },
        "required": ["task_id", "from_entry_id", "to_entry_id", "kind"],
        "additionalProperties": False,
    }
    evidence = {
        "type": "object",
        "properties": {
            "bundle_id": {"type": "string"},
            "memory_id": {"type": "string"},
            "revision_id": {"type": "string"},
            "evidence_item_id": {"type": ["string", "null"]},
        },
        "required": ["bundle_id", "memory_id", "revision_id", "evidence_item_id"],
        "additionalProperties": False,
    }
    evidence_refs = {"type": "array", "items": evidence}
    if source_kind is SourceKind.TOOL:
        # A tool receipt is grounded by the tool event itself, not by a Task 2
        # memory evidence bundle. Prevent fabricated memory IDs at the
        # provider contract boundary.
        evidence_refs["maxItems"] = 0
    proposal = {
        "type": "object",
        "properties": {
            "proposal_id": {"type": "string"},
            "action": {"type": "string", "enum": [item.value for item in ProposalAction]},
            "entry_kind": {"type": ["string", "null"], "enum": [item.value for item in EntryKind] + [None]},
            "target_entry_id": {"type": ["string", "null"]},
            "content": {"type": ["string", "null"]},
            "replacement_content": {"type": ["string", "null"]},
            "priority": {"type": ["integer", "null"], "enum": [None, 1, 2, 3, 4]},
            "relation": relation,
            "evidence_refs": evidence_refs,
            "support_spans": {"type": "array", "items": {"type": "string"}},
            "basis": {"type": "string", "enum": [item.value for item in ProposalBasis]},
            "warnings": {"type": "array", "items": {"type": "string"}},
            "scope": {"type": "string", "enum": [item.value for item in TaskStateScope]},
        },
        "required": [
            "proposal_id", "action", "entry_kind", "target_entry_id", "content",
            "replacement_content", "priority", "relation", "evidence_refs",
            "support_spans", "basis", "warnings",
            "scope",
        ],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "analysis": analysis,
            "proposals": {"type": "array", "items": proposal},
            "warnings": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["analysis", "proposals", "warnings"],
        "additionalProperties": False,
    }


SYSTEM_PROMPT = """You propose durable task-state changes from one source event.
Return only the supplied JSON schema. You NEVER mutate storage and you NEVER invent facts
not supported by the current event or bounded context. Use your semantic judgment: emit an
empty proposals array whenever the event does not establish durable task state. Do not
promote a possibility, suggestion, transient exchange, or assistant intention into durable
state unless the source and context genuinely support that interpretation.

Every proposal must include a scope. Use TASK only for state that should survive switching
conversation sessions within the same task, such as a durable goal, constraint, decision,
or verified task progress. Use SESSION for temporary conversational context that is useful
only inside the current conversation session, such as a temporary code or one-off detail.
SESSION proposals are not written to the Task 3 ledger; the conversation transcript is their
durable source. Do not choose TASK merely because a user stated something explicitly.

The runtime enforces source authority and lifecycle invariants. Agent suggestions cannot
become user constraints; observed progress must be backed by a successful tool or an
observed source; corrections should supersede the active old entry instead of leaving
contradictory active truths. Every proposal should include an exact support span copied
from the current event when one exists. Use only the allowed actions and entry kinds.
Inferred proposals must not be CRITICAL.

For a TOOL source event, the tool receipt is the authoritative observation. Return
evidence_refs as an empty array; do not invent bundle_id, memory_id, or revision_id.
If recording observed progress, use basis OBSERVED and copy an exact non-empty
support span from the tool event content.

Also return request-local analysis in the analysis field. It is a semantic proposal for
the current request, not durable task truth. Use focus_entry_ids only for active entry IDs
present in the bounded context. Do not infer a replacement value or target from trigger
words; if a correction target or replacement is uncertain, leave the relevant proposal
field empty so the runtime can request explicit confirmation.

The analysis.task_stage field is required and must be a concise, non-empty label for the
current stage of the user's request. Use a neutral label such as "information_request"
when no more specific stage applies.

If the user is explicitly asking the product to save, remember, correct, update, or remove
a durable personal fact or an existing long-term memory, set memory_write_requested to true,
set query_intent to EXPLICIT_MEMORY_WRITE, put only the user-grounded content to save in
memory_content, copy an exact non-empty supporting span from the current user event into
memory_support_span, and return no Task 3 proposals. A request such as “请纠正长期记忆”
is an explicit storage request even when it uses CORRECT/UPDATE language rather than the
word “remember”. The memory_content may be a faithful normalization of that span, but must
not add facts that the span does not support. A user merely stating a preference, fact,
plan, or background is not an explicit storage request: set memory_write_requested to false
and do not use EXPLICIT_MEMORY_WRITE. Do not classify ordinary discussion, hypotheticals,
questions, or assistant suggestions as EXPLICIT_MEMORY_WRITE.

For an ordinary information question where the user is not asking to track a task item,
return an empty proposals array. For example, for the event "我的代号是什么？" with no
matching active context, return query_intent RAISE_QUESTION, retrieval_needed false, a
non-empty task_stage label, and proposals []. Do not create an OPEN_QUESTION merely because
the answer is unknown; that action is for an unresolved item the user wants tracked.
"""


class OpenAICompatibleTaskStateInterpreter:
    """Model-agnostic structured interpreter; provider only proposes, never mutates."""

    identity = "openai-compatible-task-state-interpreter"
    version = TASK_STATE_INTERPRETER_VERSION

    def __init__(self, client: LLMClientBase, *, provider: str = "openai-compatible", model: str = "configured") -> None:
        self._client = client
        self.identity = f"{provider}:{model}:task-state"

    @classmethod
    def from_config(cls, config: MemoryConfig) -> "OpenAICompatibleTaskStateInterpreter":
        if config.provider_mode is not ProviderMode.REQUIRED:
            raise ProviderConfigurationError(
                "real task-state interpretation requires MEMORY_PROVIDER_MODE=REQUIRED",
                provider=config.llm_provider_name,
                operation="task_state_interpretation.configure",
            )
        if config.llm_max_completion_tokens is None:
            config = replace(config, llm_max_completion_tokens=2048)
        return cls(
            build_llm_client(config),
            provider=config.llm_provider_name,
            model=config.llm_model_name,
        )

    def interpret(self, event: TaskEvent, context: Optional[ActiveWorkingContext]) -> TaskStateProposalBatch:
        bounded_context = None if context is None else context.structured.to_dict()
        payload = {
            "task_id": event.task_id,
            "event_id": event.event_id,
            "source_kind": event.source_kind.value,
            "content": event.content,
            "structured_payload": dict(event.structured_payload),
            "occurred_at": event.occurred_at,
            "bounded_context": bounded_context,
        }
        try:
            raw = self._client.generate_json(
                SYSTEM_PROMPT,
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
                response_schema=_proposal_schema(event.source_kind),
                # Keep the operation name stable for provider diagnostics and
                # existing integrations; the response contract is still
                # parsed with the interpreter version below.
                schema_name="task_state_proposal_batch_v1",
            )
            return self._parse(event, raw, context=context)
        except ProviderError:
            raise
        except Exception as exc:
            raise TaskStateInterpreterError(
                f"task-state structured response invalid ({type(exc).__name__})"
            ) from exc

    @staticmethod
    def _parse(
        event: TaskEvent,
        raw: Mapping[str, Any],
        *,
        context: Optional[ActiveWorkingContext] = None,
    ) -> TaskStateProposalBatch:
        if not isinstance(raw, Mapping) or not isinstance(raw.get("proposals"), list):
            raise TaskStateInterpreterError("task-state response requires proposals array")
        proposals = []
        batch_warnings = [str(warning) for warning in raw.get("warnings", [])]
        analysis = OpenAICompatibleTaskStateInterpreter._parse_analysis(raw.get("analysis"))
        # Provider intent is authoritative for meaning, but extracted content
        # is still a proposal and must be grounded in this exact source event.
        # This prevents a stale/cached model response from turning the current
        # question into a write of a fact from an earlier turn.  It is a data
        # provenance invariant, not a semantic keyword rule.
        if analysis is not None and analysis.query_intent is TaskIntent.EXPLICIT_MEMORY_WRITE:
            if (
                not analysis.memory_write_requested
                or
                event.source_kind is not SourceKind.USER
                or not analysis.memory_content
                or not analysis.memory_support_span
                or analysis.memory_support_span not in event.content
            ):
                batch_warnings.append(
                    "explicit memory candidate rejected: support span is not grounded in the source event"
                )
                analysis = None
        confirmation_required = False
        for item in raw["proposals"]:
            if not isinstance(item, Mapping):
                raise TaskStateInterpreterError("proposal must be an object")
            try:
                relation_data = item.get("relation")
                relation = None if relation_data is None else TaskRelation(
                    task_id=str(relation_data["task_id"]),
                    from_entry_id=str(relation_data["from_entry_id"]),
                    to_entry_id=str(relation_data["to_entry_id"]),
                    kind=RelationKind(str(relation_data["kind"])),
                )
                refs = tuple(
                    EvidenceRef(
                        bundle_id=str(ref["bundle_id"]), memory_id=str(ref["memory_id"]),
                        revision_id=str(ref["revision_id"]), evidence_item_id=ref.get("evidence_item_id"),
                    )
                    for ref in item.get("evidence_refs", [])
                )
                action = ProposalAction(str(item["action"]))
                entry_kind = None if item.get("entry_kind") is None else EntryKind(str(item["entry_kind"]))
                target_entry_id = item.get("target_entry_id")
                replacement_content = item.get("replacement_content")
                proposal_warnings = [str(warning) for warning in item.get("warnings", [])]
                scope = TaskStateScope(str(item.get("scope", TaskStateScope.TASK.value)))
                if action is ProposalAction.SUPERSEDE:
                    repaired = OpenAICompatibleTaskStateInterpreter._repair_supersede(
                        event,
                        context,
                        analysis=analysis,
                        entry_kind=entry_kind,
                        target_entry_id=target_entry_id,
                        replacement_content=replacement_content,
                    )
                    if repaired is None:
                        confirmation_required = True
                        batch_warnings.append(
                            "SUPERSEDE requires explicit confirmation because its target or replacement is not fully grounded"
                        )
                        continue
                    target_entry_id, replacement_content, repair_warning = repaired
                    if repair_warning:
                        proposal_warnings.append(repair_warning)
                proposals.append(TaskStateProposal(
                    proposal_id=str(item["proposal_id"]), task_id=event.task_id,
                    source_event_id=event.event_id, action=action,
                    entry_kind=entry_kind,
                    target_entry_id=target_entry_id, content=item.get("content"),
                    replacement_content=replacement_content,
                    priority=None if item.get("priority") is None else Priority(int(item["priority"])),
                    relation=relation, evidence_refs=refs,
                    support_spans=tuple(str(span) for span in item.get("support_spans", [])),
                    basis=ProposalBasis(str(item.get("basis", ProposalBasis.EXPLICIT.value))),
                    warnings=tuple(str(warning) for warning in item.get("warnings", [])),
                    scope=scope,
                ))
            except (KeyError, TypeError, ValueError) as exc:
                raise TaskStateInterpreterError(
                    f"task-state proposal decode failed ({type(exc).__name__})"
                ) from exc
        return TaskStateProposalBatch(
            task_id=event.task_id, event_id=event.event_id, proposals=tuple(proposals),
            interpreter="openai-compatible-task-state-interpreter",
            interpreter_version=TASK_STATE_INTERPRETER_VERSION,
            warnings=tuple(batch_warnings),
            analysis=analysis,
            confirmation_required=confirmation_required,
        )

    @staticmethod
    def _parse_analysis(raw: Any) -> Optional[TaskAnalysis]:
        if raw is None:
            return None
        if not isinstance(raw, Mapping):
            raise TaskStateInterpreterError("task-state analysis must be an object or null")
        try:
            task_stage = str(raw["task_stage"]).strip()
            if not task_stage:
                raise ValueError("task_stage must be non-empty")
            query_intent = TaskIntent(str(raw["query_intent"]))
            memory_write_requested = raw.get("memory_write_requested", False)
            if not isinstance(memory_write_requested, bool):
                raise TypeError("memory_write_requested must be boolean")
            retrieval_needed = raw["retrieval_needed"]
            if not isinstance(retrieval_needed, bool):
                raise TypeError("retrieval_needed must be boolean")
            information_needs = raw.get("information_needs", [])
            important_kinds = raw.get("important_kinds", [])
            focus_entry_ids = raw.get("focus_entry_ids", [])
            warnings = raw.get("warnings", [])
            if not all(isinstance(value, list) for value in (information_needs, important_kinds, focus_entry_ids, warnings)):
                raise TypeError("analysis collection fields must be arrays")
            memory_content = raw.get("memory_content")
            if memory_content is not None and not isinstance(memory_content, str):
                raise TypeError("memory_content must be string or null")
            memory_content = memory_content.strip() if memory_content else None
            memory_support_span = raw.get("memory_support_span")
            if memory_support_span is not None and not isinstance(memory_support_span, str):
                raise TypeError("memory_support_span must be string or null")
            memory_support_span = memory_support_span.strip() if memory_support_span else None
            if query_intent is not TaskIntent.EXPLICIT_MEMORY_WRITE:
                memory_content = None
                memory_support_span = None
            return TaskAnalysis(
                task_stage=task_stage,
                query_intent=query_intent,
                memory_write_requested=memory_write_requested,
                information_needs=tuple(str(item) for item in information_needs),
                retrieval_needed=retrieval_needed,
                important_kinds=tuple(EntryKind(str(item)) for item in important_kinds),
                focus_entry_ids=tuple(str(item) for item in focus_entry_ids),
                search_query=str(raw.get("search_query", "")),
                warnings=tuple(str(item) for item in warnings),
                memory_content=memory_content,
                memory_support_span=memory_support_span,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise TaskStateInterpreterError(
                f"task-state analysis decode failed ({type(exc).__name__})"
            ) from exc

    @staticmethod
    def _repair_supersede(
        event: TaskEvent,
        context: Optional[ActiveWorkingContext],
        *,
        analysis: Optional[TaskAnalysis],
        entry_kind: Optional[EntryKind],
        target_entry_id: Optional[str],
        replacement_content: Optional[str],
    ) -> Optional[tuple[str, str, Optional[str]]]:
        """Use only provider-supplied IDs/content or request explicit confirmation."""
        replacement = replacement_content.strip() if isinstance(replacement_content, str) else ""
        if target_entry_id and replacement:
            return str(target_entry_id), replacement, None
        if event.source_kind is not SourceKind.USER or context is None or analysis is None:
            return None
        if analysis.query_intent is not TaskIntent.MODIFY_EXISTING or not replacement:
            return None

        structured = context.structured
        active_entries = tuple(
            entry
            for section in (
                structured.goal,
                structured.constraints,
                structured.decisions,
                structured.progress,
                structured.open_questions,
                structured.dependencies,
                structured.relevant_background,
            )
            for entry in section
        )
        by_id = {entry.entry_id: entry for entry in active_entries}
        focused = [
            by_id[entry_id]
            for entry_id in analysis.focus_entry_ids
            if entry_id in by_id and (entry_kind is None or by_id[entry_id].kind is entry_kind)
        ]
        if len(focused) != 1:
            return None
        return focused[0].entry_id, replacement, "supersede_target_recovered_from_provider_analysis"
