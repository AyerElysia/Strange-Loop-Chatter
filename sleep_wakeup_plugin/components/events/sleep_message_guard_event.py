"""睡眠期消息拦截事件处理器。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.app.plugin_system.base import BaseEventHandler
from src.core.components.types import EventType
from src.kernel.event import EventDecision
from src.kernel.logger import get_logger

from ...managers import get_sleep_wakeup_manager

if TYPE_CHECKING:
    from ...plugin import SleepWakeupPlugin

logger = get_logger("sleep_wakeup_plugin.events.guard")

PRIVATE_CHAT = "private"


class SleepMessageGuardEvent(BaseEventHandler):
    """在睡眠状态阻挡消息事件的守护处理器。"""

    handler_name = "sleep_message_guard_event"
    handler_description = "睡眠期阻挡消息事件"
    weight = 1000
    intercept_message = True
    init_subscribe = [
        EventType.ON_MESSAGE_RECEIVED,
        EventType.ON_RECEIVED_OTHER_MESSAGE,
        EventType.ON_MESSAGE_SENT,
    ]

    def __init__(self, plugin: "SleepWakeupPlugin") -> None:
        super().__init__(plugin)
        self.plugin = plugin

    async def execute(
        self,
        event_name: str,
        params: dict[str, Any],
    ) -> tuple[EventDecision, dict[str, Any]]:
        if self._is_incoming_private_message(event_name, params):
            try:
                message = params.get("message")
                sender_id = str(getattr(message, "sender_id", "") or params.get("sender_id", ""))
                platform = str(getattr(message, "platform", "") or params.get("platform", ""))
                manager = get_sleep_wakeup_manager()
                changed = await manager.handle_private_message_wakeup(
                    sender_id=sender_id,
                    platform=platform,
                )
                if changed:
                    logger.debug(
                        f"私聊消息触发唤醒调整: event={event_name}, user={platform}:{sender_id}"
                    )
            except RuntimeError:
                logger.warning("管理器未初始化，无法处理私聊唤醒")

        try:
            manager = get_sleep_wakeup_manager()
            should_block = manager.should_block_messages()
        except RuntimeError:
            should_block = False

        if should_block:
            logger.debug(f"睡眠守护已拦截事件: {event_name}")
            return EventDecision.STOP, params

        return EventDecision.SUCCESS, params

    @staticmethod
    def _is_incoming_private_message(
        event_name: str,
        params: dict[str, Any],
    ) -> bool:
        if event_name != EventType.ON_MESSAGE_RECEIVED.value:
            return False

        message = params.get("message")
        chat_type = str(getattr(message, "chat_type", "") or params.get("chat_type", "")).lower()
        return chat_type == PRIVATE_CHAT

