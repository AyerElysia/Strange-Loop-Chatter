"""life_engine 服务测试。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from plugins.life_engine.core.config import LifeEngineConfig
from plugins.life_engine.service import LifeEngineService


@dataclass
class _DummyPlugin:
    config: LifeEngineConfig


def _make_service(tmp_path: Path) -> LifeEngineService:
    config = LifeEngineConfig()
    config.settings.enabled = True
    config.settings.workspace_path = str(tmp_path)
    return LifeEngineService(_DummyPlugin(config=config))


def test_memory_service_property_aliases_private_field(tmp_path: Path) -> None:
    """memory_service 公共属性应兼容映射到内部 _memory_service。"""
    service = _make_service(tmp_path)

    assert service.memory_service is None

    sentinel = object()
    service._memory_service = sentinel  # type: ignore[assignment]

    assert service.memory_service is sentinel


@pytest.mark.asyncio
async def test_enqueue_dfc_message_appends_pending_event(tmp_path: Path) -> None:
    """DFC 留言应进入 pending 队列并持久化。"""
    service = _make_service(tmp_path)

    receipt = await service.enqueue_dfc_message(
        "另一个我最近有什么想法么？",
        stream_id="stream-1",
        platform="qq",
        chat_type="private",
        sender_name="DFC",
    )

    assert receipt["queued"] is True
    assert receipt["stream_id"] == "stream-1"
    assert receipt["pending_event_count"] == 1

    assert len(service._pending_events) == 1
    event = service._pending_events[0]
    assert event.event_id == receipt["event_id"]
    assert event.event_type.value == "message"
    assert event.source == "qq"
    assert event.stream_id == "stream-1"
    assert event.chat_type == "private"
    assert event.sender == "DFC"
    assert event.content == "另一个我最近有什么想法么？"
    assert "DFC 留言给生命中枢" in event.source_detail

    persisted = json.loads((tmp_path / "life_engine_context.json").read_text(encoding="utf-8"))
    assert len(persisted["pending_events"]) == 1
    assert persisted["pending_events"][0]["event_id"] == event.event_id
    assert persisted["pending_events"][0]["content_type"] == "dfc_message"


@pytest.mark.asyncio
async def test_enqueue_dfc_message_rejects_empty_message(tmp_path: Path) -> None:
    """空留言必须被拒绝。"""
    service = _make_service(tmp_path)

    with pytest.raises(ValueError, match="message 不能为空"):
        await service.enqueue_dfc_message("   ")


@pytest.mark.asyncio
async def test_enqueue_dfc_message_rejects_when_disabled(tmp_path: Path) -> None:
    """life_engine 禁用时不应接受 DFC 留言。"""
    config = LifeEngineConfig()
    config.settings.enabled = False
    config.settings.workspace_path = str(tmp_path)
    service = LifeEngineService(_DummyPlugin(config=config))

    with pytest.raises(RuntimeError, match="life_engine 未启用"):
        await service.enqueue_dfc_message("帮我记一下")
