"""Booku Memory Agent 插件入口。"""

from __future__ import annotations

from src.core.components import BasePlugin, register_plugin
from src.kernel.logger import get_logger

from .agent import BookuMemoryReadAgent, BookuMemoryWriteAgent
from .agent.tools import (
    BookuMemoryCreateTool,
    BookuMemoryEditInherentTool,
    BookuMemoryRetrieveTool,
)
from .config import BookuMemoryConfig
from .event_handler import MemoryPromptInjector
from .service import BookuMemoryService

logger = get_logger("booku_memory_plugin")


@register_plugin
class BookuMemoryAgentPlugin(BasePlugin):
    """Booku 记忆插件。"""

    plugin_name: str = "booku_memory"
    plugin_description: str = "Agent 驱动的 Booku 记忆系统"
    plugin_version: str = "1.0.0"

    configs: list[type] = [BookuMemoryConfig]
    dependent_components: list[str] = []

    def get_components(self) -> list[type]:
        """返回插件组件列表。"""
        if isinstance(self.config, BookuMemoryConfig):
            if not self.config.plugin.enabled:
                logger.info("booku_memory_agent 已在配置中禁用")
                return []

            if self.config.plugin.enable_agent_proxy_mode:
                return [
                    BookuMemoryWriteAgent,
                    BookuMemoryReadAgent,
                    BookuMemoryService,
                    MemoryPromptInjector,
                ]

            return [
                BookuMemoryRetrieveTool,
                BookuMemoryCreateTool,
                BookuMemoryEditInherentTool,
                BookuMemoryService,
                MemoryPromptInjector,
            ]

        # 配置对象不可用时保持历史行为：默认启用 agent 代理模式。
        return [BookuMemoryWriteAgent, BookuMemoryReadAgent, BookuMemoryService, MemoryPromptInjector]
