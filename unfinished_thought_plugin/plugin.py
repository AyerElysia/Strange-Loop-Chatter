"""unfinished_thought_plugin 入口。"""

from __future__ import annotations

from src.app.plugin_system.base import BasePlugin, register_plugin
from src.kernel.logger import get_logger

from .commands.unfinished_thought_command import UnfinishedThoughtCommand
from .components.events import UnfinishedThoughtPromptInjector, UnfinishedThoughtScanEvent
from .config import UnfinishedThoughtConfig
from .service import UnfinishedThoughtService


logger = get_logger("unfinished_thought_plugin")


@register_plugin
class UnfinishedThoughtPlugin(BasePlugin):
    """未完成念头插件。"""

    plugin_name = "unfinished_thought_plugin"
    plugin_version = "1.0.0"
    plugin_author = "Neo-MoFox Team"
    plugin_description = "按固定对话数扫描和维护未完成念头池"

    configs = [UnfinishedThoughtConfig]
    dependent_components: list[str] = []

    def __init__(self, config: UnfinishedThoughtConfig | None = None) -> None:
        super().__init__(config)
        self.config: UnfinishedThoughtConfig = config or UnfinishedThoughtConfig()

    def get_components(self) -> list[type]:
        if not self.config.plugin.enabled:
            logger.info("unfinished_thought_plugin 已在配置中禁用")
            return []
        return [
            UnfinishedThoughtService,
            UnfinishedThoughtCommand,
            UnfinishedThoughtScanEvent,
            UnfinishedThoughtPromptInjector,
        ]

    async def on_plugin_loaded(self) -> None:
        if not self.config.plugin.enabled:
            return
        logger.info("unfinished_thought_plugin 已加载")

    async def on_plugin_unloaded(self) -> None:
        logger.info("unfinished_thought_plugin 已卸载")

