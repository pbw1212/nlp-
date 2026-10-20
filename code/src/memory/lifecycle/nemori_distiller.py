import logging
from typing import Dict, Any, List, Tuple
from src.memory.llm.llm_client import LLMClientBase, PROMPT_NEMORI_PARTITION, PROMPT_NEMORI_PREDICT_AND_RESIDUE
from src.memory.core.config import MemoryConfig
from src.memory.llm.errors import ProviderResponseError


logger = logging.getLogger(__name__)

# SemanticMetadata is the canonical storage model. Some compatible providers
# nevertheless return lifecycle/transport annotations alongside semantic tags.
# Keep the canonical model strict and adapt provider output at this boundary.
_CANONICAL_SEMANTIC_METADATA_FIELDS = frozenset(
    {"persons", "entities", "locations", "topics", "keywords", "scopes", "conditions"}
)


def _parse_provider_semantic_metadata(value: Any, *, provider: str = "provider"):
    """Normalize provider metadata into the strict canonical schema."""
    from src.memory.core.schema import SemanticMetadata

    if value is None:
        return SemanticMetadata()
    if not isinstance(value, dict):
        raise ProviderResponseError(
            "automatic lifecycle semantic_metadata must be a JSON object",
            provider=provider,
            operation="propose_inferred_lifecycle",
        )
    unknown = sorted(set(value) - _CANONICAL_SEMANTIC_METADATA_FIELDS)
    if unknown:
        logger.warning(
            "provider_semantic_metadata_fields_ignored operation=propose_inferred_lifecycle fields=%s",
            unknown,
        )
    canonical = {
        name: value[name]
        for name in _CANONICAL_SEMANTIC_METADATA_FIELDS
        if name in value
    }
    try:
        return SemanticMetadata.from_dict(canonical)
    except ValueError as exc:
        raise ProviderResponseError(
            "automatic lifecycle semantic_metadata contains invalid canonical fields",
            provider=provider,
            operation="propose_inferred_lifecycle",
        ) from exc


NEMORI_PARTITION_SCHEMA = {
    "type": "object",
    "properties": {
        "episodes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "message_indices": {
                        "type": "array",
                        "items": {"type": "integer", "minimum": 1},
                    },
                    "cue": {"type": "string"},
                    "narrative": {"type": "string"},
                    "durability": {
                        "type": "string",
                        "enum": ["DURABLE", "TRANSIENT", "UNCERTAIN"],
                    },
                },
                "required": ["message_indices", "cue", "narrative", "durability"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["episodes"],
    "additionalProperties": False,
}


