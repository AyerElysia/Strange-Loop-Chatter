"""ThoughtStream 数据模型。"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class ThoughtStream:
    """思考流——爱莉持续在意的兴趣线索。

    不是 TODO（任务），不是 Project（项目），而是"我最近一直在琢磨这件事"的持久兴趣。
    给爱莉在心跳间有事可想、有事可追。
    """

    id: str
    title: str                          # 人类可读的标题
    created_at: str                     # ISO timestamp
    last_advanced_at: str               # 上次推进时间
    advance_count: int = 0              # 推进次数
    curiosity_score: float = 0.7        # 当前好奇心强度 [0, 1]
    last_thought: str = ""              # 最近一次内心独白
    related_memories: list[str] = field(default_factory=list)  # 关联记忆节点ID
    status: str = "active"              # "active" | "dormant" | "completed"

    def is_active(self) -> bool:
        """检查思考流是否处于活跃状态。"""
        return self.status == "active"

    def should_go_dormant(self, dormancy_hours: int = 24) -> bool:
        """检查是否应该进入休眠。"""
        if self.status != "active":
            return False
        try:
            last = datetime.fromisoformat(self.last_advanced_at)
            now = datetime.now(timezone.utc)
            hours_since = (now - last).total_seconds() / 3600
            return hours_since > dormancy_hours
        except (ValueError, TypeError):
            return False
