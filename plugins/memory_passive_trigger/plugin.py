"""被动记忆浮现插件入口。"""

from __future__ import annotations

from src.core.components import BasePlugin, register_plugin

from .config import MemoryPassiveTriggerConfig
from .handler import MemoryPassiveTriggerHandler


@register_plugin
class MemoryPassiveTriggerPlugin(BasePlugin):
    """被动记忆浮现插件。

    基于用户消息的语义，自动检索并浮现相关记忆，
    模拟人类"触景生情"式的被动记忆唤起机制。
    """

    plugin_name: str = "memory_passive_trigger"
    plugin_description: str = "被动记忆浮现 - 看到关键词自动唤起相关记忆"
    plugin_version: str = "1.0.0"

    configs: list[type] = [MemoryPassiveTriggerConfig]
    dependent_components: list[str] = ["booku_memory:service:booku_memory"]

    def get_components(self) -> list[type]:
        """返回插件组件列表。"""
        if isinstance(self.config, MemoryPassiveTriggerConfig):
            if not self.config.plugin.enabled:
                return []
            if not self.config.trigger.enabled:
                return []

        return [MemoryPassiveTriggerHandler]
