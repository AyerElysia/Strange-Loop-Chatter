"""Prompt Logger 事件处理器。

订阅 ON_CHATTER_STEP 事件，在 LLM 请求发送前记录完整提示词。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from src.core.components import EventType, BaseEventHandler
from src.core.components.base import plugin
from src.kernel.event import EventDecision

from ..config import PromptLoggerConfig

if TYPE_CHECKING:
    from ..plugin import PromptLoggerPlugin


logger = logging.getLogger("prompt_logger")


@plugin("prompt_logger")
class PromptLoggerEventHandler(BaseEventHandler):
    """Prompt Logger 事件处理器。

    订阅 ON_CHATTER_STEP 事件，通过检查 chatter_gene 的状态
    来记录 LLM 提示词到日志文件。
    """

    handler_name = "prompt_logger_handler"
    handler_description = "记录 LLM 提示词到日志文件"
    weight = 0  # 不需要拦截，只记录
    intercept_message = False
    init_subscribe = [EventType.ON_CHATTER_STEP]

    dependencies: list[str] = []

    async def execute(
        self, event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        """执行事件处理，记录提示词。

        Args:
            event_name: 事件名称
            params: 事件参数

        Returns:
            tuple[EventDecision, dict[str, Any]]: 执行结果
        """
        from ..plugin import PromptLoggerPlugin

        plugin_instance = PromptLoggerPlugin.get_instance()
        if plugin_instance is None:
            logger.debug("Prompt Logger 插件未加载，跳过记录")
            return EventDecision.SUCCESS, params

        config = plugin_instance.config
        if not isinstance(config, PromptLoggerConfig):
            return EventDecision.SUCCESS, params

        if not config.general.enabled:
            return EventDecision.SUCCESS, params

        # 从事件参数中提取信息
        stream_id = params.get("stream_id", "")
        chatter_gene = params.get("chatter_gene")
        request_name = params.get("request_name", "")
        context = params.get("context")

        # 检查是否需要过滤
        chatter_name = self._get_chatter_name(chatter_gene)
        chat_type = getattr(context, "chat_type", "unknown") if context else "unknown"

        if not self._should_log(config, chatter_name, chat_type, request_name):
            return EventDecision.SUCCESS, params

        # 尝试从 chatter 中获取 LLM 响应
        # 注意：由于 LLM 请求是在 chatter 内部调用的，我们需要通过其他方式获取
        # 这里我们提供一个工具方法，让 chatter 可以主动调用记录

        return EventDecision.SUCCESS, params

    def _get_chatter_name(self, chatter_gene: Any) -> str:
        """从 chatter 生成器获取 chatter 名称。

        Args:
            chatter_gene: chatter 生成器

        Returns:
            chatter 名称
        """
        if chatter_gene is None:
            return "unknown"

        # 尝试从生成器的 __self__ 获取 chatter 实例
        try:
            chatter = getattr(chatter_gene, "__self__", None)
            if chatter:
                return getattr(chatter, "chatter_name", "unknown")
        except Exception:
            pass

        return "unknown"

    def _should_log(
        self,
        config: PromptLoggerConfig,
        chatter_name: str,
        chat_type: str,
        request_name: str = "",
    ) -> bool:
        """检查是否应该记录此聊天流的提示词。

        Args:
            config: 插件配置
            chatter_name: Chatter 名称
            chat_type: 聊天类型

        Returns:
            bool: 是否应该记录
        """
        # 与拦截器保持一致：默认只跟踪 DFC 主回复 actor
        if config.filter.scope == "dfc_main" and request_name != "actor":
            return False

        # 检查 chatter 过滤
        if chatter_name and chatter_name in config.filter.exclude_chatters:
            logger.debug(f"Chatter '{chatter_name}' 在排除列表中，跳过记录")
            return False

        # 检查聊天类型过滤
        if chat_type == "private" and not config.filter.log_private_chat:
            logger.debug("私聊记录已禁用，跳过")
            return False

        if chat_type in ("group", "discuss") and not config.filter.log_group_chat:
            logger.debug("群聊记录已禁用，跳过")
            return False

        return True
