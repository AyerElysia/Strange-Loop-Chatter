"""Prompt Logger 服务组件。

提供全局 API 供其他插件/Chatter 记录 LLM 提示词。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from src.core.components.base import BaseService, plugin
from src.kernel.logger import get_logger

if TYPE_CHECKING:
    from ..plugin import PromptLoggerPlugin


logger = get_logger("prompt_logger.service", display="提示词服务")


@plugin("prompt_logger")
class PromptLoggerService(BaseService):
    """Prompt Logger 服务组件。

    提供全局 API 供其他插件/Chatter 记录 LLM 提示词到日志文件。
    """

    service_name = "prompt_logger"
    service_description = "提示词记录服务"

    dependencies: list[str] = []

    def __init__(self, plugin_instance: Any) -> None:
        """初始化服务。

        Args:
            plugin_instance: 插件实例
        """
        self.plugin = plugin_instance

    def log_request(
        self,
        payloads: list[Any],
        stream_id: str = "",
        chatter_name: str = "",
        request_name: str = "",
        plugin_name: str = "",
        model_name: str = "",
        chat_type: str = "",
    ) -> None:
        """记录 LLM 请求提示词。

        Args:
            payloads: LLM 请求的 payloads 列表
            stream_id: 聊天流 ID
            chatter_name: Chatter 名称
            request_name: 请求名称
        """
        plugin_inst = self.plugin
        if not hasattr(plugin_inst, 'log_prompt'):
            return

        plugin_inst.log_prompt(
            payloads=payloads,
            stream_id=stream_id,
            chatter_name=chatter_name,
            request_name=request_name,
            plugin_name=plugin_name,
            model_name=model_name,
            chat_type=chat_type,
            is_response=False,
        )
        logger.debug(
            f"已记录请求：stream={stream_id[:8] if stream_id else 'N/A'}, "
            f"chatter={chatter_name}, request={request_name}"
        )

    def log_response(
        self,
        message: str,
        call_list: list[Any] | None = None,
        stream_id: str = "",
        chatter_name: str = "",
        request_name: str = "",
        plugin_name: str = "",
        model_name: str = "",
        chat_type: str = "",
    ) -> None:
        """记录 LLM 响应。

        Args:
            message: LLM 响应消息
            call_list: 工具调用列表
            stream_id: 聊天流 ID
            chatter_name: Chatter 名称
            request_name: 请求名称
        """
        plugin_inst = self.plugin
        if not hasattr(plugin_inst, 'log_prompt'):
            return

        plugin_inst.log_prompt(
            payloads=[],
            stream_id=stream_id,
            chatter_name=chatter_name,
            request_name=request_name,
            plugin_name=plugin_name,
            model_name=model_name,
            chat_type=chat_type,
            is_response=True,
            message=message,
            call_list=call_list,
        )
        logger.debug(
            f"已记录响应：stream={stream_id[:8] if stream_id else 'N/A'}, "
            f"chatter={chatter_name}, request={request_name}"
        )

    def log_llm_interaction(
        self,
        payloads: list[Any],
        message: str,
        call_list: list[Any] | None = None,
        stream_id: str = "",
        chatter_name: str = "",
        request_name: str = "",
        plugin_name: str = "",
        model_name: str = "",
        chat_type: str = "",
    ) -> None:
        """记录完整的 LLM 交互（请求 + 响应）。

        Args:
            payloads: LLM 请求的 payloads 列表
            message: LLM 响应消息
            call_list: 工具调用列表
            stream_id: 聊天流 ID
            chatter_name: Chatter 名称
            request_name: 请求名称
        """
        # 请求与响应都带上同一组来源标签，方便在日志里成对查看
        self.log_request(
            payloads,
            stream_id,
            chatter_name,
            request_name,
            plugin_name,
            model_name,
            chat_type,
        )
        self.log_response(
            message,
            call_list,
            stream_id,
            chatter_name,
            request_name,
            plugin_name,
            model_name,
            chat_type,
        )

    @staticmethod
    def get_instance() -> "PromptLoggerService | None":
        """获取 PromptLoggerService 服务实例。

        Returns:
            服务实例，未加载时返回 None
        """
        from src.core.managers import get_component_manager

        try:
            cm = get_component_manager()
            signature = "prompt_logger:service:prompt_logger"
            return cm.get_component(signature)  # type: ignore[return-value]
        except Exception:
            return None


# ============================================================================
# 便捷函数 - 供其他插件/Chatter 直接调用
# ============================================================================

from .interceptor import set_current_context, get_current_context


def set_llm_context(
    stream_id: str = "",
    chatter_name: str = "",
    plugin_name: str = "",
    request_name: str = "",
    model_name: str = "",
    chat_type: str = "",
) -> None:
    """设置当前 LLM 请求的上下文信息。

    在调用 LLMRequest.send() 之前调用此函数，可以让日志记录包含 stream_id。

    Args:
        stream_id: 聊天流 ID
        chatter_name: Chatter 名称
    """
    set_current_context(
        stream_id=stream_id,
        chatter_name=chatter_name,
        plugin_name=plugin_name,
        request_name=request_name,
        model_name=model_name,
        chat_type=chat_type,
    )


def log_prompt_request(
    payloads: list[Any],
    stream_id: str = "",
    chatter_name: str = "",
    request_name: str = "",
    plugin_name: str = "",
    model_name: str = "",
    chat_type: str = "",
) -> None:
    """便捷函数：记录 LLM 请求提示词。

    Args:
        payloads: LLM 请求的 payloads 列表
        stream_id: 聊天流 ID
        chatter_name: Chatter 名称
        request_name: 请求名称
    """
    service = PromptLoggerService.get_instance()
    if service:
        service.log_request(
            payloads,
            stream_id,
            chatter_name,
            request_name,
            plugin_name,
            model_name,
            chat_type,
        )


def log_prompt_response(
    message: str,
    call_list: list[Any] | None = None,
    stream_id: str = "",
    chatter_name: str = "",
    request_name: str = "",
    plugin_name: str = "",
    model_name: str = "",
    chat_type: str = "",
) -> None:
    """便捷函数：记录 LLM 响应。

    Args:
        message: LLM 响应消息
        call_list: 工具调用列表
        stream_id: 聊天流 ID
        chatter_name: Chatter 名称
        request_name: 请求名称
    """
    service = PromptLoggerService.get_instance()
    if service:
        service.log_response(
            message,
            call_list,
            stream_id,
            chatter_name,
            request_name,
            plugin_name,
            model_name,
            chat_type,
        )


def log_llm_interaction(
    payloads: list[Any],
    message: str,
    call_list: list[Any] | None = None,
    stream_id: str = "",
    chatter_name: str = "",
    request_name: str = "",
    plugin_name: str = "",
    model_name: str = "",
    chat_type: str = "",
) -> None:
    """便捷函数：记录完整的 LLM 交互（请求 + 响应）。

    Args:
        payloads: LLM 请求的 payloads 列表
        message: LLM 响应消息
        call_list: 工具调用列表
        stream_id: 聊天流 ID
        chatter_name: Chatter 名称
        request_name: 请求名称
    """
    service = PromptLoggerService.get_instance()
    if service:
        service.log_llm_interaction(
            payloads,
            message,
            call_list,
            stream_id,
            chatter_name,
            request_name,
            plugin_name,
            model_name,
            chat_type,
        )
