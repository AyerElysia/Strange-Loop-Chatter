"""预约延迟续话动作。"""

from __future__ import annotations

from src.core.components.base import BaseAction


class ScheduleFollowupMessageAction(BaseAction):
    """登记一条稍后检查的续话意图。"""

    action_name = "schedule_followup_message"
    action_description = (
        "当你刚刚已经发出一条回复，但觉得自己过一小会儿在对方还没回复时"
        "可能还想补一句时使用。它不会立刻发送消息，而是登记一条延迟续话计划。"
    )

    chatter_allow: list[str] = ["default_chatter", "proactive_message_plugin"]

    async def execute(
        self,
        delay_seconds: float,
        thought: str,
        topic: str,
        followup_type: str = "share_new_thought",
    ) -> tuple[bool, str]:
        """登记一条延迟续话计划。

        Args:
            delay_seconds: 过多久后再检查是否还想补一句，单位秒
            thought: 你此刻为什么还想继续说
            topic: 这次续话围绕的话题
            followup_type: 续话类型，例如 add_detail / clarify / soft_emotion / share_new_thought
        """
        plugin = self.plugin
        schedule = getattr(plugin, "schedule_followup_for_stream", None)
        if not callable(schedule):
            return False, "proactive_message_plugin 未正确加载"

        ok, message = await schedule(
            self.chat_stream,
            delay_seconds=delay_seconds,
            thought=thought,
            topic=topic,
            followup_type=followup_type,
            source="post_reply",
        )
        return ok, message

