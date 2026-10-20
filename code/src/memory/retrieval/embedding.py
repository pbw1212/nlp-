from __future__ import annotations

import math
from importlib.metadata import PackageNotFoundError, version
from dataclasses import dataclass
from typing import Iterable, Sequence, Tuple

from src.memory.llm.errors import (
    ProviderConfigurationError,
    ProviderRequestError,
    ProviderResponseError,
)


DEFAULT_FASTEMBED_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DEFAULT_FASTEMBED_DIMENSION = 384
DEFAULT_EMBEDDING_BATCH_SIZE = 32


@dataclass(frozen=True)
class EmbeddingProviderIdentity:
    provider: str
    provider_version: str
    model: str
    dimension: int
    normalization: str
    batch_size: int

    def __post_init__(self) -> None:
        if not self.provider or not self.provider_version or not self.model:
            raise ValueError("embedding provider/version/model 不能为空")
        if self.dimension <= 0 or self.batch_size <= 0:
            raise ValueError("embedding dimension/batch_size 必须大于 0")
        if self.normalization not in {"l2", "none"}:
            raise ValueError("embedding normalization 必须是 l2 或 none")


def validate_vector(
    vector: Iterable[float],
    *,
    expected_dimension: int,
    provider: str,
    operation: str,
) -> Tuple[float, ...]:
    try:
        values = tuple(float(value) for value in vector)
    except (TypeError, ValueError) as exc:
        raise ProviderResponseError(
            "embedding vector 必须只包含数值",
            provider=provider,
            operation=operation,
        ) from exc
    if len(values) != expected_dimension:
        raise ProviderResponseError(
            f"embedding dimension mismatch: expected={expected_dimension} actual={len(values)}",
            provider=provider,
            operation=operation,
        )
    if not all(math.isfinite(value) for value in values):
        raise ProviderResponseError(
            "embedding vector 包含非有限数值",
            provider=provider,
            operation=operation,
        )
    norm = math.sqrt(sum(value * value for value in values))
    if norm <= 0:
        raise ProviderResponseError(
            "embedding vector 不能是零向量",
            provider=provider,
            operation=operation,
        )
    return tuple(value / norm for value in values)


class FastEmbedEmbeddingProvider:
    """显式启用的本地 ONNX embedding backend；构造时可能下载模型。"""

    def __init__(
        self,
        *,
        model: str = DEFAULT_FASTEMBED_MODEL,
        dimension: int = DEFAULT_FASTEMBED_DIMENSION,
        batch_size: int = DEFAULT_EMBEDDING_BATCH_SIZE,
        cache_dir: str | None = None,
    ) -> None:
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:
            raise ProviderConfigurationError(
                "FastEmbed backend 未安装；请显式安装 requirements-retrieval.txt",
                provider="fastembed",
                operation="initialize",
            ) from exc
        try:
            provider_version = version("fastembed")
        except PackageNotFoundError:
            provider_version = "unknown"
        self._identity = EmbeddingProviderIdentity(
            provider="fastembed",
            provider_version=provider_version,
            model=model,
            dimension=dimension,
            normalization="l2",
            batch_size=batch_size,
        )
        try:
            kwargs = {"model_name": model}
            if cache_dir is not None:
                kwargs["cache_dir"] = cache_dir
            self._model = TextEmbedding(**kwargs)
        except Exception as exc:
            raise ProviderRequestError(
                "FastEmbed 模型初始化失败",
                provider="fastembed",
                operation="initialize",
            ) from exc

    @property
    def identity(self) -> EmbeddingProviderIdentity:
        return self._identity

    def _embed(self, texts: Sequence[str], operation: str) -> Tuple[Tuple[float, ...], ...]:
        if not all(isinstance(text, str) for text in texts):
            raise TypeError("embedding 输入必须是字符串序列")
        if not texts:
            return ()
        try:
            raw = tuple(self._model.embed(list(texts), batch_size=self.identity.batch_size))
        except Exception as exc:
            raise ProviderRequestError(
                "FastEmbed embedding 执行失败",
                provider="fastembed",
                operation=operation,
            ) from exc
        if len(raw) != len(texts):
            raise ProviderResponseError(
                "FastEmbed 返回向量数量与输入不一致",
                provider="fastembed",
                operation=operation,
            )
        return tuple(
            validate_vector(
                vector,
                expected_dimension=self.identity.dimension,
                provider=self.identity.provider,
                operation=operation,
            )
            for vector in raw
        )

    def embed_documents(self, texts: Sequence[str]) -> Tuple[Tuple[float, ...], ...]:
        return self._embed(texts, "embed_documents")

    def embed_query(self, text: str) -> Tuple[float, ...]:
        if not isinstance(text, str):
            raise TypeError("query 必须是字符串")
        return self._embed((text,), "embed_query")[0]
