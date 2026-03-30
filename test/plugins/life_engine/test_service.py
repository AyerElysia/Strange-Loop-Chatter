"""life_engine service tests."""

from __future__ import annotations

from dataclasses import dataclass

from plugins.life_engine.config import LifeEngineConfig
from plugins.life_engine.service import LifeEngineMessageRecord, LifeEngineService


@dataclass
class _DummyPlugin:
    config: LifeEngineConfig


def _make_service() -> LifeEngineService:
    config = LifeEngineConfig()
    config.settings.enabled = True
    return LifeEngineService(_DummyPlugin(config=config))


def test_record_model_reply_does_not_append_history() -> None:
    """模型回复只应记录状态，不应写回滚动历史。"""

    service = _make_service()
    seed = LifeEngineMessageRecord(
        received_at="2026-03-30T18:00:00+08:00",
        platform="qq",
        chat_type="group",
        source_label="qq | 群聊 | 测试群",
        source_detail="群ID=123",
        stream_id="stream-1",
        sender_display="Alice",
        sender_id="10001",
        message_id="msg-1",
        reply_to=None,
        message_type="text",
        content="hello",
    )
    service._message_history.append(seed)
    service._state.history_message_count = len(service._message_history)
    service._state.heartbeat_count = 7
    service._state.last_heartbeat_at = "2026-03-30T18:01:00+08:00"

    service._record_model_reply("  内部报文  ")

    assert len(service._message_history) == 1
    assert service._message_history[0] is seed
    assert service._state.history_message_count == 1
    assert service._state.last_model_reply == "内部报文"
    assert service._state.last_model_reply_at is not None
    assert service._state.last_model_error is None
