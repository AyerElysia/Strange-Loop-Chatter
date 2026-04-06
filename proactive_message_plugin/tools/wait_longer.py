"""Wait Longer 工具 - 让 LLM 表达继续等待的意愿"""

from src.core.components.base import BaseTool


class WaitLongerTool(BaseTool):
    """等待更长时间工具。

    当 LLM 觉得现在还不是主动发消息的好时机时使用此工具。
    LLM 需要指定想要等待的时长。
    """

    tool_name = "wait_longer"
    tool_description = "当你觉得现在还不是主动发消息的好时机，想再等一段时间时使用。你需要指定想要等待的时长（分钟）。"

    # 允许在默认聊天器（default_chatter）和主动消息插件的流程中使用
    chatter_allow: list[str] = ["proactive_message_plugin", "default_chatter"]

    async def execute(self, wait_minutes: int, thought: str) -> tuple[bool, str]:
        """执行等待逻辑。

        Args:
            wait_minutes: 想要等待的时长（分钟）
            thought: 你的内心想法，为什么选择继续等待
        """
        # 实际调度由插件处理，这里只返回确认信息。
        # Tool 协议必须返回 (success, result) 二元组。
        return True, f"好的，我会再等{wait_minutes}分钟。你的想法：{thought}"
