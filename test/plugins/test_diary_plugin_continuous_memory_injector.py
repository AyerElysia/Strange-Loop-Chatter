"""diary_plugin 连续记忆 prompt 注入测试。"""

from __future__ import annotations

import asyncio
from typing import Any
from types import SimpleNamespace

from plugins.diary_plugin.config import DiaryConfig
from plugins.diary_plugin.event_handler import (
    ContinuousMemoryPromptInjector,
    _PENDING_RUNTIME_USER_PROMPT_INJECTIONS,
    _push_runtime_user_prompt_injection,
)
from src.kernel.event import EventDecision


def test_continuous_memory_injects_into_dedicated_prompt_block() -> None:
    """连续记忆应注入到 system prompt 的 dedicated continuous_memory 区块。"""

    config = DiaryConfig()
    config.continuous_memory.enabled = True
    config.continuous_memory.inject_prompt = True
    config.continuous_memory.target_prompt_names = ["default_chatter_system_prompt"]

    handler = ContinuousMemoryPromptInjector(plugin=SimpleNamespace(config=config))

    block = "## 连续记忆\n\n- [L1] 已经存在的内容"

    class _DummyService:
        def render_continuous_memory_for_prompt(self, stream_id: str, chat_type: str | None = None) -> str:
            assert stream_id == "sid_x"
            assert chat_type == "private"
            return block

    handler._get_service = lambda: _DummyService()  # type: ignore[method-assign]

    params: dict[str, Any] = {
        "name": "default_chatter_system_prompt",
        "template": "{extra_info}\n{continuous_memory}",
        "values": {
            "stream_id": "sid_x",
            "chat_type": "private",
            "continuous_memory": "old",
            "extra_info": "keep",
        },
        "policies": {},
        "strict": False,
    }

    decision, out = asyncio.run(handler.execute("on_prompt_build", params))

    assert decision is EventDecision.SUCCESS
    assert out["values"]["continuous_memory"] == block
    assert out["values"]["extra_info"] == "keep"


def test_auto_diary_runtime_user_prompt_injection_is_one_shot() -> None:
    """自动日记摘要应只在下一次 user prompt 注入一次，然后清空。"""

    _PENDING_RUNTIME_USER_PROMPT_INJECTIONS.clear()
    config = DiaryConfig()
    config.auto_diary.inject_runtime_user_prompt_once = True
    config.auto_diary.runtime_user_prompt_target_names = ["default_chatter_user_prompt"]
    config.continuous_memory.enabled = False

    handler = ContinuousMemoryPromptInjector(plugin=SimpleNamespace(config=config))
    handler._get_service = lambda: None  # type: ignore[method-assign]

    _push_runtime_user_prompt_injection(
        "sid_once",
        "【自动日记摘要】测试内容\n使用提示：可将其视为前面对话的小总结，知道发生了什么即可，不必强制引用其中表述。",
    )

    params_once: dict[str, Any] = {
        "name": "default_chatter_user_prompt",
        "template": "{extra}",
        "values": {
            "stream_id": "sid_once",
            "chat_type": "private",
            "extra": "keep",
        },
        "policies": {},
        "strict": False,
    }
    decision_once, out_once = asyncio.run(handler.execute("on_prompt_build", params_once))
    assert decision_once is EventDecision.SUCCESS
    assert out_once["values"]["extra"] == (
        "keep\n【自动日记摘要】测试内容\n"
        "使用提示：可将其视为前面对话的小总结，知道发生了什么即可，不必强制引用其中表述。"
    )

    # 第二次应不再重复注入
    params_twice: dict[str, Any] = {
        "name": "default_chatter_user_prompt",
        "template": "{extra}",
        "values": {
            "stream_id": "sid_once",
            "chat_type": "private",
            "extra": "keep",
        },
        "policies": {},
        "strict": False,
    }
    decision_twice, out_twice = asyncio.run(handler.execute("on_prompt_build", params_twice))
    assert decision_twice is EventDecision.SUCCESS
    assert out_twice["values"]["extra"] == "keep"
