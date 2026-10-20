from __future__ import annotations

import json
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

from src.memory.core.schema import ChangeKind, Granularity, MemorySnapshot, MemoryType, ProvenanceAuthority
from src.memory.retrieval.applicability import ApplicabilityEngine
from src.memory.retrieval.applicability_models import (
    ApplicabilityContext,
    ApplicabilityDecision,
    ApplicabilityDimension,
    ApplicabilityStatus,
    CandidateScopeState,
    ConstraintOperator,
    ContextKnowledgeState,
    MemoryApplicabilityProfile,
)
from src.memory.retrieval.metrics import recall_at_k, reciprocal_rank
from src.memory.retrieval.recall import MemoryRecallEngine
from src.memory.storage.sqlite_manager import SQLiteMemoryManager


DATASET_ROOT = Path(__file__).resolve().parents[2] / "benchmarks/task2/applicability_eval_v1"
K_VALUES = (1, 2, 4, 8)
METHODS = ("B0_RECALL_ONLY", "B1_PERMISSIVE", "B1_STRICT", "B2_APPLICABILITY")
SUBSETS = ("supported", "rich_reasoning")


@dataclass(frozen=True)
class MemorySpec:
    memory_id: str
    domain: str
    content: str
    initial_content: Optional[str] = None


@dataclass(frozen=True)
class QuerySpec:
    query_id: str
    domain: str
    family: str
    query: str
    candidate_universe: Tuple[str, ...]
    expected_recall_targets: Tuple[str, ...]


@dataclass(frozen=True)
class GoldPair:
    memory_id: str
    label: ApplicabilityStatus
    reason_dimensions: Tuple[ApplicabilityDimension, ...]
    subset: str


@dataclass(frozen=True)
class EvaluationDataset:
    memories: Tuple[MemorySpec, ...]
    profiles: Mapping[str, MemoryApplicabilityProfile]
    queries: Tuple[QuerySpec, ...]
    contexts: Mapping[str, ApplicabilityContext]
    gold: Mapping[str, Mapping[str, GoldPair]]


@dataclass(frozen=True)
class CaseResult:
    query_id: str
    family: str
    domain: str
    memory_id: str
    original_rank: int
    recall_score: float
    gold_label: str
    predicted_label: str
    retained: bool
    reason_codes: Tuple[str, ...]
    reason_dimensions: Tuple[str, ...]
    subset: str


@dataclass(frozen=True)
class MetricSet:
    assessed_count: int
    accuracy: float
    applicable_retention_rate: float
    mismatch_rejection_rate: float
    false_rejection_rate: float
    false_acceptance_rate: float
    mismatch_contamination: float
    unknown_preservation_rate: float
    ambiguous_preservation_rate: float
    unknown_retention_rate: float
    ambiguous_retention_rate: float
    definite_decision_coverage: float
    uncertainty_rate: float


@dataclass(frozen=True)
class BreakdownMetrics:
    name: str
    gold_count: int
    accuracy: float
    mismatch_rejection_rate: float


@dataclass(frozen=True)
class MethodMetrics:
    method: str
    k: int
    recall_at_k: float
    mrr: float
    metrics: MetricSet
    by_dimension: Tuple[BreakdownMetrics, ...]
    by_family: Tuple[BreakdownMetrics, ...]
    by_subset: Tuple[BreakdownMetrics, ...]


@dataclass(frozen=True)
class EvaluationReport:
    dataset_name: str
    memory_count: int
    query_count: int
    labeled_pair_count: int
    domain_distribution: Tuple[Tuple[str, int], ...]
    dimension_distribution: Tuple[Tuple[str, int], ...]
    family_distribution: Tuple[Tuple[str, int], ...]
    methods_at_k4: Tuple[MethodMetrics, ...]
    k_sensitivity: Tuple[MethodMetrics, ...]
    cases_at_k4: Tuple[CaseResult, ...]
    high_rank_mismatch: Tuple[str, ...]
    false_accepts: Tuple[str, ...]
    false_rejects: Tuple[str, ...]
    uncertain_cases: Tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class DatasetValidationError(ValueError):
    pass


