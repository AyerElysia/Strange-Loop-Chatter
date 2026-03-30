"""DFC 与生命中枢的异步对话桥。"""

from __future__ import annotations

from typing import Annotated, Any

from src.core.components.base.tool import BaseTool
from src.core.managers import get_plugin_manager


class MessageNucleusTool(BaseTool):
    """向生命中枢留言，不等待即时回复。"""

    tool_name = "message_nucleus"
    tool_description = (
        "向生命中枢留言，请它慢慢思考后再主动唤醒你。"
        "这个工具不会同步返回中枢答案，只负责投递消息。"
        "适合询问“另一个我最近在想什么”、请中枢补充记忆、"
        "或把某个话题交给中枢继续琢磨。"
        "调用后不要假装已经拿到中枢回复。"
    )
    chatter_allow: list[str] = ["default_chatter"]

    async def execute(
        self,
        message: Annotated[str, "要转交给生命中枢的话。应直接写想问或想说的内容。"],
        stream_id: Annotated[
            str,
            "当前对话流 ID。通常留空，由系统自动填充。",
        ] = "",
        platform: Annotated[
            str,
            "当前平台名。通常留空，由系统自动填充。",
        ] = "",
        chat_type: Annotated[
            str,
            "当前聊天类型。通常留空，由系统自动填充。",
        ] = "",
        sender_name: Annotated[
            str,
            "当前说话身份展示名。通常留空，由系统自动填充。",
        ] = "",
    ) -> tuple[bool, str]:
        text = str(message or "").strip()
        if not text:
            return False, "message 不能为空"

        life_plugin = get_plugin_manager().get_plugin("life_engine")
        if life_plugin is None:
            return False, "life_engine 未加载，无法转交到生命中枢"

        service = getattr(life_plugin, "service", None)
        if service is None or not hasattr(service, "enqueue_dfc_message"):
            return False, "life_engine 服务不可用，无法转交到生命中枢"

        try:
            receipt: dict[str, Any] = await service.enqueue_dfc_message(
                message=text,
                stream_id=str(stream_id or "").strip(),
                platform=str(platform or "").strip(),
                chat_type=str(chat_type or "").strip(),
                sender_name=str(sender_name or "").strip(),
            )
        except Exception as exc:  # noqa: BLE001
            return False, f"转交到生命中枢失败: {exc}"

        event_id = str(receipt.get("event_id") or "unknown")
        return True, (
            f"已把这句话转交给生命中枢（event_id={event_id}）。"
            "不要等待即时回复；等它整理好后，会自己主动唤醒你。"
        )
