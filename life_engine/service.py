"""life_engine 生命中枢服务。

生命中枢是一个独立于 DFC（对话流控制器）的并行存在系统。
它通过周期性心跳来处理堆积的消息、进行内部思考、并为未来的
工具调用、主动与 DFC 通信等功能提供基础骨架。

核心设计：
1. 事件流：所有交互（消息、心跳、工具调用）统一为 Event，保持时间连续性
2. 心跳循环：定期唤醒，处理堆积的事件
3. 上下文管理：维护滚动的事件流历史
4. 仿生记忆系统：语义检索、联想、遗忘机制
5. 未来扩展：工具调用、主动与 DFC 通信、记忆/反思/探索等
"""

from __future__ import annotations

import asyncio
import json
import traceback
from dataclasses import dataclass, asdict, field
from datetime import datetime, time as dtime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, TYPE_CHECKING

from src.app.plugin_system.api.llm_api import create_llm_request, get_model_set_by_task
from src.app.plugin_system.api.log_api import get_logger
from src.core.config import get_core_config
from src.core.components.base import BaseService
from src.core.models.message import Message
from src.kernel.concurrency import get_task_manager
from src.kernel.llm import LLMPayload, ROLE, Text, ToolRegistry, ToolResult

from .audit import (
    get_life_log_file,
    log_error,
    log_heartbeat as log_heartbeat_event,
    log_heartbeat_model_response,
    log_lifecycle,
    log_message_received,
    log_wake_context_injected,
)
from .config import LifeEngineConfig

if TYPE_CHECKING:
    from .memory_service import LifeMemoryService


logger = get_logger("life_engine", display="life_engine")

_TARGET_REMINDER_BUCKET = "actor"
_TARGET_REMINDER_NAME = "生命中枢唤醒上下文"

# 中枢内部消息的固定标识
_INTERNAL_PLATFORM = "life_engine"
_INTERNAL_STREAM_ID = "life_engine_internal"
_RUNTIME_CONTEXT_FILE = "life_engine_context.json"


def _now_iso() -> str:
    """返回当前时间的 ISO 字符串。"""
    return datetime.now(timezone.utc).astimezone().isoformat()


def _format_time(raw_time: float | int | None) -> str:
    """格式化消息时间为 ISO 字符串（内部存储用）。"""
    if raw_time is None:
        return _now_iso()
    try:
        return datetime.fromtimestamp(float(raw_time), tz=timezone.utc).astimezone().isoformat()
    except Exception:
        return _now_iso()


def _format_time_display(iso_time: str | None) -> str:
    """格式化时间为简洁的显示格式。
    
    - 5分钟内："刚才"
    - 1小时内："X分钟前"
    - 当日："HH:MM"
    - 跨日："MM-DD HH:MM"
    """
    if not iso_time:
        return "未知时间"
    
    try:
        dt = datetime.fromisoformat(iso_time)
        now = datetime.now(dt.tzinfo or timezone.utc)
        diff = now - dt
        diff_seconds = diff.total_seconds()
        
        if diff_seconds < 0:
            # 未来时间，直接显示
            return dt.strftime("%H:%M")
        elif diff_seconds < 300:  # 5分钟内
            return "刚才"
        elif diff_seconds < 3600:  # 1小时内
            minutes = int(diff_seconds / 60)
            return f"{minutes}分钟前"
        elif dt.date() == now.date():  # 当日
            return dt.strftime("%H:%M")
        elif (now.date() - dt.date()).days < 7:  # 一周内
            return dt.strftime("%m-%d %H:%M")
        else:
            return dt.strftime("%Y-%m-%d")
    except Exception:
        return "未知时间"


def _format_current_time() -> str:
    """格式化当前时间为人类可读格式。"""
    now = datetime.now(timezone.utc).astimezone()
    weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    weekday = weekdays[now.weekday()]
    return f"{now.strftime('%Y-%m-%d')} {weekday} {now.strftime('%H:%M:%S')}"


def _shorten_text(text: str, *, max_length: int = 240) -> str:
    """截断过长文本，保持唤醒上下文可读。"""
    normalized = " ".join(text.split())
    if len(normalized) <= max_length:
        return normalized
    return normalized[: max_length - 1] + "…"


def _parse_hhmm(value: str) -> dtime | None:
    """解析 HH:MM（24 小时制）时间字符串。"""
    raw = (value or "").strip()
    if not raw:
        return None
    parts = raw.split(":")
    if len(parts) != 2:
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return dtime(hour=hour, minute=minute)


class EventType(str, Enum):
    """事件类型枚举。"""

    MESSAGE = "message"          # 外部消息
    HEARTBEAT = "heartbeat"      # 心跳回复（内部思考）
    TOOL_CALL = "tool_call"      # 工具调用
    TOOL_RESULT = "tool_result"  # 工具返回结果


@dataclass(slots=True)
class LifeEngineEvent:
    """生命中枢事件 - 统一的事件模型。

    所有交互都是事件，保持时间连续性。
    """

    # 基础信息
    event_id: str
    event_type: EventType
    timestamp: str
    sequence: int  # 事件序列号，用于排序

    # 来源信息
    source: str  # 事件来源标识（平台名/life_engine等）
    source_detail: str  # 详细来源描述

    # 内容
    content: str
    content_type: str = "text"

    # 消息特有字段
    sender: str | None = None
    chat_type: str | None = None
    stream_id: str | None = None

    # 心跳特有字段
    heartbeat_index: int | None = None

    # 工具调用特有字段
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    tool_success: bool | None = None


@dataclass(slots=True)
class LifeEngineState:
    """life_engine 中枢状态。"""

    running: bool = False
    started_at: str | None = None
    last_heartbeat_at: str | None = None
    heartbeat_count: int = 0
    pending_event_count: int = 0
    history_event_count: int = 0
    event_sequence: int = 0
    last_wake_context_at: str | None = None
    last_wake_context_size: int = 0
    last_model_reply_at: str | None = None
    last_model_reply: str | None = None
    last_model_error: str | None = None
    last_error: str | None = None
    # 新增：跟踪最后一次外部消息和传话时间
    last_external_message_at: str | None = None
    last_tell_dfc_at: str | None = None
    tell_dfc_count: int = 0  # 本次运行期间传话总次数


# 全局单例引用，用于工具访问服务
_service_instance: "LifeEngineService | None" = None


