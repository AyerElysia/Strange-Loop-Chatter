"""life_engine 生命中枢服务。

生命中枢是一个独立于 DFC（对话流控制器）的并行存在系统。
它通过周期性心跳来处理堆积的消息、进行内部思考、并为未来的
工具调用、主动唤醒 DFC 等功能提供基础骨架。

核心设计：
1. 事件流：所有交互（消息、心跳、工具调用）统一为 Event，保持时间连续性
2. 心跳循环：定期唤醒，处理堆积的事件
3. 上下文管理：维护滚动的事件流历史
4. 未来扩展：工具调用、主动唤醒 DFC、记忆/反思/探索等
"""

from __future__ import annotations

import asyncio
import json
import traceback
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

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
    """格式化消息时间。"""
    if raw_time is None:
        return _now_iso()
    try:
        return datetime.fromtimestamp(float(raw_time), tz=timezone.utc).astimezone().isoformat()
    except Exception:
        return _now_iso()


def _shorten_text(text: str, *, max_length: int = 240) -> str:
    """截断过长文本，保持唤醒上下文可读。"""
    normalized = " ".join(text.split())
    if len(normalized) <= max_length:
        return normalized
    return normalized[: max_length - 1] + "…"


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


class LifeEngineService(BaseService):
    """life_engine 心跳服务。

    这个版本使用统一的事件流模型，所有交互保持时间连续性。
    不参与正常聊天流程，不做回复决策。
    """

    service_name: str = "life_engine"
    service_description: str = "生命中枢服务，维持并行心跳与事件流上下文"
    version: str = "3.0.0"

    def __init__(self, plugin) -> None:
        super().__init__(plugin)
        self._state = LifeEngineState()
        self._heartbeat_task_id: str | None = None
        self._stop_event: asyncio.Event | None = None
        self._pending_events: list[LifeEngineEvent] = []
        self._event_history: list[LifeEngineEvent] = []
        self._lock: asyncio.Lock | None = None

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

    def _next_sequence(self) -> int:
        """获取下一个事件序列号。"""
        self._state.event_sequence += 1
        return self._state.event_sequence

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
        data["heartbeat_interval_seconds"] = int(self._cfg().settings.heartbeat_interval_seconds)
        data["model_task_name"] = self._cfg().model.task_name
        data["pending_event_count"] = len(self._pending_events)
        data["history_event_count"] = len(self._event_history)
        data["context_history_max_events"] = self._history_limit()
        data["workspace_path"] = self._cfg().settings.workspace_path
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
        """将事件追加到滚动历史中，并裁剪到上限。"""
        if not events:
            return

        async with self._get_lock():
            self._event_history.extend(events)
            limit = self._history_limit()
            if len(self._event_history) > limit:
                self._event_history = self._event_history[-limit:]
            self._state.history_event_count = len(self._event_history)
        await self._save_runtime_context()

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

        保持时间连续性，不分开展示不同类型的事件。
        """
        if not events:
            return ""

        task_name = self._cfg().model.task_name or "life"

        # 按时间顺序展示所有事件
        sorted_events = sorted(events, key=lambda e: e.sequence)

        lines: list[str] = [
            "## 生命中枢事件流",
            f"中枢任务: {task_name}",
            f"当前心跳序号: {self._state.heartbeat_count}",
            f"事件流总数: {len(sorted_events)}",
            "",
            "### 最近事件（按时间顺序）",
        ]

        for event in sorted_events:
            # 根据事件类型生成不同格式的行
            if event.event_type == EventType.MESSAGE:
                # 外部消息
                line = f"[{event.timestamp}] 📨 {event.source_detail}"
                line += f"\n    └─ {event.sender}: {event.content}"
            elif event.event_type == EventType.HEARTBEAT:
                # 心跳思考
                line = f"[{event.timestamp}] 💭 心跳#{event.heartbeat_index} 内部思考"
                line += f"\n    └─ {event.content}"
            elif event.event_type == EventType.TOOL_CALL:
                # 工具调用
                line = f"[{event.timestamp}] 🔧 调用工具: {event.tool_name}"
                if event.tool_args:
                    args_str = ", ".join(f"{k}={v}" for k, v in event.tool_args.items())
                    line += f"\n    └─ 参数: {args_str}"
            elif event.event_type == EventType.TOOL_RESULT:
                # 工具结果
                status = "✅" if event.tool_success else "❌"
                line = f"[{event.timestamp}] {status} 工具返回: {event.tool_name}"
                line += f"\n    └─ {event.content}"
            else:
                line = f"[{event.timestamp}] ❓ 未知事件: {event.content}"

            lines.append(line)

        return "\n".join(lines)

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

    def _build_heartbeat_model_prompt(self, wake_context: str) -> str:
        """构造心跳模型输入。"""
        lines = [
            "这是你的心跳时刻。",
            "",
            "**要求：必须输出一些内容，可以是：**",
            "1. 内部思考/心理独白（1-4句话，表达此刻的感受、想法或观察）",
            "2. 同时可以选择使用工具（记录想法、管理待办、操作文件等）",
            "",
            "注意：不要沉默，至少说说你此刻在想什么、感受到什么。",
            "",
            f"当前心跳序号: {self._state.heartbeat_count}",
            f"当前时间: {self._state.last_heartbeat_at or _now_iso()}",
            f"事件流中有 {self._state.history_event_count} 个事件",
        ]

        if wake_context.strip():
            lines.extend(
                [
                    "",
                    "### 最近的事件流",
                    wake_context.strip(),
                ]
            )
        else:
            lines.extend(
                [
                    "",
                    "### 最近的事件流",
                    "当前没有新事件，一切平静。",
                ]
            )

        return "\n".join(lines)

    def _build_heartbeat_system_prompt(self) -> str:
        """构造心跳模型系统提示词，从 SOUL.md / MEMORY.md / TOOL.md 读取。"""
        cfg = self._cfg()
        workspace = Path(cfg.settings.workspace_path)
        
        soul_file = workspace / "SOUL.md"
        memory_file = workspace / "MEMORY.md"
        tool_file = workspace / "TOOL.md"
        
        # 读取 SOUL.md
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
        
        # 读取 MEMORY.md
        memory_content = ""
        if memory_file.exists():
            try:
                memory_content = memory_file.read_text(encoding="utf-8").strip()
            except Exception as e:
                logger.warning(f"无法读取 MEMORY.md: {e}")

        # 读取 TOOL.md
        tool_content = ""
        if tool_file.exists():
            try:
                tool_content = tool_file.read_text(encoding="utf-8").strip()
            except Exception as e:
                logger.warning(f"无法读取 TOOL.md: {e}")
        
        # 组装系统提示词
        parts = [soul_content]
        
        if memory_content:
            parts.append("")
            parts.append("---- 灵魂文档 `soul.md` 到此结束 ----")
            parts.append("")
            parts.append(memory_content)

        if tool_content:
            parts.append("")
            parts.append("---- 记忆文档 `memory.md` 到此结束 ----")
            parts.append("")
            parts.append(tool_content)
        
        return "\n".join(parts)

    def _get_nucleus_tools(self) -> list[type]:
        """获取中枢可用的工具类列表。"""
        from .tools import ALL_TOOLS
        from .todo_tools import TODO_TOOLS
        
        return ALL_TOOLS + TODO_TOOLS

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
        response = await asyncio.wait_for(request.send(stream=False), timeout=timeout_seconds)
        max_rounds = max(1, int(self._cfg().settings.max_rounds_per_heartbeat))
        last_text = ""
        tool_event_count = 0

        for _ in range(max_rounds):
            response_text = await response
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

            response = await asyncio.wait_for(response.send(stream=False), timeout=timeout_seconds)

        if not last_text:
            # 即使模型仅进行了工具调用，也保证产生最小内心独白，避免“空心跳”。
            if tool_event_count > 0:
                last_text = f"我刚刚完成了 {tool_event_count // 2} 次工具操作，先记下这些变化，下一次心跳继续整理。"
            else:
                last_text = "此刻很安静，但我仍在持续感受与观察，准备好迎接下一次变化。"

        return last_text

    async def start(self) -> None:
        """启动心跳。"""
        if self._state.running:
            return

        cfg = self._cfg()
        if not cfg.settings.enabled:
            logger.info("life_engine 已禁用，跳过启动")
            await self.clear_runtime_context()
            return

        await self._load_runtime_context()

        self._state.running = True
        self._state.started_at = _now_iso()
        self._state.last_heartbeat_at = self._state.last_heartbeat_at or self._state.started_at
        self._state.last_error = None
        self._state.history_event_count = len(self._event_history)
        self._state.pending_event_count = len(self._pending_events)

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
            f"workspace={cfg.settings.workspace_path}"
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
