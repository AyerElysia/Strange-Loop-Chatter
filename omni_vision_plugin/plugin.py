"""Omni Vision 插件主模块。

全模态视觉插件 - 允许主模型直接接收图片，绕过 VLM 转译。
"""

from __future__ import annotations

from typing import Any

from src.app.plugin_system.base import BasePlugin, BaseConfig, register_plugin
from src.core.components.base.event_handler import BaseEventHandler
from src.core.components.types import EventType
from src.kernel.event import EventDecision
from src.kernel.logger import get_logger

from .config import OmniVisionConfig


logger = get_logger("omni_vision_plugin")


@register_plugin
class OmniVisionPlugin(BasePlugin):
    """Omni Vision 插件入口类。

    全模态视觉插件，允许主模型直接接收图片，绕过 VLM 转译。
    """

    plugin_name: str = "omni_vision_plugin"
    plugin_description: str = "全模态视觉插件 - 允许主模型直接接收图片"

    configs: list[type] = [OmniVisionConfig]

    def __init__(self, config: BaseConfig | None = None) -> None:
        """初始化插件。

        Args:
            config: 配置对象
        """
        super().__init__(config)
        self.config: OmniVisionConfig | None = None
        if config is not None and isinstance(config, OmniVisionConfig):
            self.config = config

    def get_components(self) -> list[type]:
        """返回插件包含的组件类列表。

        Returns:
            组件类列表
        """
        return [
            OmniVisionHandler,
        ]

    async def on_plugin_loaded(self) -> None:
        """插件加载时的回调。"""
        logger.info("全模态视觉插件已加载")
        if self.config and self.config.settings.enable_omni_vision:
            logger.info("全模态视觉功能已启用 - 主模型将直接接收图片")
        else:
            logger.info("全模态视觉功能已禁用 - 使用 VLM 转译图片")

    async def on_plugin_unloaded(self) -> None:
        """插件卸载时的回调。"""
        logger.info("全模态视觉插件已卸载")


class OmniVisionHandler(BaseEventHandler):
    """全模态视觉事件处理器。

    监听对话开始事件，当启用全模态视觉时，注册聊天流为跳过 VLM 识别。
    """

    handler_name: str = "omni_vision_handler"
    handler_description: str = "启用全模态视觉时，跳过 VLM 图片转译"
    weight: int = 5  # 较高优先级，在 MediaManager 处理前执行

    init_subscribe: list[EventType | str] = [
        EventType.ON_MESSAGE_RECEIVED,
    ]

    def __init__(self, plugin: Any) -> None:
        """初始化事件处理器。

        Args:
            plugin: 插件实例
        """
        super().__init__(plugin)

        # 加载配置
        self.config: OmniVisionConfig | None = None
        if hasattr(plugin, "config") and isinstance(plugin.config, OmniVisionConfig):
            self.config = plugin.config

    async def execute(
        self,
        event_name: str,
        params: dict[str, Any],
    ) -> tuple[EventDecision, dict[str, Any]]:
        """执行事件处理逻辑。

        Args:
            event_name: 事件名称
            params: 事件参数

        Returns:
            事件决策和更新后的参数
        """
        # 检查配置是否启用
        if not self.config or not self.config.settings.enable_omni_vision:
            return EventDecision.SUCCESS, params

        # 从消息对象中提取 stream_id
        message = params.get("message")
        if not message:
            return EventDecision.SUCCESS, params

        stream_id = getattr(message, "stream_id", None)
        if not stream_id:
            return EventDecision.SUCCESS, params

        # 检查消息是否包含媒体
        media_list = getattr(message, "extra", {}).get("media", [])
        has_media = bool(media_list)

        # 调用 MediaManager 跳过 VLM 识别（仅跳过图片，表情包仍需转译）
        try:
            from src.core.managers.media_manager import get_media_manager

            manager = get_media_manager()
            manager.skip_vlm_for_stream(stream_id, skip_emoji=False)

            if has_media:
                logger.info(
                    f"[图片直传] 聊天流 {stream_id[:8]} - 跳过 VLM 识别，"
                    f"媒体数量：{len(media_list)}，"
                    f"主模型将直接接收图片（表情包仍会转译）"
                )
            else:
                logger.debug(
                    f"已为聊天流 {stream_id[:8]} 注册跳过 VLM 识别（仅图片）"
                )
        except Exception as e:
            logger.error(f"跳过 VLM 识别失败：{e}")

        return EventDecision.SUCCESS, params
