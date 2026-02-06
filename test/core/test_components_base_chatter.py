"""测试 src.core.components.base.chatter 模块。"""

from datetime import datetime
from typing import Generator
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from src.core.components.base.chatter import BaseChatter, ChatterResult, Failure, Success, Wait
from src.core.components.types import ChatType
from src.core.models.message import Message


class ConcreteChatter(BaseChatter):
    """具体的 Chatter 实现用于测试。"""

    chatter_name = "test_chatter"
    chatter_description = "Test chatter"
    associated_platforms = []
    chatter_allow = []
    chat_type = ChatType.ALL

    async def execute(self, unreads: list) -> Generator[ChatterResult, None, None]:
        """执行聊天器逻辑。"""
        if not unreads:
            yield Failure("没有新消息")
            return

        yield Wait("处理中")
        yield Success("处理完成", {"count": len(unreads)})


class TestChatterResultTypes:
    """测试 Chatter 结果类型。"""

    def test_wait_creation(self):
        """测试 Wait 创建。"""
        wait = Wait("等待原因")
        assert wait.reason == "等待原因"

    def test_success_creation(self):
        """测试 Success 创建。"""
        success = Success("成功消息")
        assert success.message == "成功消息"
        assert success.data is None

    def test_success_with_data(self):
        """测试带数据的 Success。"""
        data = {"key": "value", "count": 5}
        success = Success("成功", data)
        assert success.message == "成功"
        assert success.data == data

    def test_failure_creation(self):
        """测试 Failure 创建。"""
        failure = Failure("错误消息")
        assert failure.error == "错误消息"
        assert failure.exception is None

    def test_failure_with_exception(self):
        """测试带异常的 Failure。"""
        exception = ValueError("测试异常")
        failure = Failure("错误", exception)
        assert failure.error == "错误"
        assert failure.exception == exception


class TestBaseChatter:
    """测试 BaseChatter 类。"""

    def test_chatter_initialization(self, mock_plugin):
        """测试 Chatter 初始化。"""
        chatter = ConcreteChatter("stream_123", mock_plugin)
        assert chatter.stream_id == "stream_123"
        assert chatter.plugin == mock_plugin
        assert chatter.chatter_name == "test_chatter"
        assert chatter.chatter_description == "Test chatter"

    def test_get_signature(self, mock_plugin):
        """测试获取签名。"""
        chatter = ConcreteChatter("stream_123", mock_plugin)
        assert chatter.get_signature() is None

        ConcreteChatter._plugin_ = "my_plugin"
        chatter2 = ConcreteChatter("stream_456", mock_plugin)
        assert chatter2.get_signature() == "my_plugin:chatter:test_chatter"

    @pytest.mark.asyncio
    async def test_execute_with_messages(self, mock_plugin):
        """测试执行聊天器（有消息）。"""
        chatter = ConcreteChatter("stream_123", mock_plugin)

        # 模拟消息
        mock_message = MagicMock()
        unreads = [mock_message]

        results = []
        async for result in chatter.execute(unreads):
            results.append(result)

        assert len(results) == 2
        assert isinstance(results[0], Wait)
        assert results[0].reason == "处理中"
        assert isinstance(results[1], Success)
        assert results[1].message == "处理完成"

    @pytest.mark.asyncio
    async def test_execute_without_messages(self, mock_plugin):
        """测试执行聊天器（无消息）。"""
        chatter = ConcreteChatter("stream_123", mock_plugin)

        results = []
        async for result in chatter.execute([]):
            results.append(result)

        assert len(results) == 1
        assert isinstance(results[0], Failure)
        assert results[0].error == "没有新消息"

    @pytest.mark.asyncio
    async def test_execute_with_multiple_messages(self, mock_plugin):
        """测试执行聊天器（多条消息）。"""
        chatter = ConcreteChatter("stream_123", mock_plugin)

        # 创建多条消息
        unreads = [MagicMock() for _ in range(5)]

        results = []
        async for result in chatter.execute(unreads):
            results.append(result)

        assert len(results) == 2
        assert isinstance(results[1], Success)
        assert results[1].data == {"count": 5}


