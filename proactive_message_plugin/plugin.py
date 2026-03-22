"""Proactive Message Plugin - 主动发消息插件主模块。

让 Bot 具有在用户长时间未回复时主动发消息的能力。

核心逻辑：
1. 用户 last_message 后开始计时
2. 等待 N 分钟（可配置）后触发内心独白
3. LLM 自主决定：发消息 or 继续等待
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from src.core.components.base import BasePlugin
from src.core.components.base.event_handler import BaseEventHandler
from src.core.components.types import EventType
from src.core.components.loader import register_plugin
from src.core.models.stream import ChatStream
from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.api.event_api import register_handler, publish_event

from .service import ProactiveMessageService, get_proactive_message_service
from .config import ProactiveMessageConfig
from .inner_monologue import generate_inner_monologue, extract_conversation_history
from .tools.wait_longer import WaitLongerTool

if TYPE_CHECKING:
    from src.core.components.base import BaseChatter

logger = get_logger("proactive_message_plugin", display="主动消息插件")


class ProactiveMessageEventHandler(BaseEventHandler):
    """主动发消息插件的事件处理器。

    订阅以下事件：
    - ON_MESSAGE_RECEIVED: 收到用户消息时重置等待状态
    - ON_CHATTER_STEP: Chatter 执行一步时检查是否进入 Wait 状态
    """

    plugin_name = "proactive_message_plugin"
    handler_name = "on_message"
    handler_description = "处理消息接收和 Chatter 步件事件"

    init_subscribe: list[EventType | str] = [
        EventType.ON_MESSAGE_RECEIVED,
        EventType.ON_CHATTER_STEP,
    ]

    async def execute(
        self, event_name: str, params: dict
    ) -> tuple:
        """执行事件处理。

        Args:
            event_name: 事件名称
            params: 事件参数

        Returns:
            tuple: (EventDecision, params)
        """
        from src.kernel.event import EventDecision

        plugin = self.plugin
        if not isinstance(plugin, ProactiveMessagePlugin):
            return EventDecision.SUCCESS, params

        try:
            if event_name == EventType.ON_MESSAGE_RECEIVED:
                # 收到用户消息
                chat_stream = params.get("chat_stream")
                message = params.get("message")

                if message is not None and str(getattr(message, "sender_role", "") or "").lower() == "bot":
                    return EventDecision.SUCCESS, params

                if chat_stream is None and message is not None:
                    stream_id = getattr(message, "stream_id", "")
                    if stream_id:
                        try:
                            from src.app.plugin_system.api.stream_api import get_stream

                            chat_stream = await get_stream(stream_id)
                        except Exception as error:
                            logger.debug(f"通过 stream_id 获取 chat_stream 失败：{error}")

                if chat_stream:
                    await plugin._on_user_message(chat_stream)

            elif event_name == EventType.ON_CHATTER_STEP:
                # Chatter 执行一步
                stream_id = params.get("stream_id")
                result = params.get("result")
                if stream_id and result:
                    await plugin._on_chatter_step(stream_id, params.get("context"), result)

        except Exception as e:
            logger.error(f"事件处理失败：{e}")

        return EventDecision.SUCCESS, params


@register_plugin
class ProactiveMessagePlugin(BasePlugin):
    """主动发消息插件。

    功能：
    1. 追踪用户最后消息时间
    2. 在等待达到阈值后触发内心独白
    3. 根据 LLM 决策发送消息或继续等待
    """

    plugin_name = "proactive_message_plugin"
    plugin_version = "1.0.0"
    plugin_author = "Neo-MoFox Team"
    plugin_description = "让 Bot 具有在用户长时间未回复时主动发消息的能力"
    configs = [ProactiveMessageConfig]

    def __init__(self, config: "BaseConfig | None" = None) -> None:
        super().__init__(config)
        self._service: ProactiveMessageService | None = None

    @property
    def service(self) -> ProactiveMessageService:
        """获取主动消息服务"""
        if self._service is None:
            self._service = get_proactive_message_service()
        return self._service

    def _should_ignore(self, chat_stream: ChatStream) -> bool:
        """检查是否应该忽略该聊天流"""
        from .config import ProactiveMessageConfig
        chat_type = str(getattr(chat_stream, "chat_type", "")).lower()
        config = self.config
        if not isinstance(config, ProactiveMessageConfig):
            return True
        ignored_types = config.settings.ignored_chat_types
        return chat_type in ignored_types

    async def on_plugin_loaded(self) -> None:
        """插件加载时的初始化"""
        logger.info("主动发消息插件已加载")
        # 初始化配置和服务
        _ = self.config
        _ = self.service
        # 注册事件处理器
        self._register_handlers()
        logger.info("主动发消息插件初始化完成")

    async def on_plugin_unloaded(self) -> None:
        """插件卸载时的清理"""
        logger.info("主动发消息插件已卸载")
        if self._service:
            self._service.clear_all()
        self._service = None
        self._config = None

    def get_components(self) -> list[type]:
        """获取插件内所有组件类"""
        return [WaitLongerTool, ProactiveMessageEventHandler]

    def _register_handlers(self) -> None:
        """注册事件处理器"""
        # 创建事件处理器实例
        handler = ProactiveMessageEventHandler(self)
        # 注册到事件系统
        import asyncio
        asyncio.create_task(register_handler("proactive_message_plugin:event_handler:on_message", handler))

    async def _on_user_message(self, chat_stream: ChatStream) -> None:
        """当收到用户消息时调用"""
        if not self._should_ignore(chat_stream):
            # 仅重置状态；首轮等待应从本轮消息处理完成、Chatter 重新进入 Wait 后开始。
            # 否则在 first_check_minutes 很小时，内心独白会抢在对这条用户消息的正常回复前触发。
            self.service.on_user_message(chat_stream, cancel_task=False)

    async def _on_chatter_step(self, stream_id: str, context, result) -> None:
        """当 Chatter 执行一步时调用"""
        from src.core.components.base.chatter import Wait
        if isinstance(result, Wait):
            # Bot 进入等待状态
            try:
                from src.app.plugin_system.api.stream_api import get_stream

                chat_stream = await get_stream(stream_id)
                if chat_stream and not self._should_ignore(chat_stream):
                    import asyncio
                    asyncio.create_task(self._start_waiting(chat_stream))
            except Exception as e:
                logger.debug(f"处理 Chatter Wait 状态失败：{e}")

    async def _start_waiting(self, chat_stream: ChatStream) -> None:
        """开始等待计时

        Args:
            chat_stream: 聊天流
        """
        stream_id = getattr(chat_stream, "stream_id", "")
        if not stream_id:
            return

        # 检查是否已在等待中
        state = self.service.get_state(stream_id)
        if state is not None and state.is_waiting:
            logger.debug(f"已在等待中：{stream_id[:8]}...")
            return

        # 开始等待
        logger.info(f"开始等待计时：{stream_id[:8]}...")

        async def _timeout_callback() -> None:
            await self._on_check_timeout(stream_id)

        await self.service.start_waiting(
            stream_id=stream_id,
            wait_minutes=self.config.settings.first_check_minutes,
            callback=_timeout_callback,
        )

    async def _restart_waiting(self, chat_stream: ChatStream) -> None:
        """用户新消息后，重置并从首轮等待时间重新调度。"""
        stream_id = getattr(chat_stream, "stream_id", "")
        if not stream_id:
            return

        async def _timeout_callback() -> None:
            await self._on_check_timeout(stream_id)

        await self.service.start_waiting(
            stream_id=stream_id,
            wait_minutes=self.config.settings.first_check_minutes,
            callback=_timeout_callback,
        )

    async def _on_check_timeout(self, stream_id: str) -> None:
        """当检查超时时调用 - 触发内心独白

        Args:
            stream_id: 聊天流 ID
        """
        logger.info(f"检查超时，触发内心独白：{stream_id[:8]}...")

        # 获取状态
        state = self.service.get_state(stream_id)
        if state is None:
            logger.warning(f"未找到状态：{stream_id[:8]}...")
            return

        # 使用累计等待时间，包含多次 wait_longer
        elapsed = self.service.get_total_wait_minutes(stream_id)

        # 获取聊天流信息
        try:
            from src.app.plugin_system.api.stream_api import get_stream

            chat_stream = await get_stream(stream_id)

            if chat_stream is None:
                logger.warning(f"未找到聊天流：{stream_id[:8]}...")
                return

            # 检查是否应该忽略
            if self._should_ignore(chat_stream):
                return

            # 获取用户名称
            user_name = getattr(chat_stream, "stream_name", "用户")

            # 获取对话历史
            context = getattr(chat_stream, "context", None)
            history_messages = getattr(context, "history_messages", []) if context else []
            conversation_history = extract_conversation_history(history_messages, limit=10)

            # 生成内心独白
            result = await generate_inner_monologue(
                chat_stream=chat_stream,
                elapsed_minutes=elapsed,
                user_name=user_name,
            )

            if result is None:
                logger.warning(f"内心独白无结果：{stream_id[:8]}...")
                # 无结果则继续等待
                await self._schedule_continue_waiting(stream_id, 30)
                return

            # 处理决策
            await self._handle_decision(stream_id, chat_stream, result)

        except Exception as e:
            logger.error(f"内心独白处理失败：{e}", exc_info=True)
            # 失败则继续等待
            await self._schedule_continue_waiting(stream_id, 30)

    async def _handle_decision(
        self,
        stream_id: str,
        chat_stream: ChatStream,
        result,
    ) -> None:
        """处理内心独白的决策

        Args:
            stream_id: 聊天流 ID
            chat_stream: 聊天流对象
            result: 内心独白结果
        """
        await self._inject_inner_monologue(chat_stream, result.thought)

        if result.decision == "send_message":
            # 发送消息
            if result.content:
                logger.info(f"主动发消息：{stream_id[:8]}... 内容：{result.content[:50]}...")
                await self._send_message(chat_stream, result.content)
                # 发送后不直接结束，调度“无人回复”的二次检查
                self.service.clear_state(stream_id, cancel_task=False)
                await self._schedule_post_send_followup(stream_id)
            else:
                logger.warning(f"决策发送消息但内容为空：{stream_id[:8]}...")
                await self._schedule_continue_waiting(stream_id, 15)

        elif result.decision == "wait_longer":
            # 继续等待：先 checkpoint 已等待时长，再追加新的等待
            self.service.checkpoint_wait(stream_id)
            wait_minutes = result.wait_minutes or 30
            logger.info(f"继续等待：{stream_id[:8]}... 等待{wait_minutes}分钟")
            await self._schedule_continue_waiting(stream_id, wait_minutes)

    async def _inject_inner_monologue(self, chat_stream: ChatStream, thought: str) -> None:
        if not thought.strip():
            return

        from src.core.models.message import Message, MessageType
        from src.core.managers.stream_manager import get_stream_manager
        from uuid import uuid4

        normalized_thought = thought.strip()
        history_text = f"[内心独白] {normalized_thought}"

        message = Message(
            message_id=f"inner_monologue_{uuid4().hex}",
            content=history_text,
            processed_plain_text=history_text,
            message_type=MessageType.TEXT,
            sender_id=chat_stream.bot_id or "bot",
            sender_name=f"{chat_stream.bot_nickname or 'Bot'}（内心独白）",
            sender_role="bot",
            platform=chat_stream.platform,
            chat_type=chat_stream.chat_type,
            stream_id=chat_stream.stream_id,
            is_inner_monologue=True,
        )
        await get_stream_manager().add_sent_message_to_history(message)
        try:
            from default_chatter import plugin as default_chatter_plugin_module

            push_runtime_assistant_injection = getattr(
                default_chatter_plugin_module,
                "push_runtime_assistant_injection",
                None,
            )
            if callable(push_runtime_assistant_injection):
                push_runtime_assistant_injection(chat_stream.stream_id, history_text)
        except Exception as error:
            logger.debug(f"写入实时 assistant 注入失败：{error}")
        logger.debug(f"已注入内心独白到上下文：{chat_stream.stream_id[:8]}...")

    async def _send_message(self, chat_stream: ChatStream, content: str) -> None:
        """发送消息

        Args:
            chat_stream: 聊天流
            content: 消息内容
        """
        try:
            from src.core.transport.message_send import get_message_sender
            from src.core.models.message import Message, MessageType
            from uuid import uuid4

            sender = get_message_sender()

            # 构建消息对象
            message = Message(
                message_id=f"proactive_{uuid4().hex}",
                content=content,
                processed_plain_text=content,
                message_type=MessageType.TEXT,
                sender_id=chat_stream.bot_id or "bot",
                sender_name=chat_stream.bot_nickname or "Bot",
                sender_role="bot",
                platform=chat_stream.platform,
                chat_type=chat_stream.chat_type,
                stream_id=chat_stream.stream_id,
            )

            success = await sender.send_message(message)
            if success:
                logger.info(f"主动消息发送成功：{chat_stream.stream_id[:8]}...")
            else:
                logger.error(f"主动消息发送失败：{chat_stream.stream_id[:8]}...")

        except Exception as e:
            logger.error(f"发送主动消息失败：{e}", exc_info=True)

    async def _schedule_continue_waiting(self, stream_id: str, wait_minutes: float) -> None:
        """调度继续等待

        Args:
            stream_id: 聊天流 ID
            wait_minutes: 等待分钟数
        """
        state = self.service.get_state(stream_id)
        if state is None:
            return

        # 应用最大等待时间限制（累计）
        max_wait = self.config.settings.max_wait_minutes
        total_wait = self.service.get_total_wait_minutes(stream_id)

        if total_wait >= max_wait:
            logger.info(f"已达最大等待时间，强制触发：{stream_id[:8]}...")
            # 强制触发
            await self._on_check_timeout(stream_id)
            return

        # 应用最小等待间隔
        min_wait = self.config.settings.min_wait_interval_minutes
        wait_minutes = max(wait_minutes, min_wait)

        async def _timeout_callback() -> None:
            await self._on_check_timeout(stream_id)

        # 调度下次检查
        await self.service.start_waiting(
            stream_id=stream_id,
            wait_minutes=wait_minutes,
            callback=_timeout_callback,
        )

    async def _schedule_post_send_followup(self, stream_id: str) -> None:
        """主动发送后，无人回复的二次检查调度。"""
        if not stream_id:
            return

        async def _timeout_callback() -> None:
            await self._on_check_timeout(stream_id)

        wait_minutes = getattr(self.config.settings, "post_send_followup_minutes", 10.0)
        # 发送后重置累计计时，再按 post_send_followup_minutes 重新等待
        state = self.service.get_or_create_state(stream_id)
        state.accumulated_wait_minutes = 0.0
        state.last_user_message_time = datetime.now()

        await self.service.start_waiting(
            stream_id=stream_id,
            wait_minutes=wait_minutes,
            callback=_timeout_callback,
        )


# 全局插件实例引用
_plugin_instance: ProactiveMessagePlugin | None = None


def get_proactive_message_plugin() -> ProactiveMessagePlugin | None:
    """获取主动发消息插件实例"""
    global _plugin_instance
    return _plugin_instance
