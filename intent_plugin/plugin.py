"""意图插件入口类。

负责插件初始化、组件注册、生命周期管理。
"""

from __future__ import annotations

from src.app.plugin_system.base import BasePlugin, BaseConfig, register_plugin
from src.kernel.logger import get_logger

from .config import IntentConfig
from .goal_tracker import GoalTracker
from .service import IntentService


logger = get_logger("intent_plugin")


@register_plugin
class IntentPlugin(BasePlugin):
    """意图插件入口类

    自主意图与短期目标系统，让模型具备内在驱动力。
    使用 LLM 动态生成意图，而非预定义模版，让爱莉希雅自由生成短期目标。
    """

    plugin_name: str = "intent_plugin"
    plugin_description: str = "自主意图与短期目标系统"

    configs: list[type] = [IntentConfig]

    def __init__(self, config: "BaseConfig | None" = None) -> None:
        """初始化插件"""
        super().__init__(config)

    def get_components(self) -> list[type]:
        """返回插件包含的组件类列表"""
        return [
            IntentService,
            GoalTracker,
        ]

    async def on_plugin_loaded(self) -> None:
        """插件加载时的回调"""
        logger.info("意图插件已加载")
        logger.info("意图插件初始化完成")

    async def on_plugin_unloaded(self) -> None:
        """插件卸载时的回调"""
        logger.info("意图插件已卸载")

        # 清理 System Reminder
        await self._cleanup_reminder()

    async def _cleanup_reminder(self) -> None:
        """清理 System Reminder"""
        from src.core.prompt import get_system_reminder_store

        store = get_system_reminder_store()
        store.delete("actor", "当前小想法")
        logger.debug("已清理 System Reminder")
