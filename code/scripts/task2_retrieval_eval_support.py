from __future__ import annotations

import hashlib
import json
import math
import tempfile
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence, Tuple

from src.memory.core.schema import Granularity, MemoryType
from src.memory.retrieval.dense import DenseEmbeddingRetriever
from src.memory.retrieval.embedding import EmbeddingProviderIdentity, FastEmbedEmbeddingProvider
from src.memory.retrieval.hybrid import HybridRRFMemoryRetriever
from src.memory.retrieval.metrics import recall_at_k, reciprocal_rank
from src.memory.retrieval.models import RecallRequest
from src.memory.retrieval.protocols import MemoryRetriever
from src.memory.retrieval.recall import MemoryRecallEngine
from src.memory.retrieval.tokenizer import tokenize
from src.memory.storage.sqlite_manager import SQLiteMemoryManager


DATASET_PATH = Path(__file__).resolve().parents[2] / "benchmarks/task2/retrieval_eval_v1/dataset.json"
K_VALUES = (1, 4, 8)


class BaselineProvenance(str, Enum):
    OFFICIAL = "OFFICIAL"
    PAPER_DERIVED_REIMPLEMENTATION = "PAPER_DERIVED_REIMPLEMENTATION"
    SYSTEM_ADAPTER = "SYSTEM_ADAPTER"
    OUR_BASELINE = "OUR_BASELINE"


@dataclass(frozen=True)
class MemorySpec:
    memory_id: str
    domain: str
    content: str


@dataclass(frozen=True)
class QuerySpec:
    query_id: str
    domain: str
    subset: str
    query: str
    positive_memory_ids: Tuple[str, ...]


@dataclass(frozen=True)
class RetrievalDataset:
    name: str
    memories: Tuple[MemorySpec, ...]
    queries: Tuple[QuerySpec, ...]
    hard_negatives: Mapping[str, Tuple[str, ...]]


@dataclass(frozen=True)
class RetrievalBaseline:
    name: str
    provenance: BaselineProvenance
    retriever: MemoryRetriever


@dataclass(frozen=True)
class RetrievalMetrics:
    recall_at_1: float
    recall_at_4: float
    recall_at_8: float
    mrr: float


@dataclass(frozen=True)
class SubsetMetrics:
    subset: str
    query_count: int
    metrics: RetrievalMetrics


@dataclass(frozen=True)
class QueryResult:
    query_id: str
    subset: str
    expected: Tuple[str, ...]
    ranked_ids: Tuple[str, ...]


@dataclass(frozen=True)
class BaselineResult:
    method: str
    provenance: str
    metrics: RetrievalMetrics
    subsets: Tuple[SubsetMetrics, ...]
    query_results: Tuple[QueryResult, ...]


