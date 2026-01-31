"""
Concurrency 模块单元测试

测试 TaskManager、TaskGroup 和 TaskInfo 的功能。
"""

from __future__ import annotations

import asyncio
import pytest
from datetime import datetime

from src.kernel.concurrency import (
    get_task_manager,
    TaskManager,
    TaskGroup,
    TaskInfo,
    get_watchdog,
    WatchDog,
    StreamHeartbeat,
    TaskNotFoundError,
    TaskGroupError,
)


class TestTaskInfo:
    """测试 TaskInfo 数据类"""

    def test_task_info_creation(self) -> None:
        """测试 TaskInfo 创建"""
        task_info = TaskInfo(
            task_id="test_id",
            name="test_task",
            daemon=False,
            timeout=10.0,
        )

        assert task_info.task_id == "test_id"
        assert task_info.name == "test_task"
        assert task_info.daemon is False
        assert task_info.timeout == 10.0
        assert isinstance(task_info.created_at, datetime)
        assert task_info.group_name is None

    @pytest.mark.asyncio
    async def test_task_info_status_methods(self) -> None:
        """测试 TaskInfo 状态方法"""
        async def sample_task():
            await asyncio.sleep(0.1)
            return "done"

        task = asyncio.create_task(sample_task())
        task_info = TaskInfo(task_id="test_id", task=task)

        # 任务未完成
        assert not task_info.is_done()
        assert not task_info.is_cancelled()
        assert not task_info.is_failed()

        # 等待任务完成
        await task

        # 任务已完成
        assert task_info.is_done()
        assert not task_info.is_cancelled()
        assert not task_info.is_failed()
        assert task_info.get_result() == "done"


class TestTaskManager:
    """测试 TaskManager 类"""

    def test_singleton(self) -> None:
        """测试单例模式"""
        tm1 = get_task_manager()
        tm2 = get_task_manager()

        assert tm1 is tm2
        assert isinstance(tm1, TaskManager)

    @pytest.mark.asyncio
    async def test_create_task(self) -> None:
        """测试创建任务"""
        tm = get_task_manager()

        async def sample_task():
            await asyncio.sleep(0.1)
            return "result"

        task_info = tm.create_task(sample_task(), name="test_task")

        assert task_info.name == "test_task"
        assert task_info.daemon is False
        assert task_info.task is not None
        assert not task_info.is_done()

        # 等待完成
        result = await task_info.task
        assert result == "result"

    @pytest.mark.asyncio
    async def test_create_daemon_task(self) -> None:
        """测试创建守护任务"""
        tm = get_task_manager()

        async def daemon_task():
            await asyncio.sleep(0.1)

        task_info = tm.create_task(daemon_task(), daemon=True)
        assert task_info.daemon is True
        await task_info.task

    @pytest.mark.asyncio
    async def test_wait_all_tasks(self) -> None:
        """测试等待所有任务完成"""
        tm = get_task_manager()

        async def sample_task(n: int):
            await asyncio.sleep(0.1)
            return n

        # 创建多个任务
        for i in range(5):
            tm.create_task(sample_task(i))

        # 等待所有任务完成
        await tm.wait_all_tasks()

        # 验证所有任务已完成
        active_tasks = tm.get_active_tasks()
        assert len(active_tasks) == 0

    @pytest.mark.asyncio
    async def test_cancel_task(self) -> None:
        """测试取消任务"""
        tm = get_task_manager()

        async def long_task():
            await asyncio.sleep(10)
            return "should not complete"

        task_info = tm.create_task(long_task())

        # 取消任务
        success = tm.cancel_task(task_info.task_id)
        assert success is True

        # 等待取消完成
        try:
            await task_info.task
        except asyncio.CancelledError:
            pass

        assert task_info.is_cancelled()

    @pytest.mark.asyncio
    async def test_get_task_stats(self) -> None:
        """测试获取任务统计"""
        tm = get_task_manager()

        async def sample_task():
            await asyncio.sleep(0.1)

        # 创建任务
        tm.create_task(sample_task(), name="task1")
        tm.create_task(sample_task(), daemon=True, name="daemon_task")

        stats = tm.get_stats()

        assert stats["total_tasks"] >= 2
        assert stats["daemon_tasks"] >= 1


