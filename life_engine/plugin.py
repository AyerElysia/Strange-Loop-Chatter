"""life_engine 插件入口。"""

from __future__ import annotations

from src.core.components import BasePlugin, register_plugin
from src.kernel.logger import get_logger

from .audit import (
    get_life_log_file,
    log_lifecycle,
    setup_life_audit_logger,
    teardown_life_audit_logger,
)
from .config import LifeEngineConfig
from .event_handler import LifeEngineMessageCollectorHandler
from .service import LifeEngineService


logger = get_logger("life_engine", display="life_engine")


@register_plugin
class LifeEnginePlugin(BasePlugin):
    """生命中枢最小原型插件。

    仅提供一个并行存在的后台心跳服务与旁路消息收集器，不接管正常聊天流程。
    """

    plugin_name: str = "life_engine"
    plugin_description: str = "生命中枢最小原型，维护并行心跳并收集聊天流上下文"
    plugin_version: str = "1.5.0"

    configs: list[type] = [LifeEngineConfig]
    dependent_components: list[str] = []

    def __init__(self, config: LifeEngineConfig | None = None) -> None:
        super().__init__(config)
        self._service: LifeEngineService | None = None

    @property
    def service(self) -> LifeEngineService:
        """获取插件内部服务实例。"""
        if self._service is None:
            self._service = LifeEngineService(self)
        return self._service

    def get_components(self) -> list[type]:
        """返回插件提供的组件。"""
        return [LifeEngineService, LifeEngineMessageCollectorHandler]

    async def on_plugin_loaded(self) -> None:
        """插件加载后启动心跳。"""
        setup_life_audit_logger()
        if isinstance(self.config, LifeEngineConfig) and not self.config.settings.enabled:
            logger.info("life_engine 已禁用，未启动")
            log_lifecycle(
                "disabled",
                enabled=False,
                model_task_name=self.config.model.task_name,
                log_file_path=str(get_life_log_file()),
            )
            await self.service.clear_runtime_context()
            return

        await self.service.start()

    async def on_plugin_unloaded(self) -> None:
        """插件卸载前停止心跳。"""
        if self._service is not None:
            await self._service.stop()
        log_lifecycle(
            "unloaded",
            log_file_path=str(get_life_log_file()),
        )
        teardown_life_audit_logger()
