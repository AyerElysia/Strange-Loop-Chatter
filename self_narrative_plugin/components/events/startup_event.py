"""自我叙事启动事件。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.app.plugin_system.base import BaseEventHandler
from src.core.components.types import EventType
from src.kernel.event import EventDecision
from src.app.plugin_system.api.log_api import get_logger

from ...service import get_self_narrative_service

if TYPE_CHECKING:
    from ...plugin import SelfNarrativePlugin

logger = get_logger("self_narrative_plugin.events.startup")


class SelfNarrativeStartupEvent(BaseEventHandler):
    """系统启动时初始化自我叙事服务。"""

    handler_name = "self_narrative_startup_event"
    handler_description = "系统启动时初始化 self_narrative_plugin 的定时更新"
    weight = 100
    intercept_message = False
    init_subscribe = [EventType.ON_START]

    def __init__(self, plugin: "SelfNarrativePlugin") -> None:
        super().__init__(plugin)

    async def execute(
        self,
        event_name: str,
        params: dict[str, Any],
    ) -> tuple[EventDecision, dict[str, Any]]:
        del event_name
        service = get_self_narrative_service()
        if service is None:
            logger.warning("self_narrative_service 未加载")
            return EventDecision.SUCCESS, params

        try:
            await service.initialize()
        except Exception as exc:
            logger.error(f"self_narrative 初始化失败: {exc}", exc_info=True)
        return EventDecision.SUCCESS, params