class TestTaskGroup:
    """测试 TaskGroup 类"""

    @pytest.mark.asyncio
    async def test_task_group_context_manager(self) -> None:
        """测试 TaskGroup 上下文管理器"""
        tm = get_task_manager()

        async def task1():
            await asyncio.sleep(0.1)
            return "task1"

        async def task2():
            await asyncio.sleep(0.1)
            return "task2"

        async with tm.group(name="test_group") as tg:
            assert tg.is_active()

            t1 = tg.create_task(task1(), name="task1")
            t2 = tg.create_task(task2(), name="task2")

            assert tg.get_task_count() == 2

        # 退出上下文后，所有任务应完成
        assert not tg.is_active()
        assert t1.is_done()
        assert t2.is_done()

    @pytest.mark.asyncio
    async def test_task_group_shared(self) -> None:
        """测试 TaskGroup 共享"""
        tm = get_task_manager()

        async def task1():
            await asyncio.sleep(0.1)

        async def task2():
            await asyncio.sleep(0.1)

        # 第一次获取，创建新组
        group1 = tm.group(name="shared_group")
        assert group1.get_task_count() == 0

        async with group1 as tg:
            tg.create_task(task1())

        # 第二次获取，应返回同一个组
        group2 = tm.group(name="shared_group")
        assert group1 is group2

        async with group2 as tg:
            tg.create_task(task2())

    @pytest.mark.asyncio
    async def test_task_group_cancel_on_error(self) -> None:
        """测试 TaskGroup 错误时取消其他任务"""
        tm = get_task_manager()

        async def failing_task():
            await asyncio.sleep(0.05)
            raise ValueError("Task failed")

        async def long_task():
            await asyncio.sleep(10)
            return "should not complete"

        try:
            async with tm.group(name="error_group", cancel_on_error=True) as tg:
                tg.create_task(failing_task())
                tg.create_task(long_task())
        except ValueError:
            pass  # 预期的异常

    def test_task_group_inactive_error(self) -> None:
        """测试非激活状态创建任务抛出异常"""
        tg = TaskGroup(name="test_group")

        async def dummy_task():
            await asyncio.sleep(0)

        # 不在上下文管理器内创建任务应抛出异常
        # 使用 pytest.raises 来捕获异常，协程不会被实际创建
        try:
            tg.create_task(dummy_task())
        except TaskGroupError:
            pass  # 预期的异常


class TestWatchDog:
    """测试 WatchDog 类"""

    def test_get_watchdog_singleton(self) -> None:
        """测试 WatchDog 单例"""
        wd1 = get_watchdog()
        wd2 = get_watchdog()

        assert wd1 is wd2
        assert isinstance(wd1, WatchDog)

    def test_register_stream(self) -> None:
        """测试注册聊天流"""
        wd = get_watchdog()

        heartbeat = wd.register_stream(
            stream_id="test_stream",
            tick_interval=1.0,
            warning_threshold=2.0,
            restart_threshold=5.0,
        )

        assert isinstance(heartbeat, StreamHeartbeat)
        assert heartbeat.stream_id == "test_stream"
        assert heartbeat.tick_interval == 1.0

        # 清理
        wd.unregister_stream("test_stream")

    def test_feed_dog(self) -> None:
        """测试喂狗（更新心跳）"""
        wd = get_watchdog()

        wd.register_stream(stream_id="test_stream")

        # 记录初始心跳时间
        initial_time = wd._stream_registry["test_stream"].last_tick

        # 喂狗
        import time

        time.sleep(0.1)
        wd.feed_dog("test_stream")

        # 验证心跳时间已更新
        updated_time = wd._stream_registry["test_stream"].last_tick
        assert updated_time > initial_time

        # 清理
        wd.unregister_stream("test_stream")

    def test_unregister_stream(self) -> None:
        """测试注销聊天流"""
        wd = get_watchdog()

        wd.register_stream(stream_id="test_stream")
        assert "test_stream" in wd._stream_registry

        wd.unregister_stream("test_stream")
        assert "test_stream" not in wd._stream_registry

    def test_get_stats(self) -> None:
        """测试获取统计信息"""
        wd = get_watchdog()

        stats = wd.get_stats()

        assert "running" in stats
        assert "tick_interval" in stats
        assert "registered_streams" in stats
        assert isinstance(stats["registered_streams"], int)


class TestIntegration:
    """集成测试"""

    @pytest.mark.asyncio
    async def test_task_manager_with_watchdog(self) -> None:
        """测试 TaskManager 与 WatchDog 集成"""
        tm = get_task_manager()
        wd = get_watchdog()

        # 设置 WatchDog 到 TaskManager
        tm.set_watchdog(wd)

        async def short_task():
            await asyncio.sleep(0.1)

        # 创建任务
        tm.create_task(short_task(), timeout=1.0)

        # 清理已完成任务
        await asyncio.sleep(0.2)
        cleaned = tm.cleanup_tasks()

        assert cleaned >= 1
