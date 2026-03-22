"""drive_core 命令。"""

from __future__ import annotations

from src.app.plugin_system.base import BaseCommand, cmd_route

from ..service import get_drive_core_service


class DriveCoreCommand(BaseCommand):
    """查看和推进内驱力工作区。"""

    command_name = "drive_core"
    command_description = "查看、推进或重置当前聊天流的内驱力工作区"
    command_prefix = "/"

    def _get_stream(self):
        from src.core.managers import get_stream_manager

        return get_stream_manager()._streams.get(self.stream_id)

    def _get_stream_meta(self) -> tuple[str, str, str]:
        stream = self._get_stream()
        if not stream:
            return "private", "", ""
        return (
            str(getattr(stream, "chat_type", "private")),
            str(getattr(stream, "platform", "")),
            str(getattr(stream, "stream_name", "")),
        )

    @cmd_route("view")
    async def view(self) -> tuple[bool, str]:
        service = get_drive_core_service()
        if service is None:
            return False, "drive_core 服务未加载"

        stream = self._get_stream()
        if not stream:
            return False, "未找到聊天流"

        return True, service.render_state_summary(
            stream_id=stream.stream_id,
            chat_type=str(getattr(stream, "chat_type", "private")),
            platform=str(getattr(stream, "platform", "")),
            stream_name=str(getattr(stream, "stream_name", "")),
        )

    @cmd_route("history")
    async def history(self) -> tuple[bool, str]:
        service = get_drive_core_service()
        if service is None:
            return False, "drive_core 服务未加载"

        stream = self._get_stream()
        if not stream:
            return False, "未找到聊天流"

        return True, service.render_history(
            stream_id=stream.stream_id,
            chat_type=str(getattr(stream, "chat_type", "private")),
            platform=str(getattr(stream, "platform", "")),
            stream_name=str(getattr(stream, "stream_name", "")),
        )

    @cmd_route("advance")
    async def advance(self) -> tuple[bool, str]:
        service = get_drive_core_service()
        if service is None:
            return False, "drive_core 服务未加载"

        stream = self._get_stream()
        if not stream:
            return False, "未找到聊天流"

        return await service.advance_inquiry(
            stream_id=stream.stream_id,
            chat_type=str(getattr(stream, "chat_type", "private")),
            platform=str(getattr(stream, "platform", "")),
            stream_name=str(getattr(stream, "stream_name", "")),
            trigger="manual",
        )

    @cmd_route("reset")
    async def reset(self) -> tuple[bool, str]:
        service = get_drive_core_service()
        if service is None:
            return False, "drive_core 服务未加载"

        stream = self._get_stream()
        if not stream:
            return False, "未找到聊天流"

        return service.clear_state(
            stream_id=stream.stream_id,
            chat_type=str(getattr(stream, "chat_type", "private")),
            platform=str(getattr(stream, "platform", "")),
            stream_name=str(getattr(stream, "stream_name", "")),
        )

