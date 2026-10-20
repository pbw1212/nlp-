from __future__ import annotations

import json
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Tuple

from src.memory.core.config import MemoryConfig
from src.memory.core.schema import Granularity, MemorySnapshot, MemoryType
from src.memory.retrieval.applicability import ApplicabilityEngine
from src.memory.retrieval.applicability_models import (
    ApplicabilityContext,
    ApplicabilityDimension,
    ApplicabilityStatus,
    CandidateScopeRule,
    CandidateScopeState,
    ConstraintOperator,
    ContextKnowledgeState,
    ContextValue,
    MemoryApplicabilityProfile,
)
from src.memory.retrieval.interpretation_models import (
    CONTEXT_PROMPT_VERSION,
    SCOPE_PROMPT_VERSION,
    InterpretationProviderIdentity,
)
from src.memory.retrieval.interpretation_provider import build_semantic_interpretation_provider
from src.memory.retrieval.interpreters import ContextInterpreter, MemoryScopeInterpreter
from src.memory.retrieval.models import RecallCandidate, RecallScoreBreakdown
from src.memory.retrieval.recall import MemoryRecallEngine
from src.memory.retrieval.semantic_pipeline import SemanticRetrievalPipeline
from src.memory.llm.errors import ProviderResponseError
from src.memory.storage.sqlite_manager import SQLiteMemoryManager


ROOT = Path(__file__).resolve().parents[2] / "benchmarks/task2/context_interpretation_eval_v1"


@dataclass(frozen=True)
class ContextCase:
    case_id: str
    language: str
    text: str


@dataclass(frozen=True)
class MemoryCase:
    case_id: str
    language: str
    memory_id: str
    text: str


@dataclass(frozen=True)
class PairCase:
    pair_id: str
    context_case_id: str
    memory_case_id: str


@dataclass(frozen=True)
class InterpretationDataset:
    contexts: Tuple[ContextCase, ...]
    memories: Tuple[MemoryCase, ...]
    pairs: Tuple[PairCase, ...]
    context_gold: Mapping[str, ApplicabilityContext]
    scope_gold: Mapping[str, MemoryApplicabilityProfile]
    pair_gold: Mapping[str, ApplicabilityStatus]


@dataclass(frozen=True)
class InterpretationMetrics:
    precision: float
    recall: float
    f1: float
    state_accuracy: float
    false_known_rate: float
    unsupported_known_rate: float | None
    supported_known_precision: float | None
    unsupported_known_count: int
    predicted_known_count: int


@dataclass(frozen=True)
class ScopeMetrics:
    scope_state_accuracy: float
    explicit_constraint_accuracy: float
    explicit_general_accuracy: float
    unspecified_accuracy: float
    value_accuracy: float
    operator_accuracy: float


@dataclass(frozen=True)
class AttributionMetrics:
    oracle_oracle: float
    predicted_context_oracle_scope: float
    oracle_context_predicted_scope: float
    predicted_predicted: float


@dataclass(frozen=True)
class InterpretationEvaluationReport:
    dataset_version: str
    context_count: int
    memory_count: int
    pair_count: int
    provider: Mapping[str, str]
    prompt_versions: Tuple[str, str]
    context_metrics: InterpretationMetrics
    scope_metrics: ScopeMetrics
    attribution: AttributionMetrics
    end_to_end_accuracy: float
    usage: Mapping[str, int]
    diagnostics: Mapping[str, float | None]
    errors: Mapping[str, Tuple[str, ...]]
    semantic_performance_claim_allowed: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class InterpretationDatasetError(ValueError):
    pass


@dataclass(frozen=True)
class InterpretationSmokeReport:
    provider: Mapping[str, str]
    context_passed: bool
    scope_passed: bool
    end_to_end_passed: bool
    predicted_status: str
    usage: Mapping[str, int]


