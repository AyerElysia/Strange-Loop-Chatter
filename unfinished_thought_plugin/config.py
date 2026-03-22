"""unfinished_thought_plugin 配置。"""

from __future__ import annotations

from src.app.plugin_system.base import BaseConfig, Field, SectionBase, config_section


@config_section("plugin")
class PluginSection(SectionBase):
    """插件基础配置。"""

    enabled: bool = Field(default=True, description="是否启用 unfinished_thought_plugin")
    inject_prompt: bool = Field(
        default=True,
        description="是否在 prompt 构建时注入未完成念头",
    )


@config_section("storage")
class StorageSection(SectionBase):
    """持久化配置。"""

    base_path: str = Field(
        default="data/unfinished_thoughts",
        description="未完成念头存储根目录",
    )
    max_thoughts: int = Field(
        default=20,
        ge=1,
        le=100,
        description="单个聊天流允许维护的未完成念头上限",
    )
    max_history_records: int = Field(
        default=12,
        ge=1,
        le=100,
        description="保留的扫描历史记录上限",
    )


@config_section("scan")
class ScanSection(SectionBase):
    """扫描配置。"""

    trigger_every_n_messages: int = Field(
        default=8,
        ge=1,
        le=1000,
        description="每隔多少条有效对话自动扫描一次",
    )
    history_window_size: int = Field(
        default=12,
        ge=1,
        le=100,
        description="扫描时给念头模型看的历史记录条数",
    )


@config_section("prompt")
class PromptSection(SectionBase):
    """prompt 注入配置。"""

    target_prompt_names: list[str] = Field(
        default_factory=lambda: ["default_chatter_user_prompt"],
        description="允许注入未完成念头的 prompt 模板名",
    )
    prompt_title: str = Field(
        default="未完成念头",
        description="prompt 中显示的标题",
    )
    inject_min_items: int = Field(
        default=1,
        ge=1,
        le=10,
        description="prompt 中随机注入的最小条数",
    )
    inject_max_items: int = Field(
        default=3,
        ge=1,
        le=10,
        description="prompt 中随机注入的最大条数",
    )


@config_section("model")
class ModelSection(SectionBase):
    """模型配置。"""

    task_name: str = Field(
        default="diary",
        description="扫描未完成念头使用的模型任务名",
    )
    fallback_task_name: str = Field(
        default="actor",
        description="当主任务不可用时的回退模型任务名",
    )


class UnfinishedThoughtConfig(BaseConfig):
    """unfinished_thought_plugin 配置。"""

    config_name = "config"
    config_description = "unfinished_thought_plugin 配置"

    plugin: PluginSection = Field(default_factory=PluginSection)
    storage: StorageSection = Field(default_factory=StorageSection)
    scan: ScanSection = Field(default_factory=ScanSection)
    prompt: PromptSection = Field(default_factory=PromptSection)
    model: ModelSection = Field(default_factory=ModelSection)
