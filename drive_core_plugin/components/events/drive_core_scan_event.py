"""drive_core_plugin 扫描事件处理器。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.app.plugin_system.base import BaseEventHandler
from src.core.components.types import EventType
from src.kernel.event import EventDecision
from src.kernel.logger import get_logger

from ...service import get_drive_core_service

if TYPE_CHECKING:
    from ...plugin import DriveCorePlugin


logger = get_logger("drive_core_plugin.events.scan")


class DriveCoreScanEvent(BaseEventHandler):
    """按聊天推进触发内驱力扫描。"""

    handler_name = "drive_core_scan_event"
    handler_description = "按聊天推进触发自我发问"
    weight = 7
    intercept_message = False
    init_subscribe = [EventType.ON_CHATTER_STEP]

    def __init__(self, plugin: "DriveCorePlugin") -> None:
        super().__init__(plugin)

    async def execute(
        self,
        event_name: str,
        params: dict[str, Any],
    ) -> tuple[EventDecision, dict[str, Any]]:
        del event_name

        plugin = self.plugin
        if not getattr(getattr(plugin, "config", None), "plugin", None):
            return EventDecision.SUCCESS, params
        if not getattr(plugin.config.plugin, "enabled", True):
            return EventDecision.SUCCESS, params

        stream_id = str(params.get("stream_id", "")).strip()
        if not stream_id:
            return EventDecision.SUCCESS, params

        # 只在本轮已完成 step 后推进一次，避免 before/after 双触发
        if "result" not in params:
            return EventDecision.SUCCESS, params

        context = params.get("context")
        chat_type = str(getattr(context, "chat_type", "") or "").strip() or "private"
        platform = str(getattr(context, "platform", "") or "").strip()
        stream_name = str(getattr(context, "stream_name", "") or "").strip()

        service = get_drive_core_service()
        if service is None:
            logger.debug("drive_core_service 未加载")
            return EventDecision.SUCCESS, params

        try:
            ok, message = await service.observe_chat_turn(
                stream_id=stream_id,
                chat_type=chat_type,
                platform=platform,
                stream_name=stream_name,
                trigger="chatter_step",
            )
            if not ok:
                logger.debug(f"[{stream_id[:8]}] drive_core 推进失败：{message}")
        except Exception as exc:
            logger.error(f"drive_core 扫描失败：{exc}", exc_info=True)

        return EventDecision.SUCCESS, params

