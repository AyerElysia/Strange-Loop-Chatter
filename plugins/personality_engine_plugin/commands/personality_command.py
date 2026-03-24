"""personality 命令。"""

from __future__ import annotations

from src.app.plugin_system.base import BaseCommand, cmd_route

from ..service import get_personality_engine_service


class PersonalityCommand(BaseCommand):
    """查看和推进人格状态。"""

    command_name = "personality"
    command_description = "查看、推进、重置或设置当前聊天流的人格状态"
    command_prefix = "/"

    def _get_stream(self):
        from src.core.managers import get_stream_manager

        return get_stream_manager()._streams.get(self.stream_id)

    @cmd_route("view")
    async def view(self) -> tuple[bool, str]:
        service = get_personality_engine_service()
        if service is None:
            return False, "personality_engine 服务未加载"

        stream = self._get_stream()
        if stream is None:
            return False, "未找到聊天流"

        return True, service.render_state_summary(
            stream_id=stream.stream_id,
            chat_type=str(getattr(stream, "chat_type", "private")),
        )

    @cmd_route("advance")
    async def advance(self) -> tuple[bool, str]:
        service = get_personality_engine_service()
        if service is None:
            return False, "personality_engine 服务未加载"

        stream = self._get_stream()
        if stream is None:
            return False, "未找到聊天流"

        return await service.advance_personality_step(
            stream_id=stream.stream_id,
            chat_type=str(getattr(stream, "chat_type", "private")),
            platform=str(getattr(stream, "platform", "")),
            stream_name=str(getattr(stream, "stream_name", "")),
            trigger="manual",
        )

    @cmd_route("reset")
    async def reset(self) -> tuple[bool, str]:
        service = get_personality_engine_service()
        if service is None:
            return False, "personality_engine 服务未加载"

        stream = self._get_stream()
        if stream is None:
            return False, "未找到聊天流"

        return service.reset_state(
            stream_id=stream.stream_id,
            chat_type=str(getattr(stream, "chat_type", "private")),
            platform=str(getattr(stream, "platform", "")),
            stream_name=str(getattr(stream, "stream_name", "")),
        )

    @cmd_route("set_mbti")
    async def set_mbti(self, mbti: str = "") -> tuple[bool, str]:
        service = get_personality_engine_service()
        if service is None:
            return False, "personality_engine 服务未加载"

        stream = self._get_stream()
        if stream is None:
            return False, "未找到聊天流"

        if not mbti:
            return False, "用法: /personality set_mbti <MBTI>"

        return service.set_mbti(
            stream_id=stream.stream_id,
            chat_type=str(getattr(stream, "chat_type", "private")),
            mbti=mbti,
            platform=str(getattr(stream, "platform", "")),
            stream_name=str(getattr(stream, "stream_name", "")),
        )

