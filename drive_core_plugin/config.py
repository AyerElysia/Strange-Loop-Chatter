"""drive_core_plugin 配置。"""

from __future__ import annotations

from src.app.plugin_system.base import BaseConfig, Field, SectionBase, config_section


@config_section("plugin")
class PluginSection(SectionBase):
    """插件基础配置。"""

    enabled: bool = Field(default=True, description="是否启用 drive_core_plugin")
    inject_prompt: bool = Field(
        default=True,
        description="是否在 prompt 构建时注入内驱力状态",
    )
    inherit_default_chatter_persona_prompt: bool = Field(
        default=True,
        description="是否复用 default_chatter 的完整系统人设提示词",
    )


@config_section("storage")
class StorageSection(SectionBase):
    """持久化配置。"""

    base_path: str = Field(
        default="data/drive_core",
        description="内驱力状态存储根目录",
    )
    max_history_records: int = Field(
        default=20,
        ge=1,
        le=200,
        description="保留的历史任务记录上限",
    )
    max_prompt_evidence_lines: int = Field(
        default=3,
        ge=0,
        le=10,
        description="prompt 中最多展示的证据摘要条数",
    )


@config_section("scan")
class ScanSection(SectionBase):
    """推进配置。"""

    trigger_every_n_messages: int = Field(
        default=6,
        ge=1,
        le=1000,
        description="每隔多少次有效对话触发一次内驱力推进",
    )
    history_window_size: int = Field(
        default=12,
        ge=1,
        le=100,
        description="推进时给模型看的最近历史条数",
    )
    max_inquiry_steps: int = Field(
        default=4,
        ge=1,
        le=12,
        description="单个自我课题允许推进的最大轮次",
    )


@config_section("prompt")
class PromptSection(SectionBase):
    """prompt 注入配置。"""

    target_prompt_names: list[str] = Field(
        default_factory=lambda: ["default_chatter_system_prompt"],
        description="允许注入内驱力块的 prompt 模板名",
    )
    prompt_title: str = Field(
        default="内驱力",
        description="prompt 中显示的标题",
    )
    inject_evidence: bool = Field(
        default=False,
        description="是否在 prompt 中显示证据摘要",
    )


@config_section("model")
class ModelSection(SectionBase):
    """模型配置。"""

    task_name: str = Field(
        default="diary",
        description="内驱力推进使用的模型任务名",
    )
    fallback_task_name: str = Field(
        default="actor",
        description="当主任务不可用时的回退任务名",
    )


@config_section("drive")
class DriveSection(SectionBase):
    """内驱力底噪配置。"""

    curiosity: int = Field(default=62, ge=0, le=100, description="好奇")
    initiative: int = Field(default=50, ge=0, le=100, description="行动欲")
    affinity: int = Field(default=45, ge=0, le=100, description="靠近倾向")
    withdrawal: int = Field(default=28, ge=0, le=100, description="收缩倾向")
    fatigue: int = Field(default=22, ge=0, le=100, description="疲惫")
    urgency: int = Field(default=35, ge=0, le=100, description="紧迫感")
    stability: int = Field(default=58, ge=0, le=100, description="稳定感")


class DriveCoreConfig(BaseConfig):
    """drive_core_plugin 配置。"""

    config_name = "config"
    config_description = "drive_core_plugin 配置"

    plugin: PluginSection = Field(default_factory=PluginSection)
    storage: StorageSection = Field(default_factory=StorageSection)
    scan: ScanSection = Field(default_factory=ScanSection)
    prompt: PromptSection = Field(default_factory=PromptSection)
    model: ModelSection = Field(default_factory=ModelSection)
    drive: DriveSection = Field(default_factory=DriveSection)
