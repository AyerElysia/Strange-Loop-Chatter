"""Prompt Logger 插件入口。

记录所有发送给 LLM 的完整提示词到日志文件。
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from src.core.components.base import BasePlugin
from src.core.components.loader import register_plugin
from src.kernel.logger import get_logger

from .config import PromptLoggerConfig
from .handlers.prompt_logger_handler import PromptLoggerEventHandler
from .service import PromptLoggerService

logger = get_logger("prompt_logger", display="提示词记录")


def _normalize_names(values: list[str]) -> set[str]:
    """清洗配置中的名称列表。"""
    return {value.strip() for value in values if isinstance(value, str) and value.strip()}


@register_plugin
class PromptLoggerPlugin(BasePlugin):
    """Prompt Logger 插件。

    通过 monkey-patch 自动拦截 LLM 请求与响应，并把可读提示词写入专门日志。
    """

    plugin_name = "prompt_logger"
    plugin_version = "1.1.0"
    plugin_author = "MoFox Studio"
    plugin_description = "通用提示词记录插件 — 将所有发送给 LLM 的完整提示词记录到日志文件"
    configs = [PromptLoggerConfig]

    _instance: "PromptLoggerPlugin | None" = None
    _file_handler: RotatingFileHandler | None = None

    def __init__(self, config: PromptLoggerConfig | None = None) -> None:
        super().__init__(config)
        PromptLoggerPlugin._instance = self

    def _get_config(self) -> PromptLoggerConfig | None:
        """获取已加载的配置对象。"""
        config = self.config
        return config if isinstance(config, PromptLoggerConfig) else None

    async def on_plugin_loaded(self) -> None:
        """插件加载时初始化日志处理器和拦截器。"""
        config = self._get_config()

        if config is None:
            logger.warning("配置加载失败，跳过初始化")
            return

        if not config.general.enabled:
            logger.info("提示词记录插件未启用 (enabled=false)")
            return

        self._setup_file_handler(config)

        from .interceptor import install_interceptor

        install_interceptor()

        logger.info("提示词记录插件已加载")

    async def on_plugin_unloaded(self) -> None:
        """插件卸载时清理拦截器与日志句柄。"""
        from .interceptor import uninstall_interceptor

        uninstall_interceptor()
        self._teardown_file_handler()

        logger.info("提示词记录插件已卸载")

    def _setup_file_handler(self, config: PromptLoggerConfig) -> None:
        """设置文件日志处理器。"""
        log_file = Path(config.general.log_file)
        log_file.parent.mkdir(parents=True, exist_ok=True)

        self._teardown_file_handler()

        max_bytes = config.general.max_log_size_mb * 1024 * 1024
        handler = RotatingFileHandler(
            log_file,
            maxBytes=max_bytes,
            backupCount=config.general.backup_count,
            encoding="utf-8",
        )

        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)

        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

        PromptLoggerPlugin._file_handler = handler
        logger.info(f"日志文件已初始化：{log_file.absolute()}")

    def _teardown_file_handler(self) -> None:
        """释放文件日志处理器。"""
        if self._file_handler is None:
            return

        try:
            logger.removeHandler(self._file_handler)
        except Exception:
            pass

        try:
            self._file_handler.close()
        finally:
            PromptLoggerPlugin._file_handler = None

    def get_file_handler(self) -> RotatingFileHandler | None:
        """获取文件日志处理器。"""
        return PromptLoggerPlugin._file_handler

    def _should_log(
        self,
        config: PromptLoggerConfig,
        *,
        stream_id: str = "",
        chatter_name: str = "",
        request_name: str = "",
        plugin_name: str = "",
        model_name: str = "",
        chat_type: str = "",
    ) -> bool:
        """判断当前记录是否应该写入日志。"""
        filter_config = config.filter

        if chat_type == "private" and not filter_config.log_private_chat:
            return False

        if chat_type in ("group", "discuss") and not filter_config.log_group_chat:
            return False

        include_plugins = _normalize_names(filter_config.include_plugins)
        include_chatters = _normalize_names(filter_config.include_chatters)
        include_request_names = _normalize_names(filter_config.include_request_names)
        include_models = _normalize_names(filter_config.include_models)

        exclude_plugins = _normalize_names(filter_config.exclude_plugins)
        exclude_chatters = _normalize_names(filter_config.exclude_chatters)
        exclude_request_names = _normalize_names(filter_config.exclude_request_names)
        exclude_models = _normalize_names(filter_config.exclude_models)

        if filter_config.scope == "dfc_main":
            # DFC 主回复模型默认使用 request_name="actor"
            if request_name != "actor":
                return False
        elif filter_config.scope == "custom":
            include_checks: list[bool] = []
            if include_plugins:
                include_checks.append(plugin_name in include_plugins)
            if include_chatters:
                include_checks.append(chatter_name in include_chatters)
            if include_request_names:
                include_checks.append(request_name in include_request_names)
            if include_models:
                include_checks.append(model_name in include_models)

            if include_checks and not any(include_checks):
                return False

            if not filter_config.allow_unknown_source:
                if not any((plugin_name, chatter_name, request_name, model_name)):
                    return False
        elif filter_config.scope == "all":
            pass
        else:
            logger.debug(f"未知 scope: {filter_config.scope}，将按 all 处理")

        if plugin_name and plugin_name in exclude_plugins:
            return False
        if chatter_name and chatter_name in exclude_chatters:
            return False
        if request_name and request_name in exclude_request_names:
            return False
        if model_name and model_name in exclude_models:
            return False

        return True

    def log_prompt(
        self,
        payloads: list[Any],
        stream_id: str = "",
        chatter_name: str = "",
        request_name: str = "",
        plugin_name: str = "",
        model_name: str = "",
        chat_type: str = "",
        is_response: bool = False,
        message: str = "",
        call_list: list[Any] | None = None,
    ) -> None:
        """记录提示词到日志文件。"""
        config = self._get_config()
        if config is None or not config.general.enabled:
            return

        handler = self.get_file_handler()
        if handler is None:
            return

        if not self._should_log(
            config,
            stream_id=stream_id,
            chatter_name=chatter_name,
            request_name=request_name,
            plugin_name=plugin_name,
            model_name=model_name,
            chat_type=chat_type,
        ):
            return

        from .log_formatter import format_request_for_log, format_response_for_log

        common_kwargs = {
            "stream_id": stream_id,
            "chatter_name": chatter_name,
            "request_name": request_name,
            "plugin_name": plugin_name,
            "model_name": model_name,
            "chat_type": chat_type,
            "include_timestamp": config.format.show_timestamp,
            "extra_fields": {"scope": config.filter.scope},
        }

        if is_response:
            if not config.format.show_response:
                return

            log_text = format_response_for_log(
                message=message,
                call_list=call_list,
                truncate_length=config.format.truncate_content_length,
                **common_kwargs,
            )
            source_label = plugin_name or chatter_name or request_name or "unknown"
            panel_title = f"LLM 响应 (source={source_label}, stream={stream_id[:8] if stream_id else 'N/A'})"
        else:
            if not config.format.show_request:
                return

            log_text = format_request_for_log(
                payloads=payloads,
                show_tools=config.format.show_tools,
                truncate_length=config.format.truncate_content_length,
                **common_kwargs,
            )
            source_label = plugin_name or chatter_name or request_name or "unknown"
            panel_title = f"LLM 提示词 (source={source_label}, stream={stream_id[:8] if stream_id else 'N/A'})"

        logger.print_panel(
            log_text,
            title=panel_title,
            border_style="cyan" if not is_response else "green",
        )

        handler.emit(
            logging.LogRecord(
                name="prompt_logger",
                level=logging.INFO,
                pathname=__file__,
                lineno=0,
                msg=log_text,
                args=(),
                exc_info=None,
            )
        )

    def get_components(self) -> list[type]:
        """获取插件内所有组件类。"""
        return [PromptLoggerEventHandler, PromptLoggerService]

    @classmethod
    def get_instance(cls) -> "PromptLoggerPlugin | None":
        """获取插件实例。"""
        return cls._instance


def log_prompt(
    payloads: list[Any],
    stream_id: str = "",
    chatter_name: str = "",
    request_name: str = "",
    plugin_name: str = "",
    model_name: str = "",
    chat_type: str = "",
) -> None:
    """便捷函数：记录 LLM 请求提示词。"""
    plugin = PromptLoggerPlugin.get_instance()
    if plugin:
        plugin.log_prompt(
            payloads=payloads,
            stream_id=stream_id,
            chatter_name=chatter_name,
            request_name=request_name,
            plugin_name=plugin_name,
            model_name=model_name,
            chat_type=chat_type,
            is_response=False,
        )


def log_response(
    message: str,
    call_list: list[Any] | None = None,
    stream_id: str = "",
    chatter_name: str = "",
    request_name: str = "",
    plugin_name: str = "",
    model_name: str = "",
    chat_type: str = "",
) -> None:
    """便捷函数：记录 LLM 响应。"""
    plugin = PromptLoggerPlugin.get_instance()
    if plugin:
        plugin.log_prompt(
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
