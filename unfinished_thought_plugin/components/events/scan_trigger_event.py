"""未完成念头扫描触发事件。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.app.plugin_system.base import BaseEventHandler
from src.core.components.types import EventType
from src.kernel.event import EventDecision
from src.kernel.logger import get_logger

from ...service import get_unfinished_thought_service

if TYPE_CHECKING:
    from ...plugin import UnfinishedThoughtPlugin


logger = get_logger("unfinished_thought_plugin.events.scan")


class UnfinishedThoughtScanEvent(BaseEventHandler):
    """按固定对话数累计并自动扫描未完成念头。"""

    handler_name = "unfinished_thought_scan_event"
    handler_description = "按固定对话数自动扫描未完成念头"
    weight = 6
    intercept_message = False
    init_subscribe = [EventType.ON_CHATTER_STEP]

    def __init__(self, plugin: "UnfinishedThoughtPlugin") -> None:
        super().__init__(plugin)

    async def execute(
        self,
        event_name: str,
        params: dict[str, Any],
    ) -> tuple[EventDecision, dict[str, Any]]:
        del event_name

        config = getattr(self.plugin, "config", None)
        if not config or not getattr(config.plugin, "enabled", True):
            return EventDecision.SUCCESS, params

        stream_id = str(params.get("stream_id", "")).strip()
        if not stream_id:
            return EventDecision.SUCCESS, params

        service = get_unfinished_thought_service()
        if service is None:
            return EventDecision.SUCCESS, params

        ok, message = await service.record_chat_turn(
            stream_id=stream_id,
            chat_type=str(params.get("chat_type", "private")),
            platform=str(params.get("platform", "")),
            stream_name=str(params.get("stream_name", "")),
            trigger="auto",
        )
        if not ok:
            logger.debug(f"[{stream_id[:8]}] 未完成念头计数/扫描失败：{message}")
        return EventDecision.SUCCESS, params

