"""self_narrative_plugin 入口。"""

from __future__ import annotations

from src.app.plugin_system.base import BasePlugin, register_plugin
from src.app.plugin_system.api.log_api import get_logger

from .commands.self_narrative_command import SelfNarrativeCommand
from .config import SelfNarrativeConfig
from .components.events import SelfNarrativePromptInjector, SelfNarrativeStartupEvent
from .service import initialize_self_narrative_service, get_self_narrative_service


logger = get_logger("self_narrative_plugin")


@register_plugin
class SelfNarrativePlugin(BasePlugin):
    """自我叙事插件。"""

    plugin_name = "self_narrative_plugin"
    plugin_version = "1.0.0"
    plugin_author = "Neo-MoFox Team"
    plugin_description = "按聊天流隔离的自我叙事缓存与更新系统"

    configs = [SelfNarrativeConfig]
    dependent_components: list[str] = []

    def __init__(self, config: SelfNarrativeConfig | None = None) -> None:
        super().__init__(config)
        self.config: SelfNarrativeConfig = config or SelfNarrativeConfig()

    def get_components(self) -> list[type]:
        if not self.config.plugin.enabled:
            logger.info("self_narrative_plugin 已在配置中禁用")
            return []
        return [
            SelfNarrativeService,
            SelfNarrativeCommand,
            SelfNarrativeStartupEvent,
            SelfNarrativePromptInjector,
        ]

    async def on_plugin_loaded(self) -> None:
        if not self.config.plugin.enabled:
            return
        initialize_self_narrative_service(self)
        logger.info("self_narrative_plugin 已加载")

    async def on_plugin_unloaded(self) -> None:
        service = get_self_narrative_service()
        if service is not None:
            await service.shutdown()
        logger.info("self_narrative_plugin 已卸载")


from .service import SelfNarrativeService  # noqa: E402
