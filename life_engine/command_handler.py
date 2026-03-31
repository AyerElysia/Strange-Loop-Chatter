"""life_engine 命令处理器。"""

from __future__ import annotations

from typing import Any

from src.app.plugin_system.api.log_api import get_logger
from src.core.components.base.event_handler import BaseEventHandler
from src.core.components.types import EventType
from src.kernel.event import EventDecision

logger = get_logger("life_engine", display="life_engine")


class LifeEngineCommandHandler(BaseEventHandler):
    """处理 life_engine 相关的命令。"""

    plugin_name = "life_engine"
    handler_name = "command_handler"
    handler_description = "处理 life_engine 命令（如手动触发心跳）"
    weight = 45
    intercept_message = False
    init_subscribe: list[EventType | str] = [
        EventType.ON_MESSAGE_RECEIVED,
    ]

    async def execute(
        self, event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        """检查消息是否是 life_engine 命令，如果是则执行。"""
        if event_name != EventType.ON_MESSAGE_RECEIVED.value:
            return EventDecision.PASS, params

        message = params.get("message")
        if message is None:
            return EventDecision.PASS, params

        # 获取消息文本
        content = getattr(message, "processed_plain_text", "") or getattr(message, "content", "")
        if not isinstance(content, str):
            return EventDecision.PASS, params

        content = content.strip()

        # 检查是否是心跳命令
        if content not in ("/heartbeat", "/心跳", "!heartbeat", "!心跳"):
            return EventDecision.PASS, params

        plugin = self.plugin
        if getattr(plugin, "plugin_name", "") != "life_engine":
            return EventDecision.PASS, params

        service = getattr(plugin, "service", None)
        if service is None:
            return EventDecision.PASS, params

        try:
            logger.info(f"收到手动触发心跳命令: {content}")
            result = await service.trigger_heartbeat_manually()
            
            # 构造回复消息
            if result.get("success"):
                reply_text = (
                    f"✓ 心跳已手动触发\n"
                    f"序号: #{result.get('heartbeat_count')}\n"
                    f"事件数: {result.get('event_count')}\n"
                    f"回复: {result.get('reply', '（无）')[:200]}"
                )
            else:
                reply_text = f"✗ 心跳触发失败: {result.get('error', '未知错误')}"

            # 发送回复
            from src.core.managers.stream_manager import get_stream_manager
            stream_manager = get_stream_manager()
            chat_stream = await stream_manager.get_or_create_stream(stream_id=message.stream_id)
            
            if chat_stream:
                from src.core.models.message import Message as CoreMessage
                reply_message = CoreMessage(
                    message_id=f"life_heartbeat_reply_{message.message_id}",
                    platform=message.platform,
                    chat_type=message.chat_type,
                    stream_id=message.stream_id,
                    sender_id=chat_stream.context.bot_id or "bot",
                    sender_name=chat_stream.context.bot_nickname or "Bot",
                    sender_role="assistant",
                    message_type="text",
                    content=reply_text,
                    processed_plain_text=reply_text,
                    time=message.time,
                )
                
                # 直接通过适配器发送
                from src.core.transport.distribution.distributor import get_distributor
                distributor = get_distributor()
                await distributor.send_message(reply_message)

            logger.info(f"已回复心跳命令结果: success={result.get('success')}")

        except Exception as exc:  # noqa: BLE001
            logger.error(f"处理心跳命令失败: {exc}")

        # 拦截这条消息，不让它进入正常的对话流程
        return EventDecision.INTERCEPT, params
