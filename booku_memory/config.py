"""Booku Memory Agent 插件配置。"""

from __future__ import annotations

from typing import ClassVar

from src.core.components.base.config import BaseConfig, Field, SectionBase, config_section


# 预制文件夹定义：folder_id -> 中文显示名
# 写入 Agent 会将此映射注入 system prompt，供内部 LLM 选择合适文件夹
PREDEFINED_FOLDERS: dict[str, str] = {
    "relations": "人物关系",
    "plans": "未来规划",
    "facts": "已知事实",
    "preferences": "个人偏好",
    "events": "重要事件",
    "work": "工作学习",
    "default": "未分类",
}


class BookuMemoryConfig(BaseConfig):
    """Booku Memory Agent 插件配置模型。"""

    config_name: ClassVar[str] = "config"
    config_description: ClassVar[str] = "Booku Memory Agent 配置"

    @config_section("plugin")
    class PluginSection(SectionBase):
        """插件级开关。"""

        enabled: bool = Field(default=True, description="是否启用插件")
        enable_agent_proxy_mode: bool = Field(
            default=True,
            description=(
                "是否启用 agent 代理模式。启用时对外暴露读取/写入两个 Agent；"
                "关闭时仅对外暴露 3 个 Tool：memory_retrieve（检索）、memory_create（写入）、memory_edit_inherent（编辑固有记忆）。"
            ),
        )
        inject_system_prompt: bool = Field(
            default=True,
            description="是否在 default_chatter 系统提示 extra_info 板块注入记忆引导语",
        )

    @config_section("storage")
    class StorageSection(SectionBase):
        """存储层配置。"""

        metadata_db_path: str = Field(
            default="data/booku_memory/metadata.db",
            description="SQLite 元数据数据库路径",
        )
        vector_db_path: str = Field(
            default="data/chroma_db/booku_memory",
            description="向量数据库路径",
        )
        default_folder_id: str = Field(
            default="default",
            description="默认活动记忆文件夹 ID",
        )

    @config_section("retrieval")
    class RetrievalSection(SectionBase):
        """检索与重塑配置。"""

        default_top_k: int = Field(default=5, description="默认召回条数")
        include_archived_default: bool = Field(
            default=False,
            description="默认是否检索归档记忆",
        )
        deduplication_threshold: float = Field(
            default=0.88,
            description="结果去重余弦阈值",
        )
        base_beta: float = Field(
            default=0.3,
            description="向量重塑基准强度",
        )
        logic_depth_scale: float = Field(
            default=0.5,
            description="逻辑深度对 beta 的增益系数",
        )
        core_boost_min: float = Field(default=1.2, description="核心标签最小增强")
        core_boost_max: float = Field(default=1.4, description="核心标签最大增强")
        diffusion_boost: float = Field(default=0.3, description="扩散标签增强权重")
        opposing_penalty: float = Field(default=0.5, description="对立标签惩罚权重")

    @config_section("write_conflict")
    class WriteConflictSection(SectionBase):
        """写入冲突检测配置。"""

        top_n: int = Field(default=8, description="写入冲突检查的检索样本数")
        energy_cutoff: float = Field(
            default=0.1,
            description="新颖度能量阈值，低于此值触发合并",
        )

    @config_section("time_window")
    class TimeWindowSection(SectionBase):
        """隐现记忆时间窗口与晋升配置。"""

        emergent_days: int = Field(
            default=7,
            description="隐现记忆时间窗口（天）；超出窗口后进入晋升检查",
        )
        activation_threshold: int = Field(
            default=2,
            description="隐现记忆在时间窗口内最少激活次数，达到后晋升为归档记忆，否则丢弃",
        )

    @config_section("internal_llm")
    class InternalLLMSection(SectionBase):
        """Agent 内部 LLM 决策配置。"""

        task_name: str = Field(
            default="tool_use",
            description="内部决策使用的模型任务名",
        )
        max_reasoning_steps: int = Field(
            default=12,
            description="内部 tool-calling 最大推理轮数",
        )

    plugin: PluginSection = Field(default_factory=PluginSection)
    storage: StorageSection = Field(default_factory=StorageSection)
    retrieval: RetrievalSection = Field(default_factory=RetrievalSection)
    write_conflict: WriteConflictSection = Field(default_factory=WriteConflictSection)
    time_window: TimeWindowSection = Field(default_factory=TimeWindowSection)
    internal_llm: InternalLLMSection = Field(default_factory=InternalLLMSection)
