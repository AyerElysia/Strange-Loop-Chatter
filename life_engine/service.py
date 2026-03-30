"""life_engine 最小心跳服务。"""

from __future__ import annotations

import asyncio
import traceback
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any

from src.app.plugin_system.api.llm_api import create_llm_request, get_model_set_by_task
from src.app.plugin_system.api.log_api import get_logger
from src.core.config import get_core_config
from src.core.components.base import BaseService
from src.core.models.message import Message
from src.kernel.concurrency import get_task_manager
from src.kernel.llm import LLMPayload, ROLE, Text

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
    history_message_count: int = 0
    last_wake_context_at: str | None = None
    last_wake_context_size: int = 0
    last_model_reply_at: str | None = None
    last_model_reply: str | None = None
    last_model_error: str | None = None
    last_error: str | None = None


class LifeEngineService(BaseService):
    """life_engine 心跳服务。

    这个版本只保留“并行存在”、“周期心跳”与“消息上下文堆积/注入”。
    不参与正常聊天流程，不做回复决策。
    """

    service_name: str = "life_engine"
    service_description: str = "生命中枢最小原型服务，仅维持并行心跳与事件上下文"
    version: str = "1.5.0"

    def __init__(self, plugin) -> None:
        super().__init__(plugin)
        self._state = LifeEngineState()
        self._heartbeat_task_id: str | None = None
        self._stop_event: asyncio.Event | None = None
        self._pending_messages: list[LifeEngineMessageRecord] = []
        self._message_history: list[LifeEngineMessageRecord] = []
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
        """返回滚动历史保留上限。"""
        cfg = self._cfg()
        return max(1, int(cfg.settings.context_history_max_messages))

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
        data["history_message_count"] = len(self._message_history)
        data["context_history_max_messages"] = self._history_limit()
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

    async def _append_history(self, records: list[LifeEngineMessageRecord]) -> None:
        """将记录追加到滚动历史中，并裁剪到上限。"""
        if not records:
            return

        async with self._get_lock():
            self._message_history.extend(records)
            limit = self._history_limit()
            if len(self._message_history) > limit:
                self._message_history = self._message_history[-limit:]
            self._state.history_message_count = len(self._message_history)

    async def clear_runtime_context(self) -> None:
        """清理当前唤醒上下文。"""
        async with self._get_lock():
            self._pending_messages.clear()
            self._message_history.clear()
            self._state.pending_message_count = 0
            self._state.history_message_count = 0
        self._clear_wake_context_reminder()

    def _clear_wake_context_reminder(self) -> None:
        """清除系统提醒中的中枢上下文。"""
        from src.core.prompt import get_system_reminder_store

        get_system_reminder_store().delete(_TARGET_REMINDER_BUCKET, _TARGET_REMINDER_NAME)

    def _build_wake_context_text(self, records: list[LifeEngineMessageRecord]) -> str:
        """把滚动历史消息拼成可注入的上下文文本。"""
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

        lines.append(f"### 当前滚动上下文消息数: {len(records)}")

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
        if records:
            await self._append_history(records)

        async with self._get_lock():
            context_records = list(self._message_history)

        if not context_records:
            self._clear_wake_context_reminder()
            return ""

        content = self._build_wake_context_text(context_records)
        from src.core.prompt import get_system_reminder_store

        store = get_system_reminder_store()
        store.set(_TARGET_REMINDER_BUCKET, name=_TARGET_REMINDER_NAME, content=content)

        self._state.last_wake_context_at = _now_iso()
        self._state.last_wake_context_size = len(context_records)
        log_wake_context_injected(
            task_name=self._cfg().model.task_name,
            heartbeat_prompt=self._cfg().settings.heartbeat_prompt,
            wake_context_at=self._state.last_wake_context_at,
            context_message_count=len(context_records),
            drained_message_count=len(records),
            history_message_count=len(context_records),
            source_count=len({record.source_label for record in context_records}),
            content=content,
        )
        logger.info(
            "life_engine 已注入唤醒上下文: "
            f"count={len(context_records)} "
            f"drained={len(records)} "
            f"source_count={len({record.source_label for record in context_records})} "
            f"task={self._cfg().model.task_name}"
        )
        return content

    def _record_model_reply(self, model_reply: str) -> None:
        """记录心跳模型回复，但不把回复写回历史。"""
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
        else:
            logger.info(
                f"life_engine 心跳模型回复为空: #{self._state.heartbeat_count}"
            )

    def _build_heartbeat_model_prompt(self, wake_context: str) -> str:
        """构造心跳模型输入。"""
        lines = [
            "请基于以下心跳信息，输出一段给自己看的内部报文。",
            "要求：",
            "- 只输出内部状态总结，不要对外回复用户",
            "- 保持简短，优先 1 到 4 句",
            "- 可以包含当前关注点、情绪/状态、下一步倾向",
            f"- 当前心跳序号: {self._state.heartbeat_count}",
            f"- 当前时间: {self._state.last_heartbeat_at or _now_iso()}",
            f"- 当前滚动上下文消息数: {self._state.history_message_count}",
        ]

        if wake_context.strip():
            lines.extend(
                [
                    "",
                    "### 最近消息与上下文",
                    wake_context.strip(),
                ]
            )
        else:
            lines.extend(
                [
                    "",
                    "### 最近消息与上下文",
                    "当前没有新增消息，处于空闲状态。",
                ]
            )

        return "\n".join(lines)

    def _build_heartbeat_system_prompt(self) -> str:
        """构造心跳模型系统提示词。"""
        cfg = self._cfg()
        heartbeat_prompt = cfg.settings.heartbeat_prompt.strip()

        personality = None
        try:
            personality = get_core_config().personality
        except Exception:
            personality = None

        lines = ["# 关于你"]
        if personality is not None:
            nickname = getattr(personality, "nickname", "") or ""
            alias_names = getattr(personality, "alias_names", []) or []
            personality_core = getattr(personality, "personality_core", "") or ""
            personality_side = getattr(personality, "personality_side", "") or ""
            identity = getattr(personality, "identity", "") or ""
            background_story = getattr(personality, "background_story", "") or ""
            reply_style = getattr(personality, "reply_style", "") or ""
            safety_guidelines = getattr(personality, "safety_guidelines", []) or []
            negative_behaviors = getattr(personality, "negative_behaviors", []) or []

            if nickname:
                lines.append(f"昵称：{nickname}")
            if alias_names:
                lines.append(f"别名：{'、'.join(str(item) for item in alias_names if str(item).strip())}")
            if personality_core:
                lines.append(personality_core.strip())
            if personality_side:
                lines.append(personality_side.strip())
            if identity:
                lines.append(f"身份：{identity.strip()}")
            if background_story:
                lines.extend(["", "# 背景故事", background_story.strip()])
            if reply_style:
                lines.extend(["", "# 表达风格", reply_style.strip()])
            if safety_guidelines:
                lines.extend(["", "# 安全准则", "\n".join(str(item) for item in safety_guidelines if str(item).strip())])
            if negative_behaviors:
                lines.extend(["", "# 负面行为", "\n".join(str(item) for item in negative_behaviors if str(item).strip())])
        else:
            lines.append("你是一个并行存在的生命中枢原型。")

        lines.extend(
            [
                "",
                "# 心跳任务",
                heartbeat_prompt or "每次心跳都只输出给自己看的内部报文。",
            ]
        )

        return "\n".join(lines)

    async def _run_heartbeat_model(self, wake_context: str) -> str:
        """调用 life 任务模型生成内部报文。"""
        cfg = self._cfg()
        task_name = cfg.model.task_name.strip() or "life"
        model_set = get_model_set_by_task(task_name)
        request = create_llm_request(
            model_set=model_set,
            request_name="life_engine_heartbeat",
        )
        request.add_payload(
            LLMPayload(
                ROLE.SYSTEM,
                Text(self._build_heartbeat_system_prompt()),
            )
        )
        request.add_payload(LLMPayload(ROLE.USER, Text(self._build_heartbeat_model_prompt(wake_context))))

        timeout_seconds = max(10.0, min(60.0, float(self._cfg().settings.heartbeat_interval_seconds)))
        # send() 返回 LLMResponse 对象，需要 await 它来获取实际文本
        response = await asyncio.wait_for(
            request.send(stream=False),
            timeout=timeout_seconds,
        )
        # LLMResponse 实现了 __await__，再次 await 获取文本
        response_text = await response
        return str(response_text).strip()

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
        self._state.history_message_count = 0

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
            f"prompt={cfg.settings.heartbeat_prompt.strip() if cfg.settings.heartbeat_prompt.strip() else '<empty>'}"
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

                self._state.heartbeat_count += 1
                self._state.last_heartbeat_at = _now_iso()
                injected_content = await self.inject_wake_context()
                log_heartbeat_event(
                    heartbeat_count=self._state.heartbeat_count,
                    last_heartbeat_at=self._state.last_heartbeat_at,
                    pending_message_count=self._state.pending_message_count,
                    last_wake_context_at=self._state.last_wake_context_at,
                    last_wake_context_size=self._state.last_wake_context_size,
                )
                try:
                    model_reply = await self._run_heartbeat_model(injected_content)
                    self._record_model_reply(model_reply)
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
                            f"已注入 {self._state.last_wake_context_size} 条上下文消息"
                        )
                    else:
                        logger.info(
                            f"life_engine heartbeat #{self._state.heartbeat_count} "
                            f"at {self._state.last_heartbeat_at}: 无新上下文"
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
