"""意图服务。

提供对外接口，用于查询意图/目标状态、调试等。
"""

from __future__ import annotations

from typing import Any

from src.app.plugin_system.base import BaseService
from src.kernel.logger import get_logger

from .goal_tracker import GoalTracker


logger = get_logger("intent_plugin")


class IntentService(BaseService):
    """意图服务

    提供查询意图/目标状态的对外接口。
    """

    service_name: str = "intent_service"
    service_description: str = """
    意图与目标系统服务。

    **核心功能**：
    - 查询当前活跃目标
    - 查询目标历史统计
    - 手动触发意图（调试用）
    - 清除目标状态
    """

    def __init__(self, plugin: Any = None) -> None:
        """初始化意图服务"""
        super().__init__(plugin)
        self._goal_tracker: GoalTracker | None = None

    def _get_goal_tracker(self) -> GoalTracker | None:
        """获取 GoalTracker 实例"""
        if self._goal_tracker is None:
            self._goal_tracker = self._find_goal_tracker()
        return self._goal_tracker

    def _find_goal_tracker(self) -> GoalTracker | None:
        """查找 GoalTracker 实例"""
        if self.plugin and hasattr(self.plugin, "event_handlers"):
            for handler in self.plugin.event_handlers:
                if isinstance(handler, GoalTracker):
                    return handler
        return None

    def get_active_goals(self) -> list[dict[str, Any]]:
        """获取当前活跃目标列表

        Returns:
            list[dict]: 目标信息列表
        """
        tracker = self._get_goal_tracker()
        if tracker is None:
            return []

        return [
            {
                "id": g.id,
                "intent_id": g.intent_id,
                "intent_name": g.intent_name,
                "objective": g.objective,
                "status": g.status.value,
                "current_step": g.current_step,
                "total_steps": len(g.steps),
                "priority": g.priority,
                "created_at": g.created_at.isoformat() if g.created_at else None,
            }
            for g in tracker.goal_manager.active_goals
        ]

    def get_goal_history(self, limit: int = 10) -> list[dict[str, Any]]:
        """获取目标历史记录

        Args:
            limit: 返回最近 N 条记录

        Returns:
            list[dict]: 目标信息列表
        """
        tracker = self._get_goal_tracker()
        if tracker is None:
            return []

        history = tracker.goal_manager.goal_history[-limit:]
        return [
            {
                "id": g.id,
                "intent_name": g.intent_name,
                "objective": g.objective,
                "status": g.status.value,
                "completed_at": g.completed_at.isoformat() if g.completed_at else None,
            }
            for g in history
        ]

    def get_statistics(self) -> dict[str, Any]:
        """获取统计信息

        Returns:
            dict: 统计数据
        """
        tracker = self._get_goal_tracker()
        if tracker is None:
            return {}

        return tracker.goal_manager.get_statistics()

    def get_current_reminder(self) -> str | None:
        """获取当前 System Reminder 内容

        Returns:
            str | None: Reminder 内容
        """
        tracker = self._get_goal_tracker()
        if tracker is None:
            return None

        goal = tracker.goal_manager.get_priority_goal()
        if goal is None:
            return None

        return tracker._build_reminder(goal)

    def clear_all_goals(self) -> bool:
        """清除所有目标（调试用）

        Returns:
            bool: 是否成功
        """
        tracker = self._get_goal_tracker()
        if tracker is None:
            return False

        tracker.goal_manager.active_goals.clear()
        logger.info("已清除所有活跃目标")
        return True

    def trigger_intent(self, intent_id: str) -> dict[str, Any]:
        """手动触发指定意图（调试用）

        Args:
            intent_id: 意图 ID

        Returns:
            dict: 触发结果
        """
        tracker = self._get_goal_tracker()
        if tracker is None:
            return {"success": False, "message": "GoalTracker 未找到"}

        # 查找意图
        intent = tracker.goal_manager.intent_engine.get_intent_by_id(intent_id)
        if intent is None:
            return {"success": False, "message": f"意图 {intent_id} 不存在"}

        # 创建目标
        from .intent_engine import create_goal_from_intent

        goal = create_goal_from_intent(intent)

        # 添加到活跃列表
        tracker.goal_manager.active_goals.append(goal)

        # 添加冷却
        tracker.goal_manager.intent_engine.add_cooldown(
            intent_id,
            intent.expiry_messages // 2,
        )

        logger.info(f"手动触发意图：{intent_id}")

        return {
            "success": True,
            "message": f"已触发意图：{intent.name}",
            "goal": {
                "id": goal.id,
                "objective": goal.objective,
                "steps": len(goal.steps),
            },
        }
