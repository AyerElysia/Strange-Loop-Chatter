"""life_engine 兼容工具暴露测试。"""

from __future__ import annotations

from plugins.life_engine.core.compat_tools import (
    LifeConsultNucleusTool,
    LifeMessageNucleusTool,
    LifeScheduleFollowupMessageAction,
    LifeRetrieveMemoryTool,
    LifeSearchLifeMemoryTool,
    LifeThinkAction,
)
from plugins.life_engine.core.config import LifeEngineConfig
from plugins.life_engine.core.plugin import LifeEnginePlugin


def test_life_engine_exposes_compat_tools_when_chatter_enabled() -> None:
    """启用 life_chatter 时应暴露兼容工具层。"""
    config = LifeEngineConfig()
    config.chatter.enabled = True
    plugin = LifeEnginePlugin(config=config)

    component_names = {getattr(comp, "__name__", "") for comp in plugin.get_components()}

    assert "LifeThinkAction" in component_names
    assert "LifeMessageNucleusTool" in component_names
    assert "LifeConsultNucleusTool" in component_names
    assert "LifeSearchLifeMemoryTool" in component_names
    assert "LifeRetrieveMemoryTool" in component_names
    assert "LifeScheduleFollowupMessageAction" in component_names
