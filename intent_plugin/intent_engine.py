"""意图生成引擎。

基于对话情境，使用 LLM 动态生成意图候选列表。
支持周期性意图管理（添加/更新/保留/删除）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from src.kernel.logger import get_logger

from .models import Intent, Goal, GoalStatus
from .intent_generator import IntentGenerator

logger = get_logger("intent_plugin")


@dataclass
class IntentOperation:
    """意图操作"""
    operation: Literal["add", "update", "keep", "remove"]
    intent: Intent | None = None
    intent_id: str | None = None
    progress: int | None = None
    reason: str | None = None


class Situation:
    """对话情境快照"""

    def __init__(
        self,
        is_new_user: bool = False,
        after_silent: bool = False,
        user_mention_detail: bool = False,
        negative_emotion: bool = False,
        tired: bool = False,
        confused: bool = False,
        flat_mood: bool = False,
        user_choice: bool = False,
        user_opinion: bool = False,
        deep_conversation: bool = False,
        recent_messages: list[str] = None,
    ):
        self.is_new_user = is_new_user
        self.after_silent = after_silent
        self.user_mention_detail = user_mention_detail
        self.negative_emotion = negative_emotion
        self.tired = tired
        self.confused = confused
        self.flat_mood = flat_mood
        self.user_choice = user_choice
        self.user_opinion = user_opinion
        self.deep_conversation = deep_conversation
        self.recent_messages = recent_messages or []


class IntentEngine:
    """意图生成引擎

    基于对话情境，使用 LLM 动态生成意图候选列表。
    支持周期性意图管理（添加/更新/保留/删除）。
    """

    def __init__(self, config: Any = None):
        self.config = config

        # 从配置中获取 model_task
        model_task = "actor"  # 默认值
        if config and hasattr(config, "generation"):
            model_task = getattr(config.generation, "model_task", "actor")

        self.intent_generator = IntentGenerator(model_task=model_task)
        self.intent_cooldowns: dict[str, int] = {}  # 意图 ID -> 剩余冷却消息数
        self._last_generated_intents: list[Intent] = []  # 缓存最近生成的意图

    async def generate_candidates(
        self,
        situation: Situation,
        active_goals: list[Goal],
    ) -> list[Intent]:
        """生成候选意图列表（LLM 驱动）- 旧接口，兼容调用"""
        operations = await self.manage_intents(situation, active_goals)
        # 只返回新增的意图
        return [op.intent for op in operations if op.operation == "add" and op.intent]

    async def manage_intents(
        self,
        situation: Situation,
        active_goals: list[Goal],
    ) -> list[IntentOperation]:
        """管理意图列表（LLM 驱动）- 新接口

        基于当前活跃意图，决定添加/更新/保留/删除操作。

        Returns:
            list[IntentOperation]: 操作列表
        """
        # 1. 使用 LLM 生成意图候选（只返回 add 操作的意图）
        candidate_intents = await self.intent_generator.generate_intents(
            situation=situation,
            recent_messages=situation.recent_messages,
            active_goals=active_goals,
        )

        if not candidate_intents:
            logger.debug("LLM 未生成有效意图")
            return []

        # 2. 将意图转换为 add 操作
        operations = []
        for intent in candidate_intents:
            op = IntentOperation(operation="add", intent=intent)
            operations.append(op)

        # 3. 验证和过滤操作
        valid_operations = []
        for op in operations:
            # 验证操作有效性
            if not self._validate_operation(op, active_goals):
                continue
            valid_operations.append(op)

        # 4. 应用优先级排序（add 操作优先）
        valid_operations.sort(
            key=lambda x: (
                0 if x.operation == "add" else 1,
                -getattr(x.intent, 'current_priority', 0) if x.intent else 0
            )
        )

        logger.info(f"生成 {len(valid_operations)} 个意图操作：{[op.operation for op in valid_operations]}")

        return valid_operations

    def _validate_operation(
        self,
        op: IntentOperation,
        active_goals: list[Goal],
    ) -> bool:
        """验证操作是否有效"""
        if op.operation == "add":
            if not op.intent:
                logger.warning("ADD 操作缺少 intent")
                return False
            # 检查是否已有相同意图
            if self._has_same_intent_goal(op.intent, active_goals):
                logger.debug(f"意图 {op.intent.name} 已存在，跳过添加")
                return False
            # 检查冷却
            if self._is_in_cooldown(op.intent):
                logger.debug(f"意图 {op.intent.name} 在冷却中，跳过")
                return False
            return True

        elif op.operation == "update":
            if not op.intent_id:
                logger.warning("UPDATE 操作缺少 intent_id")
                return False
            # 检查是否存在该意图
            if not any(g.id == op.intent_id for g in active_goals):
                logger.warning(f"UPDATE 操作找不到意图 {op.intent_id}")
                return False
            return True

        elif op.operation == "remove":
            if not op.intent_id:
                logger.warning("REMOVE 操作缺少 intent_id")
                return False
            return True

        elif op.operation == "keep":
            if not op.intent_id:
                logger.warning("KEEP 操作缺少 intent_id")
                return False
            return True

        return False

    def _is_intent_enabled(self, intent: Intent) -> bool:
        """检查意图是否启用"""
        if self.config is None:
            return True  # 没有配置时默认启用

        # 使用新的配置结构
        category_config = getattr(self.config, intent.category, None)
        if category_config is None:
            return True

        # 检查类别开关
        enabled = getattr(category_config, "enabled", True)
        return enabled

    def _has_same_intent_goal(
        self,
        intent: Intent,
        active_goals: list[Goal],
    ) -> bool:
        """检查是否已有相同意图的目标（通过名称匹配）"""
        for goal in active_goals:
            if (
                goal.intent_name == intent.name
                and goal.status == GoalStatus.ACTIVE
            ):
                return True
        return False

    def _is_in_cooldown(self, intent: Intent) -> bool:
        """检查是否在冷却中"""
        cooldown = self.intent_cooldowns.get(intent.id, 0)
        return cooldown > 0

    def _calculate_priority(
        self,
        intent: Intent,
        situation: Situation,
    ) -> int:
        """计算动态优先级"""
        priority = intent.base_priority

        # 情境加成（基于 LLM 已给出的 base_priority，额外加成有限）
        if situation.negative_emotion and intent.category == "emotional":
            priority += 2
            logger.debug(f"意图 {intent.name} 因负面情绪优先级 +2")
        if situation.is_new_user and intent.category == "social":
            priority += 2
            logger.debug(f"意图 {intent.name} 因新用户优先级 +2")
        if situation.deep_conversation and intent.category == "growth":
            priority += 1
            logger.debug(f"意图 {intent.name} 因深度对话优先级 +1")

        return min(priority, 10)  # 上限 10

    def decrement_cooldowns(self) -> None:
        """减少所有冷却计数"""
        for intent_id in list(self.intent_cooldowns.keys()):
            self.intent_cooldowns[intent_id] -= 1
            if self.intent_cooldowns[intent_id] <= 0:
                del self.intent_cooldowns[intent_id]

    def add_cooldown(self, intent_id: str, cooldown_messages: int) -> None:
        """添加冷却"""
        self.intent_cooldowns[intent_id] = cooldown_messages
        logger.debug(f"意图 {intent_id} 进入冷却，{cooldown_messages} 条消息后解除")

    def get_intent_by_id(self, intent_id: str) -> Intent | None:
        """根据 ID 获取意图（从缓存中查找）"""
        for intent in self._last_generated_intents:
            if intent.id == intent_id:
                return intent
        return None


def create_goal_from_intent(
    intent: Intent,
    trigger_context: dict[str, Any] = None,
) -> Goal:
    """从意图创建目标"""
    from uuid import uuid4

    # 动态生成的意图，goal_objective 直接使用 intent.description
    objective = intent.description or intent.goal_templates[0] if intent.goal_templates else "推进目标"

    goal = Goal(
        id=str(uuid4()),
        intent_id=intent.id,
        intent_name=intent.name,
        objective=objective,
        steps=[],  # 动态生成的意图没有预设步骤
        current_step=0,
        status=GoalStatus.ACTIVE,
        created_at=datetime.now(),
        updated_at=datetime.now(),
        trigger_context=trigger_context or {},
        priority=intent.current_priority,
    )

    logger.info(f"创建目标：{objective} (意图：{intent.name})")

    return goal


def decompose_to_steps(intent_name: str, objective: str) -> list:
    """将目标分解为执行步骤（简化版，动态生成意图不再预设步骤）"""
    # 动态意图不预设具体步骤，由 LLM 在对话中自然推进
    return []
