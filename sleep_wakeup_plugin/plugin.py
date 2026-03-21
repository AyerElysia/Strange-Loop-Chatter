"""sleep_wakeup_plugin 插件主类。"""

from __future__ import annotations

from typing import Any

from src.app.plugin_system.base import BasePlugin, register_plugin
from src.kernel.logger import get_logger

from .components.configs.config import Config
from .components.events.sleep_message_guard_event import SleepMessageGuardEvent
from .components.events.startup_event import SleepWakeupStartupEvent
from .managers import get_sleep_wakeup_manager, initialize_sleep_wakeup_manager


logger = get_logger("sleep_wakeup_plugin")


@register_plugin
class SleepWakeupPlugin(BasePlugin):
    """LLM 睡眠/苏醒状态机插件。"""

    plugin_name = "sleep_wakeup_plugin"
    plugin_version = "1.0.0"
    plugin_author = "minecraft1024a"
    plugin_description = "离散睡眠/苏醒状态机，支持守护决策与消息阻挡"
    configs = [Config]
    dependent_components: list[str] = []

    def __init__(self, config: Config | None = None) -> None:
        super().__init__(config)
        self.config: Config = config or Config()

    def get_components(self) -> list[type]:
        if not self.config.general.enabled:
            logger.info("sleep_wakeup_plugin 已在配置中禁用")
            return []
        return [SleepWakeupStartupEvent, SleepMessageGuardEvent]

    async def on_plugin_loaded(self) -> None:
        logger.info("sleep_wakeup_plugin 加载开始")

        if not self.config.general.enabled:
            logger.warning("插件已禁用，跳过状态机初始化")
            return

        initialize_sleep_wakeup_manager(
            plugin_name=self.plugin_name,
            config=self.config,
        )
        logger.info("sleep_wakeup_plugin 装配完成，等待 ON_START 初始化")

    async def on_plugin_unloaded(self) -> None:
        logger.info("sleep_wakeup_plugin 卸载中")
        try:
            manager = get_sleep_wakeup_manager()
            await manager.shutdown()
        except RuntimeError:
            logger.warning("管理器未初始化，跳过关闭流程")
        logger.info("sleep_wakeup_plugin 卸载完成")

    def should_block_messages(self) -> bool:
        try:
            manager = get_sleep_wakeup_manager()
            return manager.should_block_messages()
        except RuntimeError:
            return False

    def get_runtime_snapshot(self) -> dict[str, Any]:
        try:
            manager = get_sleep_wakeup_manager()
            return manager.get_runtime_snapshot()
        except RuntimeError:
            return {}