class NemoriDistiller:
    """
    NEMORI (Adaptive Memory Distillation) 蒸馏引擎:
    基于预测残差 K_in = P_actual \\ P_predicted 抽取信息增量。
    """
    def __init__(self, llm_client: LLMClientBase, config: MemoryConfig):
        self.llm_client = llm_client
        self.config = config

    def partition_dialogue(self, dialogue_window: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        """1. 对话切分与叙事重写 (Episode Partitioning)"""
        user_prompt = f"Dialogue Window:\n{dialogue_window}"
        res = self.llm_client.generate_json(
            PROMPT_NEMORI_PARTITION,
            user_prompt,
            response_schema=NEMORI_PARTITION_SCHEMA,
            schema_name="nemori_partition",
        )
        episodes = res.get("episodes")
        if not isinstance(episodes, list) or not all(isinstance(ep, dict) for ep in episodes):
            raise ProviderResponseError(
                "Episode partition 输出必须包含 dict 数组字段 episodes",
                provider=type(self.llm_client).__name__,
                operation="partition_dialogue",
            )
        for episode in episodes:
            message_indices = episode.get("message_indices")
            if (
                not isinstance(message_indices, list)
                or not message_indices
                or any(
                    isinstance(index, bool)
                    or not isinstance(index, int)
                    or index < 1
                    or index > len(dialogue_window)
                    for index in message_indices
                )
                or not isinstance(episode.get("cue"), str)
                or not episode["cue"].strip()
                or not isinstance(episode.get("narrative"), str)
                or not episode["narrative"].strip()
                or episode.get("durability") not in {"DURABLE", "TRANSIENT", "UNCERTAIN"}
            ):
                raise ProviderResponseError(
                    "Episode partition 输出缺少有效的 durability 结构",
                    provider=type(self.llm_client).__name__,
                    operation="partition_dialogue",
                )
        return episodes

    def compute_prediction_error(self, s_in_memories: List[str], cue: str, actual_narrative: str) -> Tuple[bool, str, float]:
        """
        2. 基于历史记忆 S_in 与 cue 进行预测，计算预测残差 K_in
        返回: (is_novel_info, residual_content, novelty_score)
        """
        user_prompt = f"Existing Memories S_in:\n{s_in_memories}\nCue (c_in): {cue}\nActual Narrative: {actual_narrative}"
        res = self.llm_client.generate_json(PROMPT_NEMORI_PREDICT_AND_RESIDUE, user_prompt)
        
        is_novel = res.get("is_novel_info")
        residual = res.get("prediction_error_residual")
        score = res.get("novelty_score")
        if not isinstance(is_novel, bool) or not isinstance(residual, str) or not isinstance(score, (int, float)):
            raise ProviderResponseError(
                "Prediction error 输出字段类型不符合约定",
                provider=type(self.llm_client).__name__,
                operation="compute_prediction_error",
            )
        score = float(score)

        # The provider decides whether a residual is durable.  This method is
        # retained as a compatibility API, but no local novelty threshold or
        # keyword gate is allowed to turn a provider proposal into a write.
        if is_novel and residual.strip():
            return True, residual.strip(), score
        return False, "", score

    def propose_inferred_lifecycle(
        self,
        current_memories: List[Dict[str, str]],
        candidate_text: str,
        provenance_event_id: str,
    ):
        """Ask the provider for the lifecycle operation of an inferred fact.

        Automatic memory uses the same typed lifecycle contract as explicit
        memory.  The provider may return NOOP, ADD, UPDATE, CORRECT, or REMOVE; the
        repository remains the authority that validates shape, target identity,
        provenance, and atomic application.
        """
        from src.memory.core.schema import (
            Granularity,
            LifecycleOperation,
            LifecycleProposal,
            MemoryType,
            SemanticMetadata,
        )

        schema = {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": [item.value for item in LifecycleOperation]},
                "target_memory_id": {"type": ["string", "null"]},
                "content": {"type": "string"},
                "memory_type": {"type": "string", "enum": [item.value for item in MemoryType]},
                "granularity": {"type": "string", "enum": [item.value for item in Granularity]},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "importance_score": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
                "semantic_metadata": {
                    "type": "object",
                    "properties": {
                        name: {"type": "array", "items": {"type": "string"}}
                        for name in sorted(_CANONICAL_SEMANTIC_METADATA_FIELDS)
                    },
                    "additionalProperties": False,
                },
            },
            "required": ["operation", "target_memory_id", "content", "memory_type", "granularity", "confidence", "importance_score"],
            "additionalProperties": False,
        }
        system_prompt = (
            "You are the lifecycle proposer for automatic long-term memory.\n"
            "A candidate was distilled from a multi-turn dialogue. Compare it with the active "
            "memories and return exactly one typed lifecycle proposal.\n"
            "Use NOOP when it adds no durable fact or is already represented. Use ADD for a "
            "new independent durable fact. Use UPDATE when it supplements the same fact. Use "
            "CORRECT when a newer explicit user statement supersedes or contradicts an active "
            "memory; target that memory's exact ID. Use REMOVE only when a newer user statement "
            "withdraws a preference without a replacement, or makes an unpinned profile "
            "trait clearly obsolete or fully subsumed; target that memory's exact ID and return "
            "an empty content string. Never REMOVE a memory marked is_pinned=true, and never remove "
            "anything merely because it is old, was not recently mentioned, or is uncertain. "
            "Preserve the language of the candidate narrative in content and semantic tags; "
            "do not translate user memories merely because these instructions are in English. "
            "Do not promote assistant suggestions, hypotheticals, questions, or transient discussion "
            "into user facts. Prefer the newer "
            "explicit user statement when facts conflict. Return only the requested JSON object. "
            "Set importance_score only when the candidate intentionally changes durable importance; "
            "otherwise return null. Never lower importance because of time or access age."
        )
        user_prompt = f"Active memories:\n{current_memories}\n\nCandidate narrative:\n{candidate_text}"
        result = self.llm_client.generate_json(
            system_prompt,
            user_prompt,
            response_schema=schema,
            schema_name="automatic_lifecycle",
        )
        # Some OpenAI-compatible models use the shorter action/memory_id
        # spelling even when the semantic decision is correct. Normalize that
        # transport variation at the provider boundary; do not reinterpret the
        # operation or apply a local content rule.
        operation_value = result.get("operation")
        if operation_value is None:
            operation_value = result.get("action")
        # Some OpenAI-compatible models emit NONE for the typed no-op case.
        # This is a transport alias only: semantic lifecycle decisions remain
        # provider-owned and the canonical in-memory value is still NOOP.
        if isinstance(operation_value, str) and operation_value.strip().upper() == "NONE":
            operation_value = "NOOP"
        target_value = result.get("target_memory_id")
        if target_value is None:
            target_value = result.get("memory_id")
        memory_type_value = result.get("memory_type", MemoryType.EPISODIC_GRAPH.value)
        granularity_value = result.get("granularity", Granularity.FINE_EPISODE.value)
        confidence_value = result.get("confidence", 0.8)
        importance_value = result.get("importance_score")
        try:
            operation = LifecycleOperation(str(operation_value).upper())
            memory_type = MemoryType(str(memory_type_value).upper())
            granularity = Granularity(str(granularity_value).upper())
            confidence = float(confidence_value)
            importance_score = None if importance_value is None else float(importance_value)
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderResponseError(
                "automatic lifecycle proposal has invalid enum or confidence",
                provider=type(self.llm_client).__name__,
                operation="propose_inferred_lifecycle",
            ) from exc

        target_id = target_value
        if target_id is not None and (not isinstance(target_id, str) or not target_id.strip()):
            target_id = None
        content = result.get("content", "")
        if not isinstance(content, str):
            raise ProviderResponseError(
                "automatic lifecycle proposal content must be a string",
                provider=type(self.llm_client).__name__,
                operation="propose_inferred_lifecycle",
            )
        semantic_metadata = _parse_provider_semantic_metadata(
            result.get("semantic_metadata"),
            provider=type(self.llm_client).__name__,
        )
        return LifecycleProposal(
            operation=operation,
            content=content.strip() or None,
            target_memory_id=target_id,
            memory_type=memory_type,
            granularity=granularity,
            confidence=confidence,
            importance_score=importance_score,
            semantic_metadata=semantic_metadata,
            provenance_event_ids=(provenance_event_id,),
        )

    def propose_explicit_lifecycle(
        self,
        current_memories: List[Dict[str, str]],
        candidate_text: str,
        provenance_event_id: str,
    ):
        """Generates canonical LifecycleProposal for explicit statements via structured provider output."""
        from src.memory.core.schema import (
            Granularity,
            LifecycleOperation,
            LifecycleProposal,
            MemoryType,
        )

        schema = {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": [
                        LifecycleOperation.ADD.value,
                        LifecycleOperation.NOOP.value,
                        LifecycleOperation.UPDATE.value,
                        LifecycleOperation.CORRECT.value,
                        LifecycleOperation.REMOVE.value,
                    ],
                },
                "target_memory_id": {"type": ["string", "null"]},
                "content": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["operation", "target_memory_id", "content", "reason"],
            "additionalProperties": False,
        }
        system_prompt = (
            "You are a canonical memory lifecycle decision proposer.\n"
            "Given existing active memories and a new user explicit statement, determine the lifecycle operation.\n"
            "Respond in JSON matching schema:\n"
            "{\n"
            '  "operation": "ADD" | "NOOP" | "UPDATE" | "CORRECT" | "REMOVE",\n'
            '  "target_memory_id": string or null,\n'
            '  "content": string,\n'
            '  "reason": string\n'
            "}\n"
            "Decision semantics:\n"
            "1. NOOP: Candidate is identical or semantically duplicate to an existing memory.\n"
            "2. CORRECT: Candidate directly contradicts or replaces an existing active memory; copy that memory's exact target_memory_id.\n"
            "3. UPDATE: Candidate enriches or supplements an existing active memory; copy that memory's exact target_memory_id.\n"
            "4. ADD: Candidate is a new independent memory.\n"
            "5. REMOVE: The user explicitly withdraws an existing memory without a replacement; return empty content and its exact ID. If the target is ambiguous, do not remove anything.\n"
            "For CORRECT, UPDATE, REMOVE, and NOOP against an existing memory, target_memory_id is mandatory and must be copied exactly from Active Memories.\n"
            "For ADD, target_memory_id must be null. Never invent, truncate, or infer a memory ID.\n"
        )
        user_prompt = (
            "Existing Memories (copy IDs exactly; do not invent IDs):\n"
            f"{current_memories}\n\nCandidate Statement:\n{candidate_text}"
        )
        res = self.llm_client.generate_json(
            system_prompt,
            user_prompt,
            response_schema=schema,
            schema_name="explicit_proposal",
        )

        op_str = res.get("operation")
        if not isinstance(op_str, str) or not op_str.strip():
            raise ProviderResponseError(
                "explicit lifecycle proposal operation is missing",
                provider=type(self.llm_client).__name__,
                operation="propose_explicit_lifecycle",
            )
        try:
            op = LifecycleOperation(op_str.strip().upper())
        except ValueError as exc:
            raise ProviderResponseError(
                "explicit lifecycle proposal operation is invalid",
                provider=type(self.llm_client).__name__,
                operation="propose_explicit_lifecycle",
            ) from exc

        target_id = res.get("target_memory_id")
        if not isinstance(target_id, str) or not target_id.strip():
            target_id = None

        content = res.get("content")
        if not isinstance(content, str):
            raise ProviderResponseError(
                "explicit lifecycle proposal content must be a string",
                provider=type(self.llm_client).__name__,
                operation="propose_explicit_lifecycle",
            )
        content = content.strip()
        if op in {LifecycleOperation.UPDATE, LifecycleOperation.CORRECT, LifecycleOperation.REMOVE} and not target_id:
            raise ProviderResponseError(
                "explicit lifecycle update/correction requires an exact target_memory_id",
                provider=type(self.llm_client).__name__,
                operation="propose_explicit_lifecycle",
            )
        if op is LifecycleOperation.NOOP and current_memories and not target_id:
            raise ProviderResponseError(
                "explicit lifecycle NOOP against active memories requires an exact target_memory_id",
                provider=type(self.llm_client).__name__,
                operation="propose_explicit_lifecycle",
            )
        if op in {LifecycleOperation.ADD, LifecycleOperation.UPDATE, LifecycleOperation.CORRECT} and not content:
            raise ProviderResponseError(
                "explicit lifecycle proposal content is required for a mutating operation",
                provider=type(self.llm_client).__name__,
                operation="propose_explicit_lifecycle",
            )
        if op is LifecycleOperation.ADD and target_id is not None:
            raise ProviderResponseError(
                "explicit lifecycle ADD cannot target an existing memory",
                provider=type(self.llm_client).__name__,
                operation="propose_explicit_lifecycle",
            )

        return LifecycleProposal(
            operation=op,
            content=content,
            target_memory_id=target_id,
            memory_type=MemoryType.CORE_PROFILE,
            granularity=Granularity.SEMANTIC_INSIGHT,
            confidence=0.95,
            provenance_event_ids=(provenance_event_id,),
        )
