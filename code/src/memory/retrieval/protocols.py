from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, Sequence, Tuple

from src.memory.retrieval.models import RecallRequest, RecallResult

if TYPE_CHECKING:
    from src.memory.retrieval.embedding import EmbeddingProviderIdentity


class MemoryRetriever(Protocol):
    """Task 2 recall backend 的统一只读接口。"""

    def recall_request(self, request: RecallRequest) -> RecallResult: ...


class EmbeddingProvider(Protocol):
    """Dense retriever 所依赖的 provider contract；实现负责返回有限数值。"""

    @property
    def identity(self) -> "EmbeddingProviderIdentity": ...

    def embed_documents(self, texts: Sequence[str]) -> Tuple[Tuple[float, ...], ...]: ...

    def embed_query(self, text: str) -> Tuple[float, ...]: ...
