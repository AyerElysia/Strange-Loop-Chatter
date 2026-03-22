"""Thinking Plugin - 思考工具插件主模块。

提供 think 工具，让爱莉希雅能够展现思考过程，实现多步连续行动。
"""

from src.core.components.base import BasePlugin
from src.core.components.loader import register_plugin

from .tools.think_tool import ThinkTool
from .thinker_trigger import ThinkerTrigger
from .config import ThinkingConfig


@register_plugin
class ThinkingPlugin(BasePlugin):
    """思考工具插件。

    提供 think 工具，让爱莉希雅在回复前能够展现思考过程。
    通过 FOLLOW_UP 机制，实现多步连续行动：
    思考 → 查询信息 → 再思考 → 回复用户

    Attributes:
        plugin_name: 插件名称
        plugin_version: 插件版本
        plugin_author: 插件作者
        plugin_description: 插件功能描述
    """

    plugin_name = "thinking_plugin"
    plugin_version = "1.0.0"
    plugin_author = "Neo-MoFox Team"
    plugin_description = "思考工具插件 - 让爱莉希雅展现内心活动，实现多步连续行动"
    configs = [ThinkingConfig]

    def get_components(self) -> list[type]:
        """获取插件内所有组件类。

        Returns:
            list[type]: 包含插件内所有组件类的列表
        """
        return [ThinkTool, ThinkerTrigger]

    async def on_plugin_loaded(self) -> None:
        """插件加载时的初始化钩子。

        从配置文件读取思考习惯引导词和字段开关配置，注册到 system reminder。
        """
        from src.app.plugin_system.api.prompt_api import add_system_reminder

        # 从配置读取提示词
        config = self.config

        # 注册思考习惯引导词到 actor bucket
        add_system_reminder(
            bucket="actor",
            name="thinking_habit",
            content=config.prompt.thinking_habit,
        )

        # 构建字段开关提示词
        fields_reminder = self._build_fields_reminder(config)
        if fields_reminder:
            add_system_reminder(
                bucket="actor",
                name="thinking_fields",
                content=fields_reminder,
            )

        # 注册"必须 think"的强制提醒
        must_think_reminder = self._build_must_think_reminder()
        if must_think_reminder:
            add_system_reminder(
                bucket="actor",
                name="thinking_must_think",
                content=must_think_reminder,
            )

    def _build_must_think_reminder(self) -> str:
        """构建'必须 think'的强制提醒。"""
        return """# 强制规则：先思考再回复

**在你准备回复用户之前，你必须先调用 think 工具。**

这是硬性要求——不允许直接回复。即使你觉得自己已经想清楚了，也要先调用 think 确认一下。

违反规则 = 没有遵守指令。
"""

    def _build_fields_reminder(self, config: ThinkingConfig) -> str:
        """根据字段开关配置构建提示词。

        Args:
            config: 思考工具配置

        Returns:
            str: 字段使用提示词，如无需添加则返回空字符串
        """
        fields = config.fields
        parts = []

        if fields.enable_mood:
            parts.append(
                "- **mood（心情）**：请填写你此刻的情绪状态，如'开心'、'疑惑'、'担心'等。"
            )
        if fields.enable_decision:
            parts.append(
                "- **decision（决定）**：请填写你决定的下一步行动，如'去查日记'、'再分析一下'、'直接回复'等。"
            )
        if fields.enable_expected_response:
            parts.append(
                "- **expected_response（预期反应）**：请填写你预期用户看到回复后的反应，如'应该会满意'、'可能会追问'等。"
            )

        if not parts:
            return ""

        return "# 思考工具的字段要求\n\n调用 think 工具时，除了 `thought` 必填外，以下字段已启用，且为必填项，请务必填写：\n\n" + "\n".join(parts) + "\n\n这些字段现在为必填项，不应留空；若确实无法判断，请填写 \"未知\" 或简短说明原因。"
