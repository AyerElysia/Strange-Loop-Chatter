"""Time Awareness Plugin 配置。

配置时间感知插件的启用状态和时间格式。
"""

from typing import ClassVar

from src.core.components.base.config import BaseConfig, SectionBase, config_section, Field


class TimeAwarenessConfig(BaseConfig):
    """Time Awareness Plugin 配置类。

    Attributes:
        config_name: 配置文件名称
        config_description: 配置描述
    """

    config_name: ClassVar[str] = "config"
    config_description: ClassVar[str] = "时间感知插件配置"

    @config_section("settings")
    class SettingsSection(SectionBase):
        """基础设置配置项。"""

        enabled: bool = Field(
            default=True,
            description="是否启用时间感知。设为 false 可临时禁用，无需从 core.toml 移除插件。",
        )

        inject_on_load: bool = Field(
            default=True,
            description="是否在插件加载时自动注入时间提醒。",
        )

        auto_refresh: bool = Field(
            default=False,
            description="是否启用自动刷新（每小时更新一次时间）。",
        )

    @config_section("format")
    class FormatSection(SectionBase):
        """时间格式配置项。"""

        use_chinese_shichen: bool = Field(
            default=True,
            description="是否使用中式时辰（子丑寅卯等）。",
        )

        use_zodiac: bool = Field(
            default=True,
            description="是否显示生肖年份。",
        )

        use_ke: bool = Field(
            default=True,
            description="是否显示刻（每 15 分钟为一刻）。",
        )

        custom_format: str = Field(
            default="",
            description="自定义时间格式模板（空则使用默认中式格式）。",
        )

    settings: SettingsSection = Field(default_factory=SettingsSection)
    format: FormatSection = Field(default_factory=FormatSection)
