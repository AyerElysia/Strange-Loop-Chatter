"""personality_engine_plugin 配置。"""

from __future__ import annotations

from src.app.plugin_system.base import BaseConfig, Field, SectionBase, config_section


@config_section("plugin")
class PluginSection(SectionBase):
    """插件基础配置。"""

    enabled: bool = Field(default=True, description="是否启用 personality_engine_plugin")
    inject_prompt: bool = Field(
        default=True,
        description="是否在 prompt 构建时注入人格态摘要",
    )


@config_section("storage")
class StorageSection(SectionBase):
    """存储配置。"""

    base_path: str = Field(
        default="data/personality_engine",
        description="人格状态存储根目录",
    )
    max_history_records: int = Field(
        default=30,
        ge=1,
        le=200,
        description="每个聊天流最大保留的变更历史条数",
    )


@config_section("scan")
class ScanSection(SectionBase):
    """扫描与推进配置。"""

    trigger_every_n_messages: int = Field(
        default=6,
        ge=1,
        le=1000,
        description="每隔多少次有效对话触发一次人格推进",
    )
    max_context_messages: int = Field(
        default=12,
        ge=1,
        le=100,
        description="人格推进读取的最近消息条数",
    )


@config_section("model")
class ModelSection(SectionBase):
    """模型配置。"""

    task_name: str = Field(
        default="diary",
        description="人格推进使用的模型任务名",
    )
    fallback_task_name: str = Field(
        default="actor",
        description="主任务不可用时的回退任务名",
    )
    enable_llm_selector: bool = Field(
        default=True,
        description="是否启用 LLM 选择当前补偿功能",
    )
    enable_llm_reflection: bool = Field(
        default=True,
        description="是否启用 LLM 执行结构反思判定",
    )


@config_section("personality")
class PersonalitySection(SectionBase):
    """人格更新配置。"""

    default_mbti: str = Field(default="INTJ", description="默认人格类型（16MBTI）")
    change_weight: float = Field(
        default=0.06,
        ge=0.01,
        le=0.3,
        description="每轮对选中功能的权重增量",
    )
    change_history_decay: float = Field(
        default=0.2,
        ge=0.0,
        le=1.0,
        description="未触发人格结构变更时的变更历史衰减系数",
    )
    normalize_main_threshold: float = Field(
        default=0.5,
        ge=0.31,
        le=1.0,
        description="主功能临时权重超过该阈值时触发归一化",
    )
    max_parse_retries: int = Field(
        default=2,
        ge=0,
        le=5,
        description="LLM 输出解析失败时的最大重试次数",
    )


@config_section("prompt")
class PromptSection(SectionBase):
    """prompt 注入配置。"""

    target_prompt_names: list[str] = Field(
        default_factory=lambda: ["default_chatter_system_prompt"],
        description="允许注入人格态摘要的 prompt 模板名",
    )
    prompt_title: str = Field(default="人格态", description="注入块标题")
    mode: str = Field(
        default="paper_strict",
        description="注入模式，可选 compact/paper_strict",
    )
    inject_detail_level: str = Field(
        default="detail",
        description="注入粒度，可选 compact/detail",
    )
    include_function_catalog: bool = Field(
        default=True,
        description="paper_strict 模式下是否注入八功能映射说明",
    )
    recent_history_records: int = Field(
        default=3,
        ge=0,
        le=20,
        description="paper_strict 模式下注入的近期结构变化记录条数",
    )


class PersonalityEngineConfig(BaseConfig):
    """personality_engine_plugin 配置。"""

    config_name = "config"
    config_description = "personality_engine_plugin 配置"

    plugin: PluginSection = Field(default_factory=PluginSection)
    storage: StorageSection = Field(default_factory=StorageSection)
    scan: ScanSection = Field(default_factory=ScanSection)
    model: ModelSection = Field(default_factory=ModelSection)
    personality: PersonalitySection = Field(default_factory=PersonalitySection)
    prompt: PromptSection = Field(default_factory=PromptSection)
