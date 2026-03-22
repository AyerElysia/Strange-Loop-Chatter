"""self_narrative_plugin 服务。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.app.plugin_system.base import BaseService
from src.core.config import get_model_config
from src.kernel.llm import LLMContextManager, LLMRequest
from src.kernel.llm.payload import LLMPayload, Text
from src.kernel.llm.roles import ROLE
from src.kernel.logger import get_logger
from src.kernel.scheduler import TriggerType, get_unified_scheduler

from .config import SelfNarrativeConfig
from .prompts import (
    build_self_narrative_prompt_block,
    build_self_narrative_update_system_prompt,
    build_self_narrative_update_user_prompt,
)


logger = get_logger("self_narrative_plugin")

_NARRATIVE_VERSION = 1
_STREAM_LOCKS: dict[str, asyncio.Lock] = {}
_SERVICE_INSTANCE: "SelfNarrativeService | None" = None


def _now() -> datetime:
    return datetime.now().astimezone()


def _now_iso() -> str:
    return _now().isoformat(timespec="seconds")


@dataclass
class SelfNarrativeRevision:
    """一次自我叙事更新记录。"""

    revision_id: str
    created_at: str
    trigger: str
    reference_date: str
    source_summary: str
    self_view: list[str]
    ongoing_patterns: list[str]
    open_loops: list[str]
    identity_bounds: list[str]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SelfNarrativeRevision":
        return cls(
            revision_id=str(data.get("revision_id", "")),
            created_at=str(data.get("created_at", "")),
            trigger=str(data.get("trigger", "manual")),
            reference_date=str(data.get("reference_date", "")),
            source_summary=str(data.get("source_summary", "")),
            self_view=[str(item) for item in data.get("self_view", [])],
            ongoing_patterns=[str(item) for item in data.get("ongoing_patterns", [])],
            open_loops=[str(item) for item in data.get("open_loops", [])],
            identity_bounds=[str(item) for item in data.get("identity_bounds", [])],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "revision_id": self.revision_id,
            "created_at": self.created_at,
            "trigger": self.trigger,
            "reference_date": self.reference_date,
            "source_summary": self.source_summary,
            "self_view": list(self.self_view),
            "ongoing_patterns": list(self.ongoing_patterns),
            "open_loops": list(self.open_loops),
            "identity_bounds": list(self.identity_bounds),
        }


@dataclass
class SelfNarrativeState:
    """单个聊天流的自我叙事状态。"""

    stream_id: str
    chat_type: str
    platform: str = ""
    stream_name: str = ""
    updated_at: str = ""
    last_daily_ref_date: str | None = None
    last_manual_update_at: str | None = None
    self_view: list[str] = field(default_factory=list)
    ongoing_patterns: list[str] = field(default_factory=list)
    open_loops: list[str] = field(default_factory=list)
    identity_bounds: list[str] = field(default_factory=list)
    history: list[SelfNarrativeRevision] = field(default_factory=list)

    @classmethod
    def empty(
        cls,
        *,
        stream_id: str,
        chat_type: str,
        platform: str = "",
        stream_name: str = "",
        default_identity_bounds: list[str] | None = None,
        default_self_view: list[str] | None = None,
        default_ongoing_patterns: list[str] | None = None,
        default_open_loops: list[str] | None = None,
    ) -> "SelfNarrativeState":
        return cls(
            stream_id=stream_id,
            chat_type=chat_type,
            platform=platform,
            stream_name=stream_name,
            updated_at=_now_iso(),
            self_view=list(default_self_view or []),
            ongoing_patterns=list(default_ongoing_patterns or []),
            open_loops=list(default_open_loops or []),
            identity_bounds=list(default_identity_bounds or []),
            history=[],
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SelfNarrativeState":
        history_raw = data.get("history", [])
        history = [
            SelfNarrativeRevision.from_dict(item)
            for item in history_raw
            if isinstance(item, dict)
        ]
        return cls(
            stream_id=str(data.get("stream_id", "")),
            chat_type=str(data.get("chat_type", "private")),
            platform=str(data.get("platform", "")),
            stream_name=str(data.get("stream_name", "")),
            updated_at=str(data.get("updated_at", "")),
            last_daily_ref_date=data.get("last_daily_ref_date"),
            last_manual_update_at=data.get("last_manual_update_at"),
            self_view=[str(item) for item in data.get("self_view", [])],
            ongoing_patterns=[str(item) for item in data.get("ongoing_patterns", [])],
            open_loops=[str(item) for item in data.get("open_loops", [])],
            identity_bounds=[str(item) for item in data.get("identity_bounds", [])],
            history=history,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": _NARRATIVE_VERSION,
            "stream_id": self.stream_id,
            "chat_type": self.chat_type,
            "platform": self.platform,
            "stream_name": self.stream_name,
            "updated_at": self.updated_at,
            "last_daily_ref_date": self.last_daily_ref_date,
            "last_manual_update_at": self.last_manual_update_at,
            "self_view": list(self.self_view),
            "ongoing_patterns": list(self.ongoing_patterns),
            "open_loops": list(self.open_loops),
            "identity_bounds": list(self.identity_bounds),
            "history": [item.to_dict() for item in self.history],
        }

    def has_content(self) -> bool:
        return any(
            [
                self.self_view,
                self.ongoing_patterns,
                self.open_loops,
                self.identity_bounds,
                self.history,
            ]
        )

    def current_snapshot(self) -> dict[str, Any]:
        return {
            "self_view": list(self.self_view),
            "ongoing_patterns": list(self.ongoing_patterns),
            "open_loops": list(self.open_loops),
            "identity_bounds": list(self.identity_bounds),
        }


def get_self_narrative_service() -> "SelfNarrativeService | None":
    """获取 self_narrative 服务实例。"""
    return _SERVICE_INSTANCE


class SelfNarrativeService(BaseService):
    """自我叙事服务。"""

    service_name = "self_narrative_service"
    service_description = "按聊天流隔离的自我叙事缓存与更新服务"
    version = "1.0.0"

    def __init__(self, plugin: Any) -> None:
        super().__init__(plugin)
        self._schedule_task_id: str | None = None
        self._initialized = False

    def _cfg(self) -> SelfNarrativeConfig:
        cfg = self.plugin.config
        if not isinstance(cfg, SelfNarrativeConfig):
            raise RuntimeError("self_narrative_plugin config 未正确加载")
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

    def _get_subdir(self, chat_type: str) -> str:
        return self._normalize_chat_type(chat_type)

    def _get_state_path(self, stream_id: str, chat_type: str) -> Path:
        path = self._get_base_path() / self._get_subdir(chat_type) / f"{stream_id}.json"
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
    ) -> SelfNarrativeState:
        if not path.exists():
            return SelfNarrativeState.empty(
                stream_id=stream_id,
                chat_type=chat_type,
                platform=platform,
                stream_name=stream_name,
                default_identity_bounds=self._cfg().narrative.default_identity_bounds,
                default_self_view=self._cfg().narrative.default_self_view,
                default_ongoing_patterns=self._cfg().narrative.default_ongoing_patterns,
                default_open_loops=self._cfg().narrative.default_open_loops,
            )

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.error(f"读取自我叙事失败：{path} - {exc}")
            return SelfNarrativeState.empty(
                stream_id=stream_id,
                chat_type=chat_type,
                platform=platform,
                stream_name=stream_name,
                default_identity_bounds=self._cfg().narrative.default_identity_bounds,
                default_self_view=self._cfg().narrative.default_self_view,
                default_ongoing_patterns=self._cfg().narrative.default_ongoing_patterns,
                default_open_loops=self._cfg().narrative.default_open_loops,
            )

        state = SelfNarrativeState.from_dict(data)
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
    ) -> SelfNarrativeState:
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

    def _save_state(self, state: SelfNarrativeState) -> None:
        state.updated_at = _now_iso()
        path = self._get_state_path(state.stream_id, state.chat_type)
        path.write_text(
            json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _get_lock(self, stream_id: str) -> asyncio.Lock:
        lock = _STREAM_LOCKS.get(stream_id)
        if lock is None:
            lock = asyncio.Lock()
            _STREAM_LOCKS[stream_id] = lock
        return lock

    def _reference_date(self, trigger: str, now: datetime) -> str:
        if trigger == "daily":
            return (now - timedelta(days=1)).date().isoformat()
        return now.date().isoformat()

    def _get_diary_service(self) -> Any | None:
        from src.app.plugin_system.api.service_api import get_service

        service = get_service("diary_plugin:service:diary_service")
        if service is None:
            return None
        return service

    def _get_sleep_snapshot(self) -> str:
        try:
            from src.core.managers import get_plugin_manager

            plugin = get_plugin_manager().get_plugin("sleep_wakeup_plugin")
            if plugin and hasattr(plugin, "get_runtime_snapshot"):
                snapshot = plugin.get_runtime_snapshot()
                if isinstance(snapshot, dict) and snapshot:
                    return json.dumps(snapshot, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.debug(f"获取睡眠状态快照失败：{exc}")
        return ""

    def _get_source_material(
        self,
        *,
        stream_id: str,
        chat_type: str,
        platform: str,
        stream_name: str,
        trigger: str,
        reference_date: str,
    ) -> dict[str, str]:
        diary_service = self._get_diary_service()
        diary_summary = ""
        continuous_memory = ""
        if diary_service is not None:
            try:
                diary_day = diary_service.read_date(reference_date)
                diary_lines = [event.content for event in diary_day.events]
                if diary_lines:
                    diary_summary = "\n".join(f"- {line}" for line in diary_lines[-10:])
                elif diary_day.raw_text:
                    diary_summary = diary_day.raw_text[-2000:]
            except Exception as exc:
                logger.debug(f"读取日记素材失败：{exc}")

            try:
                continuous_memory = diary_service.render_continuous_memory_for_prompt(
                    stream_id=stream_id,
                    chat_type=chat_type,
                )
            except Exception as exc:
                logger.debug(f"读取连续记忆失败：{exc}")

        state = self.get_state(stream_id, chat_type, platform=platform, stream_name=stream_name)
        current_state = json.dumps(state.current_snapshot(), ensure_ascii=False, indent=2)
        sleep_snapshot = self._get_sleep_snapshot()

        return {
            "日记摘要": diary_summary,
            "连续记忆": continuous_memory,
            "当前状态": current_state,
            "睡眠状态": sleep_snapshot,
            "聊天元数据": json.dumps(
                {
                    "trigger": trigger,
                    "reference_date": reference_date,
                    "stream_id": stream_id,
                    "chat_type": chat_type,
                    "platform": platform,
                    "stream_name": stream_name,
                },
                ensure_ascii=False,
                indent=2,
            ),
        }

    async def _call_llm_for_update(
        self,
        *,
        trigger: str,
        reference_date: str,
        current_state: SelfNarrativeState,
        sources: dict[str, str],
    ) -> dict[str, list[str]] | None:
        cfg = self._cfg()
        task_name = cfg.model.task_name.strip() or "diary"
        fallback_task_name = cfg.model.fallback_task_name.strip() or "actor"

        try:
            model_set = get_model_config().get_task(task_name)
        except Exception:
            try:
                model_set = get_model_config().get_task(fallback_task_name)
            except Exception as exc:
                logger.warning(f"未找到自我叙事模型：{task_name}/{fallback_task_name} - {exc}")
                return None

        context_manager = LLMContextManager(max_payloads=4)
        request = LLMRequest(
            model_set,
            request_name="self_narrative_update",
            context_manager=context_manager,
        )

        request.add_payload(
            LLMPayload(ROLE.SYSTEM, Text(build_self_narrative_update_system_prompt()))
        )
        request.add_payload(
            LLMPayload(
                ROLE.USER,
                Text(
                    build_self_narrative_update_user_prompt(
                        trigger=trigger,
                        reference_date=reference_date,
                        current_state=current_state.current_snapshot(),
                        sources=sources,
                    )
                ),
            )
        )

        try:
            response = await request.send()
            text = await response if not response.message else response.message
        except Exception as exc:
            logger.error(f"自我叙事更新失败：{exc}")
            return None

        if not text:
            return None

        return self._parse_update_json(str(text))

    def _parse_update_json(self, raw: str) -> dict[str, list[str]] | None:
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

        if obj is None:
            return None

        def _extract_list(key: str) -> list[str]:
            value = obj.get(key, [])
            if not isinstance(value, list):
                return []
            items = []
            for item in value:
                text = str(item).strip()
                if text:
                    items.append(text)
            return items

        return {
            "self_view": _extract_list("self_view"),
            "ongoing_patterns": _extract_list("ongoing_patterns"),
            "open_loops": _extract_list("open_loops"),
            "identity_bounds": _extract_list("identity_bounds"),
        }

    def _merge_updates(
        self,
        state: SelfNarrativeState,
        updates: dict[str, list[str]],
    ) -> SelfNarrativeState:
        cfg = self._cfg()
        limit = max(1, int(cfg.storage.max_prompt_items_per_section))

        def _merge_list(current: list[str], new_items: list[str]) -> list[str]:
            seen: set[str] = set()
            merged: list[str] = []
            for item in new_items + current:
                text = item.strip()
                if not text or text in seen:
                    continue
                seen.add(text)
                merged.append(text)
                if len(merged) >= limit:
                    break
            return merged

        state.self_view = _merge_list(state.self_view, updates.get("self_view", []))
        state.ongoing_patterns = _merge_list(
            state.ongoing_patterns, updates.get("ongoing_patterns", [])
        )
        state.open_loops = _merge_list(state.open_loops, updates.get("open_loops", []))

        identity_bounds_updates = updates.get("identity_bounds", [])
        if identity_bounds_updates:
            state.identity_bounds = _merge_list(
                state.identity_bounds, identity_bounds_updates
            )

        return state

    def _build_revision(
        self,
        *,
        trigger: str,
        reference_date: str,
        source_summary: str,
        state: SelfNarrativeState,
    ) -> SelfNarrativeRevision:
        return SelfNarrativeRevision(
            revision_id=f"rev_{uuid4().hex}",
            created_at=_now_iso(),
            trigger=trigger,
            reference_date=reference_date,
            source_summary=source_summary,
            self_view=list(state.self_view),
            ongoing_patterns=list(state.ongoing_patterns),
            open_loops=list(state.open_loops),
            identity_bounds=list(state.identity_bounds),
        )

    async def update_narrative(
        self,
        *,
        stream_id: str,
        chat_type: str,
        platform: str = "",
        stream_name: str = "",
        trigger: str = "manual",
        force: bool = False,
    ) -> tuple[bool, str]:
        if not self._is_enabled():
            return False, "self_narrative_plugin 未启用"

        normalized_chat_type = self._normalize_chat_type(chat_type)
        now = _now()
        reference_date = self._reference_date(trigger, now)

        async with self._get_lock(stream_id):
            state = self.get_state(
                stream_id,
                normalized_chat_type,
                platform=platform,
                stream_name=stream_name,
            )

            if trigger == "manual" and not force:
                cooldown = int(self._cfg().schedule.manual_cooldown_seconds)
                if state.last_manual_update_at:
                    try:
                        last_manual = datetime.fromisoformat(state.last_manual_update_at)
                        if now - last_manual < timedelta(seconds=cooldown):
                            remaining = cooldown - int((now - last_manual).total_seconds())
                            return False, f"手动更新冷却中，请 {max(0, remaining)} 秒后再试"
                    except ValueError:
                        pass

            if trigger == "daily" and state.last_daily_ref_date == reference_date:
                return True, "今日自我叙事已经更新过"

            sources = self._get_source_material(
                stream_id=stream_id,
                chat_type=normalized_chat_type,
                platform=platform,
                stream_name=stream_name,
                trigger=trigger,
                reference_date=reference_date,
            )

            updates = await self._call_llm_for_update(
                trigger=trigger,
                reference_date=reference_date,
                current_state=state,
                sources=sources,
            )
            if updates is None:
                return False, "自我叙事更新失败"

            before_snapshot = state.current_snapshot()
            state = self._merge_updates(state, updates)
            state.stream_id = stream_id
            state.chat_type = normalized_chat_type
            if platform:
                state.platform = platform
            if stream_name:
                state.stream_name = stream_name

            if trigger == "daily":
                state.last_daily_ref_date = reference_date
            else:
                state.last_manual_update_at = _now_iso()

            source_summary = "；".join(
                [
                    f"{key}={len(value.splitlines()) if value.strip() else 0}"
                    for key, value in sources.items()
                    if value.strip()
                ]
            )
            revision = self._build_revision(
                trigger=trigger,
                reference_date=reference_date,
                source_summary=source_summary or "无可见输入",
                state=state,
            )

            changed = before_snapshot != state.current_snapshot()
            state.history.append(revision)
            max_history = max(1, int(self._cfg().storage.max_history_records))
            if len(state.history) > max_history:
                state.history = state.history[-max_history:]

            self._save_state(state)

            logger.info(
                f"[{stream_id[:8]}] 自我叙事已更新 "
                f"(trigger={trigger}, changed={changed}, ref={reference_date})"
            )
            return True, "自我叙事已更新"

    async def refresh_all_daily_narratives(self, *, force: bool = False) -> tuple[int, int]:
        if not self._is_enabled():
            return 0, 0

        updated = 0
        skipped = 0
        for stream_id, chat_type, platform, stream_name in self._list_known_streams():
            ok, _ = await self.update_narrative(
                stream_id=stream_id,
                chat_type=chat_type,
                platform=platform,
                stream_name=stream_name,
                trigger="daily",
                force=force,
            )
            if ok:
                updated += 1
            else:
                skipped += 1
        return updated, skipped

    def _list_known_streams(self) -> list[tuple[str, str, str, str]]:
        results: list[tuple[str, str, str, str]] = []
        base_path = self._get_base_path()
        if not base_path.exists():
            return results

        for chat_type in ("private", "group", "discuss"):
            subdir = base_path / chat_type
            if not subdir.exists():
                continue
            for file in subdir.glob("*.json"):
                try:
                    data = json.loads(file.read_text(encoding="utf-8"))
                except Exception:
                    continue
                stream_id = str(data.get("stream_id", file.stem))
                platform = str(data.get("platform", ""))
                stream_name = str(data.get("stream_name", ""))
                results.append((stream_id, chat_type, platform, stream_name))
        return results

    def render_prompt_block(
        self,
        stream_id: str,
        chat_type: str | None = None,
    ) -> str:
        cfg = self._cfg()
        state = self.get_state(stream_id, chat_type)
        if not state.has_content():
            return ""

        history_lines = [
            f"[{item.created_at}] {item.trigger} · {item.source_summary}"
            for item in state.history
        ]
        return build_self_narrative_prompt_block(
            title=cfg.prompt.prompt_title,
            current_state=state.current_snapshot(),
            history_lines=history_lines,
            include_identity_bounds=cfg.plugin.include_identity_bounds_in_prompt,
            max_items_per_section=cfg.storage.max_prompt_items_per_section,
            include_history=cfg.plugin.include_history_in_prompt,
            history_limit=cfg.prompt.max_history_lines,
        )

    def render_state_summary(
        self,
        stream_id: str,
        chat_type: str | None = None,
    ) -> str:
        state = self.get_state(stream_id, chat_type)
        if not state.has_content():
            return "暂无自我叙事"

        block = [f"流ID：{state.stream_id}", f"聊天类型：{state.chat_type}", ""]

        def _append_section(title: str, items: list[str]) -> None:
            block.append(title)
            if items:
                block.extend(f"- {item}" for item in items)
            else:
                block.append("- （空）")
            block.append("")

        _append_section("自我理解：", state.self_view)
        _append_section("反复模式：", state.ongoing_patterns)
        _append_section("未完成问题：", state.open_loops)
        _append_section("稳定边界：", state.identity_bounds)
        return "\n".join(block).strip()

    def render_history(
        self,
        stream_id: str,
        chat_type: str | None = None,
        *,
        limit: int | None = None,
    ) -> str:
        state = self.get_state(stream_id, chat_type)
        if not state.history:
            return "暂无更新历史"

        max_history = limit or self._cfg().prompt.max_history_lines
        lines = []
        for item in state.history[-max_history:]:
            lines.extend(
                [
                    f"- {item.created_at} [{item.trigger}] ref={item.reference_date}",
                    f"  来源：{item.source_summary}",
                    f"  自我理解：{'；'.join(item.self_view) or '（空）'}",
                    f"  反复模式：{'；'.join(item.ongoing_patterns) or '（空）'}",
                    f"  未完成问题：{'；'.join(item.open_loops) or '（空）'}",
                    f"  稳定边界：{'；'.join(item.identity_bounds) or '（空）'}",
                ]
            )
        return "\n".join(lines)

    async def reset_narrative(
        self,
        *,
        stream_id: str,
        chat_type: str,
        platform: str = "",
        stream_name: str = "",
    ) -> tuple[bool, str]:
        if not self._is_enabled():
            return False, "self_narrative_plugin 未启用"

        normalized_chat_type = self._normalize_chat_type(chat_type)
        async with self._get_lock(stream_id):
            state = SelfNarrativeState.empty(
                stream_id=stream_id,
                chat_type=normalized_chat_type,
                platform=platform,
                stream_name=stream_name,
                default_identity_bounds=self._cfg().narrative.default_identity_bounds,
                default_self_view=self._cfg().narrative.default_self_view,
                default_ongoing_patterns=self._cfg().narrative.default_ongoing_patterns,
                default_open_loops=self._cfg().narrative.default_open_loops,
            )
            self._save_state(state)
        return True, "自我叙事已重置"

    async def initialize(self) -> None:
        if self._initialized:
            return
        if not self._is_enabled():
            logger.info("self_narrative_plugin 已在配置中禁用")
            return

        schedule_enabled = bool(self._cfg().schedule.enabled)
        if schedule_enabled:
            await self._start_scheduler()
            if self._cfg().schedule.catch_up_on_startup:
                await self._catch_up_on_startup()
        else:
            logger.info("self_narrative_plugin 已关闭自动调度，仅保留手动更新")

        self._initialized = True
        logger.info("self_narrative_plugin 初始化完成")

    async def shutdown(self) -> None:
        global _SERVICE_INSTANCE
        await self._stop_scheduler()
        self._initialized = False
        _SERVICE_INSTANCE = None
        logger.info("self_narrative_plugin 已关闭")

    async def _catch_up_on_startup(self) -> None:
        try:
            updated, skipped = await self.refresh_all_daily_narratives(force=True)
            if updated or skipped:
                logger.info(
                    f"self_narrative_plugin 启动补跑完成: updated={updated}, skipped={skipped}"
                )
        except Exception as exc:
            logger.warning(f"self_narrative_plugin 启动补跑失败: {exc}")

    async def _start_scheduler(self) -> None:
        if self._schedule_task_id is not None:
            return

        scheduler = get_unified_scheduler()
        delay_seconds = self._seconds_until_update_time()
        self._schedule_task_id = await scheduler.create_schedule(
            callback=self._scheduled_update,
            trigger_type=TriggerType.TIME,
            trigger_config={
                "delay_seconds": delay_seconds,
                "interval_seconds": 86400,
            },
            is_recurring=True,
            task_name="self_narrative_plugin:daily_update",
            timeout=120.0,
            max_retries=3,
            force_overwrite=True,
        )
        logger.info(
            f"self_narrative_plugin 定时任务已启动: task_id={self._schedule_task_id}, delay={delay_seconds}s"
        )

    async def _stop_scheduler(self) -> None:
        if self._schedule_task_id is None:
            return

        scheduler = get_unified_scheduler()
        task_id = self._schedule_task_id
        self._schedule_task_id = None
        try:
            await scheduler.remove_schedule(task_id)
        except Exception as exc:
            logger.warning(f"移除 self_narrative 定时任务失败: {exc}")

    async def _scheduled_update(self) -> None:
        await self.refresh_all_daily_narratives()

    def _seconds_until_update_time(self) -> int:
        try:
            hour, minute = [int(part) for part in self._cfg().schedule.update_time.split(":", 1)]
        except Exception:
            hour, minute = 0, 0

        now = _now()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        delta = target - now
        return max(1, int(delta.total_seconds()))


def initialize_self_narrative_service(plugin: Any) -> SelfNarrativeService:
    """初始化 self_narrative 服务单例。"""

    global _SERVICE_INSTANCE
    if _SERVICE_INSTANCE is None:
        _SERVICE_INSTANCE = SelfNarrativeService(plugin)
        logger.info("self_narrative_service 已初始化")
    else:
        _SERVICE_INSTANCE.plugin = plugin
    return _SERVICE_INSTANCE
