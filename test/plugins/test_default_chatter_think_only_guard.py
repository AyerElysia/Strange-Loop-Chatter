"""default_chatter think-only 防护测试。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, cast

from plugins.default_chatter.runners import (
    _THINK_ONLY_RETRY_REMINDER,
    _is_think_only_calls,
    run_enhanced,
)
from src.core.components.base import Wait
from src.kernel.llm import LLMPayload, ROLE, Text


@dataclass
class _FakeCall:
    name: str
    args: dict[str, Any]
    id: str


class _FakeResponse:
    """最小 request/response 替身。"""

    def __init__(self) -> None:
        self.payloads: list[Any] = []
        self.message: str = ""
        self.call_list: list[Any] = []
        self.send_count: int = 0

    def add_payload(self, payload: Any) -> None:
        self.payloads.append(payload)

    async def send(self, *, stream: bool = False) -> "_FakeResponse":
        _ = stream
        self.send_count += 1
        if self.send_count == 1:
            self.call_list = [_FakeCall(name="action-think", args={}, id="c1")]
            self.message = ""
        elif self.send_count == 2:
            self.call_list = [
                _FakeCall(
                    name="action-send_text",
                    args={"content": "收到啦"},
                    id="c2",
                )
            ]
            self.message = ""
        else:
            self.call_list = []
            self.message = ""
        return self

    def __await__(self):  # type: ignore[no-untyped-def]
        async def _done() -> "_FakeResponse":
            return self

        return _done().__await__()


class _FakeToolRegistry:
    def get_all(self) -> list[Any]:
        return []

    def get(self, _name: str) -> Any:
        return None


class _FakeChatter:
    """满足 run_enhanced 所需接口的最小 chatter。"""

    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self._fetch_count = 0

    def create_request(
        self,
        task: str = "actor",
        request_name: str = "",
        max_context: int | None = None,
        with_reminder: str | None = None,
    ) -> _FakeResponse:
        _ = (task, request_name, max_context, with_reminder)
        return self._response

    async def _build_system_prompt(self, _chat_stream: Any) -> str:
        return "sys"

    def _build_enhanced_history_text(self, _chat_stream: Any) -> str:
        return "hist"

    async def inject_usables(self, _request: Any) -> _FakeToolRegistry:
        return _FakeToolRegistry()

    async def fetch_unreads(self, time_format: str = "%H:%M") -> tuple[str, list[Any]]:
        _ = time_format
        self._fetch_count += 1
        if self._fetch_count == 1:
            return "", [SimpleNamespace(message_id="m1")]
        return "", []

    def format_message_line(self, _msg: Any, _time_format: str = "%H:%M") -> str:
        return "line"

    async def _build_user_prompt(
        self,
        _chat_stream: Any,
        history_text: str,
        unread_lines: str,
        extra: str = "",
    ) -> str:
        _ = (history_text, unread_lines, extra)
        return "user"

    def _build_negative_behaviors_extra(self) -> str:
        return ""

    async def sub_agent(
        self,
        unreads_text: str,
        unread_msgs: list[Any],
        chat_stream: Any,
    ) -> dict[str, Any]:
        _ = (unreads_text, unread_msgs, chat_stream)
        return {"reason": "test", "should_respond": True}

    def _upsert_pending_unread_payload(
        self,
        response: _FakeResponse,
        formatted_content: Any,
    ) -> None:
        response.add_payload(LLMPayload(ROLE.USER, formatted_content))

    async def flush_unreads(self, unread_messages: list[Any]) -> int:
        return len(unread_messages)

    async def run_tool_call(
        self,
        call: _FakeCall,
        response: _FakeResponse,
        usable_map: _FakeToolRegistry,
        trigger_msg: Any,
    ) -> tuple[bool, bool]:
        _ = (call, response, usable_map, trigger_msg)
        return True, True


def test_is_think_only_calls() -> None:
    """应仅在全部调用都是 think 时返回 True。"""
    assert _is_think_only_calls([SimpleNamespace(name="action-think")]) is True
    assert _is_think_only_calls(
        [SimpleNamespace(name="plugin_x:action:think"), SimpleNamespace(name="think")]
    ) is True
    assert _is_think_only_calls([SimpleNamespace(name="action-think"), SimpleNamespace(name="action-send_text")]) is False
    assert _is_think_only_calls([]) is False


def test_run_enhanced_retries_when_only_think_called() -> None:
    """enhanced 模式遇到仅 think 调用时，应注入提醒并自动重试一轮。"""
    fake_response = _FakeResponse()
    chatter = _FakeChatter(fake_response)
    chat_stream = cast(Any, SimpleNamespace(stream_id="s1", context=SimpleNamespace(history_messages=[])))
    fake_logger = cast(
        Any,
        SimpleNamespace(
            info=lambda *_a, **_k: None,
            warning=lambda *_a, **_k: None,
            error=lambda *_a, **_k: None,
            debug=lambda *_a, **_k: None,
        ),
    )

    gen = run_enhanced(
        chatter=cast(Any, chatter),
        chat_stream=chat_stream,
        logger=fake_logger,
        pass_call_name="action-pass_and_wait",
        send_text_call_name="action-send_text",
        suspend_text="__SUSPEND__",
    )

    async def _next_item() -> Any:
        return await anext(gen)

    first = asyncio.run(_next_item())
    assert isinstance(first, Wait)
    assert fake_response.send_count == 2
    assert any(
        getattr(payload, "role", None) == ROLE.SYSTEM
        and _THINK_ONLY_RETRY_REMINDER in str(getattr(payload, "content", ""))
        for payload in fake_response.payloads
    )
