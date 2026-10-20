import time
import math
from typing import Optional
from src.memory.core.schema import ActiveStatus, MemoryItem
from src.memory.storage.sqlite_manager import SQLiteMemoryManager
from src.memory.lifecycle.crud_engine import CRUDEngine
from src.memory.llm.llm_client import LLMClientBase
from src.memory.core.config import MemoryConfig


class ConsolidationEngine:
    def __init__(self, sqlite_mgr: SQLiteMemoryManager, crud_engine: CRUDEngine, llm_client: LLMClientBase, config: MemoryConfig):
        self.sqlite_mgr = sqlite_mgr
        self.crud_engine = crud_engine
        self.llm_client = llm_client
        self.config = config
        
    def calculate_ebbinghaus_decay(self, item: MemoryItem, current_time: Optional[float] = None) -> float:
        """
        多元数据自适应艾宾浩斯衰减模型:
        S(t) = S_0 * exp( - (t - t_last_accessed) / [ I * (1 + gamma * C) * H_conf ] )
        """
        if not self.config.enable_forgetting:
            return item.decay_strength
        now = current_time if current_time is not None else time.time()
        # 若未开启衰减或已被锁死，强度保持为 1.0 不变
        if not item.enable_time_decay or item.is_pinned:
            return 1.0

        # 时间差（转换为基准天数/小时比例）
        # Wall clocks can move backwards (NTP/manual correction), and imported
        # records may contain a future timestamp. Forgetting must remain
        # monotone and must never exceed the SQLite [0, 1] score invariant.
        delta_t_hours = max(0.0, now - item.last_accessed) / 3600.0
        time_ratio = delta_t_hours / self.config.time_unit_hours

        # 多维度元数据带入分母
        i_importance = max(0.1, item.importance_score)
        c_access = item.access_count
        h_conf = max(0.1, item.current_revision.confidence if item.current_revision else 0.1)

        denom = i_importance * (1.0 + self.config.decay_gamma * c_access) * h_conf
        
        # 自然指数衰减
        decay_strength = self.config.s0_initial_strength * math.exp(-time_ratio / denom)
        return round(min(self.config.s0_initial_strength, max(0.0, decay_strength)), 4)

    def run_periodic_consolidation(self):
        """
        执行定期维护管道:
        1. 遍历所有 ACTIVE 记忆节点，计算多元数据自适应衰减 $S(t)$；
        2. 强度低于 decay_threshold 的节点移入 30 天回收站 (RECYCLE BIN)；
        3. 物理清除在回收站内已沉淀滞留超过配置期限的真正过期节点；
        4. 完成维护后返回维护结果。

        长期记忆的语义沉淀只允许走
        ``LongTermMemoryManager.run_autonomous_dream_pipeline``。旧版本在
        这里按 ``fine_episodes[:3]`` 直接调用摘要 Provider，既依赖列表位置，
        又与批量梦境管道产生两套写入策略，现已移除。
        """
        if not self.config.enable_forgetting:
            return {
                "forgetting_enabled": False,
                "decayed_memories": 0,
                "recycled_memories": 0,
                "cleaned_recycle_bin": 0,
            }
        now = time.time()
        active_memories = self.sqlite_mgr.list_memories(ActiveStatus.ACTIVE)
        recycled_count = 0

        # 1 & 2: 衰减计算与回收站移入
        for item in active_memories:
            current_s = self.calculate_ebbinghaus_decay(item, current_time=now)
            item.decay_strength = current_s
            self.sqlite_mgr.save_operational_state(item)

            if current_s < self.config.decay_threshold:
                recycled_count += int(
                    self.crud_engine.soft_delete_to_recycle_bin(
                        item.memory_id, reason="EBBINGHAUS_DECAY"
                    )
                )

        # 3: 物理清除在回收站呆满配置期限的超期节点
        cleaned = self.sqlite_mgr.cleanup_recycle_bin(
            retention_days=self.config.recycle_bin_retention_days
        )

        return {
            "forgetting_enabled": True,
            "decayed_memories": len(active_memories),
            "recycled_memories": recycled_count,
            "cleaned_recycle_bin": cleaned,
        }
