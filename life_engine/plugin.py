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
from .command_handler import LifeEngineCommandHandler
from .config import LifeEngineConfig
from .event_handler import LifeEngineMessageCollectorHandler
from .service import LifeEngineService
from .tools import ALL_TOOLS
from .todo_tools import TODO_TOOLS
from .memory_tools import MEMORY_TOOLS
from .grep_tools import GREP_TOOLS


logger = get_logger("life_engine", display="life_engine")


@register_plugin
class LifeEnginePlugin(BasePlugin):
    """生命中枢插件。

    提供一个独立于 DFC 的并行存在系统，使用统一的事件流模型处理
    消息、心跳、工具调用等交互，保持时间连续性。

    特性：
    - 统一事件流：所有交互都是事件，按时间顺序展示
    - 文件系统操作：提供限定在 workspace 内的文件操作工具
    - Grep 搜索：在私人文件系统中搜索内容
    - TODO 系统：为数字生命设计的生活愿望系统
    - 仿生记忆系统：语义检索、联想、遗忘机制
    - 子代理系统：启动独立代理处理复杂任务
    - 可配置可见事件数：通过 context_history_max_events 控制
    """

    plugin_name: str = "life_engine"
    plugin_description: str = "生命中枢，维护并行心跳与统一事件流上下文"
    plugin_version: str = "3.2.0"

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
        return [
            LifeEngineService,
            LifeEngineMessageCollectorHandler,
            LifeEngineCommandHandler,
            *ALL_TOOLS,
            *TODO_TOOLS,
            *MEMORY_TOOLS,
            *GREP_TOOLS,
        ]

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
