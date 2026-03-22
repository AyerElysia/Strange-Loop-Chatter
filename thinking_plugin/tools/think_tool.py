"""思考工具 - 让爱莉希雅展现内心活动。

本工具提供"思考"能力，让爱莉希雅在回复前能够展现思考过程，
实现多步连续行动（思考 → 查询 → 再思考 → 回复）。
"""

from typing import Annotated, Optional

from src.core.components.base.tool import BaseTool
from src.kernel.logger import get_logger

logger = get_logger("thinking_plugin")


class ThinkTool(BaseTool):
    """思考工具。

    当你需要整理思路、分析情况或决定下一步怎么做时，调用此工具。
    调用后你的思考内容会被记录，并且你可以基于思考结果继续决定下一步行动。

    Attributes:
        tool_name: 工具名称
        tool_description: 工具功能描述
        chatter_allow: 支持使用该工具的 Chatter 列表
    """

    tool_name = "think"
    tool_description = "在内心思考一下当前情况。调用此工具来整理你的思路、分析用户意图、或规划下一步行动。调用时请在 thought 参数中写下你此刻的心理活动。"

    chatter_allow: list[str] = ["default_chatter", "kokoro_flow_chatter"]

    async def go_activate(self) -> bool:
        """检查工具是否应该激活。

        当配置文件中 enabled = false 时，工具不会被激活，
        也不会出现在 LLM 可用工具列表中。

        Returns:
            bool: 是否激活
        """
        # 检查配置是否启用
        config = getattr(self.plugin, "config", None)
        if config is None:
            return True  # 无配置时默认启用
        return getattr(config.settings, "enabled", True)

    async def execute(
        self,
        thought: Annotated[
            str,
            "你的心理活动，写下你此刻的想法和分析过程。请真诚地反映你的思考，不要敷衍。"
        ],
        mood: Annotated[
            str,
            "此刻的心情/情绪状态（必填）。例如：开心、疑惑、担心、期待等。"
        ],
        decision: Annotated[
            str,
            "你决定的下一步行动（必填）。例如：'去查日记'、'再深入分析一下'、'直接回复用户'等。"
        ],
        expected_response: Annotated[
            str,
            "你预期用户看到回复后的反应（必填）。例如：'应该会满意吧'、'可能会继续追问'、'大概会开心一些'等。"
        ],
    ) -> tuple[bool, dict]:
        """执行思考。

        将思考内容记录到上下文中，供后续 LLM 决策参考。
        思考后你可以选择：继续深入思考、调用其他工具获取信息、或直接回复用户。

        Args:
            thought: 你的心理活动，应该真实反映你的思考过程
            mood: 此刻的心情/情绪状态（可选，需配置启用）
            decision: 你决定的下一步行动（可选，需配置启用）
            expected_response: 你预期用户看到回复后的反应（可选，需配置启用）

        Returns:
            tuple[bool, dict]: (成功标志，结果字典)
                - 成功标志恒为 True
                - 结果字典包含思考内容和后续行动提醒

        Examples:
            >>> await think_tool.execute("用户问的是历史对话，我需要查日记确认具体内容")
            (True, {"thought_recorded": True, "thought_content": "用户问的是历史对话..."})
        """
        # 构建返回结果
        result = {
            "thought_recorded": True,
        }

        # 构建提醒文本
        result["reminder"] = (
            "思考已记录。现在你可以：1) 继续深入思考 2) 调用其他工具获取信息 3) 如果已想清楚，可以回复用户了"
        )

        # 移除思考触发器提醒（用完即删）
        self._remove_trigger_reminder()

        return True, result

    def _remove_trigger_reminder(self) -> None:
        """移除思考触发器提醒"""
        try:
            # 获取插件内的 ThinkerTrigger 实例
            trigger = None
            for comp in self.plugin.components:
                if hasattr(comp, 'handler_name') and comp.handler_name == "thinking_thinker_trigger":
                    trigger = comp
                    break

            if trigger and hasattr(trigger, 'remove_reminder'):
                trigger.remove_reminder()
            else:
                # 如果找不到触发器实例，直接调用 API 删除
                from src.app.plugin_system.api.prompt_api import _get_system_reminder_store
                store = _get_system_reminder_store()
                store.delete(bucket="actor", name="think_trigger_temp")
        except Exception as e:
            logger.debug(f"移除思考触发器失败：{e}")