def _read(name: str, root: Path = ROOT) -> Mapping[str, Any]:
    value = json.loads((root / name).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") not in {1, 2}:
        raise InterpretationDatasetError(f"invalid {name}")
    return value


def load_dataset(
    root: Path = ROOT,
    *,
    expected_counts: tuple[int, int, int] = (30, 30, 10),
) -> InterpretationDataset:
    runtime, gold = _read("runtime.json", root), _read("gold.json", root)
    if set(runtime) != {"schema_version", "contexts", "memories", "pairs"}:
        raise InterpretationDatasetError("runtime fields invalid")
    if set(gold) != {"schema_version", "context_gold", "scope_gold", "pair_gold"}:
        raise InterpretationDatasetError("gold fields invalid")
    contexts = tuple(ContextCase(**item) for item in runtime["contexts"])
    memories = tuple(MemoryCase(**item) for item in runtime["memories"])
    pairs = tuple(PairCase(**item) for item in runtime["pairs"])
    context_ids = {item.case_id for item in contexts}
    memory_ids = {item.case_id for item in memories}
    if (len(contexts), len(memories), len(pairs)) != expected_counts:
        raise InterpretationDatasetError("v1 size mismatch")
    if len(context_ids) != len(contexts) or len(memory_ids) != len(memories):
        raise InterpretationDatasetError("duplicate IDs")
    if {item.language for item in contexts + memories} != {"zh", "en", "mixed"}:
        raise InterpretationDatasetError("language coverage missing")

    context_gold = {}
    for item in gold["context_gold"]:
        values = tuple(
            ContextValue(
                ApplicabilityDimension(raw[0]), raw[1], ContextKnowledgeState(raw[2]), tuple(raw[3])
            )
            for raw in item["values"]
        )
        context_gold[item["case_id"]] = ApplicabilityContext(values)
    scope_gold = {}
    for item in gold["scope_gold"]:
        rules = tuple(
            CandidateScopeRule(
                ApplicabilityDimension(raw[0]), raw[1], CandidateScopeState(raw[2]),
                tuple(raw[3]), ConstraintOperator(raw[4])
            )
            for raw in item["rules"]
        )
        scope_gold[item["case_id"]] = MemoryApplicabilityProfile(item["case_id"], rules)
    pair_gold = {item[0]: ApplicabilityStatus(item[1]) for item in gold["pair_gold"]}
    if set(context_gold) != context_ids or set(scope_gold) != memory_ids:
        raise InterpretationDatasetError("gold coverage mismatch")
    if set(pair_gold) != {item.pair_id for item in pairs}:
        raise InterpretationDatasetError("pair gold coverage mismatch")
    if any(item.context_case_id not in context_ids or item.memory_case_id not in memory_ids for item in pairs):
        raise InterpretationDatasetError("pair reference missing")
    return InterpretationDataset(contexts, memories, pairs, context_gold, scope_gold, pair_gold)


class OracleFixtureProvider:
    """用独立人工 gold 驱动的 orchestration oracle；不是 semantic model。"""

    def __init__(self, dataset: InterpretationDataset, revisions: Mapping[str, str]) -> None:
        self.dataset = dataset
        self.revisions = revisions
        self._identity = InterpretationProviderIdentity("oracle-fixture", "human-gold-v1")

    @property
    def identity(self):
        return self._identity

    def interpret(self, task):
        if task.kind.value == "CURRENT_CONTEXT":
            case_id = task.payload["sources"][0]["source_id"]
            context = self.dataset.context_gold[case_id]
            return {
                "schema_version": 1,
                "values": [
                    {
                        "dimension": value.dimension.value, "field": value.field,
                        "state": value.state.value, "values": list(value.values),
                        "evidence": [] if value.state is ContextKnowledgeState.UNKNOWN else [{
                            "evidence_ref": f"source:{case_id}", "kind": "EXPLICIT", "reason": "human gold"
                        }],
                    }
                    for value in context.values
                ],
                "warnings": [],
            }
        memory_id = task.payload["memory_id"]
        profile = self.dataset.scope_gold[memory_id]
        return {
            "schema_version": 1, "memory_id": memory_id,
            "rules": [
                {
                    "dimension": rule.dimension.value, "field": rule.field,
                    "scope_state": rule.scope_state.value, "values": list(rule.values),
                    "operator": rule.operator.value,
                    "evidence": [] if rule.scope_state is CandidateScopeState.UNSPECIFIED else [{
                        "evidence_ref": f"revision:{self.revisions[memory_id]}",
                        "kind": "EXPLICIT", "reason": "human gold",
                    }],
                }
                for rule in profile.rules
            ],
            "warnings": [],
        }


class CheckpointProvider:
    """Marker provider used to rescore saved predictions without network calls."""

    def __init__(self, dataset_version: str):
        self._identity = InterpretationProviderIdentity("round1-checkpoint", dataset_version)

    @property
    def identity(self):
        return self._identity

    @property
    def usage(self):
        return {"request_count": 0, "input_tokens": 0, "output_tokens": 0}

    def interpret(self, task):
        raise ProviderResponseError(
            "checkpoint prediction missing; rescore cannot reconstruct provider output",
            provider=self._identity.provider,
            operation=task.kind.value.lower(),
        )


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _optional_ratio(numerator: int, denominator: int) -> float | None:
    """Undefined diagnostic rates remain N/A instead of masquerading as zero."""
    return numerator / denominator if denominator else None


def _context_metrics(gold, predicted) -> tuple[InterpretationMetrics, Tuple[str, ...]]:
    tp = fp = fn = correct_state = total_state = false_known = unknown_total = 0
    unsupported_known = predicted_known = 0
    errors = []
    for case_id, gold_context in gold.items():
        pred_context = predicted[case_id]
        gold_map = {item.key: item for item in gold_context.values}
        pred_map = {item.key: item for item in pred_context.values}
        gold_keys, pred_keys = set(gold_map), set(pred_map)
        tp += len(gold_keys & pred_keys)
        fp += len(pred_keys - gold_keys)
        fn += len(gold_keys - pred_keys)
        for key in set(gold_map) | set(pred_map):
            total_state += 1
            if key in gold_map and key in pred_map and gold_map[key].state is pred_map[key].state:
                correct_state += 1
            gold_unknown = key not in gold_map or gold_map[key].state is ContextKnowledgeState.UNKNOWN
            if gold_unknown:
                unknown_total += 1
                if key in pred_map and pred_map[key].state is ContextKnowledgeState.KNOWN:
                    false_known += 1
            if key in pred_map and pred_map[key].state is ContextKnowledgeState.KNOWN:
                predicted_known += 1
                if not (key in gold_map and gold_map[key].state is ContextKnowledgeState.KNOWN and gold_map[key].values == pred_map[key].values):
                    unsupported_known += 1
        exact_gold = {(item.key, item.state, item.values) for item in gold_context.values}
        exact_pred = {(item.key, item.state, item.values) for item in pred_context.values}
        if exact_gold != exact_pred:
            errors.append(case_id)
    precision, recall = _ratio(tp, tp + fp), _ratio(tp, tp + fn)
    return InterpretationMetrics(
        precision, recall, _ratio(2 * precision * recall, precision + recall),
        _ratio(correct_state, total_state), _ratio(false_known, unknown_total),
        _optional_ratio(unsupported_known, predicted_known),
        _optional_ratio(predicted_known - unsupported_known, predicted_known),
        unsupported_known, predicted_known,
    ), tuple(errors)


def _scope_metrics(gold, predicted) -> tuple[ScopeMetrics, Tuple[str, ...]]:
    state_ok = value_ok = operator_ok = total = 0
    state_totals = {state: 0 for state in CandidateScopeState}
    state_correct = {state: 0 for state in CandidateScopeState}
    errors = []
    for case_id, gold_profile in gold.items():
        pred_profile = predicted[case_id]
        gold_map, pred_map = ({item.key: item for item in profile.rules} for profile in (gold_profile, pred_profile))
        for key in set(gold_map) | set(pred_map):
            total += 1
            if key in gold_map and key in pred_map:
                state_ok += gold_map[key].scope_state is pred_map[key].scope_state
                gold_state = gold_map[key].scope_state
                state_totals[gold_state] += 1
                state_correct[gold_state] += gold_state is pred_map[key].scope_state
                value_ok += gold_map[key].values == pred_map[key].values
                operator_ok += gold_map[key].operator is pred_map[key].operator
            elif key in gold_map:
                state_totals[gold_map[key].scope_state] += 1
        if gold_map != pred_map:
            errors.append(case_id)
    return ScopeMetrics(
        _ratio(state_ok, total),
        _ratio(state_correct[CandidateScopeState.EXPLICIT_CONSTRAINT], state_totals[CandidateScopeState.EXPLICIT_CONSTRAINT]),
        _ratio(state_correct[CandidateScopeState.EXPLICIT_GENERAL], state_totals[CandidateScopeState.EXPLICIT_GENERAL]),
        _ratio(state_correct[CandidateScopeState.UNSPECIFIED], state_totals[CandidateScopeState.UNSPECIFIED]),
        _ratio(value_ok, total),
        _ratio(operator_ok, total),
    ), tuple(errors)


def _candidate(snapshot: MemorySnapshot) -> RecallCandidate:
    return RecallCandidate(snapshot, 1, 1.0, RecallScoreBreakdown(1.0, 0.0), (), ())


def _attribution(dataset, snapshots, predicted_contexts, predicted_profiles, *, sparse_profiles: bool):
    engine = ApplicabilityEngine(sparse_profiles=sparse_profiles)
    counts = [0, 0, 0, 0]
    errors = [[], [], [], []]
    for pair in dataset.pairs:
        candidate = _candidate(snapshots[pair.memory_case_id])
        combinations = (
            (dataset.context_gold[pair.context_case_id], dataset.scope_gold[pair.memory_case_id]),
            (predicted_contexts[pair.context_case_id], dataset.scope_gold[pair.memory_case_id]),
            (dataset.context_gold[pair.context_case_id], predicted_profiles[pair.memory_case_id]),
            (predicted_contexts[pair.context_case_id], predicted_profiles[pair.memory_case_id]),
        )
        for index, (context, profile) in enumerate(combinations):
            actual = engine.assess(candidate, context, profile).overall
            if actual is dataset.pair_gold[pair.pair_id]:
                counts[index] += 1
            else:
                errors[index].append(pair.pair_id)
    total = len(dataset.pairs)
    return AttributionMetrics(*(_ratio(value, total) for value in counts)), tuple(tuple(item) for item in errors)


def _diagnostics(gold_contexts, predicted_contexts, gold_profiles, predicted_profiles):
    context_gold_fields = context_pred_fields = context_correct_fields = 0
    context_correct_values = context_correct_states = 0
    empty_gold_cases = empty_gold_abstained = 0
    for case_id, gold in gold_contexts.items():
        gold_map = {item.key: item for item in gold.values}
        pred_map = {item.key: item for item in predicted_contexts[case_id].values}
        shared = set(gold_map) & set(pred_map)
        context_gold_fields += len(gold_map)
        context_pred_fields += len(pred_map)
        context_correct_fields += len(shared)
        context_correct_values += sum(gold_map[key].values == pred_map[key].values for key in shared)
        context_correct_states += sum(gold_map[key].state is pred_map[key].state for key in shared)
        if not gold_map:
            empty_gold_cases += 1
            empty_gold_abstained += not pred_map

    scope_gold_fields = scope_pred_fields = scope_correct_fields = 0
    scope_correct_values = scope_correct_states = 0
    for case_id, gold in gold_profiles.items():
        gold_map = {item.key: item for item in gold.rules}
        pred_map = {item.key: item for item in predicted_profiles[case_id].rules}
        shared = set(gold_map) & set(pred_map)
        scope_gold_fields += len(gold_map)
        scope_pred_fields += len(pred_map)
        scope_correct_fields += len(shared)
        scope_correct_values += sum(gold_map[key].values == pred_map[key].values for key in shared)
        scope_correct_states += sum(
            gold_map[key].scope_state is pred_map[key].scope_state for key in shared
        )

    return {
        "context_field_selection_precision": _ratio(context_correct_fields, context_pred_fields),
        "context_field_selection_recall": _ratio(context_correct_fields, context_gold_fields),
        "context_field_selection_f1": _ratio(2 * context_correct_fields, context_pred_fields + context_gold_fields),
        "context_field_selection_accuracy": _ratio(context_correct_fields, context_gold_fields),
        "context_false_field_invention_rate": _ratio(
            context_pred_fields - context_correct_fields, context_pred_fields
        ),
        "context_given_field_value_accuracy": _ratio(context_correct_values, context_correct_fields),
        "context_given_field_state_accuracy": _ratio(context_correct_states, context_correct_fields),
        "unsupported_field_abstention_rate": _optional_ratio(
            empty_gold_abstained, empty_gold_cases
        ),
        "scope_field_selection_precision": _ratio(scope_correct_fields, scope_pred_fields),
        "scope_field_selection_recall": _ratio(scope_correct_fields, scope_gold_fields),
        "scope_field_selection_f1": _ratio(2 * scope_correct_fields, scope_pred_fields + scope_gold_fields),
        "scope_field_selection_accuracy": _ratio(scope_correct_fields, scope_gold_fields),
        "scope_false_field_invention_rate": _ratio(
            scope_pred_fields - scope_correct_fields, scope_pred_fields
        ),
        "scope_given_field_value_accuracy": _ratio(scope_correct_values, scope_correct_fields),
        "scope_given_field_state_accuracy": _ratio(scope_correct_states, scope_correct_fields),
    }


def evaluate(
    provider_name: str = "oracle-fixture",
    *,
    dataset_root: Path = ROOT,
    dataset_version: str = "context_interpretation_eval_v1",
    expected_counts: tuple[int, int, int] = (30, 30, 10),
    prediction_checkpoint: Path | None = None,
    staged: bool = False,
) -> InterpretationEvaluationReport:
    dataset = load_dataset(dataset_root, expected_counts=expected_counts)
    with tempfile.TemporaryDirectory(prefix="task2-interpretation-") as temp_dir:
        repository = SQLiteMemoryManager(str(Path(temp_dir) / "interpretation.db"))
        snapshots = {}
        revisions = {}
        for memory in dataset.memories:
            event, _ = repository.register_ingestion_event(memory.text, event_id=f"interpret-{memory.memory_id}")
            repository.create_memory(
                memory_id=memory.memory_id, content=memory.text,
                memory_type=MemoryType.PREFERENCE_RULES,
                granularity=Granularity.SEMANTIC_INSIGHT, event_ids=(event.event_id,),
            )
            snapshot = repository.get_current(memory.memory_id)
            snapshots[memory.case_id] = snapshot
            revisions[memory.case_id] = snapshot.revision.revision_id
        if provider_name == "oracle-fixture":
            provider = OracleFixtureProvider(dataset, revisions)
            semantic_claim = False
        elif provider_name == "checkpoint":
            provider = CheckpointProvider(dataset_version)
            semantic_claim = False
        elif provider_name == "real":
            provider = build_semantic_interpretation_provider(MemoryConfig.from_env())
            semantic_claim = True
        else:
            raise ValueError(f"unknown provider: {provider_name}")

        context_interpreter = ContextInterpreter(provider, staged=staged)
        scope_interpreter = MemoryScopeInterpreter(provider, staged=staged)
        checkpoint_data: dict[str, Any] = {
            "contexts": {}, "scopes": {},
            "usage": {"request_count": 0, "input_tokens": 0, "output_tokens": 0},
        }
        if prediction_checkpoint is not None and prediction_checkpoint.is_file():
            loaded = json.loads(prediction_checkpoint.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and set(loaded) == {"contexts", "scopes", "usage"}:
                checkpoint_data = loaded
        prior_usage = dict(checkpoint_data["usage"])
        predicted_contexts = {
            case_id: ApplicabilityContext.from_dict(value)
            for case_id, value in checkpoint_data["contexts"].items()
            if case_id in dataset.context_gold
        }
        context_validation_errors = []
        for case in dataset.contexts:
            if case.case_id in predicted_contexts:
                continue
            try:
                predicted_contexts[case.case_id] = context_interpreter.interpret(
                    case.text, query_id=case.case_id
                ).context
                checkpoint_data["contexts"][case.case_id] = {
                    "values": [
                        {
                            "dimension": value.dimension.value,
                            "field": value.field,
                            "state": value.state.value,
                            "values": list(value.values),
                        }
                        for value in predicted_contexts[case.case_id].values
                    ]
                }
                checkpoint_data["usage"] = _combined_usage(
                    prior_usage, getattr(provider, "usage", {})
                )
                _write_checkpoint(prediction_checkpoint, checkpoint_data)
            except ProviderResponseError:
                context_validation_errors.append(case.case_id)
                predicted_contexts[case.case_id] = ApplicabilityContext(())
        predicted_profiles = {
            case_id: MemoryApplicabilityProfile.from_dict(value)
            for case_id, value in checkpoint_data["scopes"].items()
            if case_id in dataset.scope_gold
        }
        scope_validation_errors = []
        for case in dataset.memories:
            if case.case_id in predicted_profiles:
                continue
            try:
                predicted_profiles[case.case_id] = scope_interpreter.interpret(
                    snapshots[case.case_id]
                ).profile
                checkpoint_data["scopes"][case.case_id] = {
                    "memory_id": predicted_profiles[case.case_id].memory_id,
                    "rules": [
                        {
                            "dimension": rule.dimension.value,
                            "field": rule.field,
                            "scope_state": rule.scope_state.value,
                            "values": list(rule.values),
                            "operator": rule.operator.value,
                            "evidence_refs": list(rule.evidence_refs),
                        }
                        for rule in predicted_profiles[case.case_id].rules
                    ],
                }
                checkpoint_data["usage"] = _combined_usage(
                    prior_usage, getattr(provider, "usage", {})
                )
                _write_checkpoint(prediction_checkpoint, checkpoint_data)
            except ProviderResponseError:
                scope_validation_errors.append(case.case_id)
                predicted_profiles[case.case_id] = MemoryApplicabilityProfile(
                    case.memory_id, ()
                )
        context_metrics, context_errors = _context_metrics(dataset.context_gold, predicted_contexts)
        scope_metrics, scope_errors = _scope_metrics(dataset.scope_gold, predicted_profiles)
        attribution, attribution_errors = _attribution(
            dataset, snapshots, predicted_contexts, predicted_profiles,
            sparse_profiles=dataset_version.endswith("_v2"),
        )
        diagnostics = _diagnostics(
            dataset.context_gold, predicted_contexts, dataset.scope_gold, predicted_profiles
        )
        usage = _combined_usage(prior_usage, getattr(provider, "usage", {}))

    return InterpretationEvaluationReport(
        dataset_version=dataset_version,
        context_count=len(dataset.contexts), memory_count=len(dataset.memories), pair_count=len(dataset.pairs),
        provider=asdict(provider.identity),
        prompt_versions=(CONTEXT_PROMPT_VERSION, SCOPE_PROMPT_VERSION),
        context_metrics=context_metrics, scope_metrics=scope_metrics, attribution=attribution,
        end_to_end_accuracy=attribution.predicted_predicted,
        usage=usage,
        diagnostics=diagnostics,
        errors={
            "context": context_errors, "scope": scope_errors,
            "context_validation": tuple(context_validation_errors),
            "scope_validation": tuple(scope_validation_errors),
            "oracle_oracle": attribution_errors[0], "predicted_oracle": attribution_errors[1],
            "oracle_predicted": attribution_errors[2], "predicted_predicted": attribution_errors[3],
        },
        semantic_performance_claim_allowed=semantic_claim,
    )


def _write_checkpoint(path: Path | None, data: Mapping[str, Any]) -> None:
    if path is None:
        return
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(path)


def _combined_usage(prior: Mapping[str, int], current: Mapping[str, int]) -> dict[str, int]:
    return {
        key: int(prior.get(key, 0)) + int(current.get(key, 0))
        for key in ("request_count", "input_tokens", "output_tokens")
    }


def smoke(provider_name: str = "real") -> InterpretationSmokeReport:
    """真实 1 context + 1 scope + 1 recall-to-applicability smoke。"""
    if provider_name != "real":
        raise ValueError("real smoke 不允许 fake/oracle provider")
    dataset = load_dataset()
    context_case = next(item for item in dataset.contexts if item.case_id == "c01")
    memory_case = next(item for item in dataset.memories if item.case_id == "m06")
    with tempfile.TemporaryDirectory(prefix="task2-interpretation-smoke-") as temp_dir:
        repository = SQLiteMemoryManager(str(Path(temp_dir) / "smoke.db"))
        event, _ = repository.register_ingestion_event(
            memory_case.text, event_id="phase2e-real-smoke-memory"
        )
        repository.create_memory(
            memory_id=memory_case.memory_id,
            content=memory_case.text,
            memory_type=MemoryType.PREFERENCE_RULES,
            granularity=Granularity.SEMANTIC_INSIGHT,
            event_ids=(event.event_id,),
        )
        provider = build_semantic_interpretation_provider(MemoryConfig.from_env())
        pipeline = SemanticRetrievalPipeline(
            MemoryRecallEngine(repository),
            ContextInterpreter(provider),
            MemoryScopeInterpreter(provider),
        )
        result = pipeline.run(context_case.text, query_id=context_case.case_id, top_k=1)
        context_passed = bool(result.context_interpretation.facts) and all(
            fact.evidence or fact.value.state is ContextKnowledgeState.UNKNOWN
            for fact in result.context_interpretation.facts
        )
        scope_passed = bool(result.scope_interpretations[0].rules) and all(
            trace.evidence or trace.rule.scope_state is CandidateScopeState.UNSPECIFIED
            for trace in result.scope_interpretations[0].rules
        )
        actual = result.applicability_result.assessments[0].overall
        return InterpretationSmokeReport(
            provider=asdict(provider.identity),
            context_passed=context_passed,
            scope_passed=scope_passed,
            end_to_end_passed=bool(result.recall_result.candidates)
            and bool(result.applicability_result.assessments),
            predicted_status=actual.value,
            usage=dict(getattr(provider, "usage", {})),
        )
