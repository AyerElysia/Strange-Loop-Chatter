"""人格扫描事件处理器。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.app.plugin_system.base import BaseEventHandler
from src.core.components.types import EventType
from src.kernel.event import EventDecision
from src.kernel.logger import get_logger

from ...service import get_personality_engine_service

if TYPE_CHECKING:
    from ...plugin import PersonalityEnginePlugin


logger = get_logger("personality_engine_plugin.events.scan")


class PersonalityScanEvent(BaseEventHandler):
    """按聊天推进触发人格更新。"""

    handler_name = "personality_scan_event"
    handler_description = "按聊天推进触发人格引擎扫描"
    weight = 6
    intercept_message = False
    init_subscribe = [EventType.ON_CHATTER_STEP_RESULT]

    def __init__(self, plugin: "PersonalityEnginePlugin") -> None:
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

        context = params.get("context")
        stream_id = str(params.get("stream_id", "")).strip() or str(
            getattr(context, "stream_id", "") or ""
        ).strip()
        if not stream_id:
            return EventDecision.SUCCESS, params

        chat_type = (
            str(params.get("chat_type", "") or "").strip()
            or str(getattr(context, "chat_type", "") or "").strip()
            or "private"
        )
        platform = str(getattr(context, "platform", "") or "").strip()
        stream_name = str(getattr(context, "stream_name", "") or "").strip()

        service = get_personality_engine_service()
        if service is None:
            logger.debug("personality_engine_service 未加载")
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
                logger.debug(f"[{stream_id[:8]}] 人格推进失败：{message}")
        except Exception as exc:
            logger.error(f"人格扫描失败：{exc}", exc_info=True)

        return EventDecision.SUCCESS, params
