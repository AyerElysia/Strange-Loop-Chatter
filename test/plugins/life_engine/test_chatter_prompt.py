"""life_engine 对话提示词与叙事测试。"""

from __future__ import annotations

from types import SimpleNamespace

from plugins.life_engine.core.chatter import LifeChatter
from plugins.life_engine.tools.file_tools import LifeEngineWakeDFCTool


def test_life_chatter_prompt_states_single_subject_runtime_modes() -> None:
    """LifeChatter 提示词应明确只有一个主体、多个运行模式。"""
    chat_stream = SimpleNamespace(
        bot_nickname="Neo",
        bot_id="bot-1",
        platform="qq",
        chat_type="private",
        stream_id="stream-1",
    )

    prompt = LifeChatter._build_fixed_chat_framework(chat_stream)

    assert "同一个主体" in prompt
    assert "运行模式" in prompt
    assert "不是两个意识体" in prompt


def test_tell_dfc_tool_description_frames_as_runtime_mode_sync() -> None:
    """nucleus_tell_dfc 的叙事应指向运行模式同步，而不是双意识。"""
    description = LifeEngineWakeDFCTool.tool_description

    assert "对外运行模式" in description
    assert "两个意识体" not in description
