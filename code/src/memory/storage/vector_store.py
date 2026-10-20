import math
from typing import List, Tuple, Dict, Any


class InMemoryVectorStore:
    """
    兼容旧名称的字符 2-gram 词法召回占位实现。

    它不是 embedding，也不提供真正的语义检索；仅供 Task 1 原型做零依赖
    的近似字符串匹配。Task 2 不应把它描述成 semantic/vector retrieval。
    """
    retrieval_kind = "lexical_character_2gram_placeholder"
    def __init__(self):
        self.vectors: Dict[str, Tuple[str, Dict[str, float]]] = {}

    def _text_to_vector(self, text: str) -> Dict[str, float]:
        vec: Dict[str, float] = {}
        # 提取字符 2-gram 词项；这是词法特征，不是语义嵌入。
        for i in range(len(text) - 1):
            gram = text[i:i+2]
            vec[gram] = vec.get(gram, 0.0) + 1.0
        # 归一化 L2 范数
        norm = math.sqrt(sum(v * v for v in vec.values()))
        if norm > 0:
            for k in vec:
                vec[k] /= norm
        return vec

    def _cosine_similarity(self, vec1: Dict[str, float], vec2: Dict[str, float]) -> float:
        score = 0.0
        for k, v in vec1.items():
            if k in vec2:
                score += v * vec2[k]
        return score

    def add_or_update(self, memory_id: str, content: str):
        vec = self._text_to_vector(content)
        self.vectors[memory_id] = (content, vec)

    def delete(self, memory_id: str):
        if memory_id in self.vectors:
            del self.vectors[memory_id]

    def reload_from_sqlite(self, active_items: List[Any]):
        """从 SQLite 中同步并载入所有活跃记忆，恢复/重建内存向量索引"""
        self.vectors.clear()
        for item in active_items:
            content = getattr(item, "content", "")
            memory_id = getattr(item, "memory_id", "")
            if memory_id and content:
                self.add_or_update(memory_id, content)

    def search(self, query: str, top_k: int = 5) -> List[Tuple[str, float]]:
        if top_k <= 0:
            return []
        q_vec = self._text_to_vector(query)
        if not q_vec:
            return []
        scores = []
        for mid, (content, vec) in self.vectors.items():
            sim = self._cosine_similarity(q_vec, vec)
            if sim > 0.0:
                scores.append((mid, sim))
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]
