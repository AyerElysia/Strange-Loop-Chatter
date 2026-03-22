"""unfinished_thought 命令。"""

from __future__ import annotations

import shlex

from src.app.plugin_system.base import BaseCommand, cmd_route

from ..service import get_unfinished_thought_service


class UnfinishedThoughtCommand(BaseCommand):
    """查看和管理未完成念头。"""

    command_name = "unfinished_thought"
    command_description = "查看、添加、扫描、暂停或清理当前聊天流的未完成念头"
    command_prefix = "/"

    def _get_stream(self):
        from src.core.managers import get_stream_manager

        return get_stream_manager()._streams.get(self.stream_id)

    def _get_stream_meta(self) -> tuple[str, str, str]:
        stream = self._get_stream()
        if not stream:
            return "", "", ""
        return (
            str(getattr(stream, "chat_type", "private")),
            str(getattr(stream, "platform", "")),
            str(getattr(stream, "stream_name", "")),
        )

    async def execute(self, message_text: str) -> tuple[bool, str]:
        """优先把 add 子命令的剩余文本视作完整内容。"""

        text = str(message_text or "").strip()
        if not text:
            return await super().execute(text)

        try:
            parts = shlex.split(text)
        except ValueError as exc:
            return False, f"参数解析错误: {exc}"

        if parts and parts[0] == "add":
            content = " ".join(parts[1:]).strip()
            if not content:
                return False, "内容不能为空"
            return await self.add(content)

        return await super().execute(text)

    @cmd_route("view")
    async def view(self) -> tuple[bool, str]:
        service = get_unfinished_thought_service()
        if service is None:
            return False, "unfinished_thought 服务未加载"

        stream = self._get_stream()
        if not stream:
            return False, "未找到聊天流"

        return True, service.render_state_summary(
            stream_id=stream.stream_id,
            chat_type=str(getattr(stream, "chat_type", "private")),
        )

    @cmd_route("history")
    async def history(self) -> tuple[bool, str]:
        service = get_unfinished_thought_service()
        if service is None:
            return False, "unfinished_thought 服务未加载"

        stream = self._get_stream()
        if not stream:
            return False, "未找到聊天流"

        return True, service.render_history(
            stream_id=stream.stream_id,
            chat_type=str(getattr(stream, "chat_type", "private")),
        )

    @cmd_route("scan")
    async def scan(self) -> tuple[bool, str]:
        service = get_unfinished_thought_service()
        if service is None:
            return False, "unfinished_thought 服务未加载"

        stream = self._get_stream()
        if not stream:
            return False, "未找到聊天流"

        return await service.scan_thoughts(
            stream_id=stream.stream_id,
            chat_type=str(getattr(stream, "chat_type", "private")),
            platform=str(getattr(stream, "platform", "")),
            stream_name=str(getattr(stream, "stream_name", "")),
            trigger="manual",
        )

    @cmd_route("add")
    async def add(self, content: str) -> tuple[bool, str]:
        service = get_unfinished_thought_service()
        if service is None:
            return False, "unfinished_thought 服务未加载"

        stream = self._get_stream()
        if not stream:
            return False, "未找到聊天流"

        return await service.add_thought(
            stream_id=stream.stream_id,
            chat_type=str(getattr(stream, "chat_type", "private")),
            platform=str(getattr(stream, "platform", "")),
            stream_name=str(getattr(stream, "stream_name", "")),
            content=content,
        )

    @cmd_route("resolve")
    async def resolve(self, selector: str) -> tuple[bool, str]:
        service = get_unfinished_thought_service()
        if service is None:
            return False, "unfinished_thought 服务未加载"

        stream = self._get_stream()
        if not stream:
            return False, "未找到聊天流"

        return await service.set_thought_status(
            stream_id=stream.stream_id,
            chat_type=str(getattr(stream, "chat_type", "private")),
            platform=str(getattr(stream, "platform", "")),
            stream_name=str(getattr(stream, "stream_name", "")),
            selector=selector,
            status="resolved",
        )

    @cmd_route("pause")
    async def pause(self, selector: str) -> tuple[bool, str]:
        service = get_unfinished_thought_service()
        if service is None:
            return False, "unfinished_thought 服务未加载"

        stream = self._get_stream()
        if not stream:
            return False, "未找到聊天流"

        return await service.set_thought_status(
            stream_id=stream.stream_id,
            chat_type=str(getattr(stream, "chat_type", "private")),
            platform=str(getattr(stream, "platform", "")),
            stream_name=str(getattr(stream, "stream_name", "")),
            selector=selector,
            status="paused",
        )

    @cmd_route("clear")
    async def clear(self) -> tuple[bool, str]:
        service = get_unfinished_thought_service()
        if service is None:
            return False, "unfinished_thought 服务未加载"

        stream = self._get_stream()
        if not stream:
            return False, "未找到聊天流"

        return await service.clear_thoughts(
            stream_id=stream.stream_id,
            chat_type=str(getattr(stream, "chat_type", "private")),
            platform=str(getattr(stream, "platform", "")),
            stream_name=str(getattr(stream, "stream_name", "")),
        )
