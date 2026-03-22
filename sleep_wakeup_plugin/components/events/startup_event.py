"""睡眠插件启动初始化事件处理器。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.app.plugin_system.base import BaseEventHandler
from src.core.components.types import EventType
from src.kernel.event import EventDecision
from src.kernel.logger import get_logger

from ...managers import get_sleep_wakeup_manager

if TYPE_CHECKING:
    from ...plugin import SleepWakeupPlugin

logger = get_logger("sleep_wakeup_plugin.events.startup")


class SleepWakeupStartupEvent(BaseEventHandler):
    """在 ON_START 时初始化运行时与调度。"""

    handler_name = "sleep_wakeup_startup_event"
    handler_description = "系统启动时初始化 sleep_wakeup_plugin 调度"
    weight = 100
    intercept_message = False
    init_subscribe = [EventType.ON_START]

    def __init__(self, plugin: "SleepWakeupPlugin") -> None:
        super().__init__(plugin)
        self.plugin = plugin

    async def execute(
        self,
        event_name: str,
        params: dict[str, Any],
    ) -> tuple[EventDecision, dict[str, Any]]:
        del event_name
        try:
            manager = get_sleep_wakeup_manager()
            await manager.initialize()
            logger.info("sleep_wakeup_plugin 启动初始化成功")
        except RuntimeError as exc:
            logger.error(f"管理器未初始化，无法启动: {exc}", exc_info=True)
        except Exception as exc:
            logger.error(f"ON_START 初始化失败: {exc}", exc_info=True)
        return EventDecision.SUCCESS, params

