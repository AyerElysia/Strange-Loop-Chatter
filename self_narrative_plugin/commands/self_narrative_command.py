"""self_narrative 命令。"""

from __future__ import annotations

from src.app.plugin_system.base import BaseCommand, cmd_route

from ..service import get_self_narrative_service


class SelfNarrativeCommand(BaseCommand):
    """查看和更新自我叙事。"""

    command_name = "self_narrative"
    command_description = "查看、更新或重置当前聊天流的自我叙事"
    command_prefix = "/"

    @cmd_route("update")
    async def update(self) -> tuple[bool, str]:
        """立即更新当前聊天流的自我叙事。"""

        service = get_self_narrative_service()
        if service is None:
            return False, "self_narrative 服务未加载"

        stream_id = self.stream_id
        if not stream_id:
            return False, "缺少 stream_id"

        from src.core.managers import get_stream_manager

        stream = get_stream_manager()._streams.get(stream_id)
        if not stream:
            return False, "未找到聊天流"

        ok, message = await service.update_narrative(
            stream_id=stream.stream_id,
            chat_type=str(getattr(stream, "chat_type", "private")),
            platform=str(getattr(stream, "platform", "")),
            stream_name=str(getattr(stream, "stream_name", "")),
            trigger="manual",
        )
        return ok, message

    @cmd_route("view")
    async def view(self) -> tuple[bool, str]:
        """查看当前自我叙事。"""

        service = get_self_narrative_service()
        if service is None:
            return False, "self_narrative 服务未加载"

        from src.core.managers import get_stream_manager

        stream = get_stream_manager()._streams.get(self.stream_id)
        if not stream:
            return False, "未找到聊天流"

        summary = service.render_state_summary(
            stream_id=stream.stream_id,
            chat_type=str(getattr(stream, "chat_type", "private")),
        )
        return True, summary

    @cmd_route("history")
    async def history(self) -> tuple[bool, str]:
        """查看最近更新历史。"""

        service = get_self_narrative_service()
        if service is None:
            return False, "self_narrative 服务未加载"

        from src.core.managers import get_stream_manager

        stream = get_stream_manager()._streams.get(self.stream_id)
        if not stream:
            return False, "未找到聊天流"

        history_text = service.render_history(
            stream_id=stream.stream_id,
            chat_type=str(getattr(stream, "chat_type", "private")),
        )
        return True, history_text

    @cmd_route("reset")
    async def reset(self) -> tuple[bool, str]:
        """重置当前自我叙事。"""

        service = get_self_narrative_service()
        if service is None:
            return False, "self_narrative 服务未加载"

        from src.core.managers import get_stream_manager

        stream = get_stream_manager()._streams.get(self.stream_id)
        if not stream:
            return False, "未找到聊天流"

        return await service.reset_narrative(
            stream_id=stream.stream_id,
            chat_type=str(getattr(stream, "chat_type", "private")),
            platform=str(getattr(stream, "platform", "")),
            stream_name=str(getattr(stream, "stream_name", "")),
        )
