"""life_engine 最小心跳服务。"""

from __future__ import annotations

import asyncio
import traceback
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any

from src.app.plugin_system.api.log_api import get_logger
from src.core.components.base import BaseService
from src.core.models.message import Message
from src.kernel.concurrency import get_task_manager

from .audit import (
    get_life_log_file,
    log_error,
    log_heartbeat as log_heartbeat_event,
    log_lifecycle,
    log_message_received,
    log_wake_context_injected,
)
from .config import LifeEngineConfig


logger = get_logger("life_engine", display="life_engine")

_TARGET_REMINDER_BUCKET = "actor"
_TARGET_REMINDER_NAME = "生命中枢唤醒上下文"


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


@dataclass(slots=True)
class LifeEngineMessageRecord:
    """life_engine 收到的单条消息记录。"""

    received_at: str
    platform: str
    chat_type: str
    source_label: str
    source_detail: str
    stream_id: str
    sender_display: str
    sender_id: str
    message_id: str
    reply_to: str | None
    message_type: str
    content: str


@dataclass(slots=True)
class LifeEngineState:
    """life_engine 最小状态。"""

    running: bool = False
    started_at: str | None = None
    last_heartbeat_at: str | None = None
    heartbeat_count: int = 0
    pending_message_count: int = 0
    last_wake_context_at: str | None = None
    last_wake_context_size: int = 0
    last_error: str | None = None


