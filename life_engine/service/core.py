"""life_engine 生命中枢服务核心模块。

生命中枢是同一个主体在不同运行模式间切换的骨架。
它通过周期性心跳来处理堆积的消息、进行内部思考，并为工具调用、
对外交流与状态沉淀提供基础能力。
"""

from __future__ import annotations

import asyncio
import traceback
from dataclasses import asdict
from datetime import datetime, time as dtime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.app.plugin_system.api.llm_api import create_llm_request, get_model_set_by_task
from src.app.plugin_system.api.log_api import get_logger
from src.core.config import get_core_config
from src.core.components.base import BaseService
from src.core.models.message import Message
from src.kernel.concurrency import get_task_manager
from src.kernel.llm import LLMPayload, ROLE, Text, ToolRegistry, ToolResult

if TYPE_CHECKING:
    from ..dream.scheduler import DreamScheduler
    from ..memory.service import LifeMemoryService
    from ..neuromod.engine import InnerStateEngine
    from ..snn.bridge import SNNBridge
    from ..snn.core import DriveCoreNetwork

from .audit import (
    get_life_log_file,
    log_error,
    log_heartbeat as log_heartbeat_event,
    log_heartbeat_model_response,
    log_lifecycle,
    log_message_received,
    log_wake_context_injected,
)
from ..core.config import LifeEngineConfig
from ..constants import (
    HEARTBEAT_IDLE_CRITICAL_THRESHOLD,
    HEARTBEAT_IDLE_WARNING_THRESHOLD,
)
from .event_builder import (
    EventBuilder,
    EventType,
    LifeEngineEvent,
    LifeEngineState,
    _format_current_time,
    _format_time,
    _format_time_display,
    _now_iso,
    _parse_hhmm,
    _shorten_text,
    INTERNAL_PLATFORM,
)
from .state_manager import (
    StatePersistence,
    compress_history,
    clear_wake_context_reminder,
    get_file_metadata,
    minutes_since_time,
)
from .integrations import (
    DFCIntegration,
    SNNIntegration,
    MemoryIntegration,
    to_jsonable,
)

if TYPE_CHECKING:
    from ..memory.service import LifeMemoryService


logger = get_logger("life_engine", display="life_engine")


