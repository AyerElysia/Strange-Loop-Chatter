"""proactive_message_plugin 消息发送归一化测试。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PLUGIN_ROOT = Path("/root/Elysia/Neo-MoFox/plugins")
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from plugins.proactive_message_plugin.plugin import ProactiveMessagePlugin


class _FakeSender:
    """最小化消息发送器。"""

    def __init__(self) -> None:
        self.messages = []

    async def send_message(self, message) -> bool:
        self.messages.append(message)
        return True


def test_send_message_normalizes_list_content_before_persisting(monkeypatch: pytest.MonkeyPatch) -> None:
    """列表内容应拆成多条字符串消息，避免 list 进入数据库层。"""
    fake_sender = _FakeSender()
    monkeypatch.setattr(
        "src.core.transport.message_send.get_message_sender",
        lambda: fake_sender,
    )

    plugin = ProactiveMessagePlugin()
    chat_stream = SimpleNamespace(
        stream_id="sid_001",
        platform="qq",
        chat_type="private",
        bot_id="bot_001",
        bot_nickname="爱莉",
    )

    ok = asyncio.run(
        plugin._send_message(
            chat_stream,
            ["  第一段  ", "", "第二段"],
        )
    )

    assert ok is True
    assert [msg.content for msg in fake_sender.messages] == ["第一段", "第二段"]
    assert [msg.processed_plain_text for msg in fake_sender.messages] == ["第一段", "第二段"]
    assert all(isinstance(msg.content, str) for msg in fake_sender.messages)
    assert all(isinstance(msg.processed_plain_text, str) for msg in fake_sender.messages)
