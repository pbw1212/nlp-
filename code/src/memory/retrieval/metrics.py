from __future__ import annotations

from typing import Collection, Mapping, Sequence


def recall_at_k(ranked_ids: Sequence[str], relevant_ids: Collection[str], k: int) -> float:
    """单 query set recall：top-k 命中 relevant 数 / relevant 总数。"""

    if not relevant_ids:
        raise ValueError("relevant_ids 不能为空")
    if k <= 0:
        return 0.0
    return len(set(ranked_ids[:k]) & set(relevant_ids)) / len(set(relevant_ids))


def reciprocal_rank(ranked_ids: Sequence[str], relevant_ids: Collection[str]) -> float:
    if not relevant_ids:
        raise ValueError("relevant_ids 不能为空")
    relevant = set(relevant_ids)
    for rank, memory_id in enumerate(ranked_ids, start=1):
        if memory_id in relevant:
            return 1.0 / rank
    return 0.0


def mean_recall_at_k(
    rankings: Mapping[str, Sequence[str]], expected: Mapping[str, Collection[str]], k: int
) -> float:
    if not expected:
        raise ValueError("expected queries 不能为空")
    return sum(recall_at_k(rankings.get(query_id, ()), targets, k) for query_id, targets in expected.items()) / len(expected)


def mean_reciprocal_rank(
    rankings: Mapping[str, Sequence[str]], expected: Mapping[str, Collection[str]]
) -> float:
    if not expected:
        raise ValueError("expected queries 不能为空")
    return sum(reciprocal_rank(rankings.get(query_id, ()), targets) for query_id, targets in expected.items()) / len(expected)