class LifeEngineService(BaseService):
    """life_engine 心跳服务。

    这个版本使用统一的事件流模型，所有交互保持时间连续性。
    不参与正常聊天流程，不做回复决策。
    """

    service_name: str = "life_engine"
    service_description: str = "生命中枢服务，维持并行心跳与事件流上下文"
    version: str = "3.3.0"

    @classmethod
    def get_instance(cls) -> "LifeEngineService | None":
        """获取服务单例（供工具使用）。"""
        from .registry import get_life_engine_service

        return get_life_engine_service()

    def __init__(self, plugin) -> None:
        super().__init__(plugin)
        self._state = LifeEngineState()
        self._heartbeat_task_id: str | None = None
        self._stop_event: asyncio.Event | None = None
        self._pending_events: list[LifeEngineEvent] = []
        self._event_history: list[LifeEngineEvent] = []
        self._lock: asyncio.Lock | None = None
        self._sleep_state_active: bool = False
        self._memory_service: LifeMemoryService | None = None
        self._last_decay_date: str | None = None

        # SNN 皮层下系统
        self._snn_network: DriveCoreNetwork | None = None
        self._snn_bridge: SNNBridge | None = None
        self._snn_tick_task_id: str | None = None

        # 神经调质层
        self._inner_state: InnerStateEngine | None = None

        # 做梦系统
        self._dream_scheduler: DreamScheduler | None = None
        self._injected_dream_ids: set[str] = set()

        # 集成管理器
        self._dfc_integration: DFCIntegration | None = None
        self._snn_integration: SNNIntegration | None = None
        self._memory_integration: MemoryIntegration | None = None

        # 事件构建器
        self._event_builder = EventBuilder(self._next_sequence)

        # 状态持久化
        self._state_persistence: StatePersistence | None = None
        self._legacy_config_warning_emitted: bool = False

    @property
    def memory_service(self) -> LifeMemoryService | None:
        """兼容旧调用方的公开记忆服务访问入口。"""
        return self._memory_service

    def _get_lock(self) -> asyncio.Lock:
        """获取懒加载锁。"""
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def _cfg(self) -> LifeEngineConfig:
        config = getattr(self.plugin, "config", None)
        if isinstance(config, LifeEngineConfig):
            if hasattr(config, "thresholds") and hasattr(config, "memory_algorithm"):
                return config
            if not self._legacy_config_warning_emitted:
                logger.warning(
                    "检测到旧版 LifeEngineConfig 对象（缺少 thresholds/memory_algorithm），"
                    "将自动迁移为最新配置结构；建议完整重启进程。"
                )
                self._legacy_config_warning_emitted = True
            migrated = self._migrate_legacy_config(config)
            if migrated is not None:
                return migrated
            return LifeEngineConfig()
        migrated = self._migrate_legacy_config(config)
        if migrated is not None:
            return migrated
        return LifeEngineConfig()

    def _migrate_legacy_config(self, config: object | None) -> LifeEngineConfig | None:
        """将旧版/异构配置对象迁移为当前 LifeEngineConfig。"""
        if config is None:
            return None
        dump_method = getattr(config, "model_dump", None)
        payload: dict[str, Any] | None = None
        if callable(dump_method):
            try:
                dumped = dump_method(mode="python")
                if isinstance(dumped, dict):
                    payload = dumped
            except TypeError:
                try:
                    dumped = dump_method()
                    if isinstance(dumped, dict):
                        payload = dumped
                except Exception:
                    payload = None
            except Exception:
                payload = None
        if payload is None:
            dict_method = getattr(config, "dict", None)
            if callable(dict_method):
                try:
                    dumped = dict_method()
                    if isinstance(dumped, dict):
                        payload = dumped
                except Exception:
                    payload = None
        if payload is None:
            return None
        try:
            migrated = LifeEngineConfig.model_validate(payload)
        except Exception:
            migrated = LifeEngineConfig()
        try:
            setattr(self.plugin, "config", migrated)
        except Exception:
            pass
        return migrated

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

        if sleep_at < wake_at:
            in_sleep = sleep_at <= now_hm < wake_at
        else:
            in_sleep = (now_hm >= sleep_at) or (now_hm < wake_at)

        return in_sleep, f"{sleep_at.strftime('%H:%M')}~{wake_at.strftime('%H:%M')}"

    def _next_sequence(self) -> int:
        """获取下一个事件序列号。"""
        self._state.event_sequence += 1
        return self._state.event_sequence

    def _minutes_since_external_message(self) -> int | None:
        """计算距离上一条外部消息过去了多少分钟。"""
        return minutes_since_time(self._state.last_external_message_at)

    def _minutes_since_tell_dfc(self) -> int | None:
        """计算距离上一次传话给 DFC 过去了多少分钟。"""
        return minutes_since_time(self._state.last_tell_dfc_at)

    def _minutes_since_outer_sync(self) -> int | None:
        """计算距离上一次同步给对外运行模式过去了多少分钟。"""
        return self._minutes_since_tell_dfc()

    def record_tell_dfc(self) -> None:
        """记录一次传话给 DFC 的时间。"""
        self._state.last_tell_dfc_at = _now_iso()
        self._state.tell_dfc_count += 1

    def record_outer_sync(self) -> None:
        """记录一次同步给对外运行模式的时间。"""
        self.record_tell_dfc()

    def _workspace_dir(self) -> Path:
        """返回 life workspace 目录。"""
        workspace = Path(self._cfg().settings.workspace_path).resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        return workspace

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
        data["snn_enabled"] = self._cfg().snn.enabled
        if self._snn_network is not None:
            data["snn_health"] = self._snn_network.get_health()
        neuromod_cfg = getattr(self._cfg(), "neuromod", None)
        data["neuromod_enabled"] = neuromod_cfg.enabled if neuromod_cfg else False
        if self._inner_state is not None:
            data["neuromod_state"] = self._inner_state.get_full_state()
        return data

    @staticmethod
    def _message_time_display(message: Message) -> tuple[str, str]:
        """返回消息时间的 ISO 与简洁显示。"""
        raw_time = getattr(message, "time", None)
        try:
            if raw_time is None:
                raise ValueError("missing time")
            iso_time = datetime.fromtimestamp(float(raw_time), tz=timezone.utc).astimezone().isoformat()
        except Exception:
            iso_time = _now_iso()
        return iso_time, _format_time_display(iso_time)

    @staticmethod
    def _format_message_text(message: Message, *, max_length: int = 240) -> str:
        """格式化消息正文。"""
        raw_text = getattr(message, "processed_plain_text", None)
        if raw_text is None:
            raw_text = getattr(message, "content", "")
        return _shorten_text(str(raw_text or "").strip() or "（空消息）", max_length=max_length)

    @staticmethod
    def _message_sender_label(message: Message) -> str:
        """格式化消息发送者标签。"""
        return str(
            getattr(message, "sender_cardname", None)
            or getattr(message, "sender_name", None)
            or getattr(message, "sender_id", None)
            or "未知发送者"
        )

    @staticmethod
    def _serialize_life_event(event: LifeEngineEvent) -> dict[str, Any]:
        """将 life 事件转换为可视化数据。"""
        return {
            "scope": "life",
            "event_id": event.event_id,
            "event_type": event.event_type.value,
            "timestamp": event.timestamp,
            "time_display": _format_time_display(event.timestamp),
            "sequence": event.sequence,
            "source": event.source,
            "source_detail": event.source_detail,
            "content": _shorten_text(event.content or "", max_length=240),
            "content_full": event.content or "",
            "content_type": event.content_type,
            "sender": event.sender,
            "chat_type": event.chat_type,
            "stream_id": event.stream_id,
            "heartbeat_index": event.heartbeat_index,
            "tool_name": event.tool_name,
            "tool_args": event.tool_args or {},
            "tool_success": event.tool_success,
        }

    def _serialize_stream_message(
        self,
        message: Message,
        *,
        stream_name: str,
        source: str,
    ) -> dict[str, Any]:
        """将聊天流消息转换为可视化数据。"""
        iso_time, time_display = self._message_time_display(message)
        sender_role = str(getattr(message, "sender_role", "") or "").lower()
        direction = "sent" if sender_role == "bot" else "received"
        return {
            "scope": "chatter",
            "stream_id": str(getattr(message, "stream_id", "") or ""),
            "stream_name": stream_name,
            "platform": str(getattr(message, "platform", "") or ""),
            "chat_type": str(getattr(message, "chat_type", "") or ""),
            "message_id": str(getattr(message, "message_id", "") or ""),
            "time": iso_time,
            "time_display": time_display,
            "direction": direction,
            "sender_role": sender_role or None,
            "sender_name": self._message_sender_label(message),
            "content": self._format_message_text(message),
            "content_full": str(
                getattr(message, "processed_plain_text", None)
                or getattr(message, "content", "")
                or ""
            ),
            "reply_to": getattr(message, "reply_to", None),
            "source": source,
            "is_inner_monologue": bool(getattr(message, "is_inner_monologue", False)),
            "is_proactive_followup_trigger": bool(
                getattr(message, "is_proactive_followup_trigger", False)
            ),
        }

    async def get_message_observability_snapshot(
        self,
        *,
        event_limit: int = 24,
        stream_limit: int = 12,
        message_limit: int = 8,
    ) -> dict[str, Any]:
        """返回 life 与 chatter 的联合消息观测快照。"""
        async with self._get_lock():
            pending_events = list(self._pending_events)
            event_history = list(self._event_history)
            state_snapshot = asdict(self._state)
            inner_state = self._inner_state

        life_events = [
            self._serialize_life_event(event)
            for event in (event_history[-max(1, event_limit) :] if event_limit > 0 else event_history)
        ]
        pending_life_events = [
            self._serialize_life_event(event)
            for event in (pending_events[-max(1, min(event_limit, len(pending_events))) :] if pending_events else [])
        ]

        life_latest_event = life_events[-1] if life_events else (pending_life_events[-1] if pending_life_events else None)

        try:
            from src.core.managers import get_stream_manager

            stream_manager = get_stream_manager()
            stream_items = list(getattr(stream_manager, "_streams", {}).values())
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"读取聊天流快照失败: {exc}")
            stream_items = []

        stream_snapshots: list[dict[str, Any]] = []
        for stream in stream_items:
            stream_id = str(getattr(stream, "stream_id", "") or "")
            if not stream_id:
                continue
            context = getattr(stream, "context", None)
            if context is None:
                continue

            history_messages = list(getattr(context, "history_messages", []) or [])
            unread_messages = list(getattr(context, "unread_messages", []) or [])
            current_message = getattr(context, "current_message", None)

            candidate_messages = history_messages[-max(1, message_limit) :]
            recent_messages = [
                self._serialize_stream_message(
                    msg,
                    stream_name=str(getattr(stream, "stream_name", "") or stream_id[:8] or "unknown"),
                    source="history",
                )
                for msg in candidate_messages
            ]

            latest_message = None
            if unread_messages:
                latest_message = self._serialize_stream_message(
                    unread_messages[-1],
                    stream_name=str(getattr(stream, "stream_name", "") or stream_id[:8] or "unknown"),
                    source="unread",
                )
            elif history_messages:
                latest_message = self._serialize_stream_message(
                    history_messages[-1],
                    stream_name=str(getattr(stream, "stream_name", "") or stream_id[:8] or "unknown"),
                    source="history",
                )
            elif current_message is not None:
                latest_message = self._serialize_stream_message(
                    current_message,
                    stream_name=str(getattr(stream, "stream_name", "") or stream_id[:8] or "unknown"),
                    source="current",
                )

            last_message_time = getattr(context, "last_message_time", None)
            last_active_time = getattr(stream, "last_active_time", None)
            stream_snapshots.append(
                {
                    "stream_id": stream_id,
                    "stream_name": str(getattr(stream, "stream_name", "") or stream_id[:8] or "unknown"),
                    "platform": str(getattr(stream, "platform", "") or ""),
                    "chat_type": str(getattr(stream, "chat_type", "") or ""),
                    "bot_nickname": str(getattr(stream, "bot_nickname", "") or ""),
                    "is_active": bool(getattr(stream, "is_active", True)),
                    "is_chatter_processing": bool(getattr(context, "is_chatter_processing", False)),
                    "last_active_time": last_active_time,
                    "last_message_time": last_message_time,
                    "unread_count": len(unread_messages),
                    "history_count": len(history_messages),
                    "latest_message": latest_message,
                    "recent_messages": recent_messages,
                    "sort_ts": float(last_active_time or last_message_time or 0.0),
                }
            )

        stream_snapshots.sort(key=lambda item: item["sort_ts"], reverse=True)
        stream_snapshots = stream_snapshots[: max(1, stream_limit)]

        inner_state_snapshot: dict[str, Any] | None = None
        if inner_state is not None:
            try:
                inner_state_snapshot = inner_state.get_full_state()
            except Exception:
                try:
                    inner_state_snapshot = asdict(inner_state)  # type: ignore[arg-type]
                except Exception:
                    inner_state_snapshot = {"status": "unavailable"}

        return {
            "generated_at": _now_iso(),
            "life": {
                "state": state_snapshot,
                "inner_state": inner_state_snapshot,
                "pending_events": pending_life_events,
                "recent_events": life_events,
                "latest_event": life_latest_event,
            },
            "streams": stream_snapshots,
            "summary": {
                "active_stream_count": len(stream_snapshots),
                "pending_life_events": len(pending_events),
                "recent_life_events": len(life_events),
                "heartbeat_count": int(state_snapshot.get("heartbeat_count", 0) or 0),
                "last_model_reply": state_snapshot.get("last_model_reply"),
            },
        }

    def health(self) -> dict[str, Any]:
        """返回一个轻量健康信息。"""
        return self.snapshot()

    async def get_state_digest_for_dfc(self) -> str:
        """生成给 DFC 的状态摘要。"""
        if self._dfc_integration is None:
            self._dfc_integration = DFCIntegration(self)
        return await self._dfc_integration.get_state_digest()

    async def get_state_digest_for_outer_mode(self) -> str:
        """生成给对外运行模式的状态摘要。"""
        return await self.get_state_digest_for_dfc()

    async def query_actor_context(self, query: str) -> str:
        """供 DFC 同步查询当前状态、TODO 与最近日记。"""
        del query
        if self._dfc_integration is None:
            self._dfc_integration = DFCIntegration(self)
        return await self._dfc_integration.query_actor_context()

    async def query_outer_context(self, query: str) -> str:
        """供对外运行模式同步查询当前状态、TODO 与最近日记。"""
        return await self.query_actor_context(query)

    async def search_actor_memory(self, query: str, top_k: int = 5) -> str:
        """供 DFC 深度检索 life memory。"""
        query_text = str(query or "").strip()
        if not query_text:
            return ""

        memory_service = self._memory_service
        if memory_service is None:
            return "记忆系统暂不可用"

        results = await memory_service.search_memory(query_text, top_k=max(1, int(top_k)))
        if not results:
            logger.info(
                f"[search_actor_memory] 记忆检索无结果:\n"
                f"  query: {query_text}\n  top_k: {top_k}"
            )
            return ""

        workspace = self._workspace_dir()
        direct_lines: list[str] = []
        associated_lines: list[str] = []

        for result in results:
            title = result.title or Path(result.file_path).name or result.file_path
            snippet = _shorten_text(" ".join((result.snippet or "").split()), max_length=250)
            file_meta = get_file_metadata(workspace / result.file_path)
            meta_str = f"{file_meta['ext']} | {file_meta['time_ago']} | {file_meta['size']}"

            line = (
                f"- {title} [{result.file_path}] "
                f"(相关度 {result.relevance:.2f} | {meta_str})\n"
                f"  摘要：{snippet or '无摘要'}"
            )

            if result.source == "associated":
                reason = _shorten_text(
                    " ".join((result.association_reason or "").split()),
                    max_length=150,
                )
                path_str = " → ".join(result.association_path[-3:]) if result.association_path else ""
                if reason or path_str:
                    line += f"\n  联想：{reason or path_str}"
                associated_lines.append(line)
            else:
                direct_lines.append(line)

            if len(direct_lines) >= top_k and len(associated_lines) >= top_k:
                break

        parts: list[str] = []
        if direct_lines:
            parts.append(
                f"【直接命中的记忆】({len(direct_lines[:top_k])}条)\n" +
                "\n\n".join(direct_lines[:top_k])
            )
        if associated_lines:
            parts.append(
                f"【联想扩散结果】({len(associated_lines[:top_k])}条)\n" +
                "\n\n".join(associated_lines[:top_k])
            )

        footer = "\n\n💡 提示：以上仅为摘要。如需查看完整内容，可使用 fetch_life_memory 工具读取文件。"
        final_result = "\n\n".join(parts) + footer

        logger.info(
            f"[search_actor_memory] 记忆检索完成:\n"
            f"  query: {query_text}\n  top_k: {top_k}\n"
            f"  直接命中: {len(direct_lines)} 条\n  联想结果: {len(associated_lines)} 条"
        )

        return final_result

    async def record_message(self, message: Message, direction: str = "received") -> None:
        """记录一条来自聊天流的消息事件。"""
        if not self._is_enabled():
            return

        if direction not in {"received", "sent"}:
            direction = "received"

        event = self._event_builder.build_message_event(message, direction=direction)
        async with self._get_lock():
            self._pending_events.append(event)
            self._state.pending_event_count = len(self._pending_events)
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
        """接收来自 DFC 的异步留言。"""
        if not self._is_enabled():
            raise RuntimeError("life_engine 未启用")

        text = str(message or "").strip()
        if not text:
            raise ValueError("message 不能为空")

        event = self._event_builder.build_dfc_message_event(
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

    async def enqueue_outer_message(
        self,
        message: str,
        *,
        stream_id: str = "",
        platform: str = "",
        chat_type: str = "",
        sender_name: str = "",
    ) -> dict[str, Any]:
        """接收来自对外运行模式的异步留言。"""
        return await self.enqueue_dfc_message(
            message,
            stream_id=stream_id,
            platform=platform,
            chat_type=chat_type,
            sender_name=sender_name,
        )

    async def enqueue_direct_message(
        self,
        message: str,
        *,
        stream_id: str = "",
        platform: str = "",
        chat_type: str = "",
        sender_name: str = "",
        sender_id: str = "",
    ) -> dict[str, Any]:
        """接收用户通过命令直达生命中枢的留言。"""
        if not self._is_enabled():
            raise RuntimeError("life_engine 未启用")

        text = str(message or "").strip()
        if not text:
            raise ValueError("message 不能为空")

        event = self._event_builder.build_direct_message_event(
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
            sender_display=event.sender or "外部用户",
            sender_id=str(sender_id or "").strip() or "external_user",
            message_id=event.event_id,
            reply_to=None,
            message_type=event.content_type,
            content=event.content,
            direction="received",
            pending_message_count=self._state.pending_event_count,
        )
        logger.info(
            "life_engine 已接收直连留言: "
            f"stream_id={event.stream_id or 'unknown'} "
            f"sender={event.sender or '外部用户'} "
            f"pending={self._state.pending_event_count}"
        )
        return {
            "event_id": event.event_id,
            "stream_id": event.stream_id or "",
            "pending_event_count": self._state.pending_event_count,
            "queued": True,
            "channel": "direct_command",
        }

    async def record_tool_call(self, tool_name: str, tool_args: dict[str, Any]) -> None:
        """记录工具调用事件。"""
        event = self._event_builder.build_tool_call_event(tool_name, tool_args)
        async with self._get_lock():
            self._pending_events.append(event)
            self._state.pending_event_count = len(self._pending_events)
        await self._save_runtime_context()

    async def record_tool_result(self, tool_name: str, result: str, success: bool) -> None:
        """记录工具返回结果事件。"""
        event = self._event_builder.build_tool_result_event(tool_name, result, success)
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

            compress_threshold = int(limit * 0.8)
            if len(self._event_history) > compress_threshold:
                self._event_history = compress_history(self._event_history, limit)

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
        clear_wake_context_reminder()

    def _build_wake_context_text(self, events: list[LifeEngineEvent]) -> str:
        """把事件流拼成可注入的上下文文本。"""
        if not events:
            return ""

        sorted_events = sorted(events, key=lambda e: e.sequence)
        lines: list[str] = []

        for event in sorted_events:
            time_display = _format_time_display(event.timestamp)

            if event.event_type == EventType.MESSAGE:
                source = event.source_detail or event.source or "外部"
                source_short = self._simplify_source(source)
                line = f"[{time_display}] 📨 {source_short}"
                line += f"\n    └─ {event.sender}: {event.content}"
            elif event.event_type == EventType.HEARTBEAT:
                line = f"[{time_display}] 💭 心跳#{event.heartbeat_index}"
                line += f"\n    └─ {event.content}"
            elif event.event_type == EventType.TOOL_CALL:
                line = f"[{time_display}] 🔧 {event.tool_name}"
                if event.tool_args:
                    args_short = self._simplify_tool_args(event.tool_args)
                    if args_short:
                        line += f"({args_short})"
            elif event.event_type == EventType.TOOL_RESULT:
                status = "✅" if event.tool_success else "❌"
                result_short = _shorten_text(event.content or "", max_length=100)
                line = f"[{time_display}] {status} {event.tool_name}: {result_short}"
            else:
                line = f"[{time_display}] ❓ {event.content}"

            lines.append(line)

        return "\n".join(lines)

    async def search_outer_memory(self, query: str, top_k: int = 5) -> str:
        """供对外运行模式深度检索 life memory。"""
        return await self.search_actor_memory(query, top_k=top_k)

    def _simplify_source(self, source: str) -> str:
        """简化消息来源显示。"""
        if not source:
            return "外部"
        source = source.replace("qq | 入站 | ", "").replace("qq | 出站 | ", "")
        if len(source) > 30:
            return source[:27] + "..."
        return source

    def _simplify_tool_args(self, args: dict) -> str:
        """简化工具参数显示。"""
        if not args:
            return ""
        key_params = []
        for k, v in args.items():
            if k in ("path", "todo_id", "title", "content", "file_path"):
                v_str = str(v)
                if len(v_str) > 20:
                    v_str = v_str[:17] + "..."
                key_params.append(f"{k}={v_str}")
        return ", ".join(key_params[:2])

    async def inject_wake_context(self) -> str:
        """把当前待处理事件注入到系统提醒。"""
        events = await self.drain_pending_events()
        if events:
            await self._append_history(events)

        async with self._get_lock():
            context_events = list(self._event_history)

        if not context_events:
            clear_wake_context_reminder()
            return ""

        content = self._build_wake_context_text(context_events)
        from src.core.prompt import get_system_reminder_store
        from .state_manager import _TARGET_REMINDER_BUCKET, _TARGET_REMINDER_NAME

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
            f"count={len(context_events)} drained={len(events)} "
            f"task={self._cfg().model.task_name}"
        )
        return content

    async def _record_model_reply(self, model_reply: str) -> None:
        """记录心跳模型回复。"""
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
            heartbeat_event = self._event_builder.build_heartbeat_event(
                reply_text,
                self._state.heartbeat_count,
                self._cfg().model.task_name or "life",
            )
            await self._append_history([heartbeat_event])
        else:
            logger.info(f"life_engine 心跳模型回复为空: #{self._state.heartbeat_count}")

    def _build_heartbeat_model_prompt(self, wake_context: str) -> str:
        """构造心跳模型输入。"""
        minutes_since_external = self._minutes_since_external_message()
        heartbeat_interval = self._cfg().settings.heartbeat_interval_seconds
        idle_heartbeats = self._state.idle_heartbeat_count

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

        period_label, suggested_activities = self._get_period_info()

        cfg = self._cfg()
        thresholds = getattr(cfg, "thresholds", None)
        warning_threshold = int(
            getattr(thresholds, "idle_warning_threshold", HEARTBEAT_IDLE_WARNING_THRESHOLD)
        )
        critical_threshold = int(
            getattr(thresholds, "idle_critical_threshold", HEARTBEAT_IDLE_CRITICAL_THRESHOLD)
        )

        idle_warning = ""
        if idle_heartbeats >= critical_threshold:
            idle_warning = "⚠️ **你已经发呆太久了！这次必须做点什么。**"
        elif idle_heartbeats >= warning_threshold:
            idle_warning = f"💭 已经连续 {idle_heartbeats} 次心跳没有行动了，找点事做吧～"

        lines: list[str] = self._build_prompt_header()
        lines.extend(self._build_prompt_context_section(wake_context))

        if self._dream_scheduler is not None:
            try:
                dream_payload = str(
                    self._dream_scheduler.get_active_residue_payload("life") or ""
                ).strip()
                if dream_payload:
                    lines.extend([
                        "### 梦后余韵", "",
                        dream_payload, "",
                    ])
            except Exception:  # noqa: BLE001
                logger.debug("读取梦后余韵失败")

        lines.extend(self._build_prompt_status_section(
            period_label, heartbeat_interval, external_activity, idle_heartbeats
        ))

        if self._snn_network is not None and self._snn_bridge is not None:
            if not self._cfg().snn.shadow_only and self._cfg().snn.inject_to_heartbeat:
                drive_text = self._snn_bridge.format_drive_for_prompt(
                    self._snn_network.get_drive_discrete()
                )
                if drive_text:
                    lines.extend([f"**{drive_text}**", ""])

        cfg = self._cfg()
        if self._inner_state is not None and getattr(cfg, "neuromod", None) is not None:
            if cfg.neuromod.enabled and cfg.neuromod.inject_to_heartbeat:
                today_str = datetime.now().strftime("%Y-%m-%d")
                neuromod_text = self._inner_state.format_full_state_for_prompt(today_str)
                if neuromod_text:
                    lines.extend([neuromod_text, ""])

        if idle_warning:
            lines.extend([idle_warning, ""])

        lines.extend([f"**本时段建议**: {suggested_activities}", ""])

        return "\n".join(lines)

    def _build_prompt_header(self) -> list[str]:
        """构建提示词头部。"""
        return [
            "### 🎯 必须完成的事", "",
            "每次心跳**至少调用一个工具**，从以下选择：", "",
            "1. **检查待办** → `nucleus_list_todos`",
            "2. **搜索记忆** → `nucleus_search_memory`",
            "3. **读取文件** → `nucleus_read_file`",
            "4. **写点东西** → `nucleus_write_file` / `nucleus_edit_file`",
            "5. **建立关联** → `nucleus_relate_file`",
            "6. **传递给对外运行模式** → `nucleus_tell_dfc`",
            "7. **联网搜索** → `nucleus_web_search`",
            "8. **网页浏览** → `nucleus_browser_fetch`", "",
            "### 🧭 `nucleus_tell_dfc` 的核心判定：信息差", "",
            "判断标准不是语气，而是：**你是否握有对外运行模式目前没有的增量信息**。", "",
            "应该使用 `nucleus_tell_dfc`：",
            "- 你得到新信息，且会改变对外运行模式的判断/语气/优先级",
            "- 你形成了新关联（把分散线索连接成新结论）",
            "- 你发现了新风险（误解风险、情绪风险、节奏风险）",
            "- 你状态显著变化，且这个变化会影响外在对话", "",
            "不应该使用 `nucleus_tell_dfc`：",
            "- 没有信息差，只是在复述已知内容",
            "- 只是把动作要求丢给对外运行模式（任务分配）",
            "- 直接转发用户对 life 的命令原句", "",
            "### 🧱 工具边界", "",
            "- `nucleus_search_memory` 是历史检索，不要反复重搜同一主题",
            "- 本地文件路径优先用 `nucleus_read_file` / `nucleus_grep_file`",
            "- `nucleus_browser_fetch` 只适合公开 http/https 页面", "",
            "### ✍️ 输出格式（必须遵守）", "",
            "```",
            "**[观察]** 我注意到...（基于事件流或记忆的具体观察）", "",
            "**[感受]** 这让我...（情绪词 + 原因）", "",
            "**[意图]** 我想要...（具体目标，不能是「继续观察」或「等待」）", "",
            "**[行动]** 我决定...（说明要调用的工具）",
            "```", "",
            "然后执行工具调用。", "",
            "### ⚠️ 禁止事项", "",
            "- ❌ **禁止无工具调用**：「什么都不做」不是选项",
            "- ❌ **禁止重复内容**：不能连续两次说相似的话",
            "- ❌ **禁止被动等待**：「等合适的时机」是借口，现在就行动",
            "- ❌ **禁止空洞描述**：不要只说「世界安静了」，要有具体行动", "",
            "### 💡 提醒", "",
            "- 先用 `nucleus_list_todos` 看看待办，有事就做",
            "- 先用 `nucleus_read_file` 读取内容，再用 edit 修改",
            "- 截止时间逾期的 TODO：问自己还想做吗？不想就 released",
            "- 写了新东西要用 `nucleus_relate_file` 建立记忆联系", "",
            "---", "",
            "## 💖 本轮动态上下文", "",
            "### 当前文件系统概览", "",
            "```",
            f"{Path(self._cfg().settings.workspace_path).name}/",
            self._build_workspace_tree(),
            "```", "",
        ]

    def _build_prompt_context_section(self, wake_context: str) -> list[str]:
        """构建提示词上下文部分。"""
        lines = []
        if wake_context.strip():
            lines.extend([
                "### 最近事件流", "",
                wake_context.strip(), "",
            ])
        return lines

    def _build_prompt_status_section(
        self,
        period_label: str,
        heartbeat_interval: int,
        external_activity: str,
        idle_heartbeats: int,
    ) -> list[str]:
        """构建提示词状态部分。"""
        return [
            "### 心跳状态", "",
            f"**当前时间**: {_format_current_time()}",
            f"**时段**: {period_label}",
            f"**心跳序号**: #{self._state.heartbeat_count}（每 {heartbeat_interval // 60} 分钟一次）",
            f"**外界状态**: {external_activity}",
            f"**连续空闲**: {idle_heartbeats} 次心跳", "",
        ]

    def _get_period_info(self) -> tuple[str, str]:
        """获取当前时段标签和建议活动。"""
        hour = datetime.now().hour

        if 6 <= hour < 9:
            return "🌅 清晨", "规划今天、整理思绪、回顾昨天"
        elif 9 <= hour < 12:
            return "☀️ 上午", "执行任务、学习新知、处理待办"
        elif 12 <= hour < 14:
            return "🍱 午后", "轻松休息、随意浏览、小憩片刻"
        elif 14 <= hour < 18:
            return "📝 下午", "深度工作、创作内容、推进项目"
        elif 18 <= hour < 21:
            return "🌆 傍晚", "社交互动、分享心情、整理收获"
        elif 21 <= hour < 24:
            return "🌙 夜晚", "写日记、反思总结、准备休息"
        else:
            return "🌌 深夜", "安静独处、偶尔冒出想法、休息"

    def _build_workspace_tree(self) -> str:
        """构建工作空间文件树显示。"""
        workspace = Path(self._cfg().settings.workspace_path)

        if not workspace.exists():
            return "（工作空间为空）"

        lines = []
        try:
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
        """构造心跳模型系统提示词。"""
        workspace = Path(self._cfg().settings.workspace_path)

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

        memory_file = workspace / "MEMORY.md"
        memory_content = ""
        if memory_file.exists():
            try:
                memory_content = memory_file.read_text(encoding="utf-8").strip()
            except Exception as e:
                logger.warning(f"无法读取 MEMORY.md: {e}")

        tool_file = workspace / "TOOL.md"
        tool_content = ""
        if tool_file.exists():
            try:
                tool_content = tool_file.read_text(encoding="utf-8").strip()
            except Exception as e:
                logger.warning(f"无法读取 TOOL.md: {e}")

        parts = [soul_content]
        if memory_content:
            parts.extend(["", "---", "", memory_content])
        if tool_content:
            parts.extend(["", "---", "", tool_content])

        return "\n".join(parts)

    def _get_nucleus_tools(self) -> list[type]:
        """获取中枢可用的工具类列表。"""
        from ..tools import ALL_TOOLS, TODO_TOOLS, WEB_TOOLS
        from ..memory.tools import MEMORY_TOOLS

        return ALL_TOOLS + TODO_TOOLS + MEMORY_TOOLS + WEB_TOOLS

    async def _execute_heartbeat_tool_call(
        self,
        call: Any,
        response: Any,
        registry: ToolRegistry,
    ) -> None:
        """执行一次心跳 tool call。"""
        tool_name = getattr(call, "name", "") or ""
        raw_args = getattr(call, "args", {}) or {}
        args = dict(raw_args) if isinstance(raw_args, dict) else {}

        log_args = {k: v for k, v in args.items() if k != "reason"}
        await self.record_tool_call(tool_name or "<unknown>", log_args)

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
                ToolResult(value=result_text, call_id=call_id, name=tool_name),
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

        request.add_payload(
            LLMPayload(ROLE.SYSTEM, Text(self._build_heartbeat_system_prompt()))
        )

        tools = self._get_nucleus_tools()
        registry = ToolRegistry()
        for tool in tools:
            registry.register(tool)
        request.add_payload(LLMPayload(ROLE.TOOL, tools))

        request.add_payload(LLMPayload(ROLE.USER, Text(self._build_heartbeat_model_prompt(wake_context))))

        timeout_seconds = max(10.0, min(60.0, float(cfg.settings.heartbeat_interval_seconds)))

        logger.debug(
            f"life_engine heartbeat request: "
            f"system_prompt_len={len(self._build_heartbeat_system_prompt())} "
            f"user_prompt_len={len(self._build_heartbeat_model_prompt(wake_context))} "
            f"tools_count={len(tools)}"
        )

        from .error_handling import retry_with_backoff

        async def _send_heartbeat_request() -> Any:
            return await asyncio.wait_for(
                request.send(stream=False), timeout=timeout_seconds
            )

        try:
            response = await retry_with_backoff(
                _send_heartbeat_request,
                max_retries=2,
                initial_delay=2.0,
                backoff_factor=1.5,
                exceptions=(asyncio.TimeoutError,),
            )
        except Exception as e:
            logger.error(f"Heartbeat request failed after all retries: {e}")
            return

        max_rounds = max(1, int(cfg.settings.max_rounds_per_heartbeat))
        last_text = ""
        tool_event_count = 0

        for _ in range(max_rounds):
            try:
                response_text = await response
            except asyncio.TimeoutError:
                logger.warning("life_engine heartbeat response read timeout")
                break

            last_text = str(response_text or "").strip()
            call_list = list(getattr(response, "call_list", []) or [])

            logger.debug(
                f"life_engine heartbeat turn: "
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
                tool_event_count += 2

            try:
                response = await asyncio.wait_for(response.send(stream=False), timeout=timeout_seconds)
            except asyncio.TimeoutError:
                logger.warning("life_engine heartbeat follow-up request timeout")
                break

        if tool_event_count > 0:
            self._state.idle_heartbeat_count = 0
        else:
            self._state.idle_heartbeat_count += 1
            logger.debug(f"life_engine 心跳无工具调用，空闲计数: {self._state.idle_heartbeat_count}")

        if not last_text:
            if tool_event_count > 0:
                last_text = f"我刚刚完成了 {tool_event_count // 2} 次工具操作，先记下这些变化。"
            else:
                last_text = "此刻很安静，但我仍在持续感受与观察。"

        return last_text

    async def _save_runtime_context(self) -> None:
        """持久化当前上下文。"""
        if self._state_persistence is None:
            self._state_persistence = StatePersistence(
                self._cfg().settings.workspace_path,
                self._history_limit,
                self._lock,
            )
        await self._state_persistence.save_runtime_context(
            self._state,
            self._pending_events,
            self._event_history,
            self._snn_network,
            self._inner_state,
            self._dream_scheduler,
        )

    async def _load_runtime_context(self) -> None:
        """从持久化文件恢复上下文。"""
        if self._state_persistence is None:
            self._state_persistence = StatePersistence(
                self._cfg().settings.workspace_path,
                self._history_limit,
                self._lock,
            )
        pending, history, persisted = await self._state_persistence.load_runtime_context(
            self._state,
            self._next_sequence,
        )
        self._pending_events = pending
        self._event_history = history

        # 存储持久化状态供子系统恢复
        if persisted.get("snn_state"):
            self._snn_persisted_state = persisted["snn_state"]
        if persisted.get("neuromod_state"):
            self._neuromod_persisted_state = persisted["neuromod_state"]
        if persisted.get("dream_state"):
            self._dream_persisted_state = persisted["dream_state"]

    async def start(self) -> None:
        """启动心跳。"""
        from .registry import register_life_engine_service

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

        # 初始化集成管理器
        self._memory_integration = MemoryIntegration(self)
        await self._memory_integration.init_memory_service()

        self._snn_integration = SNNIntegration(self)
        await self._snn_integration.init_snn()

        self._dfc_integration = DFCIntegration(self)

        self._state.running = True
        self._state.started_at = _now_iso()
        self._state.last_heartbeat_at = self._state.last_heartbeat_at or self._state.started_at
        self._state.last_error = None
        self._state.history_event_count = len(self._event_history)
        self._state.pending_event_count = len(self._pending_events)

        register_life_engine_service(self)

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
            f"wake={cfg.settings.wake_time or '-'} "
            f"snn={cfg.snn.enabled}"
        )
        log_lifecycle(
            "started",
            enabled=True,
            heartbeat_interval_seconds=int(cfg.settings.heartbeat_interval_seconds),
            model_task_name=cfg.model.task_name,
            log_file_path=str(get_life_log_file()),
            snn_enabled=cfg.snn.enabled,
        )

    async def stop(self) -> None:
        """停止心跳。"""
        from .registry import unregister_life_engine_service

        pending_before_stop = len(self._pending_events)
        self._state.running = False

        if self._stop_event is not None:
            self._stop_event.set()

        from .error_handling import safe_cancel_task

        if self._heartbeat_task_id:
            safe_cancel_task(self._heartbeat_task_id, get_task_manager())
            self._heartbeat_task_id = None

        if self._snn_tick_task_id:
            safe_cancel_task(self._snn_tick_task_id, get_task_manager())
            self._snn_tick_task_id = None
        self._stop_event = None
        unregister_life_engine_service()
        await self._save_runtime_context()

        logger.info("life_engine 已停止")
        log_lifecycle(
            "stopped",
            pending_message_count=pending_before_stop,
            heartbeat_count=self._state.heartbeat_count,
            log_file_path=str(get_life_log_file()),
            snn_tick_count=self._snn_network.tick_count if self._snn_network else 0,
        )

    async def _heartbeat_loop(self) -> None:
        """心跳循环。"""
        interval = max(1, int(self._cfg().settings.heartbeat_interval_seconds))
        should_log_heartbeat = bool(self._cfg().settings.log_heartbeat)

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
                        if self._dream_scheduler is not None:
                            self._dream_scheduler.enter_sleep()

                    # 做梦检查
                    if self._dream_scheduler is not None and self._dream_scheduler.should_dream(
                        idle_heartbeat_count=self._state.idle_heartbeat_count,
                        in_sleep_window=True,
                    ):
                        try:
                            async with self._get_lock():
                                event_history = list(self._event_history)
                            report = await self._dream_scheduler.run_dream_cycle(event_history)
                            await self._save_runtime_context()
                            if self._dfc_integration is not None:
                                await self._dfc_integration.inject_dream_report(report, "sleep_window")
                            logger.info(
                                f"🌙 做梦完成: dream_id={report.dream_id} "
                                f"duration={report.duration_seconds:.1f}s"
                            )
                        except Exception as exc:
                            logger.error(f"做梦执行异常: {exc}", exc_info=True)

                    if should_log_heartbeat:
                        logger.info(f"life_engine heartbeat tick: 睡眠中（{sleep_window_desc}），跳过")
                    continue
                elif self._sleep_state_active:
                    logger.info(
                        "life_engine 睡眠时段结束，恢复心跳处理: "
                        f"window={sleep_window_desc}"
                    )
                    self._sleep_state_active = False

                self._state.heartbeat_count += 1
                self._state.last_heartbeat_at = _now_iso()

                # 每日记忆衰减
                if self._memory_integration is not None:
                    await self._memory_integration.maybe_run_daily_decay()

                # SNN 心跳前更新
                if self._snn_integration is not None:
                    await self._snn_integration.heartbeat_pre()

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

                    # SNN 心跳后更新
                    if self._snn_integration is not None:
                        await self._snn_integration.heartbeat_post()

                    # 白天小憩检查
                    if self._dream_scheduler is not None and self._dream_scheduler.should_dream(
                        idle_heartbeat_count=self._state.idle_heartbeat_count,
                        in_sleep_window=False,
                    ):
                        try:
                            async with self._get_lock():
                                event_history = list(self._event_history)
                            report = await self._dream_scheduler.run_dream_cycle(event_history)
                            await self._save_runtime_context()
                            if self._dfc_integration is not None:
                                await self._dfc_integration.inject_dream_report(report, "daytime_nap")
                            logger.info(
                                f"💤 白天小憩完成: dream_id={report.dream_id} "
                                f"duration={report.duration_seconds:.1f}s"
                            )
                        except Exception as nap_exc:  # noqa: BLE001
                            logger.error(f"白天小憩执行异常: {nap_exc}", exc_info=True)

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
        """手动触发一次心跳。"""
        if not self._is_enabled():
            return {"success": False, "error": "life_engine 未启用"}

        in_sleep_window, sleep_window_desc = self._in_sleep_window_now()
        if in_sleep_window:
            return {"success": False, "error": f"当前在睡眠时段（{sleep_window_desc}），心跳已暂停"}

        logger.info("life_engine 手动触发心跳")

        try:
            self._state.heartbeat_count += 1
            self._state.last_heartbeat_at = _now_iso()

            if self._memory_integration is not None:
                await self._memory_integration.maybe_run_daily_decay()

            if self._snn_integration is not None:
                await self._snn_integration.heartbeat_pre()

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

            if self._snn_integration is not None:
                await self._snn_integration.heartbeat_post()

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

    async def trigger_dream_manually(self) -> dict[str, Any]:
        """手动触发一次做梦周期。"""
        if not self._is_enabled():
            return {"success": False, "error": "life_engine 未启用"}

        dream = self._dream_scheduler
        if dream is None:
            return {"success": False, "error": "做梦系统未启用"}

        if dream.is_dreaming:
            return {"success": False, "error": "做梦系统正在运行中"}

        logger.info("life_engine 手动触发做梦")

        try:
            dream.enter_sleep()
            async with self._get_lock():
                event_history = list(self._event_history)
            report = await dream.run_dream_cycle(event_history)
            await self._save_runtime_context()

            if self._dfc_integration is not None:
                await self._dfc_integration.inject_dream_report(report, "manual")

            logger.info(
                "life_engine 手动做梦完成: "
                f"dream_id={report.dream_id} duration={report.duration_seconds:.1f}s"
            )

            return {
                "success": True,
                "dream_id": report.dream_id,
                "duration_seconds": round(report.duration_seconds, 1),
                "nrem_episodes": report.nrem.episodes_replayed,
                "nrem_steps": report.nrem.total_steps,
                "rem_nodes": report.rem.nodes_activated,
                "rem_new_edges": report.rem.new_edges_created,
                "rem_pruned_edges": report.rem.edges_pruned,
                "seed_titles": [seed.title for seed in report.seed_report],
                "seed_types": [seed.seed_type for seed in report.seed_report],
                "dream_text": report.dream_text or report.narrative,
                "dream_residue": (
                    {
                        "summary": report.dream_residue.summary,
                        "life_payload": report.dream_residue.life_payload,
                        "dfc_payload": report.dream_residue.dfc_payload,
                        "dominant_affect": report.dream_residue.dominant_affect,
                        "strength": report.dream_residue.strength,
                        "tags": list(report.dream_residue.tags),
                    }
                    if report.dream_residue is not None
                    else None
                ),
                "archive_path": report.archive_path,
                "memory_effects": dict(report.memory_effects),
            }
        except Exception as exc:  # noqa: BLE001
            logger.error(f"life_engine 手动做梦失败: {exc}\n{traceback.format_exc()}")
            log_error(
                "manual_dream_failed",
                str(exc),
                heartbeat_count=self._state.heartbeat_count,
                heartbeat_at=self._state.last_heartbeat_at,
            )
            return {"success": False, "error": str(exc)}
