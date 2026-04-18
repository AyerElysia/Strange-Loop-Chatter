"""tts_voice_plugin.actions.tts_action 行为测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from plugins.tts_voice_plugin.actions.tts_action import TTSVoiceAction
from plugins.tts_voice_plugin.config import TTSVoiceConfig


def _build_action(*, always_available: bool) -> TTSVoiceAction:
    cfg = TTSVoiceConfig()
    cfg.components.action_always_available = always_available

    plugin = SimpleNamespace(config=cfg, tts_service=None)
    chat_stream = SimpleNamespace(
        stream_id="s1",
        context=SimpleNamespace(history_messages=[]),
    )
    return TTSVoiceAction(chat_stream=chat_stream, plugin=plugin)


@pytest.mark.asyncio
async def test_go_activate_returns_true_when_always_available_enabled(monkeypatch) -> None:
    """常驻可用开关开启时应直接激活，不走随机/关键词/LLM 判定。"""
    action = _build_action(always_available=True)

    async def _should_not_call(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("always_available=True 时不应调用动态激活分支")

    monkeypatch.setattr(action, "_random_activation", _should_not_call)
    monkeypatch.setattr(action, "_keyword_match", _should_not_call)
    monkeypatch.setattr(action, "_llm_judge_activation", _should_not_call)

    assert await action.go_activate() is True


@pytest.mark.asyncio
async def test_go_activate_uses_keyword_when_always_available_disabled(monkeypatch) -> None:
    """关闭常驻可用后，命中关键词应激活。"""
    action = _build_action(always_available=False)
    action._last_message = "我想听你用语音说一句晚安"

    async def _random_false(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return False

    async def _llm_false(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return False

    monkeypatch.setattr(action, "_random_activation", _random_false)
    monkeypatch.setattr(action, "_llm_judge_activation", _llm_false)

    assert await action.go_activate() is True


@pytest.mark.asyncio
async def test_go_activate_returns_false_when_all_conditions_miss(monkeypatch) -> None:
    """关闭常驻可用且无随机/关键词/LLM命中时应不激活。"""
    action = _build_action(always_available=False)
    action._last_message = "今天天气不错"

    async def _random_false(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return False

    async def _llm_false(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return False

    monkeypatch.setattr(action, "_random_activation", _random_false)
    monkeypatch.setattr(action, "_llm_judge_activation", _llm_false)

    assert await action.go_activate() is False
