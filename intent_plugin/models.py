"""数据模型定义。

定义 Intent、Goal、GoalStep、GoalStatus 等核心数据结构。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class GoalStatus(str, Enum):
    """目标状态枚举"""

    PENDING = "pending"  # 等待执行
    ACTIVE = "active"  # 正在执行
    COMPLETED = "completed"  # 已完成
    ABANDONED = "abandoned"  # 已放弃（用户打断/情境变化）
    FAILED = "failed"  # 失败（超时/无法完成）
    PAUSED = "paused"  # 已暂停（等待时机）


@dataclass
class GoalStep:
    """目标步骤"""

    index: int  # 步骤索引
    action: str  # 建议行动
    keywords: list[str]  # 成功关键词（用于自动检测）
    optional: bool = False  # 是否可选步骤


@dataclass
class Intent:
    """意图定义"""

    id: str  # 唯一标识，如 "social_curiosity"
    name: str  # 显示名称，如 "了解用户"
    description: str  # 详细描述
    category: str  # 分类：social/emotional/growth

    # 触发条件
    trigger_context: str = ""  # 触发情境描述
    trigger_conditions: list[str] = field(default_factory=list)

    # 优先级配置
    base_priority: int = 5  # 基础优先级 (1-10)
    dynamic_boost: dict[str, int] = field(default_factory=dict)

    # 过期配置
    expiry_messages: int = 15  # 多少条消息后过期
    expiry_seconds: int = 300  # 多少秒后过期

    # 目标模板
    goal_templates: list[str] = field(default_factory=list)

    # 运行时状态（不持久化）
    current_priority: int = 5  # 当前动态优先级
    last_triggered: datetime | None = None  # 上次触发时间


@dataclass
class Goal:
    """短期目标"""

    id: str  # 唯一标识 (UUID)
    intent_id: str  # 所属意图 ID
    intent_name: str  # 所属意图名称（冗余，方便显示）
    objective: str  # 目标描述

    # 执行步骤
    steps: list[GoalStep] = field(default_factory=list)
    current_step: int = 0  # 当前步骤索引 (0-based)

    # 状态追踪
    status: GoalStatus = GoalStatus.ACTIVE
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime | None = None

    # 上下文
    trigger_context: dict[str, Any] = field(default_factory=dict)
    success_condition: str = ""  # 成功条件描述
    notes: str = ""  # 备注/额外信息

    # 优先级（从意图继承）
    priority: int = 5

    def is_completed(self) -> bool:
        """检查是否完成"""
        return self.status == GoalStatus.COMPLETED

    def is_active(self) -> bool:
        """检查是否正在执行"""
        return self.status == GoalStatus.ACTIVE

    def is_expired(self, message_count: int, max_age_seconds: int = 600) -> bool:
        """检查是否过期"""
        # 检查时间过期
        age = (datetime.now() - self.created_at).total_seconds()
        if age > max_age_seconds:
            return True

        # 检查消息数量过期（由外部传入当前消息计数）
        # 这里简化处理，假设每个目标有自己的消息计数器
        return False

    def has_next_step(self) -> bool:
        """检查是否还有下一步"""
        return self.current_step < len(self.steps)

    def get_current_step(self) -> GoalStep | None:
        """获取当前步骤"""
        if self.current_step < len(self.steps):
            return self.steps[self.current_step]
        return None

    def complete(self) -> None:
        """标记为完成"""
        self.status = GoalStatus.COMPLETED
        self.completed_at = datetime.now()
        self.updated_at = datetime.now()

    def abandon(self) -> None:
        """标记为放弃"""
        self.status = GoalStatus.ABANDONED
        self.updated_at = datetime.now()

    def to_dict(self) -> dict[str, Any]:
        """转换为字典（用于序列化）"""
        return {
            "id": self.id,
            "intent_id": self.intent_id,
            "intent_name": self.intent_name,
            "objective": self.objective,
            "steps": [
                {
                    "index": s.index,
                    "action": s.action,
                    "keywords": s.keywords,
                    "optional": s.optional,
                }
                for s in self.steps
            ],
            "current_step": self.current_step,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "completed_at": self.completed_at.isoformat()
            if self.completed_at
            else None,
            "priority": self.priority,
            "success_condition": self.success_condition,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Goal:
        """从字典创建"""
        steps = [
            GoalStep(
                index=s["index"],
                action=s["action"],
                keywords=s["keywords"],
                optional=s.get("optional", False),
            )
            for s in data.get("steps", [])
        ]

        return cls(
            id=data["id"],
            intent_id=data["intent_id"],
            intent_name=data.get("intent_name", ""),
            objective=data["objective"],
            steps=steps,
            current_step=data.get("current_step", 0),
            status=GoalStatus(data.get("status", "active")),
            created_at=datetime.fromisoformat(data["created_at"])
            if data.get("created_at")
            else datetime.now(),
            updated_at=datetime.fromisoformat(data["updated_at"])
            if data.get("updated_at")
            else datetime.now(),
            completed_at=datetime.fromisoformat(data["completed_at"])
            if data.get("completed_at")
            else None,
            priority=data.get("priority", 5),
            success_condition=data.get("success_condition", ""),
            notes=data.get("notes", ""),
        )