@dataclass(frozen=True)
class RetrievalEvaluationReport:
    dataset: str
    memory_count: int
    query_count: int
    hard_negative_count: int
    provider: Mapping[str, Any]
    semantic_performance_claim_allowed: bool
    methods: Tuple[BaselineResult, ...]
    failure_analysis: Mapping[str, Tuple[str, ...]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RetrievalDatasetError(ValueError):
    pass


def load_dataset(path: Path = DATASET_PATH) -> RetrievalDataset:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if set(payload) != {"schema_version", "name", "memories", "queries", "hard_negatives"}:
        raise RetrievalDatasetError("dataset root fields invalid")
    if payload["schema_version"] != 1:
        raise RetrievalDatasetError("unsupported retrieval dataset version")
    memories = tuple(MemorySpec(**item) for item in payload["memories"])
    queries = tuple(
        QuerySpec(
            query_id=item["query_id"],
            domain=item["domain"],
            subset=item["subset"],
            query=item["query"],
            positive_memory_ids=tuple(item["positive_memory_ids"]),
        )
        for item in payload["queries"]
    )
    memory_ids = tuple(item.memory_id for item in memories)
    query_ids = tuple(item.query_id for item in queries)
    if len(memories) < 40 or len(memories) > 80:
        raise RetrievalDatasetError("retrieval v1 requires 40-80 memories")
    if len(queries) < 30 or len(queries) > 50:
        raise RetrievalDatasetError("retrieval v1 requires 30-50 queries")
    if len(set(memory_ids)) != len(memory_ids) or len(set(query_ids)) != len(query_ids):
        raise RetrievalDatasetError("memory/query IDs must be unique")
    valid_subsets = {"lexical_exact", "semantic_paraphrase", "mixed"}
    if {item.subset for item in queries} - valid_subsets:
        raise RetrievalDatasetError("unknown retrieval subset")
    for query in queries:
        if not query.positive_memory_ids or not set(query.positive_memory_ids) <= set(memory_ids):
            raise RetrievalDatasetError(f"invalid positives: {query.query_id}")
    if len({item.domain for item in memories}) < 8:
        raise RetrievalDatasetError("retrieval v1 requires at least 8 domains")
    hard_negatives = {
        query_id: tuple(memory_ids_value)
        for query_id, memory_ids_value in payload["hard_negatives"].items()
    }
    if set(hard_negatives) != set(query_ids):
        raise RetrievalDatasetError("hard negatives must cover every query")
    positives = {query.query_id: set(query.positive_memory_ids) for query in queries}
    for query_id, negative_ids in hard_negatives.items():
        if not negative_ids or not set(negative_ids) <= set(memory_ids):
            raise RetrievalDatasetError(f"invalid hard negatives: {query_id}")
        if set(negative_ids) & positives[query_id]:
            raise RetrievalDatasetError(f"hard negative overlaps positive: {query_id}")
    return RetrievalDataset(payload["name"], memories, queries, hard_negatives)


class DeterministicTestEmbeddingProvider:
    """契约测试专用 token-hash provider；不是 semantic embedding。"""

    def __init__(self, dimension: int = 128) -> None:
        self._identity = EmbeddingProviderIdentity(
            provider="deterministic-test-only",
            provider_version="1",
            model="token-hash-v1",
            dimension=dimension,
            normalization="l2",
            batch_size=64,
        )

    @property
    def identity(self) -> EmbeddingProviderIdentity:
        return self._identity

    def _vector(self, text: str) -> Tuple[float, ...]:
        values = [0.0] * self.identity.dimension
        terms = tokenize(text) or (text.strip() or "<empty>",)
        for term in terms:
            digest = hashlib.sha256(term.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.identity.dimension
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            values[index] += sign
        norm = math.sqrt(sum(value * value for value in values))
        return tuple(value / norm for value in values)

    def embed_documents(self, texts: Sequence[str]) -> Tuple[Tuple[float, ...], ...]:
        return tuple(self._vector(text) for text in texts)

    def embed_query(self, text: str) -> Tuple[float, ...]:
        return self._vector(text)


def build_provider(name: str):
    if name == "deterministic-test":
        return DeterministicTestEmbeddingProvider(), False
    if name == "fastembed":
        return FastEmbedEmbeddingProvider(), True
    raise ValueError(f"unknown provider: {name}")


def _metrics(results: Sequence[QueryResult]) -> RetrievalMetrics:
    if not results:
        return RetrievalMetrics(0.0, 0.0, 0.0, 0.0)
    return RetrievalMetrics(
        recall_at_1=sum(recall_at_k(item.ranked_ids, set(item.expected), 1) for item in results) / len(results),
        recall_at_4=sum(recall_at_k(item.ranked_ids, set(item.expected), 4) for item in results) / len(results),
        recall_at_8=sum(recall_at_k(item.ranked_ids, set(item.expected), 8) for item in results) / len(results),
        mrr=sum(reciprocal_rank(item.ranked_ids, set(item.expected)) for item in results) / len(results),
    )


def _evaluate_baseline(dataset: RetrievalDataset, baseline: RetrievalBaseline) -> BaselineResult:
    query_results = []
    for query in dataset.queries:
        result = baseline.retriever.recall_request(RecallRequest(query.query, top_k=8))
        query_results.append(QueryResult(
            query_id=query.query_id,
            subset=query.subset,
            expected=query.positive_memory_ids,
            ranked_ids=tuple(candidate.snapshot.memory_id for candidate in result.candidates),
        ))
    results = tuple(query_results)
    subsets = tuple(
        SubsetMetrics(subset, len(selected), _metrics(selected))
        for subset in ("lexical_exact", "semantic_paraphrase", "mixed")
        if (selected := tuple(item for item in results if item.subset == subset))
    )
    return BaselineResult(
        baseline.name,
        baseline.provenance.value,
        _metrics(results),
        subsets,
        results,
    )


def evaluate(provider_name: str = "deterministic-test") -> RetrievalEvaluationReport:
    dataset = load_dataset()
    provider, semantic_claim_allowed = build_provider(provider_name)
    with tempfile.TemporaryDirectory(prefix="task2-retrieval-") as temp_dir:
        repository = SQLiteMemoryManager(str(Path(temp_dir) / "retrieval.db"))
        for memory in dataset.memories:
            event, _ = repository.register_ingestion_event(
                memory.content, event_id=f"retrieval-{memory.memory_id}"
            )
            repository.create_memory(
                memory_id=memory.memory_id,
                content=memory.content,
                memory_type=MemoryType.PREFERENCE_RULES,
                granularity=Granularity.SEMANTIC_INSIGHT,
                event_ids=(event.event_id,),
            )
        lexical = MemoryRecallEngine(repository)
        dense = DenseEmbeddingRetriever(repository, provider)
        hybrid = HybridRRFMemoryRetriever(lexical, dense)
        baselines = (
            RetrievalBaseline("BM25", BaselineProvenance.OUR_BASELINE, lexical),
            RetrievalBaseline("DENSE", BaselineProvenance.OUR_BASELINE, dense),
            RetrievalBaseline("HYBRID_RRF", BaselineProvenance.OUR_BASELINE, hybrid),
        )
        results = tuple(_evaluate_baseline(dataset, baseline) for baseline in baselines)

    by_method = {result.method: result for result in results}
    categories = {"bm25_only": [], "dense_only": [], "hybrid_recovery": [], "all_failed": []}
    for index, query in enumerate(dataset.queries):
        expected = set(query.positive_memory_ids)
        bm25 = bool(expected & set(by_method["BM25"].query_results[index].ranked_ids[:4]))
        dense_hit = bool(expected & set(by_method["DENSE"].query_results[index].ranked_ids[:4]))
        hybrid_hit = bool(expected & set(by_method["HYBRID_RRF"].query_results[index].ranked_ids[:4]))
        if bm25 and not dense_hit:
            categories["bm25_only"].append(query.query_id)
        if dense_hit and not bm25:
            categories["dense_only"].append(query.query_id)
        if hybrid_hit and not (bm25 and dense_hit):
            categories["hybrid_recovery"].append(query.query_id)
        if not bm25 and not dense_hit and not hybrid_hit:
            categories["all_failed"].append(query.query_id)

    return RetrievalEvaluationReport(
        dataset=dataset.name,
        memory_count=len(dataset.memories),
        query_count=len(dataset.queries),
        hard_negative_count=sum(len(items) for items in dataset.hard_negatives.values()),
        provider=asdict(provider.identity),
        semantic_performance_claim_allowed=semantic_claim_allowed,
        methods=results,
        failure_analysis={key: tuple(value) for key, value in categories.items()},
    )
