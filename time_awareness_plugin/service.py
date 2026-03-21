"""时间感知服务 - 追踪每个聊天流的时间状态"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from src.kernel.logger import get_logger

if TYPE_CHECKING:
    from src.core.models.stream import ChatStream

logger = get_logger("time_awareness", display="时间感知")


@dataclass
class ChatTimeState:
    """单个聊天流的时间状态"""

    stream_id: str
    last_user_message_time: datetime | None = None  # 上次用户消息时间
    waiting_since: datetime | None = None  # 开始等待时间（bot 进入 Wait 状态时）
    waiting_duration_seconds: float = 0.0  # 已等待时长（秒）

    def elapsed_minutes(self) -> float:
        """获取距离上次用户消息过去了多少分钟"""
        if self.last_user_message_time is None:
            return 0.0
        delta = datetime.now() - self.last_user_message_time
        return delta.total_seconds() / 60.0

    def waiting_minutes(self) -> float:
        """获取 bot 已等待了多少分钟"""
        if self.waiting_since is None:
            return 0.0
        delta = datetime.now() - self.waiting_since
        return delta.total_seconds() / 60.0

    def update_last_user_message(self) -> None:
        """更新 last_user_message_time 为当前时间"""
        self.last_user_message_time = datetime.now()
        # 收到用户消息时重置等待状态
        self.waiting_since = None
        self.waiting_duration_seconds = 0.0

    def start_waiting(self) -> None:
        """标记 bot 开始等待用户消息"""
        if self.waiting_since is None:
            self.waiting_since = datetime.now()

    def update_waiting_duration(self) -> None:
        """更新已等待时长"""
        if self.waiting_since:
            delta = datetime.now() - self.waiting_since
            self.waiting_duration_seconds = delta.total_seconds()


class TimeAwarenessService:
    """时间感知服务 - 单例模式"""

    _instance: TimeAwarenessService | None = None
    _states: dict[str, ChatTimeState]

    def __new__(cls) -> TimeAwarenessService:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._states = {}
        return cls._instance

    def get_state(self, stream_id: str) -> ChatTimeState:
        """获取或创建聊天流的时间状态

        Args:
            stream_id: 聊天流 ID

        Returns:
            ChatTimeState: 时间状态对象
        """
        if stream_id not in self._states:
            self._states[stream_id] = ChatTimeState(stream_id=stream_id)
            logger.debug(f"为聊天流 {stream_id[:8]}... 创建新的时间状态")
        return self._states[stream_id]

    def on_user_message(self, stream_id: str) -> None:
        """当收到用户消息时调用

        Args:
            stream_id: 聊天流 ID
        """
        state = self.get_state(stream_id)
        state.update_last_user_message()
        logger.debug(
            f"聊天流 {stream_id[:8]}... 收到用户消息，"
            f"当前距离上次消息：{state.elapsed_minutes():.1f} 分钟"
        )

    def on_bot_wait(self, stream_id: str) -> None:
        """当 bot 进入 Wait 状态时调用

        Args:
            stream_id: 聊天流 ID
        """
        state = self.get_state(stream_id)
        state.start_waiting()
        logger.debug(
            f"聊天流 {stream_id[:8]}... bot 进入等待状态，"
            f"已等待：{state.waiting_minutes():.1f} 分钟"
        )

    def get_elapsed_minutes(self, stream_id: str) -> float:
        """获取距离上次用户消息过去了多少分钟

        Args:
            stream_id: 聊天流 ID

        Returns:
            float: 过去的分钟数
        """
        state = self.get_state(stream_id)
        return state.elapsed_minutes()

    def get_waiting_minutes(self, stream_id: str) -> float:
        """获取 bot 已等待了多少分钟

        Args:
            stream_id: 聊天流 ID

        Returns:
            float: 等待的分钟数
        """
        state = self.get_state(stream_id)
        state.update_waiting_duration()
        return state.waiting_minutes()

    def get_time_info_for_prompt(self, stream_id: str) -> str:
        """生成用于注入到 prompt 的时间信息

        Args:
            stream_id: 聊天流 ID

        Returns:
            str: 时间信息字符串
        """
        now = datetime.now()
        weekday_map = {
            0: "星期一",
            1: "星期二",
            2: "星期三",
            3: "星期四",
            4: "星期五",
            5: "星期六",
            6: "星期日",
        }
        weekday = weekday_map.get(now.weekday(), "未知")

        time_str = now.strftime("%Y-%m-%d %H:%M:%S")
        elapsed = self.get_elapsed_minutes(stream_id)

        # 构建时间感知提示词
        time_info = f"现在是 {time_str}，{weekday}。"

        if elapsed > 0:
            # 根据时间长短给出不同的描述
            if elapsed < 1:
                time_info += " 用户刚刚发送了消息。"
            elif elapsed < 5:
                time_info += f" 用户在约{elapsed:.0f}分钟前发送了消息。"
            elif elapsed < 30:
                time_info += f" 用户已经{elapsed:.0f}分钟没有和你说话了。"
            elif elapsed < 60:
                time_info += f" 用户已经{elapsed:.0f}分钟没有和你说话了，可能暂时在忙别的事情。"
            elif elapsed < 120:
                time_info += f" 用户已经{elapsed:.0f}分钟（约{elapsed/60:.1f}小时）没有和你说话了。"
            else:
                time_info += f" 用户已经{elapsed:.0f}分钟（约{elapsed/60:.1f}小时）没有和你说话了，可能已经离开或去休息了。"

        return time_info

    def clear_state(self, stream_id: str) -> None:
        """清除聊天流的时间状态

        Args:
            stream_id: 聊天流 ID
        """
        if stream_id in self._states:
            del self._states[stream_id]
            logger.debug(f"已清除聊天流 {stream_id[:8]}... 的时间状态")

    def clear_all(self) -> None:
        """清除所有时间状态"""
        self._states.clear()
        logger.debug("已清除所有时间状态")


def get_time_awareness_service() -> TimeAwarenessService:
    """获取全局 TimeAwarenessService 单例"""
    return TimeAwarenessService()