class TestChatterAttributes:
    """测试 Chatter 类属性。"""

    def test_chatter_with_all_attributes(self, mock_plugin):
        """测试带有所有属性的聊天器。"""
        from src.core.components.types import ChatType

        class FullChatter(BaseChatter):
            chatter_name = "full_chatter"
            chatter_description = "Full chatter description"
            associated_platforms = ["telegram", "discord"]
            chatter_allow = ["chatter1", "chatter2"]
            chat_type = ChatType.GROUP
            dependencies = ["other_plugin:service:memory"]

            async def execute(self, unreads: list) -> Generator[ChatterResult, None, None]:
                yield Success("done")

        chatter = FullChatter("stream_123", mock_plugin)
        assert chatter.chatter_name == "full_chatter"
        assert chatter.chatter_description == "Full chatter description"
        assert chatter.associated_platforms == ["telegram", "discord"]
        assert chatter.chatter_allow == ["chatter1", "chatter2"]
        assert chatter.chat_type == ChatType.GROUP
        assert chatter.dependencies == ["other_plugin:service:memory"]

    def test_different_chat_types(self, mock_plugin):
        """测试不同聊天类型。"""
        # 分别测试每种聊天类型
        class PrivateChatter(BaseChatter):
            chatter_name = "chatter_private"
            chat_type = ChatType.PRIVATE

            async def execute(self, unreads: list) -> Generator[ChatterResult, None, None]:
                yield Success("done")

        class GroupChatter(BaseChatter):
            chatter_name = "chatter_group"
            chat_type = ChatType.GROUP

            async def execute(self, unreads: list) -> Generator[ChatterResult, None, None]:
                yield Success("done")

        class DiscussChatter(BaseChatter):
            chatter_name = "chatter_discuss"
            chat_type = ChatType.DISCUSS

            async def execute(self, unreads: list) -> Generator[ChatterResult, None, None]:
                yield Success("done")

        class AllChatter(BaseChatter):
            chatter_name = "chatter_all"
            chat_type = ChatType.ALL

            async def execute(self, unreads: list) -> Generator[ChatterResult, None, None]:
                yield Success("done")

        # 测试每种类型
        assert PrivateChatter("stream_123", mock_plugin).chat_type == ChatType.PRIVATE
        assert GroupChatter("stream_123", mock_plugin).chat_type == ChatType.GROUP
        assert DiscussChatter("stream_123", mock_plugin).chat_type == ChatType.DISCUSS
        assert AllChatter("stream_123", mock_plugin).chat_type == ChatType.ALL


class TestChatterExecutePatterns:
    """测试 Chatter 执行模式。"""

    @pytest.mark.asyncio
    async def test_multiple_waits(self, mock_plugin):
        """测试多个 Wait。"""
        class MultiWaitChatter(BaseChatter):
            chatter_name = "multi_wait"

            async def execute(self, unreads: list) -> Generator[ChatterResult, None, None]:
                yield Wait("步骤1")
                yield Wait("步骤2")
                yield Wait("步骤3")
                yield Success("完成")

        chatter = MultiWaitChatter("stream_123", mock_plugin)

        results = []
        async for result in chatter.execute([MagicMock()]):
            results.append(result)

        assert len(results) == 4
        assert all(isinstance(r, Wait) for r in results[:3])
        assert results[0].reason == "步骤1"
        assert results[1].reason == "步骤2"
        assert results[2].reason == "步骤3"
        assert isinstance(results[3], Success)

    @pytest.mark.asyncio
    async def test_immediate_success(self, mock_plugin):
        """测试立即成功。"""
        class ImmediateSuccessChatter(BaseChatter):
            chatter_name = "immediate_success"

            async def execute(self, unreads: list) -> Generator[ChatterResult, None, None]:
                yield Success("立即完成")

        chatter = ImmediateSuccessChatter("stream_123", mock_plugin)

        results = []
        async for result in chatter.execute([MagicMock()]):
            results.append(result)

        assert len(results) == 1
        assert isinstance(results[0], Success)
        assert results[0].message == "立即完成"

    @pytest.mark.asyncio
    async def test_immediate_failure(self, mock_plugin):
        """测试立即失败。"""
        class ImmediateFailureChatter(BaseChatter):
            chatter_name = "immediate_failure"

            async def execute(self, unreads: list) -> Generator[ChatterResult, None, None]:
                yield Failure("立即失败")

        chatter = ImmediateFailureChatter("stream_123", mock_plugin)

        results = []
        async for result in chatter.execute([MagicMock()]):
            results.append(result)

        assert len(results) == 1
        assert isinstance(results[0], Failure)
        assert results[0].error == "立即失败"


