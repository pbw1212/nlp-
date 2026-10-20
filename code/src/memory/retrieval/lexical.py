from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Dict, Iterable, Mapping, Sequence, Tuple

from src.memory.core.schema import MemorySnapshot
from src.memory.retrieval.models import RecallScoreBreakdown
from src.memory.retrieval.tokenizer import tokenize, unique_terms


METADATA_FIELDS = (
    "persons",
    "entities",
    "locations",
    "topics",
    "keywords",
    "scopes",
    "conditions",
)
DEFAULT_BM25_K1 = 1.5
DEFAULT_BM25_B = 0.75
DEFAULT_METADATA_WEIGHT = 0.35


@dataclass(frozen=True)
class LexicalScore:
    snapshot: MemorySnapshot
    breakdown: RecallScoreBreakdown
    matched_terms: Tuple[str, ...]
    matched_metadata_fields: Tuple[Tuple[str, Tuple[str, ...]], ...]


@dataclass(frozen=True)
class _Document:
    snapshot: MemorySnapshot
    content_tokens: Tuple[str, ...]
    metadata_tokens: Mapping[str, Tuple[str, ...]]


class LexicalBM25Index:
    """Ephemeral BM25 lexical index；不是 semantic/vector index。"""

    def __init__(
        self,
        snapshots: Sequence[MemorySnapshot],
        *,
        k1: float = DEFAULT_BM25_K1,
        b: float = DEFAULT_BM25_B,
        metadata_weight: float = DEFAULT_METADATA_WEIGHT,
    ) -> None:
        if k1 <= 0:
            raise ValueError("k1 必须大于 0")
        if not 0 <= b <= 1:
            raise ValueError("b 必须位于 [0, 1]")
        if metadata_weight < 0:
            raise ValueError("metadata_weight 不能为负数")
        self.k1 = k1
        self.b = b
        self.metadata_weight = metadata_weight
        self._documents = tuple(self._build_document(snapshot) for snapshot in snapshots)
        self._content_df = self._document_frequency(doc.content_tokens for doc in self._documents)
        metadata_documents = (
            tuple(token for values in doc.metadata_tokens.values() for token in values)
            for doc in self._documents
        )
        self._metadata_df = self._document_frequency(metadata_documents)
        self._average_document_length = (
            sum(len(doc.content_tokens) for doc in self._documents) / len(self._documents)
            if self._documents
            else 0.0
        )

    @property
    def corpus_size(self) -> int:
        return len(self._documents)

    @staticmethod
    def _build_document(snapshot: MemorySnapshot) -> _Document:
        metadata = snapshot.revision.semantic_metadata
        metadata_tokens = {
            field: tuple(
                token
                for value in getattr(metadata, field)
                for token in tokenize(value)
            )
            for field in METADATA_FIELDS
        }
        return _Document(snapshot, tokenize(snapshot.revision.content), metadata_tokens)

    @staticmethod
    def _document_frequency(documents: Iterable[Tuple[str, ...]]) -> Dict[str, int]:
        frequencies: Dict[str, int] = {}
        for tokens in documents:
            for token in set(tokens):
                frequencies[token] = frequencies.get(token, 0) + 1
        return frequencies

    def _idf(self, term: str, frequencies: Mapping[str, int]) -> float:
        document_frequency = frequencies.get(term, 0)
        return math.log(
            1.0 + (self.corpus_size - document_frequency + 0.5) / (document_frequency + 0.5)
        )

    def _content_score(self, query_terms: Tuple[str, ...], document: _Document) -> float:
        if not document.content_tokens or self._average_document_length <= 0:
            return 0.0
        term_frequencies = Counter(document.content_tokens)
        length_normalization = 1.0 - self.b + self.b * (
            len(document.content_tokens) / self._average_document_length
        )
        score = 0.0
        for term in query_terms:
            frequency = term_frequencies.get(term, 0)
            if frequency == 0:
                continue
            numerator = frequency * (self.k1 + 1.0)
            denominator = frequency + self.k1 * length_normalization
            score += self._idf(term, self._content_df) * numerator / denominator
        return score

    def _metadata_score(
        self, query_terms: Tuple[str, ...], document: _Document
    ) -> tuple[float, Tuple[Tuple[str, Tuple[str, ...]], ...]]:
        if not query_terms or self.metadata_weight == 0:
            return 0.0, ()
        total_weight = sum(self._idf(term, self._metadata_df) for term in query_terms)
        field_matches = []
        matched_union: set[str] = set()
        for field in METADATA_FIELDS:
            field_terms = set(document.metadata_tokens[field])
            matches = tuple(term for term in query_terms if term in field_terms)
            if matches:
                field_matches.append((field, matches))
                matched_union.update(matches)
        if not matched_union or total_weight <= 0:
            return 0.0, tuple(field_matches)
        matched_weight = sum(self._idf(term, self._metadata_df) for term in matched_union)
        return self.metadata_weight * matched_weight / total_weight, tuple(field_matches)

    def score(self, query: str) -> Tuple[LexicalScore, ...]:
        query_terms = unique_terms(tokenize(query))
        if not query_terms:
            return ()
        scored = []
        for document in self._documents:
            content_score = self._content_score(query_terms, document)
            metadata_score, metadata_matches = self._metadata_score(query_terms, document)
            breakdown = RecallScoreBreakdown(content_score, metadata_score)
            if breakdown.total <= 0:
                continue
            content_term_set = set(document.content_tokens)
            matched_terms = tuple(term for term in query_terms if term in content_term_set)
            scored.append(
                LexicalScore(document.snapshot, breakdown, matched_terms, metadata_matches)
            )
        return tuple(
            sorted(scored, key=lambda item: (-item.breakdown.total, item.snapshot.memory_id))
        )
