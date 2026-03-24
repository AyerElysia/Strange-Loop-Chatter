"""personality_engine_plugin 入口。"""

from __future__ import annotations

from src.app.plugin_system.base import BasePlugin, register_plugin
from src.kernel.logger import get_logger

from .commands.personality_command import PersonalityCommand
from .components.events import PersonalityPromptInjector, PersonalityScanEvent
from .config import PersonalityEngineConfig
from . import service as personality_service_module
from .service import (
    PersonalityEngineService,
    get_personality_engine_service,
    initialize_personality_engine_service,
)


logger = get_logger("personality_engine_plugin")


@register_plugin
class PersonalityEnginePlugin(BasePlugin):
    """JPAF 人格引擎插件。"""

    plugin_name = "personality_engine_plugin"
    plugin_description = "按聊天流演化的人格引擎"
    plugin_version = "1.2.0"

    configs: list[type] = [PersonalityEngineConfig]
    dependent_components: list[str] = []

    def __init__(self, config: PersonalityEngineConfig | None = None) -> None:
        super().__init__(config)
        self.config: PersonalityEngineConfig = config or PersonalityEngineConfig()
        self._service = None

    @property
    def service(self):
        if self._service is None:
            self._service = get_personality_engine_service()
        return self._service

    def get_components(self) -> list[type]:
        if not self.config.plugin.enabled:
            logger.info("personality_engine_plugin 已在配置中禁用")
            return []
        return [
            PersonalityEngineService,
            PersonalityCommand,
            PersonalityScanEvent,
            PersonalityPromptInjector,
        ]

    async def on_plugin_loaded(self) -> None:
        initialize_personality_engine_service(self)
        logger.info("personality_engine_plugin 已加载")

    async def on_plugin_unloaded(self) -> None:
        personality_service_module._SERVICE_INSTANCE = None
        self._service = None
        logger.info("personality_engine_plugin 已卸载")
