from typing import Tuple

from src.memory.core.schema import MemoryType


class MemoryRouter:
    """
    统一双轨写入路由器。

    Semantic intent is supplied by the Provider-facing caller. This class only
    maps an already-authorized typed decision to a storage path; it must not
    infer intent from local words or regular expressions.
    """

    def route_input(self, text: str, is_explicit: bool = False) -> Tuple[str, MemoryType]:
        """
        根据属性决定写入类型和路由路径:
        Fast Path -> Core Profile / Explicit Rule
        Slow Path -> Episode / Trajectory for NEMORI蒸馏
        """
        if is_explicit:
            return "FAST_PATH", MemoryType.CORE_PROFILE
        return "SLOW_PATH", MemoryType.EPISODIC_GRAPH
