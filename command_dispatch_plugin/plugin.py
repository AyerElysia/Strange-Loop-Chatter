"""command_dispatch_plugin 入口。"""

from __future__ import annotations

from src.app.plugin_system.base import BasePlugin, register_plugin

from .components.events.command_dispatch_event import CommandDispatchEventHandler


@register_plugin
class CommandDispatchPlugin(BasePlugin):
    """聊天命令分流插件。"""

    plugin_name = "command_dispatch_plugin"
    plugin_version = "1.0.0"
    plugin_author = "Neo-MoFox Team"
    plugin_description = "在消息入站时拦截并执行聊天命令"

    dependent_components: list[str] = []

    def get_components(self) -> list[type]:
        return [CommandDispatchEventHandler]
