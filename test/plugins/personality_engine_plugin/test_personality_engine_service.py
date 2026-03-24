"""personality_engine_plugin service 测试。"""

from __future__ import annotations

import asyncio

from plugins.personality_engine_plugin.config import PersonalityEngineConfig
from plugins.personality_engine_plugin.service import PersonalityEngineService


def test_advance_step_can_change_mbti_with_reflection(tmp_path) -> None:
    """当补偿功能超过主导阈值时，应触发 MBTI 结构变化。"""

    config = PersonalityEngineConfig()
    config.storage.base_path = str(tmp_path)
    plugin = type("Plugin", (), {"config": config})()
    service = PersonalityEngineService(plugin)

    state = service.get_state(stream_id="sid_reflect", chat_type="private")
    state.mbti = "INTJ"  # main=Ni, aux=Te
    state.weights = {
        "Ti": 0.0,
        "Te": 0.2,
        "Fi": 0.0,
        "Fe": 0.0,
        "Ni": 0.3,
        "Ne": 0.0,
        "Si": 0.5,
        "Se": 0.0,
    }
    state.change_history = {k: 0.0 for k in state.weights}
    service._save_state(state)

    async def _fake_selector(*, trigger: str, state, recent_messages: str):
        del trigger, state, recent_messages
        return "Si", "test", "test_hypothesis"

    service._select_function_with_llm = _fake_selector  # type: ignore[method-assign]

    ok, _ = asyncio.run(
        service.advance_personality_step(
            stream_id="sid_reflect",
            chat_type="private",
            trigger="unit_test",
        )
    )

    assert ok is True
    latest = service.get_state(stream_id="sid_reflect", chat_type="private")
    assert latest.mbti == "ISTJ"
    assert latest.last_selected_function == "Si"
    assert latest.current_hypothesis == "test_hypothesis"
    assert len(latest.history) == 1
    assert latest.history[0].old_mbti == "INTJ"
    assert latest.history[0].new_mbti == "ISTJ"


def test_advance_step_falls_back_to_main_function_when_no_context(tmp_path) -> None:
    """无上下文且禁用 LLM 选择时，应回退到主导功能。"""

    config = PersonalityEngineConfig()
    config.storage.base_path = str(tmp_path)
    config.model.enable_llm_selector = False
    plugin = type("Plugin", (), {"config": config})()
    service = PersonalityEngineService(plugin)

    ok, _ = asyncio.run(
        service.advance_personality_step(
            stream_id="sid_fallback",
            chat_type="private",
            trigger="unit_test",
        )
    )
    assert ok is True

    state = service.get_state(stream_id="sid_fallback", chat_type="private")
    # default_mbti=INTJ, main=Ni
    assert state.last_selected_function == "Ni"


def test_advance_step_uses_llm_reflection_decision_when_available(tmp_path) -> None:
    """当反思 LLM 给出 judgment=yes 时，应按其决策应用结构变化。"""

    config = PersonalityEngineConfig()
    config.storage.base_path = str(tmp_path)
    config.model.enable_llm_selector = True
    config.model.enable_llm_reflection = True
    plugin = type("Plugin", (), {"config": config})()
    service = PersonalityEngineService(plugin)

    state = service.get_state(stream_id="sid_llm_reflect", chat_type="private")
    state.mbti = "INTJ"  # main=Ni, aux=Te
    state.weights = {
        "Ti": 0.05,
        "Te": 0.22,
        "Fi": 0.05,
        "Fe": 0.05,
        "Ni": 0.30,
        "Ne": 0.05,
        "Si": 0.50,
        "Se": 0.05,
    }
    state.change_history = {k: 0.0 for k in state.weights}
    service._save_state(state)

    async def _fake_selector(*, trigger: str, state, recent_messages: str):
        del trigger, state, recent_messages
        return "Si", "test", "llm_hypothesis"

    async def _fake_reflect_with_llm(
        *,
        action: str,
        trigger: str,
        state,
        selected_function: str,
        recent_messages: str,
    ):
        del trigger, state, recent_messages
        assert action == "change_main"
        assert selected_function == "Si"
        return {
            "judgment": "yes",
            "reason": "频繁使用 Si，主导发生变化",
            "main_weight": 0.35,
            "ori_main_weight": 0.12,
        }

    service._select_function_with_llm = _fake_selector  # type: ignore[method-assign]
    service._reflect_with_llm = _fake_reflect_with_llm  # type: ignore[method-assign]

    ok, _ = asyncio.run(
        service.advance_personality_step(
            stream_id="sid_llm_reflect",
            chat_type="private",
            trigger="unit_test",
        )
    )
    assert ok is True

    latest = service.get_state(stream_id="sid_llm_reflect", chat_type="private")
    assert latest.mbti == "ISTJ"
    assert latest.last_selected_function == "Si"
    assert latest.current_hypothesis == "llm_hypothesis"
    assert len(latest.history) == 1
    assert latest.history[0].old_mbti == "INTJ"
    assert latest.history[0].new_mbti == "ISTJ"