class TestFetchAndFlushUnreads:
    """测试 fetch_and_flush_unreads 方法。"""

    @pytest.mark.asyncio
    async def test_fetch_empty_unreads(self, mock_plugin):
        """测试获取空的未读消息。"""
        chatter = ConcreteChatter("stream_123", mock_plugin)

        # Mock stream manager
        with patch('src.core.managers.stream_manager.get_stream_manager') as mock_sm:
            mock_stream = MagicMock()
            mock_stream.context.unread_messages = []
            mock_sm.return_value._streams.get.return_value = mock_stream

            text, messages = await chatter.fetch_and_flush_unreads()

            assert text == ""
            assert messages == []

    @pytest.mark.asyncio
    async def test_fetch_single_message(self, mock_plugin):
        """测试获取单条消息。"""
        chatter = ConcreteChatter("stream_123", mock_plugin)

        # 创建测试消息
        msg = Message(
            message_id="msg_1",
            time=datetime.now().timestamp(),
            content="你好",
            sender_id="user_1",
            sender_name="Alice"
        )

        with patch('src.core.managers.stream_manager.get_stream_manager') as mock_sm:
            mock_stream = MagicMock()
            mock_stream.context.unread_messages = [msg]
            mock_stream.context.add_history_message = MagicMock()
            mock_sm.return_value._streams.get.return_value = mock_stream

            text, messages = await chatter.fetch_and_flush_unreads()

            assert "Alice" in text
            assert "你好" in text
            assert len(messages) == 1
            mock_stream.context.add_history_message.assert_called_once_with(msg)
            assert len(mock_stream.context.unread_messages) == 0

    @pytest.mark.asyncio
    async def test_fetch_multiple_messages_grouped(self, mock_plugin):
        """测试获取多条消息（分组模式）。"""
        chatter = ConcreteChatter("stream_123", mock_plugin)

        messages = [
            Message(
                message_id=f"msg_{i}",
                time=datetime.now().timestamp(),
                content=f"消息{i}",
                sender_id=f"user_{i}",
                sender_name=f"User{i}"
            )
            for i in range(3)
        ]

        with patch('src.core.managers.stream_manager.get_stream_manager') as mock_sm:
            mock_stream = MagicMock()
            mock_stream.context.unread_messages = messages
            mock_stream.context.add_history_message = MagicMock()
            mock_sm.return_value._streams.get.return_value = mock_stream

            text, fetched = await chatter.fetch_and_flush_unreads(format_as_group=True)

            # 验证格式
            lines = text.split("\n")
            assert len(lines) == 3
            assert "User0" in lines[0]
            assert "消息0" in lines[0]

            # 验证flush
            assert len(fetched) == 3
            assert mock_stream.context.add_history_message.call_count == 3
            assert len(mock_stream.context.unread_messages) == 0

    @pytest.mark.asyncio
    async def test_fetch_non_grouped(self, mock_plugin):
        """测试非分组模式。"""
        chatter = ConcreteChatter("stream_123", mock_plugin)

        msg = Message(
            message_id="msg_1",
            time=datetime.now().timestamp(),
            content="测试",
            sender_id="user_1",
            sender_name="Test"
        )

        with patch('src.core.managers.stream_manager.get_stream_manager') as mock_sm:
            mock_stream = MagicMock()
            mock_stream.context.unread_messages = [msg]
            mock_stream.context.add_history_message = MagicMock()
            mock_sm.return_value._streams.get.return_value = mock_stream

            text, messages = await chatter.fetch_and_flush_unreads(format_as_group=False)

            assert text == ""  # 非分组模式不返回格式化文本
            assert len(messages) == 1

    @pytest.mark.asyncio
    async def test_fetch_with_missing_stream(self, mock_plugin):
        """测试流不存在的情况。"""
        chatter = ConcreteChatter("stream_123", mock_plugin)

        with patch('src.core.managers.stream_manager.get_stream_manager') as mock_sm:
            mock_sm.return_value._streams.get.return_value = None

            text, messages = await chatter.fetch_and_flush_unreads()

            assert text == ""
            assert messages == []

    @pytest.mark.asyncio
    async def test_custom_time_format(self, mock_plugin):
        """测试自定义时间格式。"""
        chatter = ConcreteChatter("stream_123", mock_plugin)

        msg = Message(
            message_id="msg_1",
            time=datetime(2024, 1, 1, 14, 30).timestamp(),
            content="测试",
            sender_id="user_1",
            sender_name="Test"
        )

        with patch('src.core.managers.stream_manager.get_stream_manager') as mock_sm:
            mock_stream = MagicMock()
            mock_stream.context.unread_messages = [msg]
            mock_stream.context.add_history_message = MagicMock()
            mock_sm.return_value._streams.get.return_value = mock_stream

            # 使用完整时间格式
            text, _ = await chatter.fetch_and_flush_unreads(time_format="%Y-%m-%d %H:%M")

            assert "2024-01-01" in text
            assert "14:30" in text
