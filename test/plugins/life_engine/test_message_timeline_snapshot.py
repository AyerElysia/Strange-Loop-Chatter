"""life / chatter 联合消息快照测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from plugins.life_engine.core.config import LifeEngineConfig
from plugins.life_engine.service.core import LifeEngineService
from plugins.life_engine.service.event_builder import EventType, LifeEngineEvent
from src.core.models.message import Message, MessageType


class _FakeStreamManager:
    def __init__(self, streams) -> None:
        self._streams = streams


def test_message_observability_snapshot_includes_life_and_chatter(monkeypatch) -> None:
    plugin = SimpleNamespace(config=LifeEngineConfig())
    service = LifeEngineService(plugin)

    life_event = LifeEngineEvent(
        event_id="life-1",
        event_type=EventType.MESSAGE,
        timestamp="2026-04-18T12:00:00+08:00",
        sequence=1,
        source="qq",
        source_detail="qq | 入站 | 私聊 | Alice | 用户ID=u1",
        content="life 收到了一条消息",
        sender="Alice",
        chat_type="private",
        stream_id="stream-1",
    )
    service._event_history = [life_event]
    service._pending_events = []

    chatter_message = Message(
        message_id="msg-1",
        time=1713412800.0,
        content="你好，life",
        processed_plain_text="你好，life",
        message_type=MessageType.TEXT,
        sender_id="u1",
        sender_name="Alice",
        sender_role="user",
        platform="qq",
        chat_type="private",
        stream_id="stream-1",
    )

    stream = SimpleNamespace(
        stream_id="stream-1",
        stream_name="Alice",
        platform="qq",
        chat_type="private",
        bot_nickname="Neo",
        is_active=True,
        is_chatter_processing=False,
        last_active_time=1713412800.0,
        context=SimpleNamespace(
            history_messages=[chatter_message],
            unread_messages=[],
            current_message=None,
            last_message_time=1713412800.0,
            is_chatter_processing=False,
        ),
    )

    fake_manager = _FakeStreamManager({"stream-1": stream})
    monkeypatch.setattr("src.core.managers.get_stream_manager", lambda: fake_manager)

    snapshot = asyncio.run(service.get_message_observability_snapshot())

    assert snapshot["life"]["latest_event"]["event_id"] == "life-1"
    assert snapshot["life"]["latest_event"]["event_type"] == "message"
    assert snapshot["streams"][0]["stream_id"] == "stream-1"
    assert snapshot["streams"][0]["latest_message"]["message_id"] == "msg-1"
    assert snapshot["streams"][0]["latest_message"]["content"] == "你好，life"
