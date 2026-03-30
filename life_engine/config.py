"""life_engine 插件配置。"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from src.core.components.base.config import BaseConfig, Field, SectionBase, config_section


# 默认工作空间路径
_DEFAULT_WORKSPACE = str(Path(__file__).parent.parent.parent / "data" / "life_engine_workspace")


class LifeEngineConfig(BaseConfig):
    """life_engine 插件配置。"""

    config_name: ClassVar[str] = "config"
    config_description: ClassVar[str] = "生命中枢最小原型配置"

    @config_section("settings")
    class SettingsSection(SectionBase):
        """基础设置。"""

        enabled: bool = Field(
            default=True,
            description="是否启用 life_engine。设为 false 时不启动心跳。",
        )

        heartbeat_interval_seconds: int = Field(
            default=30,
            description="心跳间隔（秒）。",
        )

        log_heartbeat: bool = Field(
            default=True,
            description="是否在每次心跳时输出日志。",
        )

        context_history_max_events: int = Field(
            default=100,
            ge=1,
            description="滚动事件流最多保留的事件条数（包括心跳、消息、工具调用等）。",
        )

        workspace_path: str = Field(
            default=_DEFAULT_WORKSPACE,
            description="中枢文件系统操作的工作空间路径。中枢只能在此目录下进行文件操作。",
        )

        max_rounds_per_heartbeat: int = Field(
            default=3,
            ge=1,
            le=10,
            description="单次心跳内允许模型连续进行工具调用的最大轮数（防止死循环）。",
        )

    @config_section("model")
    class ModelSection(SectionBase):
        """中枢模型任务设置。"""

        task_name: str = Field(
            default="life",
            description="中枢任务使用的模型任务名称，对应 config/model.toml 中的 [model_tasks.life]。",
        )

    settings: SettingsSection = Field(default_factory=SettingsSection)
    model: ModelSection = Field(default_factory=ModelSection)
