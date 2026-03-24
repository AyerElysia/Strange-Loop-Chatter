"""personality 扫描事件测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from plugins.personality_engine_plugin.components.events.personality_scan_event import (
    PersonalityScanEvent,
)
from plugins.personality_engine_plugin.config import PersonalityEngineConfig
from src.kernel.event import EventDecision


def test_personality_scan_event_calls_service() -> None:
    """收到 chatter_step 结果时，扫描事件应调用人格服务。"""

    config = PersonalityEngineConfig()
    config.plugin.enabled = True
    handler = PersonalityScanEvent(plugin=SimpleNamespace(config=config))

    class _DummyService:
        async def observe_chat_turn(
            self,
            *,
            stream_id: str,
            chat_type: str,
            platform: str = "",
            stream_name: str = "",
            trigger: str = "auto",
        ) -> tuple[bool, str]:
            assert stream_id == "sid_scan"
            assert chat_type == "private"
            assert platform == "qq"
            assert stream_name == "测试流"
            assert trigger == "chatter_step"
            return True, "ok"

    import plugins.personality_engine_plugin.components.events.personality_scan_event as module

    module.get_personality_engine_service = lambda: _DummyService()  # type: ignore[method-assign]

    params: dict[str, Any] = {
        "stream_id": "sid_scan",
        "context": SimpleNamespace(
            chat_type="private",
            platform="qq",
            stream_name="测试流",
        ),
    }
    decision, out = asyncio.run(handler.execute("on_chatter_step_result", params))
    assert decision is EventDecision.SUCCESS
    assert out["stream_id"] == "sid_scan"

