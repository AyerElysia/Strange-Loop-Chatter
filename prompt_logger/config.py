"""Prompt Logger 插件配置模块。"""

from __future__ import annotations

from typing import ClassVar, Literal

from src.core.components.base.config import BaseConfig, SectionBase, config_section, Field


class PromptLoggerConfig(BaseConfig):
    """Prompt Logger 插件配置类。"""

    config_name: ClassVar[str] = "config"

    @config_section("general")
    class GeneralSection(SectionBase):
        """通用配置。"""

        enabled: bool = Field(
            default=True,
            description="是否启用提示词记录功能"
        )
        log_file: str = Field(
            default="logs/prompt_logger/prompt.log",
            description="提示词日志文件路径（相对于项目根目录）"
        )
        max_log_size_mb: int = Field(
            default=10,
            description="单个日志文件的最大大小（MB），超过后自动轮转"
        )
        backup_count: int = Field(
            default=5,
            description="保留的备份日志文件数量"
        )

    @config_section("format")
    class FormatSection(SectionBase):
        """日志格式配置。"""

        show_request: bool = Field(
            default=True,
            description="是否记录 LLM 请求提示词"
        )
        show_response: bool = Field(
            default=True,
            description="是否记录 LLM 响应内容"
        )
        show_tools: bool = Field(
            default=True,
            description="是否在日志中显示工具参数"
        )
        show_timestamp: bool = Field(
            default=True,
            description="是否在日志中显示时间戳"
        )
        truncate_content_length: int = Field(
            default=0,
            description="单个 payload 内容的最大截断长度（0 表示不限制）"
        )

    @config_section("filter")
    class FilterSection(SectionBase):
        """日志过滤配置。"""

        scope: Literal["dfc_main", "custom", "all"] = Field(
            default="dfc_main",
            description=(
                "记录范围。dfc_main 仅记录 DFC 主回复模型，"
                "custom 按 include/exclude 列表筛选，all 记录所有来源。"
            ),
        )

        include_plugins: list[str] = Field(
            default_factory=list,
            description="仅记录这些插件来源（需要堆栈或上下文能识别 plugin_name）",
        )
        include_chatters: list[str] = Field(
            default_factory=list,
            description="仅记录这些 chatter 名称",
        )
        include_request_names: list[str] = Field(
            default_factory=list,
            description="仅记录这些 request_name",
        )
        include_models: list[str] = Field(
            default_factory=list,
            description="仅记录这些模型标识",
        )

        exclude_plugins: list[str] = Field(
            default_factory=list,
            description="排除这些插件来源",
        )
        exclude_chatters: list[str] = Field(
            default_factory=list,
            description="排除这些 chatter 名称",
        )
        exclude_request_names: list[str] = Field(
            default_factory=list,
            description="排除这些 request_name",
        )
        exclude_models: list[str] = Field(
            default_factory=list,
            description="排除这些模型标识",
        )

        log_private_chat: bool = Field(
            default=True,
            description="是否记录私聊聊天流的提示词"
        )
        log_group_chat: bool = Field(
            default=True,
            description="是否记录群聊聊天流的提示词"
        )
        allow_unknown_source: bool = Field(
            default=True,
            description=(
                "当无法识别 plugin_name / chatter_name 时，"
                "是否仍允许仅凭 request_name 命中记录。"
            ),
        )

    general: GeneralSection = Field(default_factory=GeneralSection)
    format: FormatSection = Field(default_factory=FormatSection)
    filter: FilterSection = Field(default_factory=FilterSection)
