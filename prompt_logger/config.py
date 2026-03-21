"""Prompt Logger 插件配置模块。"""

from __future__ import annotations

from typing import ClassVar

from src.kernel.config import ConfigBase, SectionBase, config_section, Field


class PromptLoggerConfig(ConfigBase):
    """Prompt Logger 插件配置类。"""

    config_file_name: ClassVar[str] = "plugins/prompt_logger"

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
            default=5000,
            description="单个 payload 内容的最大截断长度（0 表示不限制）"
        )

    @config_section("filter")
    class FilterSection(SectionBase):
        """日志过滤配置。"""

        log_private_chat: bool = Field(
            default=True,
            description="是否记录私聊聊天流的提示词"
        )
        log_group_chat: bool = Field(
            default=True,
            description="是否记录群聊聊天流的提示词"
        )
        excluded_chatters: list[str] = Field(
            default_factory=list,
            description="不记录提示词的 Chatter 名称列表"
        )
