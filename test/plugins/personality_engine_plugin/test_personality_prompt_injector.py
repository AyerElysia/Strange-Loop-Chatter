"""personality prompt 注入测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from plugins.personality_engine_plugin.components.events.personality_prompt_injector import (
    PersonalityPromptInjector,
)
from plugins.personality_engine_plugin.config import PersonalityEngineConfig
from src.kernel.event import EventDecision


def test_personality_injects_into_system_prompt_tail() -> None:
    """人格态应注入 system prompt 的 extra_info 区块。"""

    config = PersonalityEngineConfig()
    config.plugin.enabled = True
    config.plugin.inject_prompt = True
    config.prompt.target_prompt_names = ["default_chatter_system_prompt"]

    handler = PersonalityPromptInjector(plugin=SimpleNamespace(config=config))
    block = "【人格态】\n- 当前类型：INTJ（Ni-Te）"

    class _DummyService:
        def render_prompt_block(
            self,
            *,
            stream_id: str,
            chat_type: str | None = None,
        ) -> str:
            assert stream_id == "sid_personality"
            assert chat_type == "group"
            return block

    import plugins.personality_engine_plugin.components.events.personality_prompt_injector as module

    module.get_personality_engine_service = lambda: _DummyService()  # type: ignore[method-assign]

    params: dict[str, Any] = {
        "name": "default_chatter_system_prompt",
        "template": "{extra_info}\n{personality}",
        "values": {
            "stream_id": "sid_personality",
            "chat_type": "group",
            "extra_info": "keep",
            "extra": "leave_me",
        },
        "policies": {},
        "strict": False,
    }

    decision, out = asyncio.run(handler.execute("on_prompt_build", params))

    assert decision is EventDecision.SUCCESS
    assert out["values"]["extra_info"] == "keep\n\n" + block
    assert out["values"]["extra"] == "leave_me"

