"""Proactive Message Plugin - 状态管理服务。

管理每个聊天流的状态，包括最后消息时间、下次检查时间等。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from src.kernel.logger import get_logger
from src.kernel.scheduler import get_unified_scheduler, TriggerType

if TYPE_CHECKING:
    from src.core.models.stream import ChatStream

logger = get_logger("proactive_message_service", display="主动消息服务")


@dataclass
class StreamState:
    """单个聊天流的状态"""

    stream_id: str
    last_user_message_time: datetime
    accumulated_wait_minutes: float = 0.0
    next_check_time: datetime | None = None  # 下次检查时间
    is_waiting: bool = False  # 是否在等待中
    scheduler_task_name: str | None = None  # 调度任务名称

    def elapsed_minutes(self) -> float:
        """获取距离上次用户消息过去了多少分钟"""
        delta = datetime.now() - self.last_user_message_time
        return delta.total_seconds() / 60.0

    def reset(self, new_last_message_time: datetime) -> None:
        """重置状态（收到新用户消息时调用）"""
        self.last_user_message_time = new_last_message_time
        self.accumulated_wait_minutes = 0.0
        self.next_check_time = None
        self.is_waiting = False
        self.scheduler_task_name = None


class ProactiveMessageService:
    """主动消息服务 - 单例模式"""

    _instance: ProactiveMessageService | None = None
    _states: dict[str, StreamState]
    _scheduler = None

    def __new__(cls) -> ProactiveMessageService:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._states = {}
            cls._instance._scheduler = get_unified_scheduler()
        return cls._instance

    def get_state(self, stream_id: str) -> StreamState | None:
        """获取聊天流状态

        Args:
            stream_id: 聊天流 ID

        Returns:
            StreamState | None: 状态对象，如果不存在返回 None
        """
        return self._states.get(stream_id)

    def get_or_create_state(self, stream_id: str, last_user_message_time: datetime | None = None) -> StreamState:
        """获取或创建聊天流状态

        Args:
            stream_id: 聊天流 ID
            last_user_message_time: 上次用户消息时间，如果是新建状态则必须提供

        Returns:
            StreamState: 状态对象
        """
        if stream_id not in self._states:
            if last_user_message_time is None:
                last_user_message_time = datetime.now()
            self._states[stream_id] = StreamState(
                stream_id=stream_id,
                last_user_message_time=last_user_message_time,
            )
            logger.debug(f"为聊天流 {stream_id[:8]}... 创建新的状态")
        return self._states[stream_id]

    def get_total_wait_minutes(self, stream_id: str) -> float:
        state = self._states.get(stream_id)
        if not state:
            return 0.0
        delta = datetime.now() - state.last_user_message_time
        return state.accumulated_wait_minutes + delta.total_seconds() / 60.0

    def checkpoint_wait(self, stream_id: str) -> None:
        """在一次 timeout 后将本轮等待累加，并重置计时起点。"""
        state = self._states.get(stream_id)
        if not state:
            return
        delta = datetime.now() - state.last_user_message_time
        state.accumulated_wait_minutes += delta.total_seconds() / 60.0
        state.last_user_message_time = datetime.now()

    def on_user_message(self, chat_stream: ChatStream, cancel_task: bool = True) -> None:
        """当收到用户消息时调用

        重置等待状态并取消已调度的检查任务。

        Args:
            chat_stream: 聊天流对象
            cancel_task: 是否取消当前已调度任务。若随后会立即以同名任务重新调度，
                可设为 False 以避免异步取消误删新任务。
        """
        stream_id = getattr(chat_stream, "stream_id", "")
        if not stream_id:
            return

        now = datetime.now()

        if stream_id in self._states:
            state = self._states[stream_id]
            scheduled_task_name = state.scheduler_task_name
            state.reset(now)

            # 取消已调度的检查任务
            if cancel_task and scheduled_task_name:
                try:
                    import asyncio

                    asyncio.create_task(self._scheduler.remove_schedule_by_name(scheduled_task_name))
                except Exception as e:
                    logger.debug(f"取消任务失败（可能已执行）: {e}")
                state.scheduler_task_name = None

            logger.debug(f"聊天流 {stream_id[:8]}... 收到用户消息，重置等待状态")
        else:
            # 创建新状态
            self._states[stream_id] = StreamState(
                stream_id=stream_id,
                last_user_message_time=now,
            )
            logger.debug(f"聊天流 {stream_id[:8]}... 创建新状态")

    async def start_waiting(
        self,
        stream_id: str,
        wait_minutes: float,
        callback,
    ) -> str | None:
        """开始等待并调度下次检查

        Args:
            stream_id: 聊天流 ID
            wait_minutes: 等待分钟数
            callback: 到期时调用的回调函数

        Returns:
            str | None: 调度任务名称，如果失败返回 None
        """
        state = self.get_or_create_state(stream_id)

        # 应用最小等待间隔限制（尊重调用方传入的等待时间，不再强制 5 分钟）
        # 仍保留下限 0.01 以避免 0 导致立即触发
        wait_minutes = max(wait_minutes, 0.01)

        # 应用最大等待时间限制
        max_wait = 180.0  # 默认最大 3 小时
        wait_minutes = min(wait_minutes, max_wait)

        next_check_time = datetime.now() + timedelta(minutes=wait_minutes)
        state.next_check_time = next_check_time
        state.is_waiting = True

        # 任务名称：确保每个 stream_id 只有一个活跃任务
        task_name = f"proactive_check_{stream_id}"
        state.scheduler_task_name = task_name

        try:
            await self._scheduler.create_schedule(
                callback=callback,
                trigger_type=TriggerType.TIME,
                trigger_config={"trigger_at": next_check_time},
                task_name=task_name,
                force_overwrite=True,  # 覆盖旧任务
            )
            logger.info(f"已调度检查任务：{stream_id[:8]}... 将在 {wait_minutes:.1f} 分钟后检查")
            return task_name
        except Exception as e:
            logger.error(f"调度检查任务失败：{e}")
            return None

    async def trigger_inner_monologue(self, stream_id: str) -> None:
        """触发内心独白（由 scheduler 调用）

        Args:
            stream_id: 聊天流 ID
        """
        state = self.get_state(stream_id)
        if state is None:
            logger.warning(f"触发内心独白但未找到状态：{stream_id[:8]}...")
            return

        logger.info(f"触发内心独白：{stream_id[:8]}... 已等待 {state.elapsed_minutes():.0f} 分钟")

        # 实际的内心独白逻辑由 inner_monologue.py 处理
        # 这里只是占位，实际调用会在 plugin.py 中注入
        # 通过事件或直接调用来触发

    def clear_state(self, stream_id: str, cancel_task: bool = True) -> None:
        """清除聊天流状态

        Args:
            stream_id: 聊天流 ID
            cancel_task: 是否同时取消已调度任务
        """
        if stream_id in self._states:
            state = self._states[stream_id]
            # 取消已调度的任务
            if cancel_task and state.scheduler_task_name:
                try:
                    import asyncio

                    asyncio.create_task(self._scheduler.remove_schedule_by_name(state.scheduler_task_name))
                except Exception as e:
                    logger.debug(f"取消任务失败：{e}")
            del self._states[stream_id]
            logger.debug(f"已清除聊天流 {stream_id[:8]}... 的状态")

    def clear_all(self) -> None:
        """清除所有状态"""
        # 取消所有调度任务
        for state in self._states.values():
            if state.scheduler_task_name:
                try:
                    import asyncio

                    asyncio.create_task(self._scheduler.remove_schedule_by_name(state.scheduler_task_name))
                except Exception:
                    pass
        self._states.clear()
        logger.debug("已清除所有状态")


def get_proactive_message_service() -> ProactiveMessageService:
    """获取全局 ProactiveMessageService 单例"""
    return ProactiveMessageService()
