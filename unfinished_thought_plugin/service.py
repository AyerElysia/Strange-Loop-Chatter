"""unfinished_thought_plugin 服务。"""

from __future__ import annotations

import asyncio
import json
import random
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.app.plugin_system.base import BaseService
from src.core.config import get_model_config
from src.kernel.event import EventDecision
from src.kernel.llm import LLMContextManager, LLMRequest
from src.kernel.llm.payload import LLMPayload, Text
from src.kernel.llm.roles import ROLE
from src.kernel.logger import get_logger

from .config import UnfinishedThoughtConfig
from .prompts import (
    build_unfinished_thought_prompt_block,
    build_unfinished_thought_scan_system_prompt,
    build_unfinished_thought_scan_user_prompt,
)


logger = get_logger("unfinished_thought_plugin")

_STATE_VERSION = 1
_STREAM_LOCKS: dict[str, asyncio.Lock] = {}


def _now() -> datetime:
    return datetime.now().astimezone()


def _now_iso() -> str:
    return _now().isoformat(timespec="seconds")


def _normalize_text(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _normalize_status(value: Any, default: str = "open") -> str:
    status = str(value or "").strip().lower()
    if status in {"open", "paused", "resolved", "expired"}:
        return status
    return default


def _clamp_int(value: Any, default: int, minimum: int = 1, maximum: int = 10) -> int:
    try:
        number = int(value)
    except Exception:
        return default
    return max(minimum, min(maximum, number))


@dataclass
class UnfinishedThoughtItem:
    """单条未完成念头。"""

    thought_id: str
    title: str
    content: str
    status: str = "open"
    priority: int = 1
    reason: str = ""
    source_event: str = ""
    created_at: str = ""
    updated_at: str = ""
    last_mentioned_at: str = ""
    mention_count: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UnfinishedThoughtItem":
        return cls(
            thought_id=str(data.get("thought_id", "")),
            title=str(data.get("title", "")),
            content=str(data.get("content", "")),
            status=str(data.get("status", "open")),
            priority=_clamp_int(data.get("priority", 1), 1, 1, 10),
            reason=str(data.get("reason", "")),
            source_event=str(data.get("source_event", "")),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
            last_mentioned_at=str(data.get("last_mentioned_at", "")),
            mention_count=_clamp_int(data.get("mention_count", 0), 0, 0, 10_000),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "thought_id": self.thought_id,
            "title": self.title,
            "content": self.content,
            "status": self.status,
            "priority": self.priority,
            "reason": self.reason,
            "source_event": self.source_event,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_mentioned_at": self.last_mentioned_at,
            "mention_count": self.mention_count,
        }


@dataclass
class ThoughtScanRecord:
    """一次扫描记录。"""

    record_id: str
    created_at: str
    trigger: str
    source_summary: str
    recent_message_count: int
    new_count: int
    updated_count: int
    resolved_count: int
    paused_count: int

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ThoughtScanRecord":
        return cls(
            record_id=str(data.get("record_id", "")),
            created_at=str(data.get("created_at", "")),
            trigger=str(data.get("trigger", "auto")),
            source_summary=str(data.get("source_summary", "")),
            recent_message_count=_clamp_int(data.get("recent_message_count", 0), 0, 0, 10_000),
            new_count=_clamp_int(data.get("new_count", 0), 0, 0, 10_000),
            updated_count=_clamp_int(data.get("updated_count", 0), 0, 0, 10_000),
            resolved_count=_clamp_int(data.get("resolved_count", 0), 0, 0, 10_000),
            paused_count=_clamp_int(data.get("paused_count", 0), 0, 0, 10_000),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "created_at": self.created_at,
            "trigger": self.trigger,
            "source_summary": self.source_summary,
            "recent_message_count": self.recent_message_count,
            "new_count": self.new_count,
            "updated_count": self.updated_count,
            "resolved_count": self.resolved_count,
            "paused_count": self.paused_count,
        }


@dataclass
class UnfinishedThoughtState:
    """单个聊天流的未完成念头状态。"""

    stream_id: str
    chat_type: str
    platform: str = ""
    stream_name: str = ""
    updated_at: str = ""
    message_count_since_scan: int = 0
    thoughts: list[UnfinishedThoughtItem] = field(default_factory=list)
    history: list[ThoughtScanRecord] = field(default_factory=list)

    @classmethod
    def empty(
        cls,
        *,
        stream_id: str,
        chat_type: str,
        platform: str = "",
        stream_name: str = "",
    ) -> "UnfinishedThoughtState":
        return cls(
            stream_id=stream_id,
            chat_type=chat_type,
            platform=platform,
            stream_name=stream_name,
            updated_at=_now_iso(),
            message_count_since_scan=0,
            thoughts=[],
            history=[],
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UnfinishedThoughtState":
        thoughts = [
            UnfinishedThoughtItem.from_dict(item)
            for item in data.get("thoughts", [])
            if isinstance(item, dict)
        ]
        history = [
            ThoughtScanRecord.from_dict(item)
            for item in data.get("history", [])
            if isinstance(item, dict)
        ]
        return cls(
            stream_id=str(data.get("stream_id", "")),
            chat_type=str(data.get("chat_type", "private")),
            platform=str(data.get("platform", "")),
            stream_name=str(data.get("stream_name", "")),
            updated_at=str(data.get("updated_at", "")),
            message_count_since_scan=_clamp_int(
                data.get("message_count_since_scan", 0), 0, 0, 10_000
            ),
            thoughts=thoughts,
            history=history,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": _STATE_VERSION,
            "stream_id": self.stream_id,
            "chat_type": self.chat_type,
            "platform": self.platform,
            "stream_name": self.stream_name,
            "updated_at": self.updated_at,
            "message_count_since_scan": self.message_count_since_scan,
            "thoughts": [item.to_dict() for item in self.thoughts],
            "history": [item.to_dict() for item in self.history],
        }

    def has_content(self) -> bool:
        return bool(self.thoughts or self.history or self.message_count_since_scan)

    def snapshot(self) -> dict[str, Any]:
        return {
            "stream_id": self.stream_id,
            "chat_type": self.chat_type,
            "platform": self.platform,
            "stream_name": self.stream_name,
            "message_count_since_scan": self.message_count_since_scan,
            "thoughts": [item.to_dict() for item in self.thoughts],
        }


def get_unfinished_thought_service() -> "UnfinishedThoughtService | None":
    """获取 unfinished_thought 服务实例。"""

    from src.app.plugin_system.api.service_api import get_service

    service = get_service("unfinished_thought_plugin:service:unfinished_thought_service")
    if isinstance(service, UnfinishedThoughtService):
        return service
    return None


class UnfinishedThoughtService(BaseService):
    """未完成念头服务。"""

    service_name = "unfinished_thought_service"
    service_description = "按聊天流隔离的未完成念头维护服务"
    version = "1.0.0"

    def _cfg(self) -> UnfinishedThoughtConfig:
        cfg = self.plugin.config
        if not isinstance(cfg, UnfinishedThoughtConfig):
            raise RuntimeError("unfinished_thought_plugin config 未正确加载")
        return cfg

    def _is_enabled(self) -> bool:
        try:
            return bool(self._cfg().plugin.enabled)
        except RuntimeError:
            return False

    def _normalize_chat_type(self, chat_type: str | None) -> str:
        raw = str(chat_type or "").lower()
        if raw in {"private", "group", "discuss"}:
            return raw
        if raw == "guild":
            return "group"
        return "private"

    def _get_base_path(self) -> Path:
        return Path(self._cfg().storage.base_path)

    def _get_state_path(self, stream_id: str, chat_type: str) -> Path:
        path = self._get_base_path() / self._normalize_chat_type(chat_type) / f"{stream_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _find_existing_state_path(self, stream_id: str) -> tuple[str, Path] | None:
        for chat_type in ("private", "group", "discuss"):
            path = self._get_state_path(stream_id, chat_type)
            if path.exists():
                return chat_type, path
        return None

    def _load_state_from_path(
        self,
        path: Path,
        *,
        stream_id: str,
        chat_type: str,
        platform: str = "",
        stream_name: str = "",
    ) -> UnfinishedThoughtState:
        if not path.exists():
            return UnfinishedThoughtState.empty(
                stream_id=stream_id,
                chat_type=chat_type,
                platform=platform,
                stream_name=stream_name,
            )

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.error(f"读取未完成念头失败：{path} - {exc}")
            return UnfinishedThoughtState.empty(
                stream_id=stream_id,
                chat_type=chat_type,
                platform=platform,
                stream_name=stream_name,
            )

        state = UnfinishedThoughtState.from_dict(data)
        if not state.stream_id:
            state.stream_id = stream_id
        if not state.chat_type:
            state.chat_type = self._normalize_chat_type(chat_type)
        if platform and not state.platform:
            state.platform = platform
        if stream_name and not state.stream_name:
            state.stream_name = stream_name
        return state

    def get_state(
        self,
        stream_id: str,
        chat_type: str | None = None,
        *,
        platform: str = "",
        stream_name: str = "",
    ) -> UnfinishedThoughtState:
        normalized = self._normalize_chat_type(chat_type)
        if chat_type is None:
            found = self._find_existing_state_path(stream_id)
            if found is not None:
                normalized, path = found
                return self._load_state_from_path(
                    path,
                    stream_id=stream_id,
                    chat_type=normalized,
                    platform=platform,
                    stream_name=stream_name,
                )

        path = self._get_state_path(stream_id, normalized)
        return self._load_state_from_path(
            path,
            stream_id=stream_id,
            chat_type=normalized,
            platform=platform,
            stream_name=stream_name,
        )

    def _save_state(self, state: UnfinishedThoughtState) -> None:
        state.updated_at = _now_iso()
        self._compact_state(state)
        path = self._get_state_path(state.stream_id, state.chat_type)
        path.write_text(
            json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _compact_state(self, state: UnfinishedThoughtState) -> None:
        max_thoughts = max(1, int(self._cfg().storage.max_thoughts))
        max_history = max(1, int(self._cfg().storage.max_history_records))

        status_rank = {
            "open": 0,
            "paused": 1,
            "resolved": 2,
            "expired": 3,
        }

        def _sort_key(item: UnfinishedThoughtItem) -> tuple[int, int, str]:
            return (
                status_rank.get(item.status, 4),
                -int(item.priority),
                item.updated_at or item.created_at or "",
            )

        state.thoughts.sort(key=_sort_key)
        if len(state.thoughts) > max_thoughts:
            state.thoughts = state.thoughts[:max_thoughts]

        if len(state.history) > max_history:
            state.history = state.history[-max_history:]

    def _get_lock(self, stream_id: str) -> asyncio.Lock:
        lock = _STREAM_LOCKS.get(stream_id)
        if lock is None:
            lock = asyncio.Lock()
            _STREAM_LOCKS[stream_id] = lock
        return lock

    def _default_title(self, content: str) -> str:
        text = str(content or "").strip()
        if not text:
            return "未命名念头"
        if len(text) <= 10:
            return text
        return f"{text[:10]}…"

    def _build_thought_lines(self, state: UnfinishedThoughtState) -> list[str]:
        lines: list[str] = []
        for index, thought in enumerate(self._ordered_thoughts(state), start=1):
            title = thought.title or thought.content[:20]
            content = thought.content
            lines.append(
                f"{index}. [{thought.status}] {title} | p={thought.priority} | {content}"
            )
        return lines

    def _ordered_thoughts(self, state: UnfinishedThoughtState) -> list[UnfinishedThoughtItem]:
        status_rank = {"open": 0, "paused": 1, "resolved": 2, "expired": 3}
        return sorted(
            state.thoughts,
            key=lambda item: (
                status_rank.get(item.status, 4),
                -int(item.priority),
                item.updated_at or item.created_at or "",
            ),
        )

    def _find_thought(self, state: UnfinishedThoughtState, selector: str) -> UnfinishedThoughtItem | None:
        normalized_selector = str(selector or "").strip()
        if not normalized_selector:
            return None

        for thought in state.thoughts:
            if thought.thought_id == normalized_selector:
                return thought

        if normalized_selector.isdigit():
            index = int(normalized_selector) - 1
            ordered = self._ordered_thoughts(state)
            if 0 <= index < len(ordered):
                return ordered[index]

        normalized_title = _normalize_text(normalized_selector)
        for thought in state.thoughts:
            if _normalize_text(thought.title) == normalized_title:
                return thought

        return None

    def _collect_recent_history(
        self,
        *,
        stream_id: str,
        chat_type: str,
        platform: str,
        stream_name: str,
    ) -> tuple[list[str], Any | None]:
        from src.core.managers import get_stream_manager

        stream_manager = get_stream_manager()
        chat_stream = stream_manager._streams.get(stream_id)
        if not chat_stream:
            return [], None

        context = getattr(chat_stream, "context", None)
        if context is None:
            return [], chat_stream

        all_messages = list(context.history_messages) + list(context.unread_messages)
        window_size = max(1, int(self._cfg().scan.history_window_size))
        recent_messages = all_messages[-window_size:]

        bot_id = str(getattr(chat_stream, "bot_id", "") or "")
        bot_nickname = str(getattr(chat_stream, "bot_nickname", "") or "")
        lines: list[str] = []
        for msg in recent_messages:
            sender = str(getattr(msg, "sender_name", "未知"))
            sender_id = str(getattr(msg, "sender_id", "") or "")
            if bot_id and sender_id == bot_id:
                sender = f"{bot_nickname or sender}（自己）"
            content = getattr(
                msg,
                "processed_plain_text",
                str(getattr(msg, "content", "")),
            )
            lines.append(f"{sender}: {content}")

        return lines, chat_stream

    def _get_model_set(self) -> list[dict[str, Any]] | None:
        cfg = self._cfg()
        task_name = cfg.model.task_name.strip() or "diary"
        fallback_task_name = cfg.model.fallback_task_name.strip() or "actor"

        try:
            return get_model_config().get_task(task_name)
        except Exception:
            try:
                return get_model_config().get_task(fallback_task_name)
            except Exception as exc:
                logger.warning(f"未找到未完成念头模型：{task_name}/{fallback_task_name} - {exc}")
                return None

    def _parse_scan_json(self, raw: str) -> dict[str, Any] | None:
        if not raw:
            return None

        obj: dict[str, Any] | None = None
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                obj = parsed
        except json.JSONDecodeError:
            start = raw.find("{")
            end = raw.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    parsed = json.loads(raw[start : end + 1])
                    if isinstance(parsed, dict):
                        obj = parsed
                except json.JSONDecodeError:
                    return None

        return obj

    def _extract_list(self, obj: dict[str, Any], key: str) -> list[Any]:
        value = obj.get(key, [])
        return value if isinstance(value, list) else []

    async def _call_llm_for_scan(
        self,
        *,
        trigger: str,
        current_state: UnfinishedThoughtState,
        recent_history: list[str],
    ) -> dict[str, Any] | None:
        model_set = self._get_model_set()
        if not model_set:
            return None

        context_manager = LLMContextManager(max_payloads=4)
        request = LLMRequest(
            model_set,
            request_name="unfinished_thought_scan",
            context_manager=context_manager,
        )
        request.add_payload(
            LLMPayload(ROLE.SYSTEM, Text(build_unfinished_thought_scan_system_prompt()))
        )
        request.add_payload(
            LLMPayload(
                ROLE.USER,
                Text(
                    build_unfinished_thought_scan_user_prompt(
                        trigger=trigger,
                        current_state=current_state.snapshot(),
                        recent_history=recent_history,
                    )
                ),
            )
        )

        try:
            response = await request.send()
            text = await response if not response.message else response.message
        except Exception as exc:
            logger.error(f"未完成念头扫描失败：{exc}")
            return None

        if not text:
            return None
        return self._parse_scan_json(str(text))

    def _apply_scan_result(
        self,
        state: UnfinishedThoughtState,
        result: dict[str, Any],
        *,
        trigger: str,
        recent_message_count: int,
    ) -> ThoughtScanRecord:
        now = _now_iso()
        new_count = 0
        updated_count = 0
        resolved_count = 0
        paused_count = 0

        def _touch(item: UnfinishedThoughtItem) -> None:
            item.updated_at = now
            item.last_mentioned_at = now
            item.mention_count += 1

        existing_by_id = {item.thought_id: item for item in state.thoughts}

        for item in self._extract_list(result, "new_thoughts"):
            if not isinstance(item, dict):
                continue
            content = str(item.get("content", "")).strip()
            if not content:
                continue
            title = str(item.get("title", "")).strip() or self._default_title(content)
            normalized_content = _normalize_text(content)
            duplicate = None
            for thought in state.thoughts:
                if _normalize_text(thought.content) == normalized_content and thought.status != "expired":
                    duplicate = thought
                    break
            if duplicate is not None:
                duplicate.title = title
                duplicate.content = content
                duplicate.priority = _clamp_int(item.get("priority", duplicate.priority), duplicate.priority, 1, 10)
                duplicate.reason = str(item.get("reason", duplicate.reason))
                duplicate.status = _normalize_status(item.get("status", "open"))
                duplicate.source_event = trigger
                _touch(duplicate)
                updated_count += 1
                continue

            thought = UnfinishedThoughtItem(
                thought_id=f"th_{uuid4().hex}",
                title=title,
                content=content,
                status=_normalize_status(item.get("status", "open")),
                priority=_clamp_int(item.get("priority", 1), 1, 1, 10),
                reason=str(item.get("reason", "")),
                source_event=trigger,
                created_at=now,
                updated_at=now,
                last_mentioned_at=now,
                mention_count=1,
            )
            state.thoughts.append(thought)
            new_count += 1
            existing_by_id[thought.thought_id] = thought

        for item in self._extract_list(result, "updates"):
            if not isinstance(item, dict):
                continue
            thought_id = str(item.get("thought_id", "")).strip()
            thought = existing_by_id.get(thought_id)
            if thought is None:
                continue

            title = str(item.get("title", "")).strip()
            content = str(item.get("content", "")).strip()
            status = str(item.get("status", "")).strip()
            reason = str(item.get("reason", "")).strip()
            priority = item.get("priority", None)

            if title:
                thought.title = title
            if content:
                thought.content = content
            if status in {"open", "paused", "resolved", "expired"}:
                thought.status = status
            if reason:
                thought.reason = reason
            if priority is not None:
                thought.priority = _clamp_int(priority, thought.priority, 1, 10)
            thought.status = _normalize_status(thought.status, default="open")
            thought.source_event = trigger
            _touch(thought)
            updated_count += 1

        for thought_id in self._extract_list(result, "resolved_ids"):
            thought = existing_by_id.get(str(thought_id))
            if thought is None:
                continue
            thought.status = "resolved"
            thought.source_event = trigger
            _touch(thought)
            resolved_count += 1

        for thought_id in self._extract_list(result, "paused_ids"):
            thought = existing_by_id.get(str(thought_id))
            if thought is None:
                continue
            thought.status = "paused"
            thought.source_event = trigger
            _touch(thought)
            paused_count += 1

        self._compact_state(state)
        record = ThoughtScanRecord(
            record_id=f"scan_{uuid4().hex}",
            created_at=now,
            trigger=trigger,
            source_summary=f"new={new_count}, update={updated_count}, resolved={resolved_count}, paused={paused_count}",
            recent_message_count=recent_message_count,
            new_count=new_count,
            updated_count=updated_count,
            resolved_count=resolved_count,
            paused_count=paused_count,
        )
        state.history.append(record)
        self._compact_state(state)
        return record

    async def scan_thoughts(
        self,
        *,
        stream_id: str,
        chat_type: str,
        platform: str = "",
        stream_name: str = "",
        trigger: str = "manual",
        restore_message_count_on_failure: int | None = None,
    ) -> tuple[bool, str]:
        if not self._is_enabled():
            return False, "unfinished_thought_plugin 未启用"

        normalized_chat_type = self._normalize_chat_type(chat_type)
        async with self._get_lock(stream_id):
            state = self.get_state(
                stream_id,
                normalized_chat_type,
                platform=platform,
                stream_name=stream_name,
            )
            recent_history, chat_stream = self._collect_recent_history(
                stream_id=stream_id,
                chat_type=normalized_chat_type,
                platform=platform,
                stream_name=stream_name,
            )
            if chat_stream is not None:
                platform = str(getattr(chat_stream, "platform", platform) or platform)
                stream_name = str(getattr(chat_stream, "stream_name", stream_name) or stream_name)

            previous_count = (
                restore_message_count_on_failure
                if restore_message_count_on_failure is not None
                else state.message_count_since_scan
            )
            state.message_count_since_scan = 0

            result = await self._call_llm_for_scan(
                trigger=trigger,
                current_state=state,
                recent_history=recent_history,
            )
            if result is None:
                state.message_count_since_scan = max(0, int(previous_count))
                self._save_state(state)
                return False, "未完成念头扫描失败"

            record = self._apply_scan_result(
                state,
                result,
                trigger=trigger,
                recent_message_count=len(recent_history),
            )
            # 记录本次扫描所使用的历史窗口长度
            record.recent_message_count = len(recent_history)
            state.stream_id = stream_id
            state.chat_type = normalized_chat_type
            if platform:
                state.platform = platform
            if stream_name:
                state.stream_name = stream_name
            self._save_state(state)

            logger.info(
                f"[{stream_id[:8]}] 未完成念头已更新 "
                f"(trigger={trigger}, {record.source_summary})"
            )
            return True, "未完成念头已更新"

    async def record_chat_turn(
        self,
        *,
        stream_id: str,
        chat_type: str,
        platform: str = "",
        stream_name: str = "",
        trigger: str = "auto",
    ) -> tuple[bool, str]:
        if not self._is_enabled():
            return False, "unfinished_thought_plugin 未启用"

        normalized_chat_type = self._normalize_chat_type(chat_type)
        threshold = max(1, int(self._cfg().scan.trigger_every_n_messages))

        async with self._get_lock(stream_id):
            state = self.get_state(
                stream_id,
                normalized_chat_type,
                platform=platform,
                stream_name=stream_name,
            )
            state.message_count_since_scan += 1
            current_count = state.message_count_since_scan
            if platform:
                state.platform = platform
            if stream_name:
                state.stream_name = stream_name
            if current_count < threshold:
                self._save_state(state)
                return True, f"计数 {current_count}/{threshold}"

            state.message_count_since_scan = 0
            self._save_state(state)

        return await self.scan_thoughts(
            stream_id=stream_id,
            chat_type=normalized_chat_type,
            platform=platform,
            stream_name=stream_name,
            trigger=trigger,
            restore_message_count_on_failure=current_count,
        )

    def _make_thought_payloads(
        self,
        state: UnfinishedThoughtState,
    ) -> list[dict[str, Any]]:
        active = [
            item
            for item in self._ordered_thoughts(state)
            if item.status in {"open", "paused"}
        ]
        if not active:
            return []

        min_items = int(self._cfg().prompt.inject_min_items)
        max_items = int(self._cfg().prompt.inject_max_items)
        upper_bound = min(len(active), max_items)
        lower_bound = min(min_items, upper_bound)
        if upper_bound <= 0:
            return []
        sample_size = random.randint(lower_bound, upper_bound)
        selected = random.sample(active, k=sample_size)

        return [
            {
                "thought_id": item.thought_id,
                "title": item.title,
                "content": item.content,
                "status": item.status,
            }
            for item in selected
        ]

    def render_prompt_block(
        self,
        stream_id: str,
        chat_type: str | None = None,
    ) -> str:
        cfg = self._cfg()
        state = self.get_state(stream_id, chat_type)
        if not state.thoughts:
            return ""

        payloads = self._make_thought_payloads(state)
        if not payloads:
            return ""

        return build_unfinished_thought_prompt_block(
            title=cfg.prompt.prompt_title,
            thoughts=payloads,
            max_items=cfg.prompt.inject_max_items,
        )

    def render_state_summary(
        self,
        stream_id: str,
        chat_type: str | None = None,
    ) -> str:
        state = self.get_state(stream_id, chat_type)
        if not state.has_content():
            return "暂无未完成念头"

        ordered = self._ordered_thoughts(state)
        open_count = len([item for item in ordered if item.status == "open"])
        paused_count = len([item for item in ordered if item.status == "paused"])
        resolved_count = len([item for item in ordered if item.status == "resolved"])
        expired_count = len([item for item in ordered if item.status == "expired"])

        lines = [
            f"流ID：{state.stream_id}",
            f"聊天类型：{state.chat_type}",
            f"消息计数：{state.message_count_since_scan}",
            f"状态统计：open={open_count} paused={paused_count} resolved={resolved_count} expired={expired_count}",
            "",
            "未完成念头：",
        ]

        thought_lines = self._build_thought_lines(state)
        if thought_lines:
            lines.extend(thought_lines)
        else:
            lines.append("- （空）")

        return "\n".join(lines).strip()

    def render_history(
        self,
        stream_id: str,
        chat_type: str | None = None,
        *,
        limit: int | None = None,
    ) -> str:
        state = self.get_state(stream_id, chat_type)
        if not state.history:
            return "暂无扫描历史"

        max_history = limit or self._cfg().storage.max_history_records
        lines: list[str] = []
        for item in state.history[-max_history:]:
            lines.extend(
                [
                    f"- {item.created_at} [{item.trigger}]",
                    f"  来源：{item.source_summary}",
                    f"  数量：new={item.new_count} update={item.updated_count} resolved={item.resolved_count} paused={item.paused_count}",
                ]
            )
        return "\n".join(lines)

    async def add_thought(
        self,
        *,
        stream_id: str,
        chat_type: str,
        content: str,
        title: str = "",
        priority: int = 1,
        reason: str = "manual",
        platform: str = "",
        stream_name: str = "",
    ) -> tuple[bool, str]:
        if not self._is_enabled():
            return False, "unfinished_thought_plugin 未启用"

        normalized_chat_type = self._normalize_chat_type(chat_type)
        text = str(content or "").strip()
        if not text:
            return False, "内容不能为空"

        async with self._get_lock(stream_id):
            state = self.get_state(
                stream_id,
                normalized_chat_type,
                platform=platform,
                stream_name=stream_name,
            )
            now = _now_iso()
            item = UnfinishedThoughtItem(
                thought_id=f"th_{uuid4().hex}",
                title=title.strip() or self._default_title(text),
                content=text,
                status="open",
                priority=_clamp_int(priority, 1, 1, 10),
                reason=reason,
                source_event="manual",
                created_at=now,
                updated_at=now,
                last_mentioned_at=now,
                mention_count=1,
            )
            state.thoughts.append(item)
            if platform:
                state.platform = platform
            if stream_name:
                state.stream_name = stream_name
            self._save_state(state)
            return True, f"已添加未完成念头：{item.title}"

    async def set_thought_status(
        self,
        *,
        stream_id: str,
        chat_type: str,
        selector: str,
        status: str,
        platform: str = "",
        stream_name: str = "",
    ) -> tuple[bool, str]:
        if not self._is_enabled():
            return False, "unfinished_thought_plugin 未启用"

        normalized_chat_type = self._normalize_chat_type(chat_type)
        normalized_status = status.strip().lower()
        if normalized_status not in {"open", "paused", "resolved", "expired"}:
            return False, "无效状态"

        async with self._get_lock(stream_id):
            state = self.get_state(
                stream_id,
                normalized_chat_type,
                platform=platform,
                stream_name=stream_name,
            )
            thought = self._find_thought(state, selector)
            if thought is None:
                return False, "未找到对应念头"

            thought.status = normalized_status
            thought.updated_at = _now_iso()
            thought.last_mentioned_at = thought.updated_at
            if platform:
                state.platform = platform
            if stream_name:
                state.stream_name = stream_name
            self._save_state(state)
            return True, f"已更新念头状态：{thought.title} -> {normalized_status}"

    async def clear_thoughts(
        self,
        *,
        stream_id: str,
        chat_type: str,
        platform: str = "",
        stream_name: str = "",
    ) -> tuple[bool, str]:
        if not self._is_enabled():
            return False, "unfinished_thought_plugin 未启用"

        normalized_chat_type = self._normalize_chat_type(chat_type)
        async with self._get_lock(stream_id):
            state = self.get_state(
                stream_id,
                normalized_chat_type,
                platform=platform,
                stream_name=stream_name,
            )
            state.thoughts = []
            state.message_count_since_scan = 0
            if platform:
                state.platform = platform
            if stream_name:
                state.stream_name = stream_name
            self._save_state(state)
            return True, "已清空未完成念头"
