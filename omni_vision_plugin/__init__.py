"""Omni Vision 插件。

全模态视觉插件 - 允许主模型直接接收图片，绕过 VLM 转译。
"""

from .plugin import OmniVisionPlugin, OmniVisionHandler
from .config import OmniVisionConfig

__all__ = [
    "OmniVisionPlugin",
    "OmniVisionHandler",
    "OmniVisionConfig",
]
