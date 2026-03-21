"""意图插件配置类。

定义意图插件的可配置项，包括意图分类开关、全局设置等。
"""

from __future__ import annotations

from src.app.plugin_system.base import BaseConfig, Field, SectionBase, config_section


@config_section("social")
class SocialSection(SectionBase):
    """社交类意图配置。"""

    enabled: bool = Field(
        default=True,
        description="是否启用社交类意图（了解用户、开启话题、记住细节等）",
    )
    weight: float = Field(
        default=1.0,
        description="社交类意图的权重（0-1，影响生成概率）",
    )


@config_section("emotional")
class EmotionalSection(SectionBase):
    """情感类意图配置。"""

    enabled: bool = Field(
        default=True,
        description="是否启用情感类意图（情感支持、制造惊喜、表达共情等）",
    )
    weight: float = Field(
        default=1.0,
        description="情感类意图的权重（0-1，影响生成概率）",
    )


@config_section("growth")
class GrowthSection(SectionBase):
    """成长类意图配置。"""

    enabled: bool = Field(
        default=True,
        description="是否启用成长类意图（学习喜好、构建回忆、知识分享等）",
    )
    weight: float = Field(
        default=1.0,
        description="成长类意图的权重（0-1，影响生成概率）",
    )


@config_section("generation")
class GenerationSection(SectionBase):
    """LLM 意图生成配置。"""

    model_task: str = Field(
        default="actor",
        description="用于意图生成的 LLM 模型任务名称",
    )
    max_candidates: int = Field(
        default=3,
        description="每次最多生成的意图候选数量",
    )
    diversity_temperature: float = Field(
        default=0.7,
        description="生成多样性温度（0-1，越高越多样化）",
    )
    intent_generation_interval: int = Field(
        default=3,
        description="意图生成间隔（每 N 条消息触发一次意图生成）",
    )


@config_section("settings")
class SettingsSection(SectionBase):
    """全局设置配置。"""

    max_active_intents: int = Field(
        default=3,
        description="最多同时活跃的意图数量",
    )
    min_priority_threshold: int = Field(
        default=3,
        description="意图优先级阈值（低于此值不触发）",
    )
    goal_timeout_messages: int = Field(
        default=20,
        description="目标超时配置（多少条消息后超时）",
    )
    goal_timeout_seconds: int = Field(
        default=600,
        description="目标超时配置（多少秒后超时）",
    )
    intent_cooldown_messages: int = Field(
        default=15,
        description="同一意图两次触发之间的最小间隔（消息数）",
    )


@config_section("reminder")
class ReminderSection(SectionBase):
    """System Reminder 配置。"""

    show_current_goal: bool = Field(
        default=True,
        description="是否显示当前目标",
    )
    show_progress_hint: bool = Field(
        default=True,
        description="是否显示进度提示",
    )
    tone_style: str = Field(
        default="natural",
        description="语气风格 (natural/cute/professional)",
    )


class IntentConfig(BaseConfig):
    """意图插件配置。

    配置项说明：
    - social: 社交类意图配置
    - emotional: 情感类意图配置
    - growth: 成长类意图配置
    - generation: LLM 意图生成配置
    - settings: 全局设置
    - reminder: System Reminder 配置

    默认配置路径：config/plugins/intent_plugin/config.toml
    """

    config_name = "config"
    config_description = "意图插件配置（LLM 动态生成模式）"

    social: SocialSection = Field(default_factory=SocialSection)
    emotional: EmotionalSection = Field(default_factory=EmotionalSection)
    growth: GrowthSection = Field(default_factory=GrowthSection)
    generation: GenerationSection = Field(default_factory=GenerationSection)
    settings: SettingsSection = Field(default_factory=SettingsSection)
    reminder: ReminderSection = Field(default_factory=ReminderSection)
