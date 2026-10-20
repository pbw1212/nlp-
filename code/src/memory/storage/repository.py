from typing import List, Optional, Protocol

from src.memory.core.schema import MemoryRevision, MemorySnapshot


class MemoryReadRepository(Protocol):
    """未来 Task 2 只能依赖此 current-safe read boundary。"""

    def get_current(self, memory_id: str) -> Optional[MemorySnapshot]: ...

    def list_current(self) -> List[MemorySnapshot]: ...

    def get_history(self, memory_id: str) -> List[MemoryRevision]: ...
