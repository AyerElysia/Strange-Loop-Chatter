"""流循环管理器。

``StreamLoopManager`` 负责管理所有聊天流的 Tick 驱动器生命周期：
- 为每个活跃流创建独立的 ``asyncio.Task``（运行 ``run_chat_stream``）
- 提供启动/停止/强制重启驱动器的接口
- 计算 Tick 间隔、刷新缓存、强制分发判定

参考 old/chat/message_manager/distribution_manager.py 中的 StreamLoopManager。
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from src.kernel.logger import get_logger

if TYPE_CHECKING:
    from src.core.models.stream import ChatStream, StreamContext

logger = get_logger("stream_loop_manager", display="StreamLoop")

# ============================================================================
# 默认配置常量
# ============================================================================

_DEFAULT_MAX_CONCURRENT_STREAMS = 10
_DEFAULT_FORCE_DISPATCH_THRESHOLD = 20
_DEFAULT_PRIVATE_INTERVAL_ACTIVE = 0.5
_DEFAULT_PRIVATE_INTERVAL_IDLE = 5.0
_DEFAULT_GROUP_INTERVAL_BASE = 5.0
_DEFAULT_THINKING_TIMEOUT = 120.0


class StreamLoopManager:
    """流循环管理器 — 基于 Generator + Tick 的事件驱动模式。

    为每个聊天流维护一个独立的驱动器任务（``run_chat_stream``），
    驱动器内部通过 ``conversation_loop`` 异步生成器按需产出 Tick 事件。

    Attributes:
        is_running: 管理器是否处于运行状态
        max_concurrent_streams: 最大并发处理流数

    Examples:
        >>> manager = get_stream_loop_manager()
        >>> await manager.start()
        >>> await manager.start_stream_loop("stream_abc")
    """

    def __init__(
        self,
        max_concurrent_streams: int = _DEFAULT_MAX_CONCURRENT_STREAMS,
    ) -> None:
        """初始化流循环管理器。

        Args:
            max_concurrent_streams: 最大并发处理流数
        """
        self.max_concurrent_streams = max_concurrent_streams
        self.is_running = False

        # 强制分发策略
        self.force_dispatch_unread_threshold: int = _DEFAULT_FORCE_DISPATCH_THRESHOLD

        # 流启动锁：防止并发启动同一个流的多个任务
        self._stream_start_locks: dict[str, asyncio.Lock] = {}

        # 并发控制
        self._processing_semaphore = asyncio.Semaphore(max_concurrent_streams)

        # 统计信息
        self._stats: dict[str, Any] = {
            "active_streams": 0,
            "total_loops": 0,
            "total_process_cycles": 0,
            "total_failures": 0,
            "start_time": time.time(),
        }

        logger.info(f"StreamLoopManager 初始化完成 (最大并发: {max_concurrent_streams})")

    # ========================================================================
    # 生命周期管理
    # ========================================================================

    async def start(self) -> None:
        """启动流循环管理器。"""
        if self.is_running:
            logger.warning("StreamLoopManager 已经在运行")
            return
        self.is_running = True
        logger.info("StreamLoopManager 已启动")

    async def stop(self) -> None:
        """停止流循环管理器，取消所有驱动器任务。"""
        if not self.is_running:
            return

        self.is_running = False

        from src.core.managers.stream_manager import get_stream_manager

        sm = get_stream_manager()
        cancel_tasks: list[tuple[str, asyncio.Task]] = []  # type: ignore[type-arg]

        for stream_id, chat_stream in sm._streams.items():
            ctx = chat_stream.context
            if ctx.stream_loop_task and not ctx.stream_loop_task.done():
                ctx.stream_loop_task.cancel()
                cancel_tasks.append((stream_id, ctx.stream_loop_task))

        if cancel_tasks:
            logger.info(f"正在取消 {len(cancel_tasks)} 个流循环任务...")
            await asyncio.gather(
                *[self._wait_for_task_cancel(sid, t) for sid, t in cancel_tasks],
                return_exceptions=True,
            )

        logger.info("StreamLoopManager 已停止")

    # ========================================================================
    # 流循环控制
    # ========================================================================

    async def start_stream_loop(self, stream_id: str, force: bool = False) -> bool:
        """启动指定流的驱动器任务。

        如果任务已在运行且非强制模式，则直接返回 True。

        Args:
            stream_id: 流 ID
            force: 是否强制启动（先取消现有任务再重新创建）

        Returns:
            bool: 是否成功启动
        """
        context = await self._get_stream_context(stream_id)
        if not context:
            logger.warning(f"无法获取流上下文: {stream_id[:8]}")
            return False

        # 快速路径：任务已在运行
        if not force and context.stream_loop_task and not context.stream_loop_task.done():
            logger.debug(f"[管理器] stream={stream_id[:8]}, 任务已在运行")
            return True

        # 获取或创建启动锁
        if stream_id not in self._stream_start_locks:
            self._stream_start_locks[stream_id] = asyncio.Lock()
        lock = self._stream_start_locks[stream_id]

        async with lock:
            # 强制启动时先取消旧任务
            if force and context.stream_loop_task and not context.stream_loop_task.done():
                logger.warning(f"[管理器] stream={stream_id[:8]}, 强制启动：取消现有任务")
                old_task = context.stream_loop_task
                old_task.cancel()
                try:
                    await asyncio.wait_for(old_task, timeout=2.0)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass
                except Exception as e:
                    logger.warning(f"等待旧任务结束时出错: {e}")

            # 创建新的驱动器任务
            try:
                from src.core.transport.distribution.loop import run_chat_stream
                from src.kernel.concurrency import get_task_manager
                loop_task = get_task_manager().create_task(
                    run_chat_stream(stream_id, self),
                    name=f"chat_stream_{stream_id[:16]}",
                )
                context.stream_loop_task = loop_task.task

                self._stats["active_streams"] += 1
                self._stats["total_loops"] += 1

                logger.debug(f"[管理器] stream={stream_id[:8]}, 启动驱动器任务")
                return True

            except Exception as e:
                logger.error(f"[管理器] stream={stream_id[:8]}, 启动失败: {e}")
                return False

    async def stop_stream_loop(self, stream_id: str) -> bool:
        """停止指定流的驱动器任务。

        Args:
            stream_id: 流 ID

        Returns:
            bool: 是否成功停止
        """
        context = await self._get_stream_context(stream_id)
        if not context:
            return False

        if not context.stream_loop_task or context.stream_loop_task.done():
            return False

        task = context.stream_loop_task
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        except Exception as e:
            logger.error(f"停止任务时出错: {e}")

        context.stream_loop_task = None
        self._stats["active_streams"] = max(0, self._stats["active_streams"] - 1)
        logger.debug(f"停止流循环: {stream_id[:8]}")
        return True

    # ========================================================================
    # 内部方法 — 上下文管理
    # ========================================================================

    async def _get_stream_context(self, stream_id: str) -> "StreamContext | None":
        """获取流上下文。

        Args:
            stream_id: 流 ID

        Returns:
            StreamContext | None: 流上下文，不存在时返回 None
        """
        from src.core.managers.stream_manager import get_stream_manager

        sm = get_stream_manager()
        chat_stream: "ChatStream | None" = sm._streams.get(stream_id)
        if chat_stream:
            return chat_stream.context
        return None

    async def _flush_cached_messages_to_unread(self, stream_id: str) -> list[Any]:
        """将缓存消息刷新到未读消息列表。

        Args:
            stream_id: 流 ID

        Returns:
            list: 已刷新的消息列表
        """
        context = await self._get_stream_context(stream_id)
        if not context:
            return []

        if not context.is_cache_enabled or not context.message_cache:
            return []

        flushed: list[Any] = []
        while context.message_cache:
            msg = context.message_cache.popleft()
            context.add_unread_message(msg)
            flushed.append(msg)

        if flushed:
            logger.debug(
                f"刷新缓存消息: stream={stream_id[:8]}, 数量={len(flushed)}"
            )
        return flushed

    # ========================================================================
    # 内部方法 — 消息处理
    # ========================================================================

    async def _process_stream_messages(
        self,
        stream_id: str,
        context: "StreamContext",
    ) -> bool:
        """处理流消息，调度 Chatter。

        Args:
            stream_id: 流 ID
            context: 流上下文

        Returns:
            bool: 是否处理成功
        """
        from src.core.managers.chatter_manager import get_chatter_manager

        chatter_manager = get_chatter_manager()

        # 二次并发保护
        if context.is_chatter_processing:
            logger.warning(f"[并发保护] stream={stream_id[:8]}, 二次检查触发")
            return False

        unread_messages = context.unread_messages
        if not unread_messages:
            logger.debug(f"未读消息为空，跳过处理: {stream_id[:8]}")
            return True

        context.is_chatter_processing = True
        try:
            # 设置触发用户 ID
            last_msg = unread_messages[-1] if unread_messages else None
            if last_msg:
                context.triggering_user_id = last_msg.sender_id

            logger.debug(f"处理 {len(unread_messages)} 条未读消息: {stream_id[:8]}")

            # 获取此流的 Chatter
            chatter = chatter_manager.get_chatter_by_stream(stream_id)
            if not chatter:
                from src.core.managers.stream_manager import get_stream_manager

                sm = get_stream_manager()
                chat_stream = sm._streams.get(stream_id)
                if not chat_stream:
                    logger.debug(f"未找到流实例，无法绑定 Chatter: {stream_id[:8]}")
                    return False

                chatter = chatter_manager.get_or_create_chatter_for_stream(
                    stream_id,
                    chat_stream.chat_type,
                    chat_stream.platform,
                )
                if not chatter:
                    logger.debug(f"未找到绑定的 Chatter: {stream_id[:8]}")
                    return False

            # 执行 Chatter
            async with self._processing_semaphore:
                result_gen = chatter.execute(list(unread_messages))
                # 消费生成器结果
                if result_gen is not None:
                    async for result in result_gen:
                        logger.debug(f"Chatter 结果: {result}")

            # 清空未读消息
            context.unread_messages.clear()

            return True

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"处理异常: {stream_id[:8]} - {e}")
            return False
        finally:
            context.is_chatter_processing = False

    # ========================================================================
    # 内部方法 — 间隔计算与策略
    # ========================================================================

    async def _calculate_interval(self, stream_id: str, has_messages: bool) -> float:
        """计算下次检查间隔。

        私聊：有消息 0.5s，无消息 5.0s。
        群聊：有消息使用基础间隔，无消息使用 2 倍基础间隔。

        Args:
            stream_id: 流 ID
            has_messages: 当前是否有未读消息

        Returns:
            float: 等待秒数
        """
        from src.core.managers.stream_manager import get_stream_manager

        sm = get_stream_manager()
        chat_stream = sm._streams.get(stream_id)

        if chat_stream and chat_stream.chat_type == "private":
            return _DEFAULT_PRIVATE_INTERVAL_ACTIVE if has_messages else _DEFAULT_PRIVATE_INTERVAL_IDLE

        base_interval = _DEFAULT_GROUP_INTERVAL_BASE
        if not has_messages:
            return base_interval * 2.0

        return base_interval

    def _needs_force_dispatch(self, context: "StreamContext", unread_count: int) -> bool:
        """检查是否需要强制分发。

        当未读消息数超过阈值时触发强制分发。

        Args:
            context: 流上下文
            unread_count: 未读消息数量

        Returns:
            bool: 是否需要强制分发
        """
        if self.force_dispatch_unread_threshold <= 0:
            return False
        return unread_count > self.force_dispatch_unread_threshold

    def _recover_stale_processing_state(
        self,
        stream_id: str,
        context: "StreamContext",
    ) -> bool:
        """检测并修复 Chatter 处理标志的假死状态。

        Args:
            stream_id: 流 ID
            context: 流上下文

        Returns:
            bool: 是否进行了修复
        """
        # 如果没有关联的任务但标志为 True，说明是残留状态
        if context.stream_loop_task is None or context.stream_loop_task.done():
            # 当前驱动器自身刚刚拿到了 context，所以 task 可能未完成
            # 这里仅检查标志状态是否合理
            pass

        # 简单策略：如果标志为 True 但无其他证据，尝试清除
        context.is_chatter_processing = False
        logger.warning(f"[自愈] stream={stream_id[:8]}, 清除残留处理标志")
        return True

    # ========================================================================
    # 辅助方法
    # ========================================================================

    async def _wait_for_task_cancel(self, stream_id: str, task: asyncio.Task) -> None:  # type: ignore[type-arg]
        """等待任务取消完成。

        Args:
            stream_id: 流 ID
            task: 要等待取消的任务
        """
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        except Exception as e:
            logger.error(f"等待任务取消出错 ({stream_id[:8]}): {e}")

    def get_stats(self) -> dict[str, Any]:
        """获取统计信息。

        Returns:
            dict[str, Any]: 统计信息字典
        """
        return {
            "is_running": self.is_running,
            "active_streams": self._stats["active_streams"],
            "total_loops": self._stats["total_loops"],
            "total_process_cycles": self._stats["total_process_cycles"],
            "total_failures": self._stats["total_failures"],
            "max_concurrent_streams": self.max_concurrent_streams,
            "uptime": time.time() - self._stats["start_time"] if self.is_running else 0,
        }


# ============================================================================
# 全局单例
# ============================================================================

_global_stream_loop_manager: StreamLoopManager | None = None


def get_stream_loop_manager() -> StreamLoopManager:
    """获取全局 StreamLoopManager 单例。

    Returns:
        StreamLoopManager: 全局流循环管理器实例

    Examples:
        >>> manager = get_stream_loop_manager()
        >>> await manager.start_stream_loop("stream_abc")
    """
    global _global_stream_loop_manager
    if _global_stream_loop_manager is None:
        _global_stream_loop_manager = StreamLoopManager()
    return _global_stream_loop_manager


def reset_stream_loop_manager() -> None:
    """重置全局 StreamLoopManager 单例。主要用于测试。"""
    global _global_stream_loop_manager
    _global_stream_loop_manager = None