class _ScopedReadRepository:
    def __init__(self, repository: SQLiteMemoryManager, memory_ids: Sequence[str]):
        self._repository = repository
        self._memory_ids = frozenset(memory_ids)

    def list_current(self) -> list[MemorySnapshot]:
        return [
            snapshot
            for snapshot in self._repository.list_current()
            if snapshot.memory_id in self._memory_ids
        ]

    def get_current(self, memory_id: str) -> Optional[MemorySnapshot]:
        if memory_id not in self._memory_ids:
            return None
        return self._repository.get_current(memory_id)

    def get_history(self, memory_id: str):
        if memory_id not in self._memory_ids:
            return []
        return self._repository.get_history(memory_id)


def _read_json(path: Path) -> Mapping[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise DatasetValidationError(f"{path.name} root 必须是 object")
    return payload


def _require_exact_keys(data: Mapping[str, Any], keys: set[str], label: str) -> None:
    if set(data) != keys:
        raise DatasetValidationError(
            f"{label} fields mismatch: expected={sorted(keys)} actual={sorted(data)}"
        )


def _unique(items: Iterable[str], label: str) -> Tuple[str, ...]:
    values = tuple(items)
    if len(values) != len(set(values)):
        raise DatasetValidationError(f"{label} IDs 必须唯一")
    return values


def _runtime_gold_leak_check(payloads: Sequence[Mapping[str, Any]]) -> None:
    forbidden = {"gold", "gold_label", "expected_applicability", "labels"}

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            leaked = set(value) & forbidden
            if leaked:
                raise DatasetValidationError(f"runtime input 泄漏 gold fields: {sorted(leaked)}")
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    for payload in payloads:
        walk(payload)


def load_evaluation_dataset(root: Path = DATASET_ROOT) -> EvaluationDataset:
    memories_payload = _read_json(root / "memories.json")
    profiles_payload = _read_json(root / "profiles.json")
    queries_payload = _read_json(root / "queries.json")
    contexts_payload = _read_json(root / "contexts.json")
    labels_payload = _read_json(root / "labels.json")
    _runtime_gold_leak_check((memories_payload, profiles_payload, queries_payload, contexts_payload))

    _require_exact_keys(memories_payload, {"schema_version", "memories"}, "memories")
    _require_exact_keys(profiles_payload, {"schema_version", "profiles"}, "profiles")
    _require_exact_keys(queries_payload, {"schema_version", "queries"}, "queries")
    _require_exact_keys(contexts_payload, {"schema_version", "contexts"}, "contexts")
    _require_exact_keys(labels_payload, {"schema_version", "format", "labels"}, "labels")
    versions = {
        memories_payload["schema_version"], profiles_payload["schema_version"],
        queries_payload["schema_version"], contexts_payload["schema_version"],
        labels_payload["schema_version"],
    }
    if versions != {1}:
        raise DatasetValidationError(f"unsupported schema versions: {versions}")

    memories = []
    for item in memories_payload["memories"]:
        if not isinstance(item, dict):
            raise DatasetValidationError("memory 必须是 object")
        allowed = {"memory_id", "domain", "content", "initial_content"}
        if set(item) - allowed or not {"memory_id", "domain", "content"}.issubset(item):
            raise DatasetValidationError("memory fields invalid")
        memories.append(MemorySpec(**item))
    memory_ids = _unique((item.memory_id for item in memories), "memory")
    if not 30 <= len(memories) <= 50:
        raise DatasetValidationError("controlled v1 要求 30-50 memories")

    profiles = tuple(MemoryApplicabilityProfile.from_dict(item) for item in profiles_payload["profiles"])
    profile_ids = _unique((item.memory_id for item in profiles), "profile")
    if set(profile_ids) != set(memory_ids):
        raise DatasetValidationError("profiles 必须一一覆盖 memories")

    queries = []
    for item in queries_payload["queries"]:
        _require_exact_keys(
            item,
            {"query_id", "domain", "family", "query", "candidate_universe", "expected_recall_targets"},
            "query",
        )
        query = QuerySpec(
            query_id=item["query_id"], domain=item["domain"], family=item["family"],
            query=item["query"], candidate_universe=tuple(item["candidate_universe"]),
            expected_recall_targets=tuple(item["expected_recall_targets"]),
        )
        if len(query.candidate_universe) != 8 or len(set(query.candidate_universe)) != 8:
            raise DatasetValidationError(f"{query.query_id} candidate universe 必须恰好 8 个唯一 memory")
        if not set(query.candidate_universe) <= set(memory_ids):
            raise DatasetValidationError(f"{query.query_id} 引用不存在 memory")
        if not set(query.expected_recall_targets) <= set(query.candidate_universe):
            raise DatasetValidationError(f"{query.query_id} recall target 不在 candidate universe")
        if not query.expected_recall_targets:
            raise DatasetValidationError(f"{query.query_id} 缺少 recall target")
        queries.append(query)
    query_ids = _unique((item.query_id for item in queries), "query")
    if not 25 <= len(queries) <= 40:
        raise DatasetValidationError("controlled v1 要求 25-40 queries")

    contexts: Dict[str, ApplicabilityContext] = {}
    for item in contexts_payload["contexts"]:
        _require_exact_keys(item, {"query_id", "values"}, "context")
        query_id = item["query_id"]
        if query_id in contexts:
            raise DatasetValidationError(f"duplicate context: {query_id}")
        contexts[query_id] = ApplicabilityContext.from_dict({"values": item["values"]})
    if set(contexts) != set(query_ids):
        raise DatasetValidationError("contexts 必须一一覆盖 queries")

    gold: Dict[str, Dict[str, GoldPair]] = {}
    for entry in labels_payload["labels"]:
        _require_exact_keys(entry, {"query_id", "items"}, "gold query")
        query_id = entry["query_id"]
        if query_id in gold:
            raise DatasetValidationError(f"duplicate gold query: {query_id}")
        pairs: Dict[str, GoldPair] = {}
        for raw in entry["items"]:
            if not isinstance(raw, list) or len(raw) != 4:
                raise DatasetValidationError(f"{query_id} gold pair shape invalid")
            memory_id, label, dimensions, subset = raw
            if memory_id in pairs:
                raise DatasetValidationError(f"{query_id}/{memory_id} duplicate gold")
            if subset not in SUBSETS:
                raise DatasetValidationError(f"invalid subset: {subset}")
            try:
                pair = GoldPair(
                    memory_id=memory_id,
                    label=ApplicabilityStatus(label),
                    reason_dimensions=tuple(ApplicabilityDimension(value) for value in dimensions),
                    subset=subset,
                )
            except (TypeError, ValueError) as exc:
                raise DatasetValidationError(f"invalid gold pair {query_id}/{memory_id}") from exc
            pairs[memory_id] = pair
        gold[query_id] = pairs
    if set(gold) != set(query_ids):
        raise DatasetValidationError("gold queries 必须一一覆盖 runtime queries")
    query_by_id = {query.query_id: query for query in queries}
    for query_id, pairs in gold.items():
        if set(pairs) != set(query_by_id[query_id].candidate_universe):
            raise DatasetValidationError(f"{query_id} gold 必须完整覆盖 candidate universe")

    labels = tuple(pair for pairs in gold.values() for pair in pairs.values())
    for dimension in ApplicabilityDimension:
        dimension_pairs = [pair for pair in labels if dimension in pair.reason_dimensions]
        statuses = {pair.label for pair in dimension_pairs}
        required = {ApplicabilityStatus.MATCH, ApplicabilityStatus.MISMATCH, ApplicabilityStatus.UNKNOWN}
        if not required <= statuses:
            raise DatasetValidationError(f"dimension {dimension.value} 缺少 MATCH/MISMATCH/UNKNOWN coverage")
    ambiguous_dimensions = {
        dimension
        for pair in labels if pair.label is ApplicabilityStatus.AMBIGUOUS
        for dimension in pair.reason_dimensions
    }
    if len(ambiguous_dimensions) < 2:
        raise DatasetValidationError("AMBIGUOUS 必须覆盖多个 dimensions")
    if not any(pair.subset == "rich_reasoning" for pair in labels):
        raise DatasetValidationError("缺少 rich_reasoning subset")

    return EvaluationDataset(
        memories=tuple(memories), profiles={item.memory_id: item for item in profiles},
        queries=tuple(queries), contexts=contexts, gold=gold,
    )


def _populate(repository: SQLiteMemoryManager, memories: Sequence[MemorySpec]) -> None:
    for memory in memories:
        if memory.initial_content:
            old_event, _ = repository.register_ingestion_event(
                memory.initial_content, event_id=f"eval-{memory.memory_id}-old"
            )
            repository.create_memory(
                memory_id=memory.memory_id, content=memory.initial_content,
                memory_type=MemoryType.PREFERENCE_RULES,
                granularity=Granularity.SEMANTIC_INSIGHT,
                event_ids=(old_event.event_id,), authority=ProvenanceAuthority.EXPLICIT_USER,
            )
            event, _ = repository.register_ingestion_event(
                memory.content, event_id=f"eval-{memory.memory_id}-current"
            )
            repository.apply_revision(
                memory.memory_id, memory.content, change_kind=ChangeKind.UPDATE,
                event_ids=(event.event_id,), authority=ProvenanceAuthority.EXPLICIT_USER,
            )
        else:
            event, _ = repository.register_ingestion_event(
                memory.content, event_id=f"eval-{memory.memory_id}"
            )
            repository.create_memory(
                memory_id=memory.memory_id, content=memory.content,
                memory_type=MemoryType.PREFERENCE_RULES,
                granularity=Granularity.SEMANTIC_INSIGHT,
                event_ids=(event.event_id,), authority=ProvenanceAuthority.EXPLICIT_USER,
            )


def _constraint_matches(rule, context_value) -> bool:
    overlap = bool(set(rule.values) & set(context_value.values))
    return not overlap if rule.operator is ConstraintOperator.EXCLUDES else overlap


def _naive_prediction(
    profile: MemoryApplicabilityProfile,
    context: ApplicabilityContext,
    *,
    strict: bool,
) -> tuple[ApplicabilityStatus, bool, Tuple[str, ...]]:
    rules = {rule.key: rule for rule in profile.rules}
    values = {value.key: value for value in context.values}
    for key in sorted(set(rules) | set(values), key=lambda item: (item[0].value, item[1])):
        rule = rules.get(key)
        context_value = values.get(key)
        if rule is None or rule.scope_state is CandidateScopeState.UNSPECIFIED:
            if strict:
                return ApplicabilityStatus.MISMATCH, False, ("NAIVE_MISSING_SCOPE_REJECT",)
            continue
        if rule.scope_state is CandidateScopeState.EXPLICIT_GENERAL:
            continue
        if context_value is None or context_value.state is not ContextKnowledgeState.KNOWN:
            if strict:
                return ApplicabilityStatus.MISMATCH, False, ("NAIVE_UNKNOWN_REJECT",)
            continue
        if not _constraint_matches(rule, context_value):
            return ApplicabilityStatus.MISMATCH, False, ("NAIVE_EXACT_CONFLICT",)
    return ApplicabilityStatus.MATCH, True, ("NAIVE_PASS",)


def _safe_rate(numerator: int, denominator: int, *, empty: float = 1.0) -> float:
    return numerator / denominator if denominator else empty


def calculate_metrics(cases: Sequence[CaseResult]) -> MetricSet:
    count = len(cases)
    gold_match = [case for case in cases if case.gold_label == ApplicabilityStatus.MATCH.value]
    gold_mismatch = [case for case in cases if case.gold_label == ApplicabilityStatus.MISMATCH.value]
    gold_unknown = [case for case in cases if case.gold_label == ApplicabilityStatus.UNKNOWN.value]
    gold_ambiguous = [case for case in cases if case.gold_label == ApplicabilityStatus.AMBIGUOUS.value]
    retained = [case for case in cases if case.retained]
    return MetricSet(
        assessed_count=count,
        accuracy=_safe_rate(sum(case.gold_label == case.predicted_label for case in cases), count, empty=0.0),
        applicable_retention_rate=_safe_rate(sum(case.retained for case in gold_match), len(gold_match)),
        mismatch_rejection_rate=_safe_rate(sum(not case.retained for case in gold_mismatch), len(gold_mismatch)),
        false_rejection_rate=_safe_rate(sum(not case.retained for case in gold_match), len(gold_match), empty=0.0),
        false_acceptance_rate=_safe_rate(sum(case.retained for case in gold_mismatch), len(gold_mismatch), empty=0.0),
        mismatch_contamination=_safe_rate(
            sum(case.gold_label == ApplicabilityStatus.MISMATCH.value for case in retained),
            len(retained), empty=0.0,
        ),
        unknown_preservation_rate=_safe_rate(
            sum(case.retained and case.predicted_label == ApplicabilityStatus.UNKNOWN.value for case in gold_unknown),
            len(gold_unknown),
        ),
        ambiguous_preservation_rate=_safe_rate(
            sum(case.retained and case.predicted_label == ApplicabilityStatus.AMBIGUOUS.value for case in gold_ambiguous),
            len(gold_ambiguous),
        ),
        unknown_retention_rate=_safe_rate(
            sum(case.retained for case in gold_unknown), len(gold_unknown),
        ),
        ambiguous_retention_rate=_safe_rate(
            sum(case.retained for case in gold_ambiguous), len(gold_ambiguous),
        ),
        definite_decision_coverage=_safe_rate(
            sum(case.predicted_label in {ApplicabilityStatus.MATCH.value, ApplicabilityStatus.MISMATCH.value} for case in cases),
            count, empty=0.0,
        ),
        uncertainty_rate=_safe_rate(
            sum(case.predicted_label in {ApplicabilityStatus.UNKNOWN.value, ApplicabilityStatus.AMBIGUOUS.value} for case in cases),
            count, empty=0.0,
        ),
    )


def _breakdown(name: str, cases: Sequence[CaseResult]) -> BreakdownMetrics:
    metrics = calculate_metrics(cases)
    mismatch = [case for case in cases if case.gold_label == ApplicabilityStatus.MISMATCH.value]
    return BreakdownMetrics(
        name=name, gold_count=len(cases), accuracy=metrics.accuracy,
        mismatch_rejection_rate=_safe_rate(sum(not case.retained for case in mismatch), len(mismatch)),
    )


def _method_cases(
    method: str,
    query: QuerySpec,
    recall_result,
    context: ApplicabilityContext,
    profiles: Mapping[str, MemoryApplicabilityProfile],
    gold: Mapping[str, GoldPair],
) -> Tuple[CaseResult, ...]:
    b2 = None
    if method == "B2_APPLICABILITY":
        b2 = ApplicabilityEngine().assess_recall_result(recall_result, context, profiles)
    results = []
    for index, candidate in enumerate(recall_result.candidates):
        memory_id = candidate.snapshot.memory_id
        pair = gold[memory_id]
        if method == "B0_RECALL_ONLY":
            predicted, retained, reasons = ApplicabilityStatus.MATCH, True, ("RECALL_ONLY_RETAIN",)
        elif method == "B1_PERMISSIVE":
            predicted, retained, reasons = _naive_prediction(profiles[memory_id], context, strict=False)
        elif method == "B1_STRICT":
            predicted, retained, reasons = _naive_prediction(profiles[memory_id], context, strict=True)
        elif method == "B2_APPLICABILITY":
            assessment = b2.assessments[index]
            predicted = assessment.overall
            retained = assessment.decision is not ApplicabilityDecision.REJECT
            reasons = tuple(item.reason_code.value for item in assessment.dimensions)
        else:
            raise ValueError(f"unknown method: {method}")
        results.append(CaseResult(
            query_id=query.query_id, family=query.family, domain=query.domain,
            memory_id=memory_id, original_rank=candidate.rank,
            recall_score=round(candidate.recall_score, 6), gold_label=pair.label.value,
            predicted_label=predicted.value, retained=retained,
            reason_codes=reasons,
            reason_dimensions=tuple(dimension.value for dimension in pair.reason_dimensions),
            subset=pair.subset,
        ))
    return tuple(results)


def evaluate_dataset(dataset: Optional[EvaluationDataset] = None) -> EvaluationReport:
    dataset = dataset or load_evaluation_dataset()
    with tempfile.TemporaryDirectory(prefix="task2c-eval-") as temp_dir:
        repository = SQLiteMemoryManager(str(Path(temp_dir) / "evaluation.db"))
        _populate(repository, dataset.memories)
        rankings: Dict[str, Tuple[str, ...]] = {}
        cases_by_method_k: Dict[tuple[str, int], list[CaseResult]] = {
            (method, k): [] for method in METHODS for k in K_VALUES
        }
        query_metrics: Dict[int, list[float]] = {k: [] for k in K_VALUES}
        query_rr: list[float] = []
        for query in dataset.queries:
            scoped = _ScopedReadRepository(repository, query.candidate_universe)
            recall_full = MemoryRecallEngine(scoped).recall(query.query, top_k=8)
            ranking = tuple(candidate.snapshot.memory_id for candidate in recall_full.candidates)
            rankings[query.query_id] = ranking
            query_rr.append(reciprocal_rank(ranking, query.expected_recall_targets))
            for k in K_VALUES:
                query_metrics[k].append(recall_at_k(ranking, query.expected_recall_targets, k))
                recall = type(recall_full)(
                    query=recall_full.query, method=recall_full.method,
                    corpus_size=recall_full.corpus_size,
                    candidates=recall_full.candidates[:k], warnings=recall_full.warnings,
                )
                for method in METHODS:
                    cases_by_method_k[(method, k)].extend(_method_cases(
                        method, query, recall, dataset.contexts[query.query_id],
                        dataset.profiles, dataset.gold[query.query_id],
                    ))

        method_results = []
        for method in METHODS:
            for k in K_VALUES:
                cases = tuple(cases_by_method_k[(method, k)])
                by_dimension = tuple(
                    _breakdown(
                        dimension.value,
                        [case for case in cases if dimension.value in case.reason_dimensions],
                    )
                    for dimension in ApplicabilityDimension
                )
                families = sorted({query.family for query in dataset.queries})
                by_family = tuple(
                    _breakdown(family, [case for case in cases if case.family == family])
                    for family in families
                )
                by_subset = tuple(
                    _breakdown(subset, [case for case in cases if case.subset == subset])
                    for subset in SUBSETS
                )
                method_results.append(MethodMetrics(
                    method=method, k=k,
                    recall_at_k=sum(query_metrics[k]) / len(query_metrics[k]),
                    mrr=sum(query_rr) / len(query_rr),
                    metrics=calculate_metrics(cases), by_dimension=by_dimension,
                    by_family=by_family, by_subset=by_subset,
                ))

        cases_at_k4 = tuple(
            sorted(cases_by_method_k[("B2_APPLICABILITY", 4)], key=lambda case: (case.query_id, case.original_rank))
        )
        high_rank_mismatch = tuple(
            f"{case.query_id}/{case.memory_id}@{case.original_rank}"
            for case in cases_at_k4
            if case.gold_label == ApplicabilityStatus.MISMATCH.value and case.original_rank <= 2
        )
        false_accepts = tuple(
            f"{case.query_id}/{case.memory_id}"
            for case in cases_at_k4
            if case.gold_label == ApplicabilityStatus.MISMATCH.value and case.retained
        )
        false_rejects = tuple(
            f"{case.query_id}/{case.memory_id}"
            for case in cases_at_k4
            if case.gold_label == ApplicabilityStatus.MATCH.value and not case.retained
        )
        uncertain = tuple(
            f"{case.query_id}/{case.memory_id}:{case.predicted_label}"
            for case in cases_at_k4
            if case.predicted_label in {ApplicabilityStatus.UNKNOWN.value, ApplicabilityStatus.AMBIGUOUS.value}
        )
        domains = _distribution(memory.domain for memory in dataset.memories)
        dimensions = _distribution(
            dimension.value
            for pairs in dataset.gold.values() for pair in pairs.values()
            for dimension in pair.reason_dimensions
        )
        families = _distribution(query.family for query in dataset.queries)
        return EvaluationReport(
            dataset_name="controlled_applicability_evaluation_v1",
            memory_count=len(dataset.memories), query_count=len(dataset.queries),
            labeled_pair_count=sum(len(pairs) for pairs in dataset.gold.values()),
            domain_distribution=domains, dimension_distribution=dimensions,
            family_distribution=families,
            methods_at_k4=tuple(result for result in method_results if result.k == 4),
            k_sensitivity=tuple(method_results), cases_at_k4=cases_at_k4,
            high_rank_mismatch=high_rank_mismatch, false_accepts=false_accepts,
            false_rejects=false_rejects, uncertain_cases=uncertain,
        )


def _distribution(values: Iterable[str]) -> Tuple[Tuple[str, int], ...]:
    counts: Dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return tuple(sorted(counts.items()))
