"""Time Awareness Plugin - 时间感知插件主模块。

为爱莉希雅提供时间感知能力：
1. 注入中式时间描述到 system reminder
2. 追踪每个聊天流的用户消息时间
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from src.core.components.base import BasePlugin
from src.core.components.base.event_handler import BaseEventHandler
from src.core.components.types import EventType
from src.core.components.loader import register_plugin
from src.app.plugin_system.api.prompt_api import add_system_reminder
from src.app.plugin_system.api.event_api import register_handler

from .tools.query_time import QueryTimeTool, build_chinese_datetime
from .config import TimeAwarenessConfig
from .service import get_time_awareness_service

if TYPE_CHECKING:
    from src.core.models.stream import ChatStream


class TimeAwarenessEventHandler(BaseEventHandler):
    """时间感知插件的事件处理器。

    订阅以下事件：
    - ON_MESSAGE_RECEIVED: 收到用户消息时更新时间追踪
    """

    plugin_name = "time_awareness_plugin"
    handler_name = "time_tracker"
    handler_description = "追踪用户消息时间"

    init_subscribe: list[EventType | str] = [
        EventType.ON_MESSAGE_RECEIVED,
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
        if not isinstance(plugin, TimeAwarenessPlugin):
            return EventDecision.SUCCESS, params

        try:
            if event_name == EventType.ON_MESSAGE_RECEIVED:
                # 收到用户消息
                chat_stream = params.get("chat_stream")
                if chat_stream:
                    # 仅在私聊场景生效
                    chat_type = str(getattr(chat_stream, "chat_type", "")).lower()
                    if chat_type == "private":
                        stream_id = getattr(chat_stream, "stream_id", "")
                        if stream_id:
                            plugin.service.on_user_message(stream_id)

        except Exception as e:
            from src.app.plugin_system.api.log_api import get_logger
            logger = get_logger("time_awareness_plugin", display="时间感知插件")
            logger.error(f"事件处理失败：{e}")

        return EventDecision.SUCCESS, params


@register_plugin
class TimeAwarenessPlugin(BasePlugin):
    """时间感知插件。

    功能：
    1. 注入中式时间描述到 system reminder
    2. 追踪每个聊天流的用户消息时间

    Attributes:
        plugin_name: 插件名称
        plugin_version: 插件版本
        plugin_author: 插件作者
        plugin_description: 插件功能描述
    """

    plugin_name = "time_awareness_plugin"
    plugin_version = "2.0.0"
    plugin_author = "Neo-MoFox Team"
    plugin_description = "时间感知插件 - 为爱莉希雅注入中式时间描述和消息间隔感知能力"
    configs = [TimeAwarenessConfig]

    def __init__(self, config: "BaseConfig | None" = None) -> None:
        super().__init__(config)
        self._service = None

    @property
    def service(self):
        """获取时间感知服务"""
        if self._service is None:
            self._service = get_time_awareness_service()
        return self._service

    def get_components(self) -> list[type]:
        """获取插件内所有组件类。

        Returns:
            list[type]: 包含插件内所有组件类的列表
        """
        return [QueryTimeTool, TimeAwarenessEventHandler]

    async def on_plugin_loaded(self) -> None:
        """插件加载时的初始化钩子。

        注入当前时间描述到 system reminder，并注册事件处理器。
        """
        config = self.config

        # 检查是否启用
        if not config.settings.enabled:
            return

        # 检查是否需要在加载时注入
        if config.settings.inject_on_load:
            self._inject_time_reminder()

        # 注册事件处理器来追踪用户消息
        self._register_event_handlers()

    def _inject_time_reminder(self) -> None:
        """注入时间提醒到 system reminder。"""
        time_str = build_chinese_datetime(datetime.now())
        add_system_reminder(
            bucket="actor",
            name="current_datetime",
            content=f"现在是 {time_str}。在回复用户时，请结合当前时间给出合适的问候和回应。",
        )

    def _register_event_handlers(self) -> None:
        """注册事件处理器"""
        # 创建事件处理器实例
        handler = TimeAwarenessEventHandler(self)
        # 注册到事件系统
        import asyncio
        asyncio.create_task(register_handler("time_awareness_plugin:event_handler:time_tracker", handler))


async def on_plugin_loaded(plugin: TimeAwarenessPlugin) -> None:
    """插件加载入口。

    Args:
        plugin: 插件实例
    """
    await plugin.on_plugin_loaded()
