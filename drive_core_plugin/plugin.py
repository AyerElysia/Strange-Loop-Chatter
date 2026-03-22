"""drive_core_plugin 入口。"""

from __future__ import annotations

from src.app.plugin_system.base import BasePlugin, register_plugin
from src.kernel.logger import get_logger

from .commands.drive_core_command import DriveCoreCommand
from .components.events import DriveCorePromptInjector, DriveCoreScanEvent
from .config import DriveCoreConfig
from . import service as drive_core_service_module
from .service import (
    DriveCoreService,
    get_drive_core_service,
    initialize_drive_core_service,
)


logger = get_logger("drive_core_plugin")


@register_plugin
class DriveCorePlugin(BasePlugin):
    """内驱力 / 自我引擎插件。"""

    plugin_name: str = "drive_core_plugin"
    plugin_description: str = "让角色拥有可持续推进的内驱力和自我发问工作区"
    plugin_version: str = "1.0.0"

    configs: list[type] = [DriveCoreConfig]
    dependent_components: list[str] = []

    def __init__(self, config: DriveCoreConfig | None = None) -> None:
        super().__init__(config)
        self.config: DriveCoreConfig = config or DriveCoreConfig()
        self._service = None

    @property
    def service(self):
        if self._service is None:
            self._service = get_drive_core_service()
        return self._service

    def get_components(self) -> list[type]:
        if not self.config.plugin.enabled:
            logger.info("drive_core_plugin 已在配置中禁用")
            return []
        return [
            DriveCoreService,
            DriveCoreCommand,
            DriveCoreScanEvent,
            DriveCorePromptInjector,
        ]

    async def on_plugin_loaded(self) -> None:
        initialize_drive_core_service(self)
        logger.info("drive_core_plugin 已加载")

    async def on_plugin_unloaded(self) -> None:
        logger.info("drive_core_plugin 已卸载")
        drive_core_service_module._SERVICE_INSTANCE = None
        self._service = None
