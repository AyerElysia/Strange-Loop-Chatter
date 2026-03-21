"""目标管理器。

负责目标的创建、分解、调度、冲突解决。
"""

from __future__ import annotations

from typing import Any

from src.kernel.logger import get_logger

from .models import Goal, GoalStatus
from .intent_engine import IntentEngine, Situation, create_goal_from_intent


logger = get_logger("intent_plugin")


class GoalManager:
    """目标管理器

    负责目标的创建、分解、调度、冲突解决。
    """

    def __init__(self, intent_engine: IntentEngine):
        self.intent_engine = intent_engine
        self.active_goals: list[Goal] = []
        self.goal_history: list[Goal] = []
        self.message_count: int = 0

    async def update_goals(
        self,
        situation: Situation,
        max_active: int = 3,
    ) -> list[Goal]:
        """更新目标列表（每次对话后调用）"""
        try:
            self.message_count += 1

            # 1. 清理过期/完成的目标
            self._cleanup_completed_goals()

            # 2. 减少冷却计数
            self.intent_engine.decrement_cooldowns()

            # 3. 生成候选意图（异步调用 LLM）
            candidate_intents = await self.intent_engine.generate_candidates(
                situation,
                self.active_goals,
            )

            # 4. 从新意图创建目标
            for intent in candidate_intents:
                if len(self.active_goals) >= max_active:
                    break

                # 创建新目标
                goal = create_goal_from_intent(intent)
                self.active_goals.append(goal)

                # 添加冷却
                self.intent_engine.add_cooldown(
                    intent.id,
                    intent.expiry_messages // 2,  # 冷却时间为过期时间的一半
                )

            # 5. 重新排序（优先级高的在前）
            self.active_goals.sort(key=lambda g: g.priority, reverse=True)

            if self.active_goals:
                logger.debug(
                    f"当前活跃目标：{len(self.active_goals)} - "
                    f"[{self.active_goals[0].objective}]"
                )

            return self.active_goals

        except Exception as e:
            logger.error(f"更新目标失败：{e}")
            import traceback
            logger.error(f"堆栈追踪：{traceback.format_exc()}")
            return self.active_goals

    def _cleanup_completed_goals(self) -> None:
        """清理已完成的目标"""
        completed = [g for g in self.active_goals if g.is_completed()]
        self.active_goals = [g for g in self.active_goals if not g.is_completed()]

        # 移到历史记录
        self.goal_history.extend(completed)

        if completed:
            logger.debug(f"清理 {len(completed)} 个已完成目标")

    def get_active_goal(self, index: int = 0) -> Goal | None:
        """获取指定索引的活跃目标"""
        if 0 <= index < len(self.active_goals):
            return self.active_goals[index]
        return None

    def get_priority_goal(self) -> Goal | None:
        """获取优先级最高的目标"""
        return self.active_goals[0] if self.active_goals else None

    def get_goal_by_id(self, goal_id: str) -> Goal | None:
        """根据 ID 获取目标"""
        for goal in self.active_goals:
            if goal.id == goal_id:
                return goal
        return None

    def complete_goal(self, goal_id: str) -> bool:
        """标记目标为完成"""
        goal = self.get_goal_by_id(goal_id)
        if goal:
            goal.complete()
            logger.info(f"目标完成：{goal.objective}")
            return True
        return False

    def abandon_goal(self, goal_id: str) -> bool:
        """放弃目标"""
        goal = self.get_goal_by_id(goal_id)
        if goal:
            goal.abandon()
            logger.info(f"目标放弃：{goal.objective}")
            return True
        return False

    def get_all_goals(self) -> list[dict[str, Any]]:
        """获取所有目标状态（用于调试）"""
        return [
            {
                "id": g.id,
                "objective": g.objective,
                "intent_name": g.intent_name,
                "status": g.status.value,
                "current_step": g.current_step,
                "total_steps": len(g.steps),
                "priority": g.priority,
            }
            for g in self.active_goals + self.goal_history[-10:]  # 最近 10 个历史
        ]

    def get_statistics(self) -> dict[str, Any]:
        """获取统计信息"""
        completed = len(
            [g for g in self.goal_history if g.status == GoalStatus.COMPLETED]
        )
        abandoned = len(
            [g for g in self.goal_history if g.status == GoalStatus.ABANDONED]
        )

        return {
            "total_created": len(self.goal_history),
            "completed": completed,
            "abandoned": abandoned,
            "active": len(self.active_goals),
            "completion_rate": completed / len(self.goal_history)
            if self.goal_history
            else 0,
        }
