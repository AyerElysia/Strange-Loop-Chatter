"""CollectionManager 单元测试。

覆盖行为：
- Collection 内部组件默认不可用（INACTIVE）
- 仅当 Collection 被解包（unpack）后，内部组件才会被激活（ACTIVE）
- 跨插件引用的组件同样会被 collection 门控
- Chatter 获取 LLMUsable 时会尊重 StateManager 状态
- Collection 解包/激活按 stream_id 隔离；repack 会恢复初始门控状态
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.core.components.base.action import BaseAction
from src.core.components.base.collection import BaseCollection
from src.core.components import BasePlugin
from src.core.components.base.tool import BaseTool
from src.core.components.base.chatter import BaseChatter
from src.core.components.registry import ComponentRegistry
from src.core.components.state_manager import get_global_state_manager
from src.core.components.types import ComponentState
from src.core.managers.collection_manager import CollectionManager


class TestPlugin(BasePlugin):
    plugin_name = "test_plugin"

    def __init__(self) -> None:
        pass

    def get_components(self) -> list[type]:
        return []


class ExternalPlugin(BasePlugin):
    plugin_name = "other_plugin"

    def __init__(self) -> None:
        pass

    def get_components(self) -> list[type]:
        return []


class A1(BaseAction):
    action_name = "a1"
    action_description = "a1"

    async def execute(self, *args, **kwargs):
        return True, "ok"


class T1(BaseTool):
    tool_name = "t1"
    tool_description = "t1"

    async def execute(self, *args, **kwargs):
        return True, "ok"


class ExternalTool(BaseTool):
    tool_name = "ext"
    tool_description = "ext"

    async def execute(self, *args, **kwargs):
        return True, "ok"


class C1(BaseCollection):
    collection_name = "c1"
    collection_description = "c1"

    async def get_contents(self) -> list[str]:
        return [
            "test_plugin:action:a1",
            "test_plugin:tool:t1",
        ]


class C2CrossPlugin(BaseCollection):
    collection_name = "c2"
    collection_description = "c2"

    async def get_contents(self) -> list[str]:
        return [
            "test_plugin:action:a1",
            "other_plugin:tool:ext",
        ]


class DummyChatter(BaseChatter):
    chatter_name = "dummy"
    chatter_description = "dummy"

    async def execute(self, unreads):
        if False:
            yield None  # pragma: no cover
        return


@pytest.fixture(autouse=True)
def _clear_state_manager():
    manager = get_global_state_manager()
    manager.clear()
    yield
    manager.clear()


def _build_registry_for_c1() -> ComponentRegistry:
    registry = ComponentRegistry()
    registry.register(A1, "test_plugin:action:a1")
    registry.register(T1, "test_plugin:tool:t1")
    registry.register(C1, "test_plugin:collection:c1")
    return registry


def _build_registry_for_cross_plugin() -> ComponentRegistry:
    registry = ComponentRegistry()
    registry.register(A1, "test_plugin:action:a1")
    registry.register(ExternalTool, "other_plugin:tool:ext")
    registry.register(C2CrossPlugin, "test_plugin:collection:c2")
    return registry


@pytest.mark.asyncio
async def test_seal_then_unpack_activates_components():
    registry = _build_registry_for_c1()
    state_manager = get_global_state_manager()

    # 模拟插件加载后：组件初始为 ACTIVE（门控不再改写 ComponentState）
    state_manager.set_state("test_plugin:action:a1", ComponentState.ACTIVE)
    state_manager.set_state("test_plugin:tool:t1", ComponentState.ACTIVE)
    state_manager.set_state("test_plugin:collection:c1", ComponentState.ACTIVE)

    plugin = TestPlugin()
    manager = CollectionManager()

    with patch("src.core.managers.collection_manager.get_global_registry") as mock_get:
        mock_get.return_value = registry

        gated = await manager.seal_collection_components("test_plugin:collection:c1", plugin=plugin)
        assert set(gated) == {"test_plugin:action:a1", "test_plugin:tool:t1"}

        stream_id = "s1"
        assert manager.is_component_available("test_plugin:action:a1", stream_id) is False
        assert manager.is_component_available("test_plugin:tool:t1", stream_id) is False

        # 解包后应解除门控并激活
        unpacked = await manager.unpack_collection(
            "test_plugin:collection:c1",
            plugin=plugin,
            stream_id=stream_id,
        )
        assert A1 in unpacked
        assert T1 in unpacked

        assert manager.is_component_available("test_plugin:action:a1", stream_id) is True
        assert manager.is_component_available("test_plugin:tool:t1", stream_id) is True

        # 门控关系是全局静态的（不会被解包清空），可用性由 stream 的“已解包集合”决定
        # 优化后门控关系存储在 CollectionManager._gate_sets 中（O(1) 查找）
        assert manager._get_gate_set("test_plugin:action:a1") == {
            "test_plugin:collection:c1"
        }
        assert manager._get_gate_set("test_plugin:tool:t1") == {
            "test_plugin:collection:c1"
        }


@pytest.mark.asyncio
async def test_cross_plugin_component_not_sealed():
    registry = _build_registry_for_cross_plugin()
    state_manager = get_global_state_manager()

    state_manager.set_state("test_plugin:action:a1", ComponentState.ACTIVE)
    state_manager.set_state("other_plugin:tool:ext", ComponentState.ACTIVE)
    state_manager.set_state("test_plugin:collection:c2", ComponentState.ACTIVE)

    plugin = TestPlugin()
    manager = CollectionManager()

    with patch("src.core.managers.collection_manager.get_global_registry") as mock_get:
        mock_get.return_value = registry

        gated = await manager.seal_collection_components("test_plugin:collection:c2", plugin=plugin)
        assert set(gated) == {"test_plugin:action:a1", "other_plugin:tool:ext"}

        # 跨插件引用同样会被门控（按 stream 隔离）
        assert manager.is_component_available("test_plugin:action:a1", "s1") is False
        assert manager.is_component_available("other_plugin:tool:ext", "s1") is False


@pytest.mark.asyncio
async def test_collection_execute_triggers_unpack_and_activation():
    registry = _build_registry_for_c1()
    state_manager = get_global_state_manager()

    state_manager.set_state("test_plugin:action:a1", ComponentState.ACTIVE)
    state_manager.set_state("test_plugin:tool:t1", ComponentState.ACTIVE)
    state_manager.set_state("test_plugin:collection:c1", ComponentState.ACTIVE)

    plugin = TestPlugin()
    manager = CollectionManager()

    with patch("src.core.managers.collection_manager.get_global_registry") as mock_get:
        mock_get.return_value = registry

        # 先门控
        await manager.seal_collection_components("test_plugin:collection:c1", plugin=plugin)

        stream_id = "s1"
        assert manager.is_component_available("test_plugin:action:a1", stream_id) is False
        assert manager.is_component_available("test_plugin:tool:t1", stream_id) is False

        # 调用 collection.execute() 应触发解包并激活
        collection_instance = C1(plugin=plugin)
        with patch("src.core.managers.collection_manager.get_collection_manager") as mock_mgr:
            mock_mgr.return_value = manager
            ok, payload = await collection_instance.execute(stream_id=stream_id)
        assert ok is True
        assert payload.get("components_count") == 2

        assert manager.is_component_available("test_plugin:action:a1", stream_id) is True
        assert manager.is_component_available("test_plugin:tool:t1", stream_id) is True


@pytest.mark.asyncio
async def test_stream_isolation_and_repack_restore_initial_state():
    registry = _build_registry_for_c1()
    state_manager = get_global_state_manager()

    state_manager.set_state("test_plugin:action:a1", ComponentState.ACTIVE)
    state_manager.set_state("test_plugin:tool:t1", ComponentState.ACTIVE)
    state_manager.set_state("test_plugin:collection:c1", ComponentState.ACTIVE)

    plugin = TestPlugin()
    manager = CollectionManager()

    with patch("src.core.managers.collection_manager.get_global_registry") as mock_get:
        mock_get.return_value = registry

        await manager.seal_collection_components("test_plugin:collection:c1", plugin=plugin)

        # 两个 stream 初始都不可用
        assert manager.is_component_available("test_plugin:action:a1", "s1") is False
        assert manager.is_component_available("test_plugin:action:a1", "s2") is False

        # 仅在 s1 解包后，s1 可用，s2 不受影响
        await manager.unpack_collection("test_plugin:collection:c1", plugin=plugin, stream_id="s1")
        assert manager.is_component_available("test_plugin:action:a1", "s1") is True
        assert manager.is_component_available("test_plugin:action:a1", "s2") is False

        # repack s1 后恢复初始门控状态
        manager.repack("s1")
        assert manager.is_component_available("test_plugin:action:a1", "s1") is False


@pytest.mark.asyncio
async def test_chatter_get_llm_usables_respects_state():
    plugin = TestPlugin()

    # 直接给 plugin 注入组件列表，模拟真实插件返回
    plugin.get_components = lambda: [A1, T1, C1]  # type: ignore[method-assign]

    state_manager = get_global_state_manager()
    state_manager.set_state("test_plugin:action:a1", ComponentState.INACTIVE)
    state_manager.set_state("test_plugin:tool:t1", ComponentState.ACTIVE)
    state_manager.set_state("test_plugin:collection:c1", ComponentState.ACTIVE)

    # 需要 signature 才会被识别为 LLMUsable
    A1._signature_ = "test_plugin:action:a1"  # type: ignore[attr-defined]
    T1._signature_ = "test_plugin:tool:t1"  # type: ignore[attr-defined]
    C1._signature_ = "test_plugin:collection:c1"  # type: ignore[attr-defined]

    # 门控：先把 c1 置为“未解包默认不可用”
    manager = CollectionManager()
    registry = _build_registry_for_c1()
    with patch("src.core.managers.collection_manager.get_global_registry") as mock_get:
        mock_get.return_value = registry
        await manager.seal_collection_components("test_plugin:collection:c1", plugin=plugin)

    # 让 chatter 内部使用我们的 manager（包含该测试的 stream 解包状态）
    with patch("src.core.components.base.chatter_collection_manager") as mock_mgr:
        mock_mgr.return_value = manager

        chatter = DummyChatter(stream_id="s1", plugin=plugin)
        usables = await chatter.get_llm_usables()

    # A1 被全局 INACTIVE 禁用；T1 虽 ACTIVE 但被门控（未解包）也不可见
    assert A1 not in usables
    assert T1 not in usables
    assert C1 in usables
