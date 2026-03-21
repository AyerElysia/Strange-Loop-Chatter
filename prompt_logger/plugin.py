"""Prompt Logger 插件入口。

记录所有发送给 LLM 的完整提示词到日志文件。
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from src.core.components.base import BasePlugin
from src.core.components.loader import register_plugin
from src.kernel.logger import get_logger

from .config import PromptLoggerConfig
from .handlers.prompt_logger_handler import PromptLoggerEventHandler
from .service import PromptLoggerService

logger = get_logger("prompt_logger", display="提示词记录")


@register_plugin
class PromptLoggerPlugin(BasePlugin):
    """Prompt Logger 插件。

    在 LLM 请求发送前记录完整提示词到日志文件。
    """

    plugin_name = "prompt_logger"
    plugin_version = "1.0.0"
    plugin_author = "MoFox Studio"
    plugin_description = "通用提示词记录插件 — 将所有发送给 LLM 的完整提示词记录到日志文件"
    configs = [PromptLoggerConfig]

    _instance: "PromptLoggerPlugin | None" = None
    _file_handler: RotatingFileHandler | None = None

    def __init__(self, config: PromptLoggerConfig | None = None) -> None:
        super().__init__(config)
        PromptLoggerPlugin._instance = self

    async def on_plugin_loaded(self) -> None:
        """插件加载时初始化日志处理器和拦截器。"""
        config = self.config

        if not isinstance(config, PromptLoggerConfig):
            logger.warning("配置加载失败，跳过初始化")
            return

        if not config.general.enabled:
            logger.info("提示词记录插件未启用 (enabled=false)")
            return

        # 初始化文件日志处理器
        self._setup_file_handler(config)

        # 安装 LLM 请求拦截器（自动记录所有 LLM 交互）
        from .interceptor import install_interceptor
        install_interceptor()

        logger.info("提示词记录插件已加载")

    async def on_plugin_unloaded(self) -> None:
        """插件卸载时清理拦截器。"""
        from .interceptor import uninstall_interceptor
        uninstall_interceptor()

        if self._file_handler:
            self._file_handler.close()
            self._file_handler = None

        logger.info("提示词记录插件已卸载")

    def _setup_file_handler(self, config: PromptLoggerConfig) -> None:
        """设置文件日志处理器。

        Args:
            config: 插件配置
        """
        log_file = Path(config.general.log_file)
        log_file.parent.mkdir(parents=True, exist_ok=True)

        # 创建 RotatingFileHandler
        max_bytes = config.general.max_log_size_mb * 1024 * 1024
        handler = RotatingFileHandler(
            log_file,
            maxBytes=max_bytes,
            backupCount=config.general.backup_count,
            encoding="utf-8",
        )

        # 设置格式
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)

        # 添加到 logger
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

        PromptLoggerPlugin._file_handler = handler
        logger.info(f"日志文件已初始化：{log_file.absolute()}")

    def get_file_handler(self) -> RotatingFileHandler | None:
        """获取文件日志处理器。

        Returns:
            文件日志处理器，未初始化时返回 None
        """
        return PromptLoggerPlugin._file_handler

    def log_prompt(
        self,
        payloads: list,
        stream_id: str = "",
        chatter_name: str = "",
        is_response: bool = False,
        message: str = "",
        call_list: list | None = None,
    ) -> None:
        """记录提示词到日志文件。

        Args:
            payloads: LLM 请求的 payloads 列表
            stream_id: 聊天流 ID
            chatter_name: Chatter 名称
            is_response: 是否是响应日志
            message: 响应消息（仅响应日志需要）
            call_list: 工具调用列表（仅响应日志需要）
        """
        config = self.config

        if not isinstance(config, PromptLoggerConfig):
            return

        if not config.general.enabled:
            return

        handler = self.get_file_handler()
        if handler is None:
            return

        from .log_formatter import format_request_for_log, format_response_for_log

        if is_response:
            if not config.format.show_response:
                return
            log_text = format_response_for_log(
                message=message,
                call_list=call_list,
                stream_id=stream_id,
                chatter_name=chatter_name,
                truncate_length=config.format.truncate_content_length,
            )
            panel_title = f"LLM 响应 (stream={stream_id[:8] if stream_id else 'N/A'})"
        else:
            if not config.format.show_request:
                return
            log_text = format_request_for_log(
                payloads=payloads,
                stream_id=stream_id,
                chatter_name=chatter_name,
                show_tools=config.format.show_tools,
                truncate_length=config.format.truncate_content_length,
            )
            panel_title = f"LLM 提示词 (stream={stream_id[:8] if stream_id else 'N/A'})"

        logger.print_panel(
            log_text,
            title=panel_title,
            border_style="cyan" if not is_response else "green",
        )

        handler.emit(logging.LogRecord(
            name="prompt_logger",
            level=logging.INFO,
            pathname=__file__,
            lineno=0,
            msg=log_text,
            args=(),
            exc_info=None,
        ))

    def get_components(self) -> list[type]:
        """获取插件内所有组件类。"""
        return [PromptLoggerEventHandler, PromptLoggerService]

    @classmethod
    def get_instance(cls) -> "PromptLoggerPlugin | None":
        """获取插件实例。

        Returns:
            插件实例，未加载时返回 None
        """
        return cls._instance


def log_prompt(
    payloads: list,
    stream_id: str = "",
    chatter_name: str = "",
) -> None:
    """便捷函数：记录 LLM 请求提示词。

    Args:
        payloads: LLM 请求的 payloads 列表
        stream_id: 聊天流 ID
        chatter_name: Chatter 名称
    """
    plugin = PromptLoggerPlugin.get_instance()
    if plugin:
        plugin.log_prompt(payloads, stream_id, chatter_name)


def log_response(
    message: str,
    call_list: list | None = None,
    stream_id: str = "",
    chatter_name: str = "",
) -> None:
    """便捷函数：记录 LLM 响应。

    Args:
        message: LLM 响应消息
        call_list: 工具调用列表
        stream_id: 聊天流 ID
        chatter_name: Chatter 名称
    """
    plugin = PromptLoggerPlugin.get_instance()
    if plugin:
        plugin.log_prompt(
            payloads=[],
            stream_id=stream_id,
            chatter_name=chatter_name,
            is_response=True,
            message=message,
            call_list=call_list,
        )
