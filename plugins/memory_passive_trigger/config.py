"""被动记忆浮现插件配置定义。"""

from __future__ import annotations

from src.kernel.config import ConfigBase, SectionBase, config_section, Field


class PluginSection(SectionBase):
    """插件级开关配置。"""

    enabled: bool = Field(
        default=True,
        description="是否启用被动记忆浮现插件",
    )


class TriggerSection(SectionBase):
    """被动触发配置。"""

    enabled: bool = Field(
        default=True,
        description="是否启用被动浮现功能",
    )

    similarity_threshold: float = Field(
        default=0.75,
        ge=0.0,
        le=1.0,
        description="语义匹配阈值（0-1），越高越严格，建议范围 0.70-0.85",
    )

    max_flash_count: int = Field(
        default=2,
        ge=1,
        le=10,
        description="最多浮现几条记忆",
    )

    cooldown_seconds: int = Field(
        default=300,
        ge=0,
        description="同一条记忆的冷却时间（秒），0 表示不启用冷却",
    )

    priority_folders: list[str] = Field(
        default=["facts", "preferences", "relations"],
        description="优先触发哪些 folder 的记忆",
    )


class RetrievalSection(SectionBase):
    """检索配置。"""

    include_archived: bool = Field(
        default=True,
        description="是否检索归档层记忆",
    )

    include_knowledge: bool = Field(
        default=False,
        description="是否检索知识库",
    )

    candidate_limit: int = Field(
        default=20,
        ge=1,
        le=100,
        description="检索时最多加载多少候选记忆",
    )


class DebugSection(SectionBase):
    """调试配置。"""

    verbose: bool = Field(
        default=False,
        description="是否打印详细日志",
    )


class MemoryPassiveTriggerConfig(ConfigBase):
    """被动记忆浮现插件配置。"""

    @config_section("plugin")
    class _PluginSection(PluginSection):
        pass

    plugin: _PluginSection = Field(default_factory=_PluginSection)

    @config_section("trigger")
    class _TriggerSection(TriggerSection):
        pass

    trigger: _TriggerSection = Field(default_factory=_TriggerSection)

    @config_section("retrieval")
    class _RetrievalSection(RetrievalSection):
        pass

    retrieval: _RetrievalSection = Field(default_factory=_RetrievalSection)

    @config_section("debug")
    class _DebugSection(DebugSection):
        pass

    debug: _DebugSection = Field(default_factory=_DebugSection)
