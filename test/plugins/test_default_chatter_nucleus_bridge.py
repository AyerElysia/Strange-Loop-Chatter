"""default_chatter 与 life_engine 异步对话桥测试。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from plugins.default_chatter.config import DefaultChatterConfig
from plugins.default_chatter.nucleus_bridge import MessageNucleusTool
from plugins.default_chatter.plugin import DefaultChatter, DefaultChatterPlugin
from src.kernel.llm import ToolRegistry


class _FakeResponse:
    """最小响应对象。"""

    def __init__(self) -> None:
        self.payloads: list[Any] = []

    def add_payload(self, payload: Any) -> None:
        self.payloads.append(payload)


def _build_chatter() -> DefaultChatter:
    config = DefaultChatterConfig.from_dict({"plugin": {"enabled": True, "mode": "enhanced"}})
    plugin = DefaultChatterPlugin(config=config)
    return DefaultChatter(stream_id="stream-default", plugin=plugin)


@pytest.mark.asyncio
async def test_message_nucleus_tool_queues_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """桥接工具应把消息异步投递给 life_engine。"""
    captured: dict[str, Any] = {}

    class _FakeLifeService:
        async def enqueue_dfc_message(self, **kwargs: Any) -> dict[str, Any]:
            captured.update(kwargs)
            return {"event_id": "dfc_msg_1", "queued": True}

    class _FakePluginManager:
        def get_plugin(self, name: str) -> Any:
            if name == "life_engine":
                return SimpleNamespace(service=_FakeLifeService())
            return None

    monkeypatch.setattr(
        "plugins.default_chatter.nucleus_bridge.get_plugin_manager",
        lambda: _FakePluginManager(),
    )

    tool = MessageNucleusTool(plugin=DefaultChatterPlugin(config=DefaultChatterConfig()))
    success, result = await tool.execute(
        message="帮我问问另一个我最近在想什么",
        stream_id="stream-1",
        platform="qq",
        chat_type="private",
        sender_name="Alice",
    )

    assert success is True
    assert "不要等待即时回复" in result
    assert captured == {
        "message": "帮我问问另一个我最近在想什么",
        "stream_id": "stream-1",
        "platform": "qq",
        "chat_type": "private",
        "sender_name": "Alice",
    }


@pytest.mark.asyncio
async def test_message_nucleus_tool_fails_when_life_engine_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """life_engine 缺失时应明确失败，不得伪造已送达。"""

    class _FakePluginManager:
        def get_plugin(self, _name: str) -> None:
            return None

    monkeypatch.setattr(
        "plugins.default_chatter.nucleus_bridge.get_plugin_manager",
        lambda: _FakePluginManager(),
    )

    tool = MessageNucleusTool(plugin=DefaultChatterPlugin(config=DefaultChatterConfig()))
    success, result = await tool.execute(message="你好")

    assert success is False
    assert "life_engine 未加载" in result


@pytest.mark.asyncio
async def test_default_chatter_run_tool_call_autofills_nucleus_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """运行桥接工具时应自动补齐当前对话上下文。"""
    chatter = _build_chatter()
    captured: dict[str, Any] = {}

    async def _fake_exec_llm_usable(_usable_cls: Any, _message: Any, **kwargs: Any) -> tuple[bool, str]:
        captured.update(kwargs)
        return True, "queued"

    monkeypatch.setattr(chatter, "exec_llm_usable", _fake_exec_llm_usable)

    registry = ToolRegistry()
    registry.register(MessageNucleusTool)
    response = _FakeResponse()
    trigger_msg = SimpleNamespace(
        stream_id="stream-42",
        platform="qq",
        chat_type="group",
        sender_name="Alice",
    )
    call = SimpleNamespace(
        id="call-1",
        name="tool-message_nucleus",
        args={"message": "替我问问另一个我"},
    )

    appended, exec_success = await chatter.run_tool_call(
        call=call,
        response=response,
        usable_map=registry,
        trigger_msg=trigger_msg,
    )

    assert appended is True
    assert exec_success is True
    assert captured["message"] == "替我问问另一个我"
    assert captured["stream_id"] == "stream-42"
    assert captured["platform"] == "qq"
    assert captured["chat_type"] == "group"
    assert captured["sender_name"] == "Alice"


def test_default_chatter_plugin_exposes_message_nucleus_tool() -> None:
    """插件组件列表应包含中枢桥接工具。"""
    plugin = DefaultChatterPlugin(config=DefaultChatterConfig())

    components = plugin.get_components()

    assert MessageNucleusTool in components
    assert MessageNucleusTool.chatter_allow == ["default_chatter"]