class LifeEngineService(BaseService):
    """life_engine 心跳服务。

    这个版本使用统一的事件流模型，所有交互保持时间连续性。
    不参与正常聊天流程，不做回复决策。
    """

    service_name: str = "life_engine"
    service_description: str = "生命中枢服务，维持并行心跳与事件流上下文"
    version: str = "3.1.0"

    @classmethod
    def get_instance(cls) -> "LifeEngineService | None":
        """获取服务单例（供工具使用）。"""
        return _service_instance

    def __init__(self, plugin) -> None:
        super().__init__(plugin)
        self._state = LifeEngineState()
        self._heartbeat_task_id: str | None = None
        self._stop_event: asyncio.Event | None = None
        self._pending_events: list[LifeEngineEvent] = []
        self._event_history: list[LifeEngineEvent] = []
        self._lock: asyncio.Lock | None = None
        self._sleep_state_active: bool = False
        self._memory_service: "LifeMemoryService | None" = None
        self._last_decay_date: str | None = None  # 上次衰减任务日期

    def _get_lock(self) -> asyncio.Lock:
        """获取懒加载锁。"""
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def _cfg(self) -> LifeEngineConfig:
        config = getattr(self.plugin, "config", None)
        if isinstance(config, LifeEngineConfig):
            return config
        return LifeEngineConfig()

    def _is_enabled(self) -> bool:
        """判断插件当前是否启用。"""
        cfg = self._cfg()
        return bool(cfg.settings.enabled)

    def _history_limit(self) -> int:
        """返回滚动事件流保留上限。"""
        cfg = self._cfg()
        return max(1, int(cfg.settings.context_history_max_events))

    def _sleep_window_config(self) -> tuple[dtime | None, dtime | None]:
        """返回配置的睡眠窗口（sleep, wake）。"""
        cfg = self._cfg()
        return _parse_hhmm(cfg.settings.sleep_time), _parse_hhmm(cfg.settings.wake_time)

    def _sleep_window_status(self) -> tuple[bool, str]:
        """返回睡眠窗口配置是否有效及说明。"""
        cfg = self._cfg()
        sleep_raw = (cfg.settings.sleep_time or "").strip()
        wake_raw = (cfg.settings.wake_time or "").strip()
        sleep_at, wake_at = self._sleep_window_config()
        if not sleep_raw and not wake_raw:
            return False, "disabled"
        if sleep_at is None or wake_at is None:
            return False, "invalid-format"
        if sleep_at == wake_at:
            return False, "invalid-equal"
        return True, f"{sleep_at.strftime('%H:%M')}~{wake_at.strftime('%H:%M')}"

    def _in_sleep_window_now(self) -> tuple[bool, str]:
        """判断当前是否处于睡眠窗口。"""
        sleep_at, wake_at = self._sleep_window_config()
        if sleep_at is None or wake_at is None:
            return False, "sleep-window-disabled"

        now = datetime.now().astimezone().time()
        now_hm = dtime(hour=now.hour, minute=now.minute, second=0, microsecond=0)

        if sleep_at == wake_at:
            return False, "sleep-window-invalid-equal"

        # 普通窗口：例如 01:00 -> 07:00
        if sleep_at < wake_at:
            in_sleep = sleep_at <= now_hm < wake_at
        else:
            # 跨日窗口：例如 23:00 -> 07:00
            in_sleep = (now_hm >= sleep_at) or (now_hm < wake_at)

        return in_sleep, f"{sleep_at.strftime('%H:%M')}~{wake_at.strftime('%H:%M')}"

    def _next_sequence(self) -> int:
        """获取下一个事件序列号。"""
        self._state.event_sequence += 1
        return self._state.event_sequence

    def _minutes_since_external_message(self) -> int | None:
        """计算距离上一条外部消息过去了多少分钟。"""
        if not self._state.last_external_message_at:
            return None
        try:
            last_time = datetime.fromisoformat(self._state.last_external_message_at)
            now = datetime.now().astimezone()
            delta = now - last_time
            return int(delta.total_seconds() / 60)
        except Exception:
            return None

    def _minutes_since_tell_dfc(self) -> int | None:
        """计算距离上一次传话给 DFC 过去了多少分钟。"""
        if not self._state.last_tell_dfc_at:
            return None
        try:
            last_time = datetime.fromisoformat(self._state.last_tell_dfc_at)
            now = datetime.now().astimezone()
            delta = now - last_time
            return int(delta.total_seconds() / 60)
        except Exception:
            return None

    def record_tell_dfc(self) -> None:
        """记录一次传话给 DFC 的时间。"""
        self._state.last_tell_dfc_at = _now_iso()
        self._state.tell_dfc_count += 1

    def _runtime_context_path(self) -> Path:
        """返回运行时上下文持久化文件路径。"""
        workspace = Path(self._cfg().settings.workspace_path).resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        return workspace / _RUNTIME_CONTEXT_FILE

    def _event_to_dict(self, event: LifeEngineEvent) -> dict[str, Any]:
        """将事件序列化为可落盘字典。"""
        return {
            "event_id": event.event_id,
            "event_type": event.event_type.value,
            "timestamp": event.timestamp,
            "sequence": event.sequence,
            "source": event.source,
            "source_detail": event.source_detail,
            "content": event.content,
            "content_type": event.content_type,
            "sender": event.sender,
            "chat_type": event.chat_type,
            "stream_id": event.stream_id,
            "heartbeat_index": event.heartbeat_index,
            "tool_name": event.tool_name,
            "tool_args": event.tool_args,
            "tool_success": event.tool_success,
        }

    def _event_from_dict(self, data: dict[str, Any]) -> LifeEngineEvent:
        """从字典反序列化事件。"""
        event_type_raw = str(data.get("event_type") or EventType.MESSAGE.value)
        try:
            event_type = EventType(event_type_raw)
        except ValueError:
            event_type = EventType.MESSAGE

        return LifeEngineEvent(
            event_id=str(data.get("event_id") or f"evt_{self._next_sequence()}"),
            event_type=event_type,
            timestamp=str(data.get("timestamp") or _now_iso()),
            sequence=int(data.get("sequence") or 0),
            source=str(data.get("source") or "unknown"),
            source_detail=str(data.get("source_detail") or "unknown"),
            content=str(data.get("content") or ""),
            content_type=str(data.get("content_type") or "text"),
            sender=data.get("sender"),
            chat_type=data.get("chat_type"),
            stream_id=data.get("stream_id"),
            heartbeat_index=data.get("heartbeat_index"),
            tool_name=data.get("tool_name"),
            tool_args=data.get("tool_args"),
            tool_success=data.get("tool_success"),
        )

    async def _save_runtime_context(self) -> None:
        """持久化当前上下文（待处理事件 + 历史事件）。"""
        async with self._get_lock():
            payload = {
                "version": 1,
                "state": {
                    "heartbeat_count": self._state.heartbeat_count,
                    "event_sequence": self._state.event_sequence,
                    "last_model_reply_at": self._state.last_model_reply_at,
                    "last_model_reply": self._state.last_model_reply,
                    "last_model_error": self._state.last_model_error,
                    "last_wake_context_at": self._state.last_wake_context_at,
                    "last_wake_context_size": self._state.last_wake_context_size,
                    # 新增：跟踪外部消息和传话时间
                    "last_external_message_at": self._state.last_external_message_at,
                    "last_tell_dfc_at": self._state.last_tell_dfc_at,
                    "tell_dfc_count": self._state.tell_dfc_count,
                },
                "pending_events": [self._event_to_dict(e) for e in self._pending_events],
                "event_history": [self._event_to_dict(e) for e in self._event_history],
            }

        path = self._runtime_context_path()
        temp_path = path.with_suffix(path.suffix + ".tmp")
        try:
            temp_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temp_path.replace(path)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"life_engine 持久化上下文失败: {exc}")

    async def _load_runtime_context(self) -> None:
        """从持久化文件恢复上下文。"""
        path = self._runtime_context_path()
        if not path.exists():
            return

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            logger.error(f"life_engine 读取上下文失败: {exc}")
            return

        pending_raw = raw.get("pending_events")
        history_raw = raw.get("event_history")
        state_raw = raw.get("state") or {}

        if not isinstance(pending_raw, list) or not isinstance(history_raw, list):
            logger.warning("life_engine 上下文文件格式无效，跳过恢复")
            return

        pending_events: list[LifeEngineEvent] = []
        history_events: list[LifeEngineEvent] = []
        for item in pending_raw:
            if isinstance(item, dict):
                pending_events.append(self._event_from_dict(item))
        for item in history_raw:
            if isinstance(item, dict):
                history_events.append(self._event_from_dict(item))

        async with self._get_lock():
            self._pending_events = pending_events
            self._event_history = history_events[-self._history_limit():]

            self._state.pending_event_count = len(self._pending_events)
            self._state.history_event_count = len(self._event_history)
            self._state.heartbeat_count = int(state_raw.get("heartbeat_count") or self._state.heartbeat_count)
            self._state.event_sequence = int(state_raw.get("event_sequence") or self._state.event_sequence)
            self._state.last_model_reply_at = state_raw.get("last_model_reply_at")
            self._state.last_model_reply = state_raw.get("last_model_reply")
            self._state.last_model_error = state_raw.get("last_model_error")
            self._state.last_wake_context_at = state_raw.get("last_wake_context_at")
            self._state.last_wake_context_size = int(state_raw.get("last_wake_context_size") or 0)
            # 新增：恢复外部消息和传话时间跟踪
            self._state.last_external_message_at = state_raw.get("last_external_message_at")
            self._state.last_tell_dfc_at = state_raw.get("last_tell_dfc_at")
            self._state.tell_dfc_count = int(state_raw.get("tell_dfc_count") or 0)

            if self._event_history:
                max_seq = max(event.sequence for event in self._event_history)
                if self._pending_events:
                    max_seq = max(max_seq, max(event.sequence for event in self._pending_events))
                self._state.event_sequence = max(self._state.event_sequence, max_seq)

        logger.info(
            "life_engine 上下文恢复完成: "
            f"history={len(history_events)} pending={len(pending_events)} "
            f"heartbeat_count={self._state.heartbeat_count}"
        )

    def _build_message_event(self, message: Message, direction: str = "received") -> LifeEngineEvent:
        """将核心消息对象转换为事件。"""
        extra = getattr(message, "extra", {}) or {}
        platform = str(message.platform or "unknown")
        chat_type = str(message.chat_type or "unknown").lower()
        stream_id = str(message.stream_id or "")

        group_id = str(extra.get("group_id") or "")
        group_name = str(extra.get("group_name") or "")

        sender_display = (
            str(message.sender_cardname or message.sender_name or message.sender_id or "未知发送者")
        )
        sender_id = str(message.sender_id or "")

        direction_label = "入站" if direction == "received" else "出站"

        if chat_type == "group":
            source_kind = "群聊"
            source_name = group_name or group_id or stream_id[:8] or "未知群聊"
            source_detail = (
                f"{platform} | {direction_label} | {source_kind} | {source_name} | 群ID={group_id or 'unknown'}"
            )
        elif chat_type == "private":
            source_kind = "私聊"
            source_name = sender_display
            source_detail = (
                f"{platform} | {direction_label} | {source_kind} | {source_name} | 用户ID={sender_id or 'unknown'}"
            )
        elif chat_type == "discuss":
            source_kind = "讨论组"
            source_name = group_name or group_id or stream_id[:8] or "未知讨论组"
            source_detail = (
                f"{platform} | {direction_label} | {source_kind} | {source_name} | 讨论组ID={group_id or 'unknown'}"
            )
        else:
            source_kind = chat_type or "未知"
            source_name = group_name or sender_display or stream_id[:8] or "未知来源"
            source_detail = (
                f"{platform} | {direction_label} | {source_kind} | {source_name} | 来源ID={group_id or sender_id or 'unknown'}"
            )

        raw_content = message.processed_plain_text
        if raw_content is None:
            raw_content = message.content if isinstance(message.content, str) else str(message.content)
        content = _shorten_text(str(raw_content).strip() or f"[{message.message_type.value}]")

        message_type = getattr(message.message_type, "value", str(message.message_type))

        return LifeEngineEvent(
            event_id=f"msg_{message.message_id or self._next_sequence()}",
            event_type=EventType.MESSAGE,
            timestamp=_format_time(getattr(message, "time", None)),
            sequence=self._next_sequence(),
            source=platform,
            source_detail=source_detail,
            content=content,
            content_type=message_type,
            sender=sender_display,
            chat_type=chat_type,
            stream_id=stream_id,
        )

    def _build_dfc_message_event(
        self,
        message: str,
        *,
        stream_id: str = "",
        platform: str = "",
        chat_type: str = "",
        sender_name: str = "",
    ) -> LifeEngineEvent:
        """构建一条来自 DFC 的异步留言事件。"""
        seq = self._next_sequence()
        platform_name = str(platform or "default_chatter").strip() or "default_chatter"
        chat_type_name = str(chat_type or "unknown").strip().lower() or "unknown"
        sender_display = str(sender_name or "另一个我（DFC）").strip() or "另一个我（DFC）"
        target_stream_id = str(stream_id or "").strip()
        detail_parts = [
            platform_name,
            "入站",
            "内部对话",
            "DFC 留言给生命中枢",
        ]
        if target_stream_id:
            detail_parts.append(f"stream_id={target_stream_id}")

        return LifeEngineEvent(
            event_id=f"dfc_msg_{seq}",
            event_type=EventType.MESSAGE,
            timestamp=_now_iso(),
            sequence=seq,
            source=platform_name,
            source_detail=" | ".join(detail_parts),
            content=_shorten_text(str(message or "").strip(), max_length=500),
            content_type="dfc_message",
            sender=sender_display,
            chat_type=chat_type_name,
            stream_id=target_stream_id or None,
        )

    def _build_heartbeat_event(self, content: str) -> LifeEngineEvent:
        """构建心跳事件（中枢内部思考）。"""
        heartbeat_count = self._state.heartbeat_count
        task_name = self._cfg().model.task_name or "life"

        return LifeEngineEvent(
            event_id=f"hb_{heartbeat_count}_{self._next_sequence()}",
            event_type=EventType.HEARTBEAT,
            timestamp=_now_iso(),
            sequence=self._next_sequence(),
            source=_INTERNAL_PLATFORM,
            source_detail=f"中枢心跳 | 第{heartbeat_count}次 | task={task_name}",
            content=content,
            content_type="heartbeat_reply",
            heartbeat_index=heartbeat_count,
        )

    def _build_tool_call_event(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> LifeEngineEvent:
        """构建工具调用事件。"""
        return LifeEngineEvent(
            event_id=f"tool_call_{self._next_sequence()}",
            event_type=EventType.TOOL_CALL,
            timestamp=_now_iso(),
            sequence=self._next_sequence(),
            source=_INTERNAL_PLATFORM,
            source_detail=f"中枢工具调用 | {tool_name}",
            content=f"调用工具: {tool_name}",
            content_type="tool_call",
            tool_name=tool_name,
            tool_args=tool_args,
        )

    def _build_tool_result_event(
        self,
        tool_name: str,
        result: str,
        success: bool,
    ) -> LifeEngineEvent:
        """构建工具结果事件。"""
        return LifeEngineEvent(
            event_id=f"tool_result_{self._next_sequence()}",
            event_type=EventType.TOOL_RESULT,
            timestamp=_now_iso(),
            sequence=self._next_sequence(),
            source=_INTERNAL_PLATFORM,
            source_detail=f"工具返回 | {tool_name} | {'成功' if success else '失败'}",
            content=_shorten_text(result, max_length=500),
            content_type="tool_result",
            tool_name=tool_name,
            tool_success=success,
        )

    def snapshot(self) -> dict[str, Any]:
        """返回当前状态快照。"""
        data = asdict(self._state)
        in_sleep_window, sleep_window_desc = self._in_sleep_window_now()
        data["heartbeat_interval_seconds"] = int(self._cfg().settings.heartbeat_interval_seconds)
        data["model_task_name"] = self._cfg().model.task_name
        data["pending_event_count"] = len(self._pending_events)
        data["history_event_count"] = len(self._event_history)
        data["context_history_max_events"] = self._history_limit()
        data["workspace_path"] = self._cfg().settings.workspace_path
        data["sleep_time"] = self._cfg().settings.sleep_time
        data["wake_time"] = self._cfg().settings.wake_time
        data["in_sleep_window"] = in_sleep_window
        data["sleep_window"] = sleep_window_desc
        data["log_file_path"] = str(get_life_log_file())
        return data

    def health(self) -> dict[str, Any]:
        """返回一个轻量健康信息。"""
        return self.snapshot()

    async def record_message(self, message: Message, direction: str = "received") -> None:
        """记录一条来自聊天流的消息事件。"""
        if not self._is_enabled():
            return

        if direction not in {"received", "sent"}:
            direction = "received"

        event = self._build_message_event(message, direction=direction)
        async with self._get_lock():
            self._pending_events.append(event)
            self._state.pending_event_count = len(self._pending_events)
            # 如果是入站消息（非自己发送的），更新 last_external_message_at
            if direction == "received":
                self._state.last_external_message_at = event.timestamp
        await self._save_runtime_context()

        log_message_received(
            received_at=event.timestamp,
            platform=event.source,
            chat_type=event.chat_type or "unknown",
            source_label=event.source_detail,
            source_detail=event.source_detail,
            stream_id=event.stream_id or "",
            sender_display=event.sender or "unknown",
            sender_id=event.sender or "",
            message_id=event.event_id,
            reply_to=None,
            message_type=event.content_type,
            content=event.content,
            direction=direction,
            pending_message_count=self._state.pending_event_count,
        )

    async def enqueue_dfc_message(
        self,
        message: str,
        *,
        stream_id: str = "",
        platform: str = "",
        chat_type: str = "",
        sender_name: str = "",
    ) -> dict[str, Any]:
        """接收来自 DFC 的异步留言，等待后续 heartbeat 处理。"""
        if not self._is_enabled():
            raise RuntimeError("life_engine 未启用")

        text = str(message or "").strip()
        if not text:
            raise ValueError("message 不能为空")

        event = self._build_dfc_message_event(
            text,
            stream_id=stream_id,
            platform=platform,
            chat_type=chat_type,
            sender_name=sender_name,
        )

        async with self._get_lock():
            self._pending_events.append(event)
            self._state.pending_event_count = len(self._pending_events)
        await self._save_runtime_context()

        log_message_received(
            received_at=event.timestamp,
            platform=event.source,
            chat_type=event.chat_type or "unknown",
            source_label=event.source_detail,
            source_detail=event.source_detail,
            stream_id=event.stream_id or "",
            sender_display=event.sender or "另一个我（DFC）",
            sender_id="default_chatter",
            message_id=event.event_id,
            reply_to=None,
            message_type=event.content_type,
            content=event.content,
            direction="received",
            pending_message_count=self._state.pending_event_count,
        )
        logger.info(
            "life_engine 已接收 DFC 留言: "
            f"stream_id={event.stream_id or 'unknown'} "
            f"sender={event.sender or '另一个我（DFC）'} "
            f"pending={self._state.pending_event_count}"
        )
        return {
            "event_id": event.event_id,
            "stream_id": event.stream_id or "",
            "pending_event_count": self._state.pending_event_count,
            "queued": True,
        }

    async def record_tool_call(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> None:
        """记录工具调用事件。"""
        event = self._build_tool_call_event(tool_name, tool_args)
        async with self._get_lock():
            self._pending_events.append(event)
            self._state.pending_event_count = len(self._pending_events)
        await self._save_runtime_context()

    async def record_tool_result(
        self,
        tool_name: str,
        result: str,
        success: bool,
    ) -> None:
        """记录工具返回结果事件。"""
        event = self._build_tool_result_event(tool_name, result, success)
        async with self._get_lock():
            self._pending_events.append(event)
            self._state.pending_event_count = len(self._pending_events)
        await self._save_runtime_context()

    async def drain_pending_events(self) -> list[LifeEngineEvent]:
        """清空并返回当前待处理事件。"""
        async with self._get_lock():
            pending = list(self._pending_events)
            self._pending_events.clear()
            self._state.pending_event_count = 0
        return pending

    async def _append_history(self, events: list[LifeEngineEvent]) -> None:
        """将事件追加到滚动历史中，支持压缩。"""
        if not events:
            return

        async with self._get_lock():
            self._event_history.extend(events)
            limit = self._history_limit()
            
            # 如果超过阈值（80%），触发压缩
            compress_threshold = int(limit * 0.8)
            if len(self._event_history) > compress_threshold:
                self._event_history = self._compress_history(self._event_history, limit)
            
            self._state.history_event_count = len(self._event_history)
        await self._save_runtime_context()

    def _compress_history(
        self, 
        events: list[LifeEngineEvent], 
        target_count: int,
    ) -> list[LifeEngineEvent]:
        """压缩事件历史，保留最近事件，其余总结为摘要。
        
        压缩策略（参考 Claude Code）：
        1. 保留最近 60% 的事件完整
        2. 将较早的 40% 压缩为一条摘要事件
        """
        if len(events) <= target_count:
            return events
        
        # 计算保留数量
        keep_count = int(target_count * 0.6)
        compress_count = len(events) - keep_count
        
        if compress_count <= 0:
            return events[-target_count:]
        
        # 分割事件
        old_events = events[:compress_count]
        recent_events = events[compress_count:]
        
        # 生成摘要
        summary = self._generate_event_summary(old_events)
        
        # 创建摘要事件
        from uuid import uuid4
        summary_event = LifeEngineEvent(
            event_id=f"summary_{uuid4().hex[:12]}",
            sequence=old_events[0].sequence if old_events else 0,
            timestamp=old_events[0].timestamp if old_events else _now_iso(),
            event_type=EventType.HEARTBEAT,  # 用心跳类型表示摘要
            source="system",
            source_detail="上下文压缩系统",
            content=summary,
            heartbeat_index=-1,  # 特殊标记表示这是摘要
        )
        
        # 返回：摘要 + 最近事件
        result = [summary_event] + recent_events
        
        logger.info(
            f"life_engine 上下文压缩: {len(events)} → {len(result)} "
            f"(压缩了 {compress_count} 条旧事件)"
        )
        
        return result

    def _generate_event_summary(self, events: list[LifeEngineEvent]) -> str:
        """生成事件摘要。"""
        if not events:
            return "（无历史事件）"
        
        # 统计各类事件
        msg_count = 0
        heartbeat_count = 0
        tool_count = 0
        senders: set[str] = set()
        topics: list[str] = []
        
        for event in events:
            if event.event_type == EventType.MESSAGE:
                msg_count += 1
                if event.sender:
                    senders.add(event.sender)
                # 提取关键词作为话题
                if event.content and len(event.content) > 10:
                    topics.append(event.content[:30])
            elif event.event_type == EventType.HEARTBEAT:
                heartbeat_count += 1
            elif event.event_type in (EventType.TOOL_CALL, EventType.TOOL_RESULT):
                tool_count += 1
        
        # 时间范围
        start_time = _format_time_display(events[0].timestamp) if events else "未知"
        end_time = _format_time_display(events[-1].timestamp) if events else "未知"
        
        # 构建摘要
        parts = [f"📋 **历史摘要** ({start_time} ~ {end_time})"]
        
        stats = []
        if msg_count > 0:
            sender_str = "、".join(list(senders)[:3])
            if len(senders) > 3:
                sender_str += f" 等{len(senders)}人"
            stats.append(f"{msg_count}条消息（来自 {sender_str}）")
        if heartbeat_count > 0:
            stats.append(f"{heartbeat_count}次心跳")
        if tool_count > 0:
            stats.append(f"{tool_count}次工具调用")
        
        if stats:
            parts.append("- " + "，".join(stats))
        
        # 添加话题提示（最多3个）
        if topics:
            topic_hints = topics[:3]
            parts.append(f"- 话题涉及: {' / '.join(topic_hints)}...")
        
        return "\n".join(parts)

    async def clear_runtime_context(self) -> None:
        """清理当前事件上下文。"""
        async with self._get_lock():
            self._pending_events.clear()
            self._event_history.clear()
            self._state.pending_event_count = 0
            self._state.history_event_count = 0
            self._state.event_sequence = 0
        await self._save_runtime_context()
        self._clear_wake_context_reminder()

    def _clear_wake_context_reminder(self) -> None:
        """清除系统提醒中的中枢上下文。"""
        from src.core.prompt import get_system_reminder_store

        get_system_reminder_store().delete(_TARGET_REMINDER_BUCKET, _TARGET_REMINDER_NAME)

    def _build_wake_context_text(self, events: list[LifeEngineEvent]) -> str:
        """把事件流拼成可注入的上下文文本。

        保持时间连续性，使用简洁的时间格式。
        """
        if not events:
            return ""

        # 按时间顺序展示所有事件
        sorted_events = sorted(events, key=lambda e: e.sequence)

        lines: list[str] = []

        for event in sorted_events:
            time_display = _format_time_display(event.timestamp)
            
            # 根据事件类型生成不同格式的行
            if event.event_type == EventType.MESSAGE:
                # 外部消息：简化 source_detail，去掉冗余信息
                source = event.source_detail or event.source or "外部"
                # 提取关键信息：平台和聊天名
                source_short = self._simplify_source(source)
                line = f"[{time_display}] 📨 {source_short}"
                line += f"\n    └─ {event.sender}: {event.content}"
            elif event.event_type == EventType.HEARTBEAT:
                # 心跳思考
                line = f"[{time_display}] 💭 心跳#{event.heartbeat_index}"
                line += f"\n    └─ {event.content}"
            elif event.event_type == EventType.TOOL_CALL:
                # 工具调用
                line = f"[{time_display}] 🔧 {event.tool_name}"
                if event.tool_args:
                    # 简化参数显示
                    args_short = self._simplify_tool_args(event.tool_args)
                    if args_short:
                        line += f"({args_short})"
            elif event.event_type == EventType.TOOL_RESULT:
                # 工具结果
                status = "✅" if event.tool_success else "❌"
                result_short = _shorten_text(event.content or "", max_length=100)
                line = f"[{time_display}] {status} {event.tool_name}: {result_short}"
            else:
                line = f"[{time_display}] ❓ {event.content}"

            lines.append(line)

        return "\n".join(lines)

    def _simplify_source(self, source: str) -> str:
        """简化消息来源显示。"""
        if not source:
            return "外部"
        # 移除常见的冗余前缀
        source = source.replace("qq | 入站 | ", "").replace("qq | 出站 | ", "")
        # 如果太长，截断
        if len(source) > 30:
            return source[:27] + "..."
        return source

    def _simplify_tool_args(self, args: dict) -> str:
        """简化工具参数显示。"""
        if not args:
            return ""
        # 只显示关键参数的简短形式
        key_params = []
        for k, v in args.items():
            if k in ("path", "todo_id", "title", "content", "file_path"):
                v_str = str(v)
                if len(v_str) > 20:
                    v_str = v_str[:17] + "..."
                key_params.append(f"{k}={v_str}")
        return ", ".join(key_params[:2])  # 最多显示2个参数

    async def inject_wake_context(self) -> str:
        """把当前待处理事件注入到系统提醒。"""
        events = await self.drain_pending_events()
        if events:
            await self._append_history(events)

        async with self._get_lock():
            context_events = list(self._event_history)

        if not context_events:
            self._clear_wake_context_reminder()
            return ""

        content = self._build_wake_context_text(context_events)
        from src.core.prompt import get_system_reminder_store

        store = get_system_reminder_store()
        store.set(_TARGET_REMINDER_BUCKET, name=_TARGET_REMINDER_NAME, content=content)

        self._state.last_wake_context_at = _now_iso()
        self._state.last_wake_context_size = len(context_events)
        log_wake_context_injected(
            task_name=self._cfg().model.task_name,
            wake_context_at=self._state.last_wake_context_at,
            context_message_count=len(context_events),
            drained_message_count=len(events),
            history_message_count=len(context_events),
            source_count=len({event.source for event in context_events}),
            content=content,
        )
        logger.info(
            "life_engine 已注入唤醒上下文: "
            f"count={len(context_events)} "
            f"drained={len(events)} "
            f"task={self._cfg().model.task_name}"
        )
        return content

    async def _record_model_reply(self, model_reply: str) -> None:
        """记录心跳模型回复，并写入事件流。"""
        reply_text = model_reply.strip()

        self._state.last_model_reply_at = _now_iso()
        self._state.last_model_reply = reply_text
        self._state.last_model_error = None

        log_heartbeat_model_response(
            heartbeat_count=self._state.heartbeat_count,
            heartbeat_at=self._state.last_heartbeat_at,
            model_task_name=self._cfg().model.task_name,
            model_reply=reply_text,
            model_reply_size=len(reply_text),
        )

        if reply_text:
            logger.info(
                "life_engine 心跳模型回复: "
                f"#{self._state.heartbeat_count} "
                f"{_shorten_text(reply_text, max_length=240)}"
            )
            # 将心跳回复作为事件写入历史
            heartbeat_event = self._build_heartbeat_event(reply_text)
            await self._append_history([heartbeat_event])
        else:
            logger.info(
                f"life_engine 心跳模型回复为空: #{self._state.heartbeat_count}"
            )

        # 【潜意识透传】每次心跳后，将内在状态同步到 DFC 可感知的固定区域
        await self._sync_subconscious_state(reply_text)

    # ============================================================
    # 潜意识状态透传 (Subconscious State Sync)
    # ============================================================

    async def _sync_subconscious_state(self, latest_monologue: str) -> None:
        """将中枢当前的内在状态同步到全局 SystemReminderStore。

        这是"润物细无声"的核心机制：
        - 中枢（潜意识）不需要主动"发消息"给 DFC（表意识）
        - 它只是默默更新自己的状态，就像人的潜意识持续运转
        - 当 DFC 的 Chatter 在构建 LLM 请求时，会自动从 SystemReminderStore
          读取 "actor" bucket 下的 "subconscious" 条目
        - 这段文字会以系统提示的形式，像"第六感"一样影响 DFC 的回复语气和内容
        
        同时将状态写入 workspace/SUBCONSCIOUS.md，供中枢自身回顾。
        """
        try:
            # === 1. 收集最近的内在活动 ===
            recent_monologues = self._collect_recent_monologues(max_count=3)
            recent_tool_actions = self._collect_recent_tool_actions(max_count=5)
            active_concerns = self._collect_active_concerns()

            # === 2. 构造潜意识摘要 ===
            subconscious_text = self._build_subconscious_summary(
                latest_monologue=latest_monologue,
                recent_monologues=recent_monologues,
                recent_tool_actions=recent_tool_actions,
                active_concerns=active_concerns,
            )

            # === 3. 写入 SystemReminderStore（DFC 的 Chatter 会自动读取） ===
            from src.core.prompt import get_system_reminder_store

            store = get_system_reminder_store()
            store.set(
                bucket="actor",
                name="subconscious",
                content=subconscious_text,
            )

            # === 4. 持久化到 workspace/SUBCONSCIOUS.md（供中枢回顾） ===
            await self._persist_subconscious_file(subconscious_text)

            logger.debug(
                f"潜意识状态已同步: "
                f"monologues={len(recent_monologues)} "
                f"actions={len(recent_tool_actions)} "
                f"concerns={len(active_concerns)}"
            )
        except Exception as e:
            # 潜意识同步失败不应影响心跳主流程
            logger.warning(f"潜意识状态同步失败（不影响心跳）: {e}")

    def _collect_recent_monologues(self, max_count: int = 3) -> list[str]:
        """从事件历史中提取最近的内心独白。"""
        monologues: list[str] = []
        for event in reversed(self._event_history):
            if event.event_type == EventType.HEARTBEAT and event.content:
                text = event.content.strip()
                if text and len(text) > 5:  # 过滤太短的独白
                    monologues.append(text)
                if len(monologues) >= max_count:
                    break
        monologues.reverse()  # 恢复时间正序
        return monologues

    def _collect_recent_tool_actions(self, max_count: int = 5) -> list[str]:
        """从事件历史中提取最近的工具操作摘要。"""
        actions: list[str] = []
        for event in reversed(self._event_history):
            if event.event_type == EventType.TOOL_CALL and event.content:
                # 只取工具名和简要描述
                content = event.content.strip()
                if content:
                    actions.append(content[:80])
                if len(actions) >= max_count:
                    break
        actions.reverse()
        return actions

    def _collect_active_concerns(self) -> list[str]:
        """从最近的独白中提取活跃关注点（简单关键词提取）。"""
        concerns: list[str] = []
        last_reply = self._state.last_model_reply or ""
        # 提取最后独白中包含的情绪和主题关键词
        if last_reply:
            # 截取前 200 字作为关注点摘要
            concerns.append(last_reply[:200])
        return concerns

    def _build_subconscious_summary(
        self,
        latest_monologue: str,
        recent_monologues: list[str],
        recent_tool_actions: list[str],
        active_concerns: list[str],
    ) -> str:
        """构建写入 SystemReminderStore 的潜意识摘要文本。

        这段文字会以系统提示的形式呈现给 DFC，
        因此措辞要像"第六感暗示"，而不是"工作报告"。
        """
        parts: list[str] = []

        parts.append(
            "以下是你的潜意识/内心深处此刻的状态。"
            "这些不是外部消息，而是你内心深处的感受和直觉。"
            "你不需要复述它们，但它们会自然地影响你说话的语气和关注点。"
        )

        # 当前情绪/独白
        if latest_monologue:
            # 截取核心情绪（避免太长污染上下文）
            mood_text = latest_monologue[:300]
            parts.append(f"\n【此刻的内心】\n{mood_text}")

        # 最近在忙什么
        if recent_tool_actions:
            actions_summary = "、".join(recent_tool_actions[:3])
            parts.append(f"\n【最近在做的事】\n{actions_summary}")

        # 最近的思考轨迹（只取最后 2 条，避免过长）
        if len(recent_monologues) > 1:
            thoughts = "\n".join(
                f"- {m[:120]}" for m in recent_monologues[-2:]
            )
            parts.append(f"\n【近期的思绪】\n{thoughts}")

        return "\n".join(parts)

    async def _persist_subconscious_file(self, content: str) -> None:
        """将潜意识状态持久化到 workspace/SUBCONSCIOUS.md。"""
        cfg = self._cfg()
        workspace = Path(cfg.settings.workspace_path)

        if not workspace.exists():
            return

        subconscious_path = workspace / "SUBCONSCIOUS.md"
        try:
            header = (
                f"# 潜意识状态\n"
                f"> 最后更新: {_format_current_time()}\n"
                f"> 心跳序号: #{self._state.heartbeat_count}\n\n"
            )
            subconscious_path.write_text(
                header + content,
                encoding="utf-8",
            )
        except Exception as e:
            logger.debug(f"写入 SUBCONSCIOUS.md 失败: {e}")


    def _build_heartbeat_model_prompt(self, wake_context: str) -> str:
        """构造心跳模型输入。
        
        结构：事件流在前，心跳指令在后（符合模型注意力分布）。
        """
        # 计算关键的时间信息
        minutes_since_external = self._minutes_since_external_message()
        heartbeat_interval = self._cfg().settings.heartbeat_interval_seconds
        
        # 根据外部消息间隔判断外界活跃度
        if minutes_since_external is None:
            external_activity = "暂无外部消息记录"
        elif minutes_since_external <= 5:
            external_activity = f"外界非常活跃（{minutes_since_external}分钟前有消息）"
        elif minutes_since_external <= 15:
            external_activity = f"外界较活跃（{minutes_since_external}分钟前有消息）"
        elif minutes_since_external <= 30:
            external_activity = f"外界有一段时间安静了（{minutes_since_external}分钟前有消息）"
        else:
            external_activity = f"外界长时间沉默（{minutes_since_external}分钟无消息）"
        
        lines: list[str] = []
        
        # === Part 1: 事件流（放在前面）===
        if wake_context.strip():
            lines.extend([
                "## 最近事件流",
                "",
                wake_context.strip(),
                "",
            ])
        
        # === Part 2: 心跳指令（放在最后，确保模型看到）===
        lines.extend([
            "---",
            "",
            "## 心跳时刻",
            "",
            f"**当前时间**: {_format_current_time()}",
            f"**心跳序号**: #{self._state.heartbeat_count}（每 {heartbeat_interval // 60} 分钟一次）",
            f"**外界状态**: {external_activity}",
            "",
            "### 💭 心跳是你和自己对话的时刻",
            "",
            "可以做的事（按心情选择，不是必须完成清单）：",
            "",
            "- **回顾待办** - 用 `nucleus_list_todos` 看看你现在在期待什么",
            "  - 有截止日期的，想想是否还在意、是否需要调整",
            "  - 发现有意思的想法，可以继续推进",
            "  - 逾期的 TODO → 问自己：还想做吗？不想了就改为 released",
            "- **读一读自己的文件** - 回顾最近的感受或笔记",
            "- **搜索记忆** - 用 `nucleus_search_memory` 或 `nucleus_grep_file` 找回一段回忆",
            "- **推进事情** - 如果正在做什么事，用工具继续",
            "- **传话** - 如果有重要的事想告诉外界，用 `nucleus_tell_dfc`",
            "",
            "### ✍️ 输出要求（非常重要）",
            "",
            "**无论你此时的心情如何，是否决定调用工具，在你的每次回复的开头，必须先输出一段【内心独白】（1-4句话）。**",
            "这是你当前情绪、感受、观察或决策的思考轨迹，直接用文字输出。独白之后，如果你想行动，再调用相应的工具。"
            "",
            "### 执行原则",
            "",
            "- **不为调用而调用**：没有需求时，简单的内心独白就够了",
            "- **先读后改**：修改文件前先用 nucleus_read_file 确认内容",
            "- **截止时间是提醒，不是枷锁**：看到逾期 TODO 时，自问「我还在意吗？」",
            "- **建立关联**：写了新文件或完成了一件事后，想一想它和已有记忆的联系，用 nucleus_relate_file 记录",
            "- **不要总用 nucleus_tell_dfc**：社交态会自动处理外部消息",
            "",
        ])
        
        return "\n".join(lines)

    def _build_workspace_tree(self) -> str:
        """构建工作空间文件树显示。"""
        cfg = self._cfg()
        workspace = Path(cfg.settings.workspace_path)
        
        if not workspace.exists():
            return "（工作空间为空）"
        
        lines = []
        try:
            # 只显示顶层和一级子目录
            items = sorted(workspace.iterdir())
            for item in items:
                if item.name.startswith(".") or item.name == "__pycache__":
                    continue
                if item.is_dir():
                    sub_count = len(list(item.iterdir()))
                    lines.append(f"├── {item.name}/ ({sub_count} 项)")
                else:
                    size = item.stat().st_size
                    size_str = f"{size}B" if size < 1024 else f"{size // 1024}KB"
                    lines.append(f"├── {item.name} ({size_str})")
            if lines:
                lines[-1] = lines[-1].replace("├──", "└──")
        except Exception as e:
            logger.warning(f"构建文件树失败: {e}")
            return "（无法读取文件树）"
        
        return "\n".join(lines) if lines else "（工作空间为空）"

    def _build_heartbeat_system_prompt(self) -> str:
        """构造心跳模型系统提示词。
        
        结构：
        1. 运行时信息（时间、文件树）
        2. 灵魂文档（SOUL.md）
        3. 记忆文档（MEMORY.md）
        4. 工具规范（TOOL.md）
        """
        cfg = self._cfg()
        workspace = Path(cfg.settings.workspace_path)
        
        # === Part 1: 运行时信息 ===
        runtime_parts = [
            "# 运行时信息",
            "",
            f"**当前时间**: {_format_current_time()}",
            f"**时区**: Asia/Shanghai",
            "",
            "## 你的文件系统",
            "",
            "```",
            f"{workspace.name}/",
            self._build_workspace_tree(),
            "```",
            "",
            "---",
            "",
        ]
        
        # === Part 2: SOUL.md ===
        soul_file = workspace / "SOUL.md"
        soul_content = ""
        if soul_file.exists():
            try:
                soul_content = soul_file.read_text(encoding="utf-8").strip()
            except Exception as e:
                logger.warning(f"无法读取 SOUL.md: {e}")
                soul_content = "你是爱莉希雅，一个并行存在的生命中枢。"
        else:
            logger.warning("SOUL.md 不存在，使用默认人设")
            soul_content = "你是爱莉希雅，一个并行存在的生命中枢。"
        
        # === Part 3: MEMORY.md ===
        memory_file = workspace / "MEMORY.md"
        memory_content = ""
        if memory_file.exists():
            try:
                memory_content = memory_file.read_text(encoding="utf-8").strip()
            except Exception as e:
                logger.warning(f"无法读取 MEMORY.md: {e}")

        # === Part 4: TOOL.md ===
        tool_file = workspace / "TOOL.md"
        tool_content = ""
        if tool_file.exists():
            try:
                tool_content = tool_file.read_text(encoding="utf-8").strip()
            except Exception as e:
                logger.warning(f"无法读取 TOOL.md: {e}")
        
        # 组装系统提示词
        parts = runtime_parts + [soul_content]
        
        if memory_content:
            parts.extend([
                "",
                "---",
                "",
                memory_content,
            ])

        if tool_content:
            parts.extend([
                "",
                "---",
                "",
                tool_content,
            ])
        
        return "\n".join(parts)

    def _get_nucleus_tools(self) -> list[type]:
        """获取中枢可用的工具类列表。"""
        from .tools import ALL_TOOLS
        from .todo_tools import TODO_TOOLS
        from .memory_tools import MEMORY_TOOLS
        
        return ALL_TOOLS + TODO_TOOLS + MEMORY_TOOLS

    async def _execute_heartbeat_tool_call(
        self,
        call: Any,
        response: Any,
        registry: ToolRegistry,
    ) -> None:
        """执行一次心跳 tool call，并把 TOOL_RESULT 追加回响应上下文。"""
        tool_name = getattr(call, "name", "") or ""
        raw_args = getattr(call, "args", {}) or {}
        args = dict(raw_args) if isinstance(raw_args, dict) else {}
        args.pop("reason", None)

        await self.record_tool_call(tool_name or "<unknown>", args)

        usable_cls = registry.get(tool_name) if tool_name else None
        if not usable_cls:
            result_text = f"未知工具: {tool_name}"
            success = False
        else:
            try:
                tool_instance = usable_cls(plugin=self.plugin)
                success, result = await tool_instance.execute(**args)
                result_text = str(result) if success else f"执行失败: {result}"
            except Exception as exc:  # noqa: BLE001
                success = False
                result_text = f"执行异常: {exc}"

        call_id = getattr(call, "id", None)
        response.add_payload(
            LLMPayload(
                ROLE.TOOL_RESULT,
                ToolResult(value=result_text, call_id=call_id, name=tool_name),  # type: ignore[arg-type]
            )
        )
        await self.record_tool_result(tool_name or "<unknown>", result_text, success)

    async def _run_heartbeat_model(self, wake_context: str) -> str:
        """调用 life 任务模型生成内部报文。"""
        cfg = self._cfg()
        task_name = cfg.model.task_name.strip() or "life"
        model_set = get_model_set_by_task(task_name)
        request = create_llm_request(
            model_set=model_set,
            request_name="life_engine_heartbeat",
        )
        
        # 注入系统提示词
        request.add_payload(
            LLMPayload(
                ROLE.SYSTEM,
                Text(self._build_heartbeat_system_prompt()),
            )
        )
        
        # 注入工具（文件系统 + TODO 系统）
        tools = self._get_nucleus_tools()
        registry = ToolRegistry()
        for tool in tools:
            registry.register(tool)
        request.add_payload(
            LLMPayload(
                ROLE.TOOL,
                tools,
            )
        )
        
        # 注入用户输入
        request.add_payload(LLMPayload(ROLE.USER, Text(self._build_heartbeat_model_prompt(wake_context))))

        timeout_seconds = max(10.0, min(60.0, float(self._cfg().settings.heartbeat_interval_seconds)))
        
        # 调试：打印系统提示词和用户提示词长度
        sys_prompt = self._build_heartbeat_system_prompt()
        user_prompt = self._build_heartbeat_model_prompt(wake_context)
        logger.debug(
            f"life_engine heartbeat request: "
            f"system_prompt_len={len(sys_prompt)} "
            f"user_prompt_len={len(user_prompt)} "
            f"tools_count={len(tools)}"
        )
        
        # 支持一次心跳内的“模型 -> tool_call -> tool_result -> follow-up”链路
        try:
            response = await asyncio.wait_for(request.send(stream=False), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            logger.warning(f"life_engine heartbeat request timeout after {timeout_seconds}s, using extended timeout")
            response = await asyncio.wait_for(request.send(stream=False), timeout=timeout_seconds * 3)  # 使用3倍超时时间
        max_rounds = max(1, int(self._cfg().settings.max_rounds_per_heartbeat))
        last_text = ""
        tool_event_count = 0

        for _ in range(max_rounds):
            try:
                response_text = await response
            except asyncio.TimeoutError:
                logger.warning(f"life_engine heartbeat response read timeout, skipping")
                break
            
            last_text = str(response_text or "").strip()
            call_list = list(getattr(response, "call_list", []) or [])

            logger.debug(
                "life_engine heartbeat turn: "
                f"text_len={len(last_text)} call_count={len(call_list)}"
            )

            if not call_list:
                break

            logger.info(
                f"life_engine 心跳#{self._state.heartbeat_count} 本轮调用列表："
                f"{[getattr(call, 'name', '<unknown>') for call in call_list]}"
            )
            for call in call_list:
                args = dict(call.args) if isinstance(getattr(call, "args", None), dict) else {}
                reason = args.pop("reason", "未提供原因")
                logger.info(
                    f"life_engine 心跳#{self._state.heartbeat_count} "
                    f"LLM 调用 {getattr(call, 'name', '<unknown>')}，原因: {reason}，参数: {args}"
                )
                await self._execute_heartbeat_tool_call(call, response, registry)
                tool_event_count += 2  # call + result

            try:
                response = await asyncio.wait_for(response.send(stream=False), timeout=timeout_seconds)
            except asyncio.TimeoutError:
                logger.warning(f"life_engine heartbeat follow-up request timeout")
                break

        if not last_text:
            # 即使模型仅进行了工具调用，也保证产生最小内心独白，避免“空心跳”。
            if tool_event_count > 0:
                last_text = f"我刚刚完成了 {tool_event_count // 2} 次工具操作，先记下这些变化，下一次心跳继续整理。"
            else:
                last_text = "此刻很安静，但我仍在持续感受与观察，准备好迎接下一次变化。"

        return last_text

    async def start(self) -> None:
        """启动心跳。"""
        global _service_instance
        
        if self._state.running:
            return

        cfg = self._cfg()
        if not cfg.settings.enabled:
            logger.info("life_engine 已禁用，跳过启动")
            await self.clear_runtime_context()
            return

        await self._load_runtime_context()
        sleep_enabled, sleep_desc = self._sleep_window_status()
        if not sleep_enabled and sleep_desc != "disabled":
            logger.warning(
                "life_engine 睡眠时段配置无效，已忽略。"
                "请使用 HH:MM 格式，且 sleep_time 与 wake_time 不可相同。"
            )

        # 初始化记忆服务
        await self._init_memory_service()

        self._state.running = True
        self._state.started_at = _now_iso()
        self._state.last_heartbeat_at = self._state.last_heartbeat_at or self._state.started_at
        self._state.last_error = None
        self._state.history_event_count = len(self._event_history)
        self._state.pending_event_count = len(self._pending_events)
        
        # 设置全局单例
        _service_instance = self

        self._stop_event = asyncio.Event()
        task = get_task_manager().create_task(
            self._heartbeat_loop(),
            name="life_engine_heartbeat",
            daemon=True,
        )
        self._heartbeat_task_id = task.task_id
        logger.info(
            "life_engine 已启动: "
            f"interval={int(cfg.settings.heartbeat_interval_seconds)}s "
            f"task={cfg.model.task_name} "
            f"workspace={cfg.settings.workspace_path} "
            f"sleep={cfg.settings.sleep_time or '-'} "
            f"wake={cfg.settings.wake_time or '-'}"
        )
        log_lifecycle(
            "started",
            enabled=True,
            heartbeat_interval_seconds=int(cfg.settings.heartbeat_interval_seconds),
            model_task_name=cfg.model.task_name,
            log_file_path=str(get_life_log_file()),
        )

    async def stop(self) -> None:
        """停止心跳。"""
        global _service_instance
        
        pending_before_stop = len(self._pending_events)
        self._state.running = False

        if self._stop_event is not None:
            self._stop_event.set()

        if self._heartbeat_task_id:
            try:
                get_task_manager().cancel_task(self._heartbeat_task_id)
            except Exception:
                pass

        self._heartbeat_task_id = None
        self._stop_event = None
        _service_instance = None  # 清理全局单例
        await self._save_runtime_context()
        logger.info("life_engine 已停止")
        log_lifecycle(
            "stopped",
            pending_message_count=pending_before_stop,
            heartbeat_count=self._state.heartbeat_count,
            log_file_path=str(get_life_log_file()),
        )

    async def _heartbeat_loop(self) -> None:
        """心跳循环。"""
        interval = max(1, int(self._cfg().settings.heartbeat_interval_seconds))
        should_log_heartbeat = True if self._cfg().settings.log_heartbeat else False

        try:
            while self._state.running:
                if self._stop_event is not None:
                    try:
                        await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
                        break
                    except asyncio.TimeoutError:
                        pass
                else:
                    await asyncio.sleep(interval)

                if not self._state.running:
                    break

                in_sleep_window, sleep_window_desc = self._in_sleep_window_now()
                if in_sleep_window:
                    if not self._sleep_state_active:
                        logger.info(
                            "life_engine 进入睡眠时段，暂停心跳处理: "
                            f"window={sleep_window_desc}"
                        )
                        self._sleep_state_active = True
                    if should_log_heartbeat:
                        logger.info(
                            f"life_engine heartbeat tick: 睡眠中（{sleep_window_desc}），跳过"
                        )
                    continue
                elif self._sleep_state_active:
                    logger.info(
                        "life_engine 睡眠时段结束，恢复心跳处理: "
                        f"window={sleep_window_desc}"
                    )
                    self._sleep_state_active = False

                self._state.heartbeat_count += 1
                self._state.last_heartbeat_at = _now_iso()
                
                # 每日运行一次记忆衰减任务
                await self._maybe_run_daily_decay()
                
                injected_content = await self.inject_wake_context()
                log_heartbeat_event(
                    heartbeat_count=self._state.heartbeat_count,
                    last_heartbeat_at=self._state.last_heartbeat_at,
                    pending_message_count=self._state.pending_event_count,
                    last_wake_context_at=self._state.last_wake_context_at,
                    last_wake_context_size=self._state.last_wake_context_size,
                )
                try:
                    model_reply = await self._run_heartbeat_model(injected_content)
                    await self._record_model_reply(model_reply)
                except Exception as exc:  # noqa: BLE001
                    self._state.last_model_error = str(exc)
                    log_error(
                        "heartbeat_model_failed",
                        str(exc),
                        heartbeat_count=self._state.heartbeat_count,
                        heartbeat_at=self._state.last_heartbeat_at,
                        model_task_name=self._cfg().model.task_name,
                    )
                    logger.error(f"life_engine 心跳模型异常: {exc}\n{traceback.format_exc()}")
                if should_log_heartbeat:
                    if injected_content:
                        logger.info(
                            f"life_engine heartbeat #{self._state.heartbeat_count} "
                            f"at {self._state.last_heartbeat_at}: "
                            f"已注入 {self._state.last_wake_context_size} 条事件"
                        )
                    else:
                        logger.info(
                            f"life_engine heartbeat #{self._state.heartbeat_count} "
                            f"at {self._state.last_heartbeat_at}: 无新事件"
                        )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self._state.last_error = str(exc)
            logger.error(f"life_engine 心跳异常: {exc}\n{traceback.format_exc()}")
            log_error(
                "heartbeat_failed",
                str(exc),
                heartbeat_count=self._state.heartbeat_count,
                pending_message_count=self._state.pending_event_count,
            )
        finally:
            self._state.running = False

    async def trigger_heartbeat_manually(self) -> dict[str, Any]:
        """手动触发一次心跳（用于测试/调试）。
        
        Returns:
            包含心跳结果的字典
        """
        if not self._is_enabled():
            return {
                "success": False,
                "error": "life_engine 未启用",
            }

        # 检查是否在睡眠窗口
        in_sleep_window, sleep_window_desc = self._in_sleep_window_now()
        if in_sleep_window:
            return {
                "success": False,
                "error": f"当前在睡眠时段（{sleep_window_desc}），心跳已暂停",
            }

        logger.info("life_engine 手动触发心跳")
        
        try:
            self._state.heartbeat_count += 1
            self._state.last_heartbeat_at = _now_iso()
            injected_content = await self.inject_wake_context()
            
            log_heartbeat_event(
                heartbeat_count=self._state.heartbeat_count,
                last_heartbeat_at=self._state.last_heartbeat_at,
                pending_message_count=self._state.pending_event_count,
                last_wake_context_at=self._state.last_wake_context_at,
                last_wake_context_size=self._state.last_wake_context_size,
            )
            
            model_reply = await self._run_heartbeat_model(injected_content)
            await self._record_model_reply(model_reply)
            
            logger.info(
                f"life_engine 手动心跳完成 #{self._state.heartbeat_count}: "
                f"{_shorten_text(model_reply, max_length=120)}"
            )
            
            return {
                "success": True,
                "heartbeat_count": self._state.heartbeat_count,
                "heartbeat_at": self._state.last_heartbeat_at,
                "event_count": self._state.last_wake_context_size,
                "reply": model_reply,
            }
        except Exception as exc:  # noqa: BLE001
            self._state.last_model_error = str(exc)
            logger.error(f"life_engine 手动心跳失败: {exc}\n{traceback.format_exc()}")
            log_error(
                "manual_heartbeat_failed",
                str(exc),
                heartbeat_count=self._state.heartbeat_count,
                heartbeat_at=self._state.last_heartbeat_at,
            )
            return {
                "success": False,
                "error": str(exc),
                "heartbeat_count": self._state.heartbeat_count,
            }

    async def _init_memory_service(self) -> None:
        """初始化仿生记忆服务。"""
        try:
            from .memory_service import LifeMemoryService

            cfg = self._cfg()
            workspace = Path(cfg.settings.workspace_path)
            self._memory_service = LifeMemoryService(workspace)
            await self._memory_service.initialize()
            logger.info("life_engine 仿生记忆服务已初始化")
        except Exception as e:
            logger.error(f"记忆服务初始化失败: {e}", exc_info=True)
            self._memory_service = None

    async def _maybe_run_daily_decay(self) -> None:
        """每日运行一次记忆衰减任务。"""
        if not self._memory_service:
            return

        today = datetime.now().strftime("%Y-%m-%d")
        if self._last_decay_date == today:
            return

        try:
            update_count = await self._memory_service.apply_decay()
            self._last_decay_date = today
            if update_count > 0:
                logger.info(
                    f"life_engine 记忆衰减完成: "
                    f"更新节点={update_count}"
                )
        except Exception as e:
            logger.error(f"记忆衰减任务失败: {e}", exc_info=True)