class LifeEngineService(BaseService):
    """life_engine 心跳服务。

    这个版本只保留“并行存在”、“周期心跳”与“消息上下文堆积/注入”。
    不参与正常聊天流程，不做回复决策。
    """

    service_name: str = "life_engine"
    service_description: str = "生命中枢最小原型服务，仅维持并行心跳与事件上下文"
    version: str = "1.2.0"

    def __init__(self, plugin) -> None:
        super().__init__(plugin)
        self._state = LifeEngineState()
        self._heartbeat_task_id: str | None = None
        self._stop_event: asyncio.Event | None = None
        self._pending_messages: list[LifeEngineMessageRecord] = []
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

    def _build_record(self, message: Message) -> LifeEngineMessageRecord:
        """将核心消息对象转换为中枢记录。"""
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

        if chat_type == "group":
            source_kind = "群聊"
            source_name = group_name or group_id or stream_id[:8] or "未知群聊"
            source_detail = f"群ID={group_id or 'unknown'}"
        elif chat_type == "private":
            source_kind = "私聊"
            source_name = sender_display
            source_detail = f"用户ID={sender_id or 'unknown'}"
        elif chat_type == "discuss":
            source_kind = "讨论组"
            source_name = group_name or group_id or stream_id[:8] or "未知讨论组"
            source_detail = f"讨论组ID={group_id or 'unknown'}"
        else:
            source_kind = chat_type or "未知"
            source_name = group_name or sender_display or stream_id[:8] or "未知来源"
            source_detail = f"来源ID={group_id or sender_id or 'unknown'}"

        source_label = f"{platform} | {source_kind} | {source_name}"

        raw_content = message.processed_plain_text
        if raw_content is None:
            raw_content = message.content if isinstance(message.content, str) else str(message.content)
        content = _shorten_text(str(raw_content).strip() or f"[{message.message_type.value}]")

        message_type = getattr(message.message_type, "value", str(message.message_type))

        return LifeEngineMessageRecord(
            received_at=_format_time(getattr(message, "time", None)),
            platform=platform,
            chat_type=chat_type,
            source_label=source_label,
            source_detail=source_detail,
            stream_id=stream_id,
            sender_display=sender_display,
            sender_id=sender_id,
            message_id=str(message.message_id or ""),
            reply_to=str(message.reply_to) if message.reply_to else None,
            message_type=message_type,
            content=content,
        )

    def snapshot(self) -> dict[str, Any]:
        """返回当前状态快照。"""
        data = asdict(self._state)
        data["heartbeat_interval_seconds"] = int(self._cfg().settings.heartbeat_interval_seconds)
        data["heartbeat_prompt"] = self._cfg().settings.heartbeat_prompt
        data["model_task_name"] = self._cfg().model.task_name
        data["pending_message_count"] = len(self._pending_messages)
        data["log_file_path"] = str(get_life_log_file())
        return data

    def health(self) -> dict[str, Any]:
        """返回一个轻量健康信息。"""
        return self.snapshot()

    async def record_message(self, message: Message) -> None:
        """记录一条来自聊天流的消息。"""
        if not self._is_enabled():
            return

        record = self._build_record(message)
        async with self._get_lock():
            self._pending_messages.append(record)
            self._state.pending_message_count = len(self._pending_messages)

        log_message_received(
            received_at=record.received_at,
            platform=record.platform,
            chat_type=record.chat_type,
            source_label=record.source_label,
            source_detail=record.source_detail,
            stream_id=record.stream_id,
            sender_display=record.sender_display,
            sender_id=record.sender_id,
            message_id=record.message_id,
            reply_to=record.reply_to,
            message_type=record.message_type,
            content=record.content,
            pending_message_count=self._state.pending_message_count,
        )

    async def drain_pending_messages(self) -> list[LifeEngineMessageRecord]:
        """清空并返回当前待处理消息。"""
        async with self._get_lock():
            pending = list(self._pending_messages)
            self._pending_messages.clear()
            self._state.pending_message_count = 0
        return pending

    async def clear_runtime_context(self) -> None:
        """清理当前唤醒上下文。"""
        async with self._get_lock():
            self._pending_messages.clear()
            self._state.pending_message_count = 0
        self._clear_wake_context_reminder()

    def _clear_wake_context_reminder(self) -> None:
        """清除系统提醒中的中枢上下文。"""
        from src.core.prompt import get_system_reminder_store

        get_system_reminder_store().delete(_TARGET_REMINDER_BUCKET, _TARGET_REMINDER_NAME)

    def _build_wake_context_text(self, records: list[LifeEngineMessageRecord]) -> str:
        """把待处理消息拼成可注入的上下文文本。"""
        if not records:
            return ""

        task_name = self._cfg().model.task_name or "life"
        heartbeat_prompt = self._cfg().settings.heartbeat_prompt.strip()

        grouped: dict[str, list[LifeEngineMessageRecord]] = {}
        source_meta: dict[str, tuple[str, str, str, str]] = {}
        for record in records:
            grouped.setdefault(record.source_label, []).append(record)
            source_meta.setdefault(
                record.source_label,
                (record.platform, record.chat_type, record.source_detail, record.stream_id),
            )

        lines: list[str] = [
            "## 生命中枢唤醒上下文",
            f"中枢任务: {task_name}",
        ]
        if heartbeat_prompt:
            lines.extend(
                [
                    "### 心跳提示词",
                    heartbeat_prompt,
                ]
            )

        lines.append(f"### 本次收到消息数: {len(records)}")

        for source_label, source_records in grouped.items():
            platform, chat_type, source_detail, stream_id = source_meta[source_label]
            lines.append(f"### 来源: {source_label}")
            lines.append(f"- 平台: {platform}")
            lines.append(f"- 会话类型: {chat_type}")
            lines.append(f"- 来源详情: {source_detail}")
            if stream_id:
                lines.append(f"- stream_id: {stream_id}")
            lines.append("- 消息:")
            for record in source_records:
                line = (
                    f"  - [{record.received_at}] {record.sender_display}"
                    f" (sender_id={record.sender_id or 'unknown'})"
                    f": {record.content}"
                    f" [message_id={record.message_id or 'unknown'}]"
                )
                if record.reply_to:
                    line += f" [reply_to={record.reply_to}]"
                if record.message_type and record.message_type != "text":
                    line += f" [type={record.message_type}]"
                lines.append(line)

        return "\n".join(lines)

    async def inject_wake_context(self) -> str:
        """把当前待处理消息注入到系统提醒。"""
        records = await self.drain_pending_messages()
        if not records:
            self._clear_wake_context_reminder()
            return ""

        content = self._build_wake_context_text(records)
        from src.core.prompt import get_system_reminder_store

        store = get_system_reminder_store()
        store.set(_TARGET_REMINDER_BUCKET, name=_TARGET_REMINDER_NAME, content=content)

        self._state.last_wake_context_at = _now_iso()
        self._state.last_wake_context_size = len(records)
        log_wake_context_injected(
            task_name=self._cfg().model.task_name,
            heartbeat_prompt=self._cfg().settings.heartbeat_prompt,
            wake_context_at=self._state.last_wake_context_at,
            context_message_count=len(records),
            source_count=len({record.source_label for record in records}),
            content=content,
        )
        logger.info(
            "life_engine 已注入唤醒上下文: count=%s source_count=%s task=%s",
            len(records),
            len({record.source_label for record in records}),
            self._cfg().model.task_name,
        )
        return content

    async def start(self) -> None:
        """启动心跳。"""
        if self._state.running:
            return

        cfg = self._cfg()
        if not cfg.settings.enabled:
            logger.info("life_engine 已禁用，跳过启动")
            await self.clear_runtime_context()
            return

        await self.clear_runtime_context()

        self._state.running = True
        self._state.started_at = _now_iso()
        self._state.last_heartbeat_at = self._state.started_at
        self._state.heartbeat_count = 0
        self._state.last_error = None
        self._state.last_wake_context_at = None
        self._state.last_wake_context_size = 0

        self._stop_event = asyncio.Event()
        task = get_task_manager().create_task(
            self._heartbeat_loop(),
            name="life_engine_heartbeat",
            daemon=True,
        )
        self._heartbeat_task_id = task.task_id
        logger.info(
            "life_engine 已启动: interval=%ss task=%s prompt=%s",
            int(cfg.settings.heartbeat_interval_seconds),
            cfg.model.task_name,
            cfg.settings.heartbeat_prompt.strip() if cfg.settings.heartbeat_prompt.strip() else "<empty>",
        )
        log_lifecycle(
            "started",
            enabled=True,
            heartbeat_interval_seconds=int(cfg.settings.heartbeat_interval_seconds),
            model_task_name=cfg.model.task_name,
            heartbeat_prompt=cfg.settings.heartbeat_prompt,
            log_file_path=str(get_life_log_file()),
        )

    async def stop(self) -> None:
        """停止心跳。"""
        pending_before_stop = len(self._pending_messages)
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
        await self.clear_runtime_context()
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

                injected_content = await self.inject_wake_context()

                self._state.heartbeat_count += 1
                self._state.last_heartbeat_at = _now_iso()
                log_heartbeat_event(
                    heartbeat_count=self._state.heartbeat_count,
                    last_heartbeat_at=self._state.last_heartbeat_at,
                    pending_message_count=self._state.pending_message_count,
                    last_wake_context_at=self._state.last_wake_context_at,
                    last_wake_context_size=self._state.last_wake_context_size,
                )
                if should_log_heartbeat:
                    if injected_content:
                        logger.info(
                            "life_engine heartbeat #%s at %s: 已注入 %s 条上下文消息",
                            self._state.heartbeat_count,
                            self._state.last_heartbeat_at,
                            self._state.last_wake_context_size,
                        )
                    else:
                        logger.info(
                            "life_engine heartbeat #%s at %s: 无新上下文",
                            self._state.heartbeat_count,
                            self._state.last_heartbeat_at,
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
                pending_message_count=self._state.pending_message_count,
            )
        finally:
            self._state.running = False
