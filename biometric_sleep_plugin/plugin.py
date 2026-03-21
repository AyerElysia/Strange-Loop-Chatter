"""
Biometric Sleep System - 插件注册主类 (重构版)
"""

from typing import Any, Optional

from src.kernel.logger import get_logger
from src.core.components.base.plugin import BasePlugin
from src.core.components.loader import register_plugin
from src.core.components.base.plugin import BasePlugin

from .config import CONFIG_SCHEMA
from .src.core.sleep_manager import SleepStateManager
from .src.handlers.sleep_interceptor import SleepInterceptor
from .src.handlers.sleep_prompt import SleepStatusPrompt
from .src.commands.sleep_admin_command import SleepAdminCommand

logger = get_logger("sleep_plugin")

@register_plugin
class BiometricSleepPlugin(BasePlugin):
    """仿生睡眠与动态唤醒系统插件"""

    __plugin_meta__ = dict(
        name="Biometric Sleep System",
        description="具备生理节律的仿生睡眠与动态唤醒系统",
        usage="在配置文件中设置 start_time 和 end_time 即可",
        version="2.0.0",
        author="Kilo Code",
        categories=["utility", "personality"],
    )

    plugin_name = "biometric_sleep_plugin"
    enable_plugin = True
    dependencies = []
    python_dependencies = []
    config_file_name = "config.toml"

    config_section_descriptions = {
        "basic": "基础作息配置",
        "logic": "睡眠逻辑与唤醒配置",
        "context": "提示词注入配置",
        "filter": "范围控制配置",
    }

    config_schema = CONFIG_SCHEMA

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.manager: Optional[SleepStateManager] = None

    async def on_plugin_loaded(self):
        """插件加载时初始化管理器"""
        self.manager = SleepStateManager(self.config)
        logger.info("[SleepPlugin] 仿生睡眠系统已启动")

    def get_plugin_components(self):
        """注册组件"""
        if not self.manager:
            self.manager = SleepStateManager(self.config)

        components = []

        # 1. 注册事件拦截器
        # 注意：这里必须返回类本身，而不是 lambda 实例，否则系统无法获取 handler_name
        # 同时将 manager 注入到类属性中，以便组件初始化时获取
        SleepInterceptor.manager = self.manager # type: ignore
        components.append(
            (SleepInterceptor.get_handler_info(), SleepInterceptor)
        )

        # 2. 注册提示词注入组件
        SleepStatusPrompt.manager = self.manager # type: ignore
        components.append(
            (SleepStatusPrompt.get_prompt_info(), SleepStatusPrompt)
        )

        # 3. 注册临时管理命令
        SleepAdminCommand.manager = self.manager # type: ignore
        components.append(
            (SleepAdminCommand.get_command_info(), SleepAdminCommand)
        )

        return components

    def get_plugin_info(self) -> dict[str, Any]:
        return {
            "name": self.plugin_name,
            "display_name": "Biometric Sleep System",
            "version": "2.0.0",
            "author": "Kilo Code",
            "description": "具备生理节律的仿生睡眠与动态唤醒系统 (重构版)",
            "features": [
                "动态入睡点随机浮动",
                "疲劳延时入睡机制",
                "浅睡/深睡状态模拟",
                "特定会话唤醒阈值",
                "状态感应提示词注入"
            ],
        }