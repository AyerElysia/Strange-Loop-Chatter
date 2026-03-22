"""prompt 尾部注入器测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from plugins.drive_core_plugin.components.events.drive_core_prompt_injector import (
    DriveCorePromptInjector,
)
from plugins.drive_core_plugin.config import DriveCoreConfig
from plugins.unfinished_thought_plugin.components.events.prompt_injector import (
    UnfinishedThoughtPromptInjector,
)
from plugins.unfinished_thought_plugin.config import UnfinishedThoughtConfig
from src.kernel.event import EventDecision


def test_unfinished_thought_injects_into_user_prompt() -> None:
    """未完成念头应注入 user prompt。"""

    config = UnfinishedThoughtConfig()
    config.plugin.enabled = True
    config.plugin.inject_prompt = True
    config.prompt.target_prompt_names = ["default_chatter_user_prompt"]

    handler = UnfinishedThoughtPromptInjector(plugin=SimpleNamespace(config=config))
    block = "## 未完成念头\n\n- [open] 刚刚的话题"

    class _DummyService:
        def render_prompt_block(self, stream_id: str, chat_type: str | None = None) -> str:
            assert stream_id == "sid_x"
            assert chat_type == "private"
            return block

    import plugins.unfinished_thought_plugin.components.events.prompt_injector as module

    module.get_unfinished_thought_service = lambda: _DummyService()  # type: ignore[method-assign]

    params: dict[str, Any] = {
        "name": "default_chatter_user_prompt",
        "template": "{extra}\n{unfinished_thought}",
        "values": {
            "stream_id": "sid_x",
            "chat_type": "private",
            "extra": "keep",
        },
        "policies": {},
        "strict": False,
    }

    decision, out = asyncio.run(handler.execute("on_prompt_build", params))

    assert decision is EventDecision.SUCCESS
    assert out["values"]["extra"] == "keep\n\n" + block


def test_drive_core_injects_into_system_prompt_tail() -> None:
    """内驱力应注入 system prompt 的 extra_info 区块。"""

    config = DriveCoreConfig()
    config.plugin.enabled = True
    config.plugin.inject_prompt = True
    config.prompt.target_prompt_names = ["default_chatter_system_prompt"]

    handler = DriveCorePromptInjector(plugin=SimpleNamespace(config=config))

    block = "【内驱力】\n- 主导倾向：好奇"

    class _DummyService:
        def render_prompt_block(
            self,
            stream_id: str,
            chat_type: str | None = None,
            *,
            platform: str = "",
            stream_name: str = "",
        ) -> str:
            assert stream_id == "sid_y"
            assert chat_type == "group"
            assert platform == "qq"
            assert stream_name == "测试群"
            return block

    import plugins.drive_core_plugin.components.events.drive_core_prompt_injector as module

    module.get_drive_core_service = lambda: _DummyService()  # type: ignore[method-assign]

    params: dict[str, Any] = {
        "name": "default_chatter_system_prompt",
        "template": "{extra_info}\n{drive_core}",
        "values": {
            "stream_id": "sid_y",
            "chat_type": "group",
            "platform": "qq",
            "stream_name": "测试群",
            "extra_info": "keep",
            "extra": "leave_me_alone",
        },
        "policies": {},
        "strict": False,
    }

    decision, out = asyncio.run(handler.execute("on_prompt_build", params))

    assert decision is EventDecision.SUCCESS
    assert out["values"]["extra_info"] == "keep\n\n" + block
    assert out["values"]["extra"] == "leave_me_alone"
