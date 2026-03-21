"""睡眠/苏醒离散状态机。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from enum import Enum
from typing import Any, TypedDict, cast

from src.app.plugin_system.api.llm_api import create_llm_request, get_model_set_by_task
from src.kernel.llm import LLMContextManager
from src.kernel.llm.payload import LLMPayload, Text
from src.kernel.llm.roles import ROLE
from src.kernel.logger import get_logger


logger = get_logger("sleep_wakeup_plugin.guardian")


class CharacterState(str, Enum):
    AWAKE = "awake"
    SLEEPING = "sleeping"


class DrowsinessPhase(str, Enum):
    PRE_SLEEP = "pre_sleep"
    SLEEP = "sleep"
    PRE_WAKE = "pre_wake"
    AWAKE = "awake"


class HistoryRecord(TypedDict, total=False):
    timestamp: str
    source: str
    phase: str
    before: int
    after: int
    delta: int
    note: str


class GuardianResult(TypedDict):
    approved: bool
    reason: str
    reset_drowsiness: int


@dataclass
class RuntimeState:
    drowsiness: int = 0
    character_state: CharacterState = CharacterState.AWAKE
    lie_in_count: int = 0
    guardian_trigger_count: int = 0
    last_phase: DrowsinessPhase | None = None
    last_updated_at: str | None = None
    record_date: str | None = None
    last_sleep_report: dict[str, Any] | None = None
    history: list[HistoryRecord] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RuntimeState":
        character_state = CharacterState(
            str(data.get("character_state", CharacterState.AWAKE.value))
        )
        last_phase_raw = data.get("last_phase")
        last_phase = DrowsinessPhase(last_phase_raw) if last_phase_raw else None
        history_raw = data.get("history", [])
        history: list[HistoryRecord] = [
            cast(HistoryRecord, item) for item in history_raw if isinstance(item, dict)
        ]
        report_raw = data.get("last_sleep_report")
        report = report_raw if isinstance(report_raw, dict) else None
        return cls(
            drowsiness=int(data.get("drowsiness", 0)),
            character_state=character_state,
            lie_in_count=int(data.get("lie_in_count", 0)),
            guardian_trigger_count=int(data.get("guardian_trigger_count", 0)),
            last_phase=last_phase,
            last_updated_at=data.get("last_updated_at"),
            record_date=data.get("record_date"),
            last_sleep_report=report,
            history=history,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "drowsiness": self.drowsiness,
            "character_state": self.character_state.value,
            "lie_in_count": self.lie_in_count,
            "guardian_trigger_count": self.guardian_trigger_count,
            "last_phase": self.last_phase.value if self.last_phase else None,
            "last_updated_at": self.last_updated_at,
            "record_date": self.record_date,
            "last_sleep_report": self.last_sleep_report,
            "history": self.history,
        }


class SleepWakeupStateMachine:
    """睡眠/苏醒状态机。"""

    def __init__(
        self,
        *,
        sleep_target_time: str,
        wake_target_time: str,
        sleep_window_minutes: int,
        wake_window_minutes: int,
        pre_sleep_step: int,
        sleep_phase_step: int,
        pre_wake_step: int,
        lie_in_reset_drowsiness: int,
        max_lie_in_attempts: int,
        guardian_model_task: str,
        guardian_timeout_seconds: int,
    ) -> None:
        self.sleep_target_time = self._parse_time(sleep_target_time)
        self.wake_target_time = self._parse_time(wake_target_time)
        self.sleep_window_minutes = max(1, sleep_window_minutes)
        self.wake_window_minutes = max(1, wake_window_minutes)
        self.pre_sleep_step = max(1, pre_sleep_step)
        self.sleep_phase_step = max(1, sleep_phase_step)
        self.pre_wake_step = max(1, pre_wake_step)
        self.lie_in_reset_drowsiness = self._clamp(lie_in_reset_drowsiness)
        self.max_lie_in_attempts = max(0, max_lie_in_attempts)
        self.guardian_model_task = guardian_model_task.strip() or "actor"
        self.guardian_timeout_seconds = max(5, guardian_timeout_seconds)

    async def tick(
        self,
        state: RuntimeState,
        now: datetime,
        source: str,
    ) -> tuple[RuntimeState, list[str]]:
        phase = self.resolve_phase(now)
        before = state.drowsiness
        events: list[str] = []

        if phase == DrowsinessPhase.PRE_SLEEP:
            state.drowsiness = self._clamp(state.drowsiness + self.pre_sleep_step)
        elif phase == DrowsinessPhase.SLEEP:
            state.drowsiness = self._clamp(state.drowsiness + self.sleep_phase_step)
        elif phase == DrowsinessPhase.PRE_WAKE:
            state.drowsiness = self._clamp(state.drowsiness - self.pre_wake_step)
        else:
            state.drowsiness = 0

        if before < 100 and state.drowsiness == 100 and state.character_state == CharacterState.AWAKE:
            state.character_state = CharacterState.SLEEPING
            state.lie_in_count = 0
            events.append("switch_to_sleeping")

        if before > 0 and state.drowsiness == 0 and state.character_state == CharacterState.SLEEPING:
            guardian_result = await self._guardian_decision(state=state, phase=phase, now=now)
            state.guardian_trigger_count += 1
            if guardian_result["approved"]:
                state.character_state = CharacterState.AWAKE
                state.lie_in_count = 0
                state.last_sleep_report = self._build_sleep_report(now, guardian_result["reason"], state)
                events.append("guardian_approved")
            else:
                state.character_state = CharacterState.SLEEPING
                state.lie_in_count += 1
                state.drowsiness = guardian_result["reset_drowsiness"]
                events.append("guardian_rejected")

        state.last_phase = phase
        state.last_updated_at = now.isoformat()
        state.record_date = now.date().isoformat()
        self._append_history(
            state=state,
            source=source,
            phase=phase,
            before=before,
            after=state.drowsiness,
            note=",".join(events) if events else "tick",
            now=now,
        )
        return state, events

    async def apply_external_adjustment(
        self,
        state: RuntimeState,
        *,
        delta: int,
        now: datetime,
        source: str,
        note: str,
    ) -> tuple[RuntimeState, list[str]]:
        phase = self.resolve_phase(now)
        before = state.drowsiness
        state.drowsiness = self._clamp(state.drowsiness + delta)
        events: list[str] = []

        if before < 100 and state.drowsiness == 100 and state.character_state == CharacterState.AWAKE:
            state.character_state = CharacterState.SLEEPING
            state.lie_in_count = 0
            events.append("switch_to_sleeping")

        if before > 0 and state.drowsiness == 0 and state.character_state == CharacterState.SLEEPING:
            guardian_result = await self._guardian_decision(state=state, phase=phase, now=now)
            state.guardian_trigger_count += 1
            if guardian_result["approved"]:
                state.character_state = CharacterState.AWAKE
                state.lie_in_count = 0
                state.last_sleep_report = self._build_sleep_report(now, guardian_result["reason"], state)
                events.append("guardian_approved")
            else:
                state.character_state = CharacterState.SLEEPING
                state.lie_in_count += 1
                state.drowsiness = guardian_result["reset_drowsiness"]
                events.append("guardian_rejected")

        state.last_phase = phase
        state.last_updated_at = now.isoformat()
        state.record_date = now.date().isoformat()
        self._append_history(
            state=state,
            source=source,
            phase=phase,
            before=before,
            after=state.drowsiness,
            note=note if note else ",".join(events) if events else "external_adjustment",
            now=now,
        )
        return state, events

    def resolve_phase(self, now: datetime) -> DrowsinessPhase:
        pre_sleep_start, sleep_point, pre_wake_start, wake_point, next_pre_sleep_start = self._cycle_boundaries(now)
        if pre_sleep_start <= now < sleep_point:
            return DrowsinessPhase.PRE_SLEEP
        if sleep_point <= now < pre_wake_start:
            return DrowsinessPhase.SLEEP
        if pre_wake_start <= now < wake_point:
            return DrowsinessPhase.PRE_WAKE
        if wake_point <= now < next_pre_sleep_start:
            return DrowsinessPhase.AWAKE
        return DrowsinessPhase.AWAKE

    async def _guardian_decision(
        self,
        *,
        state: RuntimeState,
        phase: DrowsinessPhase,
        now: datetime,
    ) -> GuardianResult:
        del now
        try:
            model_set = get_model_set_by_task(self.guardian_model_task)
            context_manager = LLMContextManager(max_payloads=4)
            request = create_llm_request(
                model_set,
                request_name="sleep_guardian_decision",
                context_manager=context_manager,
            )
            history_preview = state.history[-10:]
            prompt = (
                "你是睡眠守护Agent，请基于给定状态判断是否批准苏醒。\n"
                "请严格只输出 JSON，不要输出其他文本。\n"
                '{"approved": true/false, "reason": "...", "reset_drowsiness": 0-100整数}\n'
                f"当前阶段: {phase.value}\n"
                f"当前困倦值: {state.drowsiness}\n"
                f"当前状态: {state.character_state.value}\n"
                f"赖床次数: {state.lie_in_count}\n"
                f"最大赖床次数: {self.max_lie_in_attempts}\n"
                f"建议重置困倦值: {self.lie_in_reset_drowsiness}\n"
                f"最近历史: {json.dumps(history_preview, ensure_ascii=False)}"
            )
            request.add_payload(LLMPayload(ROLE.USER, [Text(prompt)]))
            response = await request.send(stream=False)
            raw = await asyncio.wait_for(response, timeout=self.guardian_timeout_seconds)
            raw_text = (raw.message or "").strip()
            parsed = self._parse_guardian_json(raw_text)
            if parsed is None:
                logger.warning("guardian 大模型响应解析失败，使用规则回退")
                return self._guardian_decision_fallback(state=state, phase=phase)
            logger.debug(
                "guardian 决策完成: "
                f"approved={parsed['approved']}, reason={parsed['reason']}, "
                f"reset_drowsiness={parsed['reset_drowsiness']}, lie_in_count={state.lie_in_count}"
            )
            return parsed
        except Exception as exc:
            logger.warning(f"guardian 大模型调用失败，使用规则回退: {exc}")
            return self._guardian_decision_fallback(state=state, phase=phase)

    def _guardian_decision_fallback(
        self,
        *,
        state: RuntimeState,
        phase: DrowsinessPhase,
    ) -> GuardianResult:
        if phase == DrowsinessPhase.AWAKE:
            return {"approved": True, "reason": "arrived_awake_phase", "reset_drowsiness": 0}
        if state.lie_in_count >= self.max_lie_in_attempts:
            return {"approved": True, "reason": "max_lie_in_attempts_reached", "reset_drowsiness": 0}
        return {
            "approved": False,
            "reason": f"lie_in_attempt_{state.lie_in_count + 1}",
            "reset_drowsiness": self.lie_in_reset_drowsiness,
        }

    def _parse_guardian_json(self, raw: str) -> GuardianResult | None:
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
        approved = bool(obj.get("approved", False))
        reason = str(obj.get("reason", "llm_decision"))
        reset_raw = obj.get("reset_drowsiness", self.lie_in_reset_drowsiness)
        try:
            reset_value = self._clamp(int(reset_raw))
        except (TypeError, ValueError):
            reset_value = self.lie_in_reset_drowsiness
        if approved:
            reset_value = 0
        return {
            "approved": approved,
            "reason": reason,
            "reset_drowsiness": reset_value,
        }

    def _build_sleep_report(self, now: datetime, reason: str, state: RuntimeState) -> dict[str, Any]:
        return {
            "wake_time": now.isoformat(),
            "reason": reason,
            "lie_in_count": state.lie_in_count,
            "guardian_trigger_count": state.guardian_trigger_count,
            "final_drowsiness": state.drowsiness,
        }

    def _append_history(
        self,
        *,
        state: RuntimeState,
        source: str,
        phase: DrowsinessPhase,
        before: int,
        after: int,
        note: str,
        now: datetime,
    ) -> None:
        state.history.append(
            {
                "timestamp": now.isoformat(),
                "source": source,
                "phase": phase.value,
                "before": before,
                "after": after,
                "delta": after - before,
                "note": note,
            }
        )

    def _cycle_boundaries(
        self,
        now: datetime,
    ) -> tuple[datetime, datetime, datetime, datetime, datetime]:
        today = now.date()
        sleep_dt = datetime.combine(today, self.sleep_target_time)
        wake_dt = datetime.combine(today, self.wake_target_time)
        if wake_dt <= sleep_dt:
            if now.time() < self.wake_target_time:
                sleep_dt -= timedelta(days=1)
            else:
                wake_dt += timedelta(days=1)
        elif now < sleep_dt - timedelta(minutes=self.sleep_window_minutes):
            sleep_dt -= timedelta(days=1)
            wake_dt -= timedelta(days=1)

        pre_sleep_start = sleep_dt - timedelta(minutes=self.sleep_window_minutes)
        pre_wake_start = wake_dt - timedelta(minutes=self.wake_window_minutes)
        next_sleep_dt = sleep_dt + timedelta(days=1)
        next_pre_sleep_start = next_sleep_dt - timedelta(minutes=self.sleep_window_minutes)
        return pre_sleep_start, sleep_dt, pre_wake_start, wake_dt, next_pre_sleep_start

    @staticmethod
    def _parse_time(value: str) -> time:
        hour, minute = value.split(":", 1)
        return time(hour=int(hour), minute=int(minute))

    @staticmethod
    def _clamp(value: int) -> int:
        return max(0, min(100, int(value)))

