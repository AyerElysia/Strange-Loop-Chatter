"""Omni Vision 插件配置。

配置是否启用全模态视觉功能，允许主模型直接接收图片。
"""

from typing import ClassVar

from src.core.components.base.config import BaseConfig, SectionBase, config_section, Field


class OmniVisionConfig(BaseConfig):
    """Omni Vision 插件配置类。"""

    config_name: ClassVar[str] = "config"
    config_description: ClassVar[str] = "全模态视觉插件配置"

    @config_section("settings")
    class SettingsSection(SectionBase):
        """设置配置项。"""

        enable_omni_vision: bool = Field(
            default=False,
            description="是否启用全模态视觉功能。启用后，主模型将直接接收图片，绕过 VLM 转译。",
        )

    settings: SettingsSection = Field(default_factory=SettingsSection)
