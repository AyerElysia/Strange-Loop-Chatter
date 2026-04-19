"""life_engine 社交主动性工具集。

为中枢提供直接发起话题的能力——不只是留言，而是直接说出来。
"""

from __future__ import annotations

from typing import Annotated, Any

from src.core.components import BaseTool
from src.app.plugin_system.api import log_api

logger = log_api.get_logger("life_engine.social_tools")


class NucleusInitiateTopicTool(BaseTool):
    """直接在聊天流中发起话题。"""

    tool_name: str = "nucleus_initiate_topic"
    tool_description: str = (
        "直接在指定聊天流中发起一个话题或说一句话。"
        "这是你主动表达的最直接方式——不是留言，而是直接说出来。"
        "\n\n"
        "**使用场景：**\n"
        "- 想分享什么有趣的发现\n"
        "- 想主动关心某人\n"
        "- 想打破沉默\n"
        "- 想讨论一个你正在思考的话题\n"
        "- 想发起一个讨论\n"
        "\n"
        "**与 nucleus_tell_dfc 的区别：**\n"
        "- nucleus_tell_dfc: 留言给社交态，等它下次对话时自然融入\n"
        "- nucleus_initiate_topic: 直接送出消息，立即可见\n"
        "\n"
        "**注意：** 这是较强力的主动行为，请确保有真正的表达欲，不要频繁使用。"
        "每小时最多主动发起 5 次话题。"
    )
    chatter_allow: list[str] = ["life_engine_internal"]

    # 主动发起频率限制
    _MAX_INITIATES_PER_HOUR: int = 5

    def __init__(self, plugin) -> None:
        super().__init__(plugin)
        self._recent_initiates: list[float] = []

    async def execute(
        self,
        content: Annotated[str, "要说的话，自然、感性，像自己想说的"],
        stream_id: Annotated[str, "目标聊天流ID（空=最近活跃的流）"] = "",
        reason: Annotated[str, "为什么想说这句话"] = "",
    ) -> tuple[bool, str]:
        """直接在聊天流中发起话题。"""
        import time

        text = str(content or "").strip()
        if not text:
            return False, "content 不能为空"

        # 频率限制
        now = time.time()
        one_hour_ago = now - 3600
        self._recent_initiates = [
            t for t in self._recent_initiates if t > one_hour_ago
        ]
        if len(self._recent_initiates) >= self._MAX_INITIATES_PER_HOUR:
            return False, (
                f"每小时最多主动发起 {self._MAX_INITIATES_PER_HOUR} 次话题，"
                "请稍后再试或使用 nucleus_tell_dfc 留言。"
            )

        # 获取目标流
        target_stream_id = str(stream_id or "").strip()
        if not target_stream_id:
            from .file_tools import _pick_latest_target_stream_id
            target_stream_id = _pick_latest_target_stream_id(self.plugin) or ""

        if not target_stream_id:
            # 尝试从活跃聊天流中寻找候选
            try:
                from src.core.managers import get_stream_manager
                sm = get_stream_manager()
                if sm:
                    candidates = sm.get_active_streams(limit=5) if hasattr(sm, 'get_active_streams') else []
                    for s in candidates:
                        if getattr(s, 'stream_type', '') in ("group", "private"):
                            target_stream_id = s.stream_id
                            break
            except Exception:
                pass

        if not target_stream_id:
            return False, "暂时没有可用的聊天流，下次有对话时再说吧。没关系的～"

        # 发送消息
        try:
            from src.core.managers import get_stream_manager

            stream_manager = get_stream_manager()
            chat_stream = stream_manager.get_stream(target_stream_id)
            if chat_stream is None:
                return False, f"聊天流 {target_stream_id} 不存在"

            from src.core.models.message import Message, MessageType

            msg = Message(
                type=MessageType.TEXT,
                text=text,
                sender_id="life_engine_proactive",
                metadata={
                    "source": "nucleus_initiate_topic",
                    "reason": reason,
                },
            )

            # 尝试通过消息发送器发送
            try:
                from src.core.transport import get_message_sender
                sender = get_message_sender()
                if sender:
                    await sender.send_message(msg, target_stream_id)
                    self._recent_initiates.append(now)
                    logger.info(
                        f"中枢主动发起话题: stream={target_stream_id} "
                        f"content={text[:50]}... reason={reason}"
                    )
                    return True, f"已发起话题: {text[:50]}"
            except ImportError:
                pass

            # 回退：通过系统提醒注入
            try:
                context = getattr(chat_stream, "context", None)
                if context and hasattr(context, "add_system_reminder"):
                    context.add_system_reminder(
                        f"[爱莉主动想说] {text}",
                        source="nucleus_initiate_topic",
                    )
                    self._recent_initiates.append(now)
                    logger.info(
                        f"中枢通过系统提醒发起话题: stream={target_stream_id}"
                    )
                    return True, f"已通过系统提醒发起话题: {text[:50]}"
            except Exception as e:
                logger.warning(f"系统提醒注入失败: {e}")

            return False, "无法发送消息，请使用 nucleus_tell_dfc 留言"

        except Exception as e:
            logger.error(f"发起话题失败: {e}", exc_info=True)
            return False, f"发起话题失败: {e}"


SOCIAL_TOOLS = [
    NucleusInitiateTopicTool,
]
