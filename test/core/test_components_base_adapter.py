"""测试 BaseAdapter 类。"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, Mock, MagicMock, patch

import pytest

from src.core.components import BaseAdapter
from src.core.components import BasePlugin


class TestAdapter(BaseAdapter):
    """测试用的适配器实现。"""

    adapter_name = "test_adapter"
    adapter_version = "1.0.0"
    adapter_description = "Test adapter"
    platform = "test_platform"

    async def from_platform_message(self, raw: Any):
        """解析平台消息。"""
        from mofox_wire import MessageEnvelope, MessageDirection

        return MessageEnvelope(
            direction=MessageDirection.UPWARD,
            message_info={
                "platform": self.platform,
                "user_id": raw.get("user_id", "test_user"),
                "message_id": raw.get("message_id", "test_msg_id"),
            },
            message_segment=[{"type": "text", "data": raw.get("content", "test content")}],
            raw_message=raw,
        )

    async def _send_platform_message(self, envelope) -> None:
        """发送消息到平台。"""
        # 测试实现
        pass

    # 重写父类方法以避免实际调用
    async def _parent_start(self) -> None:
        """Mock 父类 start。"""
        pass

    async def _parent_stop(self) -> None:
        """Mock 父类 stop。"""
        pass

    def is_connected(self) -> bool:
        """Mock 连接状态。"""
        return True


class TestBaseAdapter:
    """测试 BaseAdapter 基类。"""

    def test_adapter_class_attributes(self):
        """测试适配器类属性。"""
        assert TestAdapter.adapter_name == "test_adapter"
        assert TestAdapter.adapter_version == "1.0.0"
        assert TestAdapter.adapter_description == "Test adapter"
        assert TestAdapter.platform == "test_platform"
        assert TestAdapter.dependencies == []

    def test_get_signature_without_plugin_name(self):
        """测试未设置插件名称时获取签名。"""
        signature = TestAdapter.get_signature()
        assert signature is None

    def test_get_signature_with_plugin_name(self):
        """测试设置插件名称后获取签名。"""
        TestAdapter._plugin_ = "test_plugin"
        signature = TestAdapter.get_signature()
        assert signature == "test_plugin:adapter:test_adapter"
        # 重置
        TestAdapter._plugin_ = "unknown_plugin"

    def test_adapter_initialization(self):
        """测试适配器初始化。"""
        mock_sink = MagicMock()
        mock_plugin = MagicMock(spec=BasePlugin)

        adapter = TestAdapter(core_sink=mock_sink, plugin=mock_plugin)

        assert adapter.plugin == mock_plugin
        assert adapter._health_check_task_info is None
        assert adapter._running is False

    @pytest.mark.asyncio
    async def test_adapter_start(self):
        """测试适配器启动。"""
        mock_sink = MagicMock()
        adapter = TestAdapter(core_sink=mock_sink)

        # Mock 父类 start 和 get_task_manager
        with patch("src.kernel.concurrency.task_manager.get_task_manager") as mock_tm:
            mock_task_info = MagicMock()
            mock_task_info.task_id = "test_task_id"
            mock_tm_instance = MagicMock()
            mock_tm_instance.create_task.return_value = mock_task_info
            mock_tm.return_value = mock_tm_instance

            # Mock 父类 start
            with patch("mofox_wire.AdapterBase.start", new_callable=AsyncMock):
                await adapter.start()

                assert adapter._running is True
                assert adapter._health_check_task_info is not None

    @pytest.mark.asyncio
    async def test_adapter_stop(self):
        """测试适配器停止。"""
        mock_sink = MagicMock()
        adapter = TestAdapter(core_sink=mock_sink)
        adapter._running = True
        adapter._health_check_task_info = MagicMock()

        # Mock get_task_manager
        with patch("src.kernel.concurrency.task_manager.get_task_manager") as mock_tm:
            mock_tm_instance = MagicMock()
            mock_tm.return_value = mock_tm_instance

            # Mock 父类 stop
            with patch("mofox_wire.AdapterBase.stop", new_callable=AsyncMock):
                await adapter.stop()

                assert adapter._running is False
                assert adapter._health_check_task_info is None

    @pytest.mark.asyncio
    async def test_on_adapter_loaded_hook(self):
        """测试适配器加载钩子。"""
        mock_sink = MagicMock()
        adapter = TestAdapter(core_sink=mock_sink)

        # 默认实现应该不抛出异常
        await adapter.on_adapter_loaded()

    @pytest.mark.asyncio
    async def test_on_adapter_unloaded_hook(self):
        """测试适配器卸载钩子。"""
        mock_sink = MagicMock()
        adapter = TestAdapter(core_sink=mock_sink)

        # 默认实现应该不抛出异常
        await adapter.on_adapter_unloaded()

    @pytest.mark.asyncio
    async def test_health_check_default(self):
        """测试默认健康检查。"""
        mock_sink = MagicMock()
        adapter = TestAdapter(core_sink=mock_sink)

        # Mock is_connected 方法
        adapter.is_connected = Mock(return_value=True)

        result = await adapter.health_check()
        assert result is True

    @pytest.mark.asyncio
    async def test_health_check_loop(self):
        """测试健康检查循环。"""
        mock_sink = MagicMock()
        adapter = TestAdapter(core_sink=mock_sink)
        adapter._running = True

        call_count = [0]

        async def mock_sleep(interval):
            call_count[0] += 1
            if call_count[0] >= 2:
                raise asyncio.CancelledError()

        with patch("asyncio.sleep", mock_sleep):
            # 由于 TestAdapter 重写了 is_connected 返回 True
            # health_check 应该返回 True，不会触发 reconnect
            await adapter._health_check_loop()

        # 测试通过，没有异常

    @pytest.mark.asyncio
    async def test_health_check_loop_triggers_reconnect(self):
        """测试健康检查失败时触发重连。"""
        mock_sink = MagicMock()
        adapter = TestAdapter(core_sink=mock_sink)
        adapter._running = True

        call_count = [0]

        async def mock_sleep(interval):
            call_count[0] += 1
            if call_count[0] >= 2:
                raise asyncio.CancelledError()

        # Mock health_check 返回 False
        with patch("asyncio.sleep", mock_sleep):
            with patch.object(adapter, "health_check", new_callable=AsyncMock, return_value=False):
                with patch.object(adapter, "reconnect", new_callable=AsyncMock) as mock_reconnect:
                    await adapter._health_check_loop()

                    # 确保重连被调用至少一次
                    assert mock_reconnect.call_count >= 1

    @pytest.mark.asyncio
    async def test_reconnect_default(self):
        """测试默认重连逻辑。"""
        mock_sink = MagicMock()
        adapter = TestAdapter(core_sink=mock_sink)

        # Mock stop 和 start 方法
        adapter.stop = AsyncMock()
        adapter.start = AsyncMock()

        await adapter.reconnect()

        adapter.stop.assert_called_once()
        adapter.start.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_platform_message_not_implemented(self):
        """测试未实现发送消息方法时抛出异常。"""
        mock_sink = MagicMock()

        # 创建一个没有实现 _send_platform_message 的适配器
        class IncompleteAdapter(BaseAdapter):
            adapter_name = "incomplete"
            platform = "test"

            async def from_platform_message(self, raw):
                pass

        adapter = IncompleteAdapter(core_sink=mock_sink)
        mock_envelope = MagicMock()

        with pytest.raises(NotImplementedError):
            await adapter._send_platform_message(mock_envelope)

    @pytest.mark.asyncio
    async def test_send_platform_message_with_transport_config(self):
        """测试有传输配置时发送消息。"""
        mock_sink = MagicMock()
        adapter = TestAdapter(core_sink=mock_sink)

        # 设置传输配置
        adapter._transport_config = {"test": "config"}

        # 由于 TestAdapter 实现了 _send_platform_message，这里不会抛出异常
        mock_envelope = MagicMock()
        await adapter._send_platform_message(mock_envelope)
        # 测试通过，没有抛出异常


class CustomAdapterWithHooks(TestAdapter):
    """带有自定义钩子的测试适配器。"""

    loaded_called = False
    unloaded_called = False

    async def on_adapter_loaded(self) -> None:
        """自定义加载钩子。"""
        CustomAdapterWithHooks.loaded_called = True
        await super().on_adapter_loaded()

    async def on_adapter_unloaded(self) -> None:
        """自定义卸载钩子。"""
        CustomAdapterWithHooks.unloaded_called = True
        await super().on_adapter_unloaded()


class TestAdapterHooks:
    """测试适配器生命周期钩子。"""

    @pytest.mark.asyncio
    async def test_custom_on_adapter_loaded(self):
        """测试自定义加载钩子被调用。"""
        mock_sink = MagicMock()
        adapter = CustomAdapterWithHooks(core_sink=mock_sink)

        CustomAdapterWithHooks.loaded_called = False

        await adapter.on_adapter_loaded()

        assert CustomAdapterWithHooks.loaded_called is True

    @pytest.mark.asyncio
    async def test_custom_on_adapter_unloaded(self):
        """测试自定义卸载钩子被调用。"""
        mock_sink = MagicMock()
        adapter = CustomAdapterWithHooks(core_sink=mock_sink)

        CustomAdapterWithHooks.unloaded_called = False

        await adapter.on_adapter_unloaded()

        assert CustomAdapterWithHooks.unloaded_called is True


class CustomAdapterWithHealthCheck(TestAdapter):
    """带有自定义健康检查的测试适配器。"""

    async def health_check(self) -> bool:
        """自定义健康检查。"""
        # 模拟检查连接状态
        return True


class TestAdapterHealthCheck:
    """测试适配器健康检查功能。"""

    @pytest.mark.asyncio
    async def test_custom_health_check(self):
        """测试自定义健康检查方法。"""
        mock_sink = MagicMock()
        adapter = CustomAdapterWithHealthCheck(core_sink=mock_sink)

        result = await adapter.health_check()
        assert result is True


class CustomAdapterWithReconnect(TestAdapter):
    """带有自定义重连逻辑的测试适配器。"""

    reconnect_called = False

    async def reconnect(self) -> None:
        """自定义重连逻辑。"""
        CustomAdapterWithReconnect.reconnect_called = True
        await super().reconnect()


class TestAdapterReconnect:
    """测试适配器重连功能。"""

    @pytest.mark.asyncio
    async def test_custom_reconnect(self):
        """测试自定义重连方法。"""
        mock_sink = MagicMock()
        adapter = CustomAdapterWithReconnect(core_sink=mock_sink)

        # Mock stop 和 start
        adapter.stop = AsyncMock()
        adapter.start = AsyncMock()

        CustomAdapterWithReconnect.reconnect_called = False

        await adapter.reconnect()

        assert CustomAdapterWithReconnect.reconnect_called is True
        adapter.stop.assert_called_once()
        adapter.start.assert_called_once()
