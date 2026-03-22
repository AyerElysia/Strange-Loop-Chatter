"""drive_core_plugin 服务。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.app.plugin_system.base import BaseService
from src.core.config import get_model_config
from src.kernel.llm import LLMContextManager, LLMRequest
from src.kernel.llm.payload import LLMPayload, Text
from src.kernel.llm.roles import ROLE
from src.kernel.logger import get_logger

from .config import DriveCoreConfig
from .prompts import build_drive_core_system_prompt, build_drive_core_user_prompt


logger = get_logger("drive_core_plugin")

_STATE_VERSION = 1
_STREAM_LOCKS: dict[str, asyncio.Lock] = {}
_SERVICE_INSTANCE: "DriveCoreService | None" = None


def _now() -> datetime:
    return datetime.now().astimezone()


def _now_iso() -> str:
    return _now().isoformat(timespec="seconds")


def _clamp(value: Any, default: int = 50) -> int:
    try:
        number = int(value)
    except Exception:
        return default
    return max(0, min(100, number))


def _clean_lines(items: list[str]) -> list[str]:
    cleaned: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if text:
            cleaned.append(text)
    return cleaned


@dataclass
class DriveAxes:
    """连续内驱力轴。"""

    curiosity: int = 62
    initiative: int = 50
    affinity: int = 45
    withdrawal: int = 28
    fatigue: int = 22
    urgency: int = 35
    stability: int = 58

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DriveAxes":
        return cls(
            curiosity=_clamp(data.get("curiosity", 62)),
            initiative=_clamp(data.get("initiative", 50)),
            affinity=_clamp(data.get("affinity", 45)),
            withdrawal=_clamp(data.get("withdrawal", 28)),
            fatigue=_clamp(data.get("fatigue", 22)),
            urgency=_clamp(data.get("urgency", 35)),
            stability=_clamp(data.get("stability", 58)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "curiosity": self.curiosity,
            "initiative": self.initiative,
            "affinity": self.affinity,
            "withdrawal": self.withdrawal,
            "fatigue": self.fatigue,
            "urgency": self.urgency,
            "stability": self.stability,
        }

    def dominant_axis(self) -> str:
        scored = {
            "curiosity": self.curiosity,
            "initiative": self.initiative,
            "affinity": self.affinity,
            "withdrawal": self.withdrawal,
            "fatigue": self.fatigue,
            "urgency": self.urgency,
            "stability": self.stability,
        }
        return max(scored, key=scored.get)

    def dominant_label(self) -> str:
        mapping = {
            "curiosity": "探索",
            "initiative": "行动",
            "affinity": "靠近",
            "withdrawal": "收缩",
            "fatigue": "休息",
            "urgency": "紧迫",
            "stability": "稳定",
        }
        return mapping.get(self.dominant_axis(), "探索")


@dataclass
class DriveTaskRecord:
    """一次自我课题的历史记录。"""

    task_id: str
    created_at: str
    closed_at: str
    trigger: str
    topic: str
    question: str
    hypothesis: str
    summary: str
    conclusion: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DriveTaskRecord":
        return cls(
            task_id=str(data.get("task_id", "")),
            created_at=str(data.get("created_at", "")),
            closed_at=str(data.get("closed_at", "")),
            trigger=str(data.get("trigger", "")),
            topic=str(data.get("topic", "")),
            question=str(data.get("question", "")),
            hypothesis=str(data.get("hypothesis", "")),
            summary=str(data.get("summary", "")),
            conclusion=str(data.get("conclusion", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "created_at": self.created_at,
            "closed_at": self.closed_at,
            "trigger": self.trigger,
            "topic": self.topic,
            "question": self.question,
            "hypothesis": self.hypothesis,
            "summary": self.summary,
            "conclusion": self.conclusion,
        }


@dataclass
class DriveWorkspace:
    """当前自我引擎工作区。"""

    task_id: str
    topic: str = ""
    question: str = ""
    hypothesis: str = ""
    next_action: str = ""
    summary: str = ""
    conclusion: str = ""
    should_close: bool = False
    status: str = "open"
    trigger: str = "auto"
    step_index: int = 0
    max_steps: int = 4
    created_at: str = ""
    updated_at: str = ""
    evidence: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    tool_trace: list[str] = field(default_factory=list)
    working_notes: list[str] = field(default_factory=list)
    source_summary: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DriveWorkspace":
        return cls(
            task_id=str(data.get("task_id", "")),
            topic=str(data.get("topic", "")),
            question=str(data.get("question", "")),
            hypothesis=str(data.get("hypothesis", "")),
            next_action=str(data.get("next_action", "")),
            summary=str(data.get("summary", "")),
            conclusion=str(data.get("conclusion", "")),
            should_close=bool(data.get("should_close", False)),
            status=str(data.get("status", "open")),
            trigger=str(data.get("trigger", "auto")),
            step_index=int(data.get("step_index", 0) or 0),
            max_steps=max(1, int(data.get("max_steps", 4) or 4)),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
            evidence=_clean_lines([str(item) for item in data.get("evidence", [])]),
            open_questions=_clean_lines(
                [str(item) for item in data.get("open_questions", [])]
            ),
            tool_trace=_clean_lines([str(item) for item in data.get("tool_trace", [])]),
            working_notes=_clean_lines(
                [str(item) for item in data.get("working_notes", [])]
            ),
            source_summary=str(data.get("source_summary", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "topic": self.topic,
            "question": self.question,
            "hypothesis": self.hypothesis,
            "next_action": self.next_action,
            "summary": self.summary,
            "conclusion": self.conclusion,
            "should_close": self.should_close,
            "status": self.status,
            "trigger": self.trigger,
            "step_index": self.step_index,
            "max_steps": self.max_steps,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "evidence": list(self.evidence),
            "open_questions": list(self.open_questions),
            "tool_trace": list(self.tool_trace),
            "working_notes": list(self.working_notes),
            "source_summary": self.source_summary,
        }

    def brief(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "topic": self.topic,
            "question": self.question,
            "hypothesis": self.hypothesis,
            "next_action": self.next_action,
            "status": self.status,
            "step_index": self.step_index,
            "max_steps": self.max_steps,
            "open_questions": list(self.open_questions),
        }


@dataclass
class DriveState:
    """单个聊天流的内驱力状态。"""

    stream_id: str
    chat_type: str
    platform: str = ""
    stream_name: str = ""
    updated_at: str = ""
    message_count_since_scan: int = 0
    axes: DriveAxes = field(default_factory=DriveAxes)
    current_workspace: DriveWorkspace | None = None
    history: list[DriveTaskRecord] = field(default_factory=list)

    @classmethod
    def empty(
        cls,
        *,
        stream_id: str,
        chat_type: str,
        platform: str = "",
        stream_name: str = "",
        axes: DriveAxes | None = None,
    ) -> "DriveState":
        return cls(
            stream_id=stream_id,
            chat_type=chat_type,
            platform=platform,
            stream_name=stream_name,
            updated_at=_now_iso(),
            message_count_since_scan=0,
            axes=axes or DriveAxes(),
            current_workspace=None,
            history=[],
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DriveState":
        history = [
            DriveTaskRecord.from_dict(item)
            for item in data.get("history", [])
            if isinstance(item, dict)
        ]
        workspace_data = data.get("current_workspace")
        workspace = (
            DriveWorkspace.from_dict(workspace_data)
            if isinstance(workspace_data, dict)
            else None
        )
        return cls(
            stream_id=str(data.get("stream_id", "")),
            chat_type=str(data.get("chat_type", "private")),
            platform=str(data.get("platform", "")),
            stream_name=str(data.get("stream_name", "")),
            updated_at=str(data.get("updated_at", "")),
            message_count_since_scan=int(data.get("message_count_since_scan", 0) or 0),
            axes=DriveAxes.from_dict(data.get("axes", {})),
            current_workspace=workspace,
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
            "axes": self.axes.to_dict(),
            "current_workspace": self.current_workspace.to_dict()
            if self.current_workspace
            else None,
            "history": [item.to_dict() for item in self.history],
        }

    def current_snapshot(self) -> dict[str, Any]:
        return {
            "axes": self.axes.to_dict(),
            "message_count_since_scan": self.message_count_since_scan,
            "current_workspace": self.current_workspace.brief()
            if self.current_workspace
            else None,
        }


def get_drive_core_service() -> "DriveCoreService | None":
    """获取 drive_core 服务实例。"""
    return _SERVICE_INSTANCE


class DriveCoreService(BaseService):
    """内驱力/自我引擎服务。"""

    service_name = "drive_core_service"
    service_description = "按聊天流隔离的内驱力工作区与自我发问引擎"
    version = "1.0.0"

    def __init__(self, plugin: Any) -> None:
        super().__init__(plugin)
        self._initialized = False

    def _cfg(self) -> DriveCoreConfig:
        cfg = getattr(self.plugin, "config", None)
        if not isinstance(cfg, DriveCoreConfig):
            raise RuntimeError("drive_core_plugin config 未正确加载")
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
    ) -> DriveState:
        if not path.exists():
            return DriveState.empty(
                stream_id=stream_id,
                chat_type=chat_type,
                platform=platform,
                stream_name=stream_name,
                axes=self._default_axes(),
            )

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.error(f"读取 drive_core 状态失败：{path} - {exc}")
            return DriveState.empty(
                stream_id=stream_id,
                chat_type=chat_type,
                platform=platform,
                stream_name=stream_name,
                axes=self._default_axes(),
            )

        state = DriveState.from_dict(data)
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
    ) -> DriveState:
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

    def _save_state(self, state: DriveState) -> None:
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

    def _default_axes(self) -> DriveAxes:
        cfg = self._cfg()
        return DriveAxes(
            curiosity=_clamp(cfg.drive.curiosity),
            initiative=_clamp(cfg.drive.initiative),
            affinity=_clamp(cfg.drive.affinity),
            withdrawal=_clamp(cfg.drive.withdrawal),
            fatigue=_clamp(cfg.drive.fatigue),
            urgency=_clamp(cfg.drive.urgency),
            stability=_clamp(cfg.drive.stability),
        )

    def _get_stream(self, stream_id: str) -> Any | None:
        try:
            from src.core.managers import get_stream_manager

            return get_stream_manager()._streams.get(stream_id)
        except Exception:
            return None

    def _get_stream_meta(
        self,
        stream_id: str,
        chat_type: str | None = None,
        *,
        platform: str = "",
        stream_name: str = "",
    ) -> tuple[str, str, str]:
        stream = self._get_stream(stream_id)
        if stream:
            chat_type = str(getattr(stream, "chat_type", chat_type or "private"))
            platform = str(getattr(stream, "platform", platform or ""))
            stream_name = str(getattr(stream, "stream_name", stream_name or ""))
        return (
            self._normalize_chat_type(chat_type),
            platform or "",
            stream_name or "",
        )

    def _get_diary_service(self) -> Any | None:
        from src.app.plugin_system.api.service_api import get_service

        return get_service("diary_plugin:service:diary_service")

    def _get_self_narrative_service(self) -> Any | None:
        from src.app.plugin_system.api.service_api import get_service

        return get_service("self_narrative_plugin:service:self_narrative_service")

    def _get_unfinished_thought_service(self) -> Any | None:
        from src.app.plugin_system.api.service_api import get_service

        return get_service("unfinished_thought_plugin:service:unfinished_thought_service")

    async def _get_shared_persona_prompt(
        self,
        *,
        chat_stream: Any,
        chat_type: str,
        platform: str,
        stream_name: str,
    ) -> str:
        cfg = self._cfg()
        if not cfg.plugin.inherit_default_chatter_persona_prompt:
            return ""

        try:
            from plugins.diary_plugin.prompts import build_shared_persona_prompt

            return await build_shared_persona_prompt(
                chat_stream=chat_stream,
                chat_type=chat_type,
                platform=platform,
                stream_name=stream_name,
            )
        except Exception as exc:
            logger.debug(f"获取共享人设提示词失败：{exc}")
            return ""

    def _collect_recent_messages(self, stream_id: str, window_size: int) -> str:
        stream = self._get_stream(stream_id)
        if not stream:
            return ""

        context = getattr(stream, "context", None)
        if context is None:
            return ""

        history = list(getattr(context, "history_messages", []) or [])
        unread = list(getattr(context, "unread_messages", []) or [])
        all_messages = history + unread
        recent = all_messages[-window_size:] if len(all_messages) > window_size else all_messages
        lines: list[str] = []
        for msg in recent:
            sender = str(getattr(msg, "sender_name", "") or getattr(msg, "sender_id", "") or "未知")
            content = str(
                getattr(msg, "processed_plain_text", "")
                or getattr(msg, "content", "")
                or ""
            ).strip()
            if content:
                lines.append(f"{sender}: {content}")
        return "\n".join(lines)

    def _collect_sources(
        self,
        *,
        stream_id: str,
        chat_type: str,
        platform: str,
        stream_name: str,
        trigger: str,
        reference_window: int,
    ) -> dict[str, str]:
        diary_summary = ""
        continuous_memory = ""
        self_narrative = ""
        unfinished_thought = ""

        diary_service = self._get_diary_service()
        if diary_service is not None:
            try:
                today_content = diary_service.read_today()
                diary_lines = [event.content for event in today_content.events]
                diary_summary = "\n".join(f"- {line}" for line in diary_lines[-10:]) if diary_lines else str(today_content.raw_text or "")[-2000:]
            except Exception as exc:
                logger.debug(f"读取日记失败：{exc}")

            try:
                continuous_memory = diary_service.render_continuous_memory_for_prompt(
                    stream_id=stream_id,
                    chat_type=chat_type,
                )
            except Exception as exc:
                logger.debug(f"读取连续记忆失败：{exc}")

        self_narrative_service = self._get_self_narrative_service()
        if self_narrative_service is not None:
            try:
                self_narrative = self_narrative_service.render_prompt_block(
                    stream_id=stream_id,
                    chat_type=chat_type,
                )
            except Exception as exc:
                logger.debug(f"读取自我叙事失败：{exc}")

        unfinished_service = self._get_unfinished_thought_service()
        if unfinished_service is not None:
            try:
                unfinished_thought = unfinished_service.render_prompt_block(
                    stream_id=stream_id,
                    chat_type=chat_type,
                )
            except Exception as exc:
                logger.debug(f"读取未完成念头失败：{exc}")

        recent_messages = self._collect_recent_messages(stream_id, reference_window)
        state = self.get_state(
            stream_id,
            chat_type,
            platform=platform,
            stream_name=stream_name,
        )

        return {
            "最近日记": diary_summary,
            "连续记忆": continuous_memory,
            "自我叙事": self_narrative,
            "未完成念头": unfinished_thought,
            "最近对话": recent_messages,
            "当前状态": json.dumps(state.current_snapshot(), ensure_ascii=False, indent=2),
        }

    def _fallback_workspace(
        self,
        state: DriveState,
        sources: dict[str, str],
        *,
        trigger: str,
    ) -> DriveWorkspace:
        dominant = state.axes.dominant_label()
        topic_map = {
            "探索": "最近最值得继续追问的主题",
            "行动": "我现在最想推进的事情",
            "靠近": "我和某个对象之间的关系意义",
            "收缩": "我为什么更想先收一收",
            "休息": "我是不是需要先停一下",
            "紧迫": "我现在最急着确认的东西",
            "稳定": "什么东西正在帮我保持连续",
        }
        topic = topic_map.get(dominant, "我现在最需要弄清的事")
        question = f"我现在最想弄清楚的，是{topic}到底在提醒我什么？"
        hypothesis = f"当前更偏向{dominant}，但证据还不够完整。"
        evidence = []
        for key in ("最近日记", "连续记忆", "自我叙事", "未完成念头", "最近对话"):
            content = sources.get(key, "").strip()
            if content:
                evidence.append(content.splitlines()[0][:120])
        if not evidence:
            evidence.append("暂无可用材料，先保守推进。")
        next_action = "先收集最近日记和相关记忆，再决定是否收束。"
        return DriveWorkspace(
            task_id=f"drive_{uuid4().hex}",
            topic=topic,
            question=question,
            hypothesis=hypothesis,
            next_action=next_action,
            summary="",
            conclusion="",
            should_close=False,
            status="open",
            trigger=trigger,
            step_index=1,
            max_steps=self._cfg().scan.max_inquiry_steps,
            created_at=_now_iso(),
            updated_at=_now_iso(),
            evidence=evidence[:5],
            open_questions=[question],
            tool_trace=["fallback"],
            working_notes=[],
            source_summary="\n".join(evidence[:5]),
        )

    def _parse_json_blob(self, raw: str) -> dict[str, Any] | None:
        if not raw:
            return None

        candidate: dict[str, Any] | None = None
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                candidate = parsed
        except json.JSONDecodeError:
            start = raw.find("{")
            end = raw.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    parsed = json.loads(raw[start : end + 1])
                    if isinstance(parsed, dict):
                        candidate = parsed
                except json.JSONDecodeError:
                    return None
        return candidate

    def _normalize_text_list(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return _clean_lines([str(item) for item in value])

    def _fallback_task_name(self) -> tuple[str, str]:
        cfg = self._cfg()
        task_name = cfg.model.task_name.strip() or "diary"
        fallback_task_name = cfg.model.fallback_task_name.strip() or "actor"
        return task_name, fallback_task_name

    async def _call_llm_for_workspace(
        self,
        *,
        trigger: str,
        state: DriveState,
        workspace: DriveWorkspace | None,
        sources: dict[str, str],
        chat_stream: Any,
    ) -> dict[str, Any] | None:
        from src.kernel.llm import LLMRequest, LLMPayload, ROLE, Text

        task_name, fallback_task_name = self._fallback_task_name()
        try:
            model_set = get_model_config().get_task(task_name)
        except Exception:
            try:
                model_set = get_model_config().get_task(fallback_task_name)
            except Exception as exc:
                logger.warning(
                    f"未找到 drive_core 模型：{task_name}/{fallback_task_name} - {exc}"
                )
                return None

        if not model_set:
            return None

        request = LLMRequest(
            model_set,
            request_name="drive_core_workspace",
            context_manager=LLMContextManager(max_payloads=4),
        )
        shared_persona_prompt = await self._get_shared_persona_prompt(
            chat_stream=chat_stream,
            chat_type=state.chat_type,
            platform=state.platform,
            stream_name=state.stream_name,
        )
        request.add_payload(
            LLMPayload(
                ROLE.SYSTEM,
                Text(build_drive_core_system_prompt(shared_persona_prompt=shared_persona_prompt)),
            )
        )
        request.add_payload(
            LLMPayload(
                ROLE.USER,
                Text(
                    build_drive_core_user_prompt(
                        trigger=trigger,
                        current_state=state.current_snapshot(),
                        sources=sources,
                        current_workspace=workspace.brief() if workspace else None,
                        max_steps=self._cfg().scan.max_inquiry_steps,
                        history_window_size=self._cfg().scan.history_window_size,
                    )
                ),
            )
        )

        try:
            response = await request.send()
            result = await response if not response.message else response.message
        except Exception as exc:
            logger.error(f"drive_core LLM 推进失败：{exc}")
            return None

        if not result:
            return None

        parsed = self._parse_json_blob(str(result))
        if parsed is None:
            return None

        return parsed

    def _build_workspace_from_payload(
        self,
        *,
        payload: dict[str, Any],
        state: DriveState,
        trigger: str,
        previous: DriveWorkspace | None,
        sources: dict[str, str],
    ) -> DriveWorkspace:
        question = str(payload.get("question", "")).strip()
        topic = str(payload.get("topic", "")).strip()
        hypothesis = str(payload.get("hypothesis", "")).strip()
        next_action = str(payload.get("next_action", "")).strip()
        summary = str(payload.get("summary", "")).strip()
        conclusion = str(payload.get("conclusion", "")).strip()
        should_close = bool(payload.get("should_close", False))
        open_questions = self._normalize_text_list(payload.get("open_questions"))

        if not question:
            question = previous.question if previous else ""
        if not topic:
            topic = previous.topic if previous else "自我发问"
        if not hypothesis:
            hypothesis = previous.hypothesis if previous else "证据还不够完整"
        if not next_action:
            next_action = previous.next_action if previous else "先继续收集证据"
        if not open_questions:
            open_questions = [question] if question else []

        evidence = list(previous.evidence if previous else [])
        for key in ("最近日记", "连续记忆", "自我叙事", "未完成念头", "最近对话"):
            block = sources.get(key, "").strip()
            if block:
                first_line = block.splitlines()[0][:160]
                if first_line and first_line not in evidence:
                    evidence.append(first_line)

        tool_trace = list(previous.tool_trace if previous else [])
        tool_trace.append(trigger)

        step_index = (previous.step_index if previous else 0) + 1
        max_steps = previous.max_steps if previous else self._cfg().scan.max_inquiry_steps
        status = "closed" if should_close or step_index >= max_steps else "open"
        if status == "closed" and not conclusion:
            conclusion = summary or hypothesis or question

        return DriveWorkspace(
            task_id=previous.task_id if previous else f"drive_{uuid4().hex}",
            topic=topic,
            question=question,
            hypothesis=hypothesis,
            next_action=next_action,
            summary=summary,
            conclusion=conclusion,
            should_close=should_close,
            status=status,
            trigger=trigger,
            step_index=step_index,
            max_steps=max_steps,
            created_at=previous.created_at if previous else _now_iso(),
            updated_at=_now_iso(),
            evidence=evidence[-8:],
            open_questions=open_questions[:3],
            tool_trace=tool_trace[-10:],
            working_notes=list(previous.working_notes if previous else []),
            source_summary="\n".join(evidence[-5:]),
        )

    def _close_workspace(self, state: DriveState) -> None:
        workspace = state.current_workspace
        if workspace is None:
            return

        state.history.insert(
            0,
            DriveTaskRecord(
                task_id=workspace.task_id,
                created_at=workspace.created_at,
                closed_at=_now_iso(),
                trigger=workspace.trigger,
                topic=workspace.topic,
                question=workspace.question,
                hypothesis=workspace.hypothesis,
                summary=workspace.summary or workspace.source_summary,
                conclusion=workspace.conclusion or workspace.summary or workspace.question,
            ),
        )
        state.history = state.history[: self._cfg().storage.max_history_records]
        state.current_workspace = None

    async def advance_inquiry(
        self,
        *,
        stream_id: str,
        chat_type: str,
        platform: str = "",
        stream_name: str = "",
        trigger: str = "auto",
    ) -> tuple[bool, str]:
        """推进一次自我发问工作流。"""

        if not self._is_enabled():
            return False, "drive_core_plugin 未启用"

        normalized_chat_type, platform, stream_name = self._get_stream_meta(
            stream_id,
            chat_type,
            platform=platform,
            stream_name=stream_name,
        )

        async with self._get_lock(stream_id):
            state = self.get_state(
                stream_id,
                normalized_chat_type,
                platform=platform,
                stream_name=stream_name,
            )
            chat_stream = self._get_stream(stream_id)
            sources = self._collect_sources(
                stream_id=stream_id,
                chat_type=normalized_chat_type,
                platform=platform,
                stream_name=stream_name,
                trigger=trigger,
                reference_window=self._cfg().scan.history_window_size,
            )

            workspace = state.current_workspace
            if workspace and workspace.status == "closed":
                self._close_workspace(state)
                workspace = None

            payload = await self._call_llm_for_workspace(
                trigger=trigger,
                state=state,
                workspace=workspace,
                sources=sources,
                chat_stream=chat_stream,
            )

            if payload is None:
                new_workspace = self._fallback_workspace(state, sources, trigger=trigger)
            else:
                new_workspace = self._build_workspace_from_payload(
                    payload=payload,
                    state=state,
                    trigger=trigger,
                    previous=workspace,
                    sources=sources,
                )

            state.current_workspace = new_workspace
            state.message_count_since_scan = 0
            self._save_state(state)
            return True, self.render_state_summary(
                stream_id=stream_id,
                chat_type=normalized_chat_type,
            )

    async def observe_chat_turn(
        self,
        *,
        stream_id: str,
        chat_type: str,
        platform: str = "",
        stream_name: str = "",
        trigger: str = "auto",
    ) -> tuple[bool, str]:
        """记录一次聊天推进，并在达到阈值时自动触发自我发问。"""

        if not self._is_enabled():
            return False, "drive_core_plugin 未启用"

        normalized_chat_type, platform, stream_name = self._get_stream_meta(
            stream_id,
            chat_type,
            platform=platform,
            stream_name=stream_name,
        )

        async with self._get_lock(stream_id):
            state = self.get_state(
                stream_id,
                normalized_chat_type,
                platform=platform,
                stream_name=stream_name,
            )
            state.message_count_since_scan += 1
            threshold = self._cfg().scan.trigger_every_n_messages
            should_advance = (
                state.current_workspace is None
                or state.current_workspace.status != "open"
                or state.message_count_since_scan >= threshold
            )
            if not should_advance:
                self._save_state(state)
                return True, self.render_state_summary(
                    stream_id=stream_id,
                    chat_type=normalized_chat_type,
                )

        return await self.advance_inquiry(
            stream_id=stream_id,
            chat_type=normalized_chat_type,
            platform=platform,
            stream_name=stream_name,
            trigger=trigger,
        )

    def render_state_summary(
        self,
        stream_id: str,
        chat_type: str | None = None,
        *,
        platform: str = "",
        stream_name: str = "",
    ) -> str:
        state = self.get_state(
            stream_id,
            chat_type,
            platform=platform,
            stream_name=stream_name,
        )
        lines = [
            "【内驱力状态】",
            f"- 聊天流: {state.stream_id[:8]} / {state.chat_type}",
            f"- 主导倾向: {state.axes.dominant_label()}",
            f"- 当前轴: 好奇{state.axes.curiosity} 行动{state.axes.initiative} 靠近{state.axes.affinity} 收缩{state.axes.withdrawal} 疲惫{state.axes.fatigue} 紧迫{state.axes.urgency} 稳定{state.axes.stability}",
            f"- 累计推进计数: {state.message_count_since_scan}",
        ]
        workspace = state.current_workspace
        if workspace:
            lines.extend(
                [
                    f"- 当前课题: {workspace.topic or '未命名'}",
                    f"- 当前问题: {workspace.question or '暂无'}",
                    f"- 当前假设: {workspace.hypothesis or '暂无'}",
                    f"- 下一步: {workspace.next_action or '暂无'}",
                    f"- 轮次: {workspace.step_index}/{workspace.max_steps}",
                    f"- 状态: {workspace.status}",
                ]
            )
            if workspace.open_questions:
                lines.append(
                    "- 子问题: " + " / ".join(workspace.open_questions[:3])
                )
        else:
            lines.append("- 当前课题: 暂无，等待新的内在推动")
        return "\n".join(lines)

    def render_history(
        self,
        stream_id: str,
        chat_type: str | None = None,
        *,
        platform: str = "",
        stream_name: str = "",
    ) -> str:
        state = self.get_state(
            stream_id,
            chat_type,
            platform=platform,
            stream_name=stream_name,
        )
        if not state.history:
            return "【内驱力历史】\n暂无历史记录"
        lines = ["【内驱力历史】"]
        for idx, item in enumerate(state.history[:10], 1):
            lines.append(
                f"{idx}. {item.topic or '未命名'} | {item.question or '暂无问题'} | {item.conclusion or item.summary or '暂无结论'}"
            )
        return "\n".join(lines)

    def render_prompt_block(
        self,
        stream_id: str,
        chat_type: str | None = None,
        *,
        platform: str = "",
        stream_name: str = "",
    ) -> str:
        cfg = self._cfg()
        if not cfg.plugin.inject_prompt:
            return ""

        state = self.get_state(
            stream_id,
            chat_type,
            platform=platform,
            stream_name=stream_name,
        )
        workspace = state.current_workspace
        if workspace is None:
            return ""

        lines = [
            f"【{cfg.prompt.prompt_title}】",
            f"- 主导倾向：{state.axes.dominant_label()}",
            f"- 当前问题：{workspace.question or '暂无'}",
            f"- 当前假设：{workspace.hypothesis or '暂无'}",
            f"- 下一步：{workspace.next_action or '继续观察'}",
        ]
        if cfg.prompt.inject_evidence and workspace.evidence:
            lines.append(
                "- 证据：" + " / ".join(workspace.evidence[: self._cfg().storage.max_prompt_evidence_lines])
            )
        return "\n".join(lines)

    def snapshot(self, stream_id: str, chat_type: str | None = None) -> dict[str, Any]:
        state = self.get_state(stream_id, chat_type)
        return state.current_snapshot()

    def clear_state(
        self,
        stream_id: str,
        chat_type: str,
        *,
        platform: str = "",
        stream_name: str = "",
    ) -> tuple[bool, str]:
        state = DriveState.empty(
            stream_id=stream_id,
            chat_type=self._normalize_chat_type(chat_type),
            platform=platform,
            stream_name=stream_name,
            axes=self._default_axes(),
        )
        self._save_state(state)
        return True, "已重置内驱力状态"


def initialize_drive_core_service(plugin: Any) -> DriveCoreService:
    """初始化 drive_core 服务单例。"""

    global _SERVICE_INSTANCE
    if _SERVICE_INSTANCE is None:
        _SERVICE_INSTANCE = DriveCoreService(plugin)
        logger.info("drive_core_service 已初始化")
    else:
        _SERVICE_INSTANCE.plugin = plugin
    return _SERVICE_INSTANCE
