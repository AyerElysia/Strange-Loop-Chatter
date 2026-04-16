"""life_engine nucleus_tell_dfc 工具测试。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from plugins.life_engine.tools import LifeEngineWakeDFCTool


@dataclass
class _DummyContext:
    unread_messages: list[Any] = field(default_factory=list)

    def add_unread_message(self, message: Any) -> None:
        self.unread_messages.append(message)


@dataclass
class _DummyStream:
    stream_id: str = "stream-1"
    platform: str = "qq"
    chat_type: str = "private"
    context: _DummyContext = field(default_factory=_DummyContext)


class _DummyStreamManager:
    def __init__(self, stream: _DummyStream) -> None:
        self._stream = stream

    async def get_or_create_stream(self, stream_id: str) -> _DummyStream | None:
        if stream_id != self._stream.stream_id:
            return None
        return self._stream

    async def get_stream_info(self, _stream_id: str) -> dict[str, str]:
        # 返回空信息，避免触发 user_query_helper 分支。
        return {}


class _DummyLoopManager:
    def __init__(self, *, start_ok: bool = True) -> None:
        self.start_ok = start_ok
        self.calls: list[tuple[str, bool]] = []

    async def start_stream_loop(self, stream_id: str, force: bool = False) -> bool:
        self.calls.append((stream_id, force))
        return self.start_ok


class _DummyLifeService:
    def __init__(self) -> None:
        self.tell_count = 0

    def _minutes_since_external_message(self) -> int:
        return 60

    def record_tell_dfc(self) -> None:
        self.tell_count += 1


def _patch_runtime(
    monkeypatch: pytest.MonkeyPatch,
    *,
    stream_manager: _DummyStreamManager,
    loop_manager: _DummyLoopManager,
    life_service: _DummyLifeService,
) -> None:
    monkeypatch.setattr(
        "src.core.managers.stream_manager.get_stream_manager",
        lambda: stream_manager,
    )
    monkeypatch.setattr(
        "src.core.transport.distribution.stream_loop_manager.get_stream_loop_manager",
        lambda: loop_manager,
    )
    monkeypatch.setattr(
        "plugins.life_engine.tools.file_tools._get_life_engine_service",
        lambda _plugin: life_service,
    )


def test_tell_dfc_default_is_queue_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认模式应只入队，不主动启动 stream loop。"""
    stream = _DummyStream()
    stream_manager = _DummyStreamManager(stream)
    loop_manager = _DummyLoopManager(start_ok=True)
    life_service = _DummyLifeService()
    _patch_runtime(
        monkeypatch,
        stream_manager=stream_manager,
        loop_manager=loop_manager,
        life_service=life_service,
    )

    tool = LifeEngineWakeDFCTool(plugin=object())
    ok, result = asyncio.run(
        tool.execute(
            message="[信息差] 我观察到她的语气在变。",
            reason="新观察：对话节奏放缓，但不急于立即打断。",
            importance="normal",
            stream_id="stream-1",
        )
    )

    assert ok is True
    assert isinstance(result, dict)
    assert result["proactive_wake"] is False
    assert result["wake_triggered"] is False
    assert len(stream.context.unread_messages) == 1
    assert loop_manager.calls == []
    assert life_service.tell_count == 1


def test_tell_dfc_proactive_wake_requires_detailed_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """开启 proactive_wake 时，reason 不详尽应直接拒绝。"""
    stream = _DummyStream()
    stream_manager = _DummyStreamManager(stream)
    loop_manager = _DummyLoopManager(start_ok=True)
    life_service = _DummyLifeService()
    _patch_runtime(
        monkeypatch,
        stream_manager=stream_manager,
        loop_manager=loop_manager,
        life_service=life_service,
    )

    tool = LifeEngineWakeDFCTool(plugin=object())
    ok, result = asyncio.run(
        tool.execute(
            message="[信息差] 需要马上说。",
            reason="很急",
            importance="high",
            proactive_wake=True,
            stream_id="stream-1",
        )
    )

    assert ok is False
    assert isinstance(result, str)
    assert "明确详尽" in result
    assert len(stream.context.unread_messages) == 0
    assert loop_manager.calls == []
    assert life_service.tell_count == 0


def test_tell_dfc_proactive_wake_starts_stream_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """合规开启 proactive_wake 时应主动唤醒 DFC。"""
    stream = _DummyStream()
    stream_manager = _DummyStreamManager(stream)
    loop_manager = _DummyLoopManager(start_ok=True)
    life_service = _DummyLifeService()
    _patch_runtime(
        monkeypatch,
        stream_manager=stream_manager,
        loop_manager=loop_manager,
        life_service=life_service,
    )

    tool = LifeEngineWakeDFCTool(plugin=object())
    ok, result = asyncio.run(
        tool.execute(
            message=(
                "[信息差] 我确认对方在群聊中对公开评价高度敏感。"
                "[影响] 若继续沿用刚才的表达，下一轮很可能触发防御并中断对话。"
                "[内在驱动] 我希望立刻把语气降下来，先稳住关系。"
            ),
            reason=(
                "信息差：我刚从近两天日志和记忆关联里确认了敏感触发点。"
                "影响：如果不立即调整，下一轮回复会放大误读风险并损伤信任。"
            ),
            importance="high",
            proactive_wake=True,
            stream_id="stream-1",
        )
    )

    assert ok is True
    assert isinstance(result, dict)
    assert result["proactive_wake"] is True
    assert result["wake_triggered"] is True
    assert loop_manager.calls == [("stream-1", False)]
    assert len(stream.context.unread_messages) == 1
    assert life_service.tell_count == 1
