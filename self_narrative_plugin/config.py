"""self_narrative_plugin 配置。"""

from __future__ import annotations

from src.app.plugin_system.base import BaseConfig, Field, SectionBase, config_section


@config_section("plugin")
class PluginSection(SectionBase):
    """插件基础配置。"""

    enabled: bool = Field(default=True, description="是否启用 self_narrative_plugin")
    inject_prompt: bool = Field(
        default=True,
        description="是否在 prompt 构建时注入自我叙事摘要",
    )
    include_identity_bounds_in_prompt: bool = Field(
        default=True,
        description="是否在 prompt 中显示稳定边界",
    )
    include_history_in_prompt: bool = Field(
        default=False,
        description="是否在 prompt 中显示近期演化历史",
    )


@config_section("storage")
class StorageSection(SectionBase):
    """持久化配置。"""

    base_path: str = Field(
        default="data/self_narratives",
        description="自我叙事存储根目录",
    )
    max_history_records: int = Field(
        default=12,
        ge=1,
        le=100,
        description="保留的历史演化记录上限",
    )
    max_prompt_items_per_section: int = Field(
        default=3,
        ge=1,
        le=10,
        description="prompt 中每个分区展示的最大条目数",
    )


@config_section("schedule")
class ScheduleSection(SectionBase):
    """调度配置。"""

    enabled: bool = Field(
        default=True,
        description="是否启用每日 0 点自动更新",
    )
    update_time: str = Field(
        default="00:00",
        description="每日自动更新触发时间（HH:MM）",
    )
    catch_up_on_startup: bool = Field(
        default=True,
        description="启动时是否补跑一次未执行的日更",
    )
    manual_cooldown_seconds: int = Field(
        default=300,
        ge=0,
        le=86400,
        description="手动命令更新冷却时间（秒）",
    )


@config_section("prompt")
class PromptSection(SectionBase):
    """prompt 注入配置。"""

    target_prompt_names: list[str] = Field(
        default_factory=lambda: ["default_chatter_system_prompt"],
        description="允许注入自我叙事的 prompt 模板名",
    )
    prompt_title: str = Field(
        default="自我叙事",
        description="prompt 中显示的标题",
    )
    max_history_lines: int = Field(
        default=3,
        ge=1,
        le=10,
        description="prompt 中展示的历史演化记录条数",
    )


@config_section("model")
class ModelSection(SectionBase):
    """模型配置。"""

    task_name: str = Field(
        default="diary",
        description="自我叙事更新使用的模型任务名",
    )
    fallback_task_name: str = Field(
        default="actor",
        description="当主任务不可用时的回退模型任务名",
    )


@config_section("narrative")
class NarrativeSection(SectionBase):
    """自我叙事默认值。"""

    default_identity_bounds: list[str] = Field(
        default_factory=lambda: [
            "我更重视真实表达，而不是迎合",
            "我倾向先理解再判断",
            "我不希望自己变得过度机械",
        ],
        description="初始稳定边界",
    )
    default_self_view: list[str] = Field(
        default_factory=list,
        description="初始自我理解",
    )
    default_ongoing_patterns: list[str] = Field(
        default_factory=list,
        description="初始反复模式",
    )
    default_open_loops: list[str] = Field(
        default_factory=list,
        description="初始开放问题",
    )


class SelfNarrativeConfig(BaseConfig):
    """self_narrative_plugin 配置。"""

    config_name = "config"
    config_description = "self_narrative_plugin 配置"

    plugin: PluginSection = Field(default_factory=PluginSection)
    storage: StorageSection = Field(default_factory=StorageSection)
    schedule: ScheduleSection = Field(default_factory=ScheduleSection)
    prompt: PromptSection = Field(default_factory=PromptSection)
    model: ModelSection = Field(default_factory=ModelSection)
    narrative: NarrativeSection = Field(default_factory=NarrativeSection)
