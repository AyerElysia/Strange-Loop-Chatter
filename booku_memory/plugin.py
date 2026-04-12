"""Booku Memory 插件入口。"""

from __future__ import annotations

from src.core.components import BasePlugin, register_plugin
from src.kernel.logger import get_logger

from .agent.tools import BookuMemoryEditInherentTool
from .config import BookuMemoryConfig
from .service import BookuMemoryService, sync_booku_memory_actor_reminder

logger = get_logger("booku_memory_plugin")


@register_plugin
class BookuMemoryAgentPlugin(BasePlugin):
    """Booku 长期记忆插件。"""

    plugin_name: str = "booku_memory"
    plugin_description: str = "长期记忆层，只保留稳定注入与单一写入口"
    plugin_version: str = "1.0.0"

    configs: list[type] = [BookuMemoryConfig]
    dependent_components: list[str] = []

    @staticmethod
    def _runtime_components() -> list[type]:
        """返回当前版本实际暴露的最小长期记忆组件。"""
        return [
            BookuMemoryEditInherentTool,
            BookuMemoryService,
        ]

    async def on_plugin_loaded(self) -> None:
        """插件加载后同步 actor reminder。"""

        await sync_booku_memory_actor_reminder(self)

    async def on_plugin_unloaded(self) -> None:
        """插件卸载时清理 actor reminder。"""

        from src.core.prompt import get_system_reminder_store

        store = get_system_reminder_store()
        store.delete("actor", "booku_memory")
        store.delete("actor", "记忆引导语")
        store.delete("actor", "专业知识引导语")

    def get_components(self) -> list[type]:
        """返回插件组件列表。"""
        if isinstance(self.config, BookuMemoryConfig):
            if not self.config.plugin.enabled:
                logger.info("booku_memory 已在配置中禁用")
                return []
            return self._runtime_components()

        return self._runtime_components()
