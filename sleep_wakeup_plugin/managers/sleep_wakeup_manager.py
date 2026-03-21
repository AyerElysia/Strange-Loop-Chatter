"""睡眠/苏醒状态机管理器。"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, cast

from src.app.plugin_system.api import storage_api
from src.core.prompt import get_system_reminder_store
from src.kernel.logger import get_logger
from src.kernel.scheduler import TriggerType, get_unified_scheduler

from ..components.configs.config import Config
from ..core.state_machine import (
    CharacterState,
    DrowsinessPhase,
    RuntimeState,
    SleepWakeupStateMachine,
)


logger = get_logger("sleep_wakeup_plugin.managers.runtime")

_manager_instance: SleepWakeupManager | None = None


def get_sleep_wakeup_manager() -> "SleepWakeupManager":
    if _manager_instance is None:
        raise RuntimeError(
            "SleepWakeupManager 尚未初始化，请在插件加载时调用 initialize_sleep_wakeup_manager()"
        )
    return _manager_instance


def initialize_sleep_wakeup_manager(
    plugin_name: str,
    config: Config,
) -> "SleepWakeupManager":
    global _manager_instance
    if _manager_instance is None:
        _manager_instance = SleepWakeupManager(plugin_name=plugin_name, config=config)
        logger.info("SleepWakeupManager 管理器已初始化")
    return _manager_instance


class SleepWakeupManager:
    """睡眠/苏醒状态机管理器。"""

    _REMINDER_BUCKET = "actor"
    _REMINDER_NAME = "sleep_report"

    def __init__(self, *, plugin_name: str, config: Config) -> None:
        self._plugin_name = plugin_name
        self._config = config
        self._state_machine = self._build_state_machine(config)
        self._runtime_state: RuntimeState = RuntimeState()
        self._state_lock = asyncio.Lock()
        self._schedule_task_id: str | None = None

    async def initialize(self) -> None:
        logger.info("SleepWakeupManager 开始初始化运行时")
        await self.load_runtime_state()
        await self.tick(source="startup")
        await self._start_scheduler()
        logger.info("SleepWakeupManager 运行时初始化完成")

    async def shutdown(self) -> None:
        logger.info("SleepWakeupManager 开始关闭")
        await self._stop_scheduler()
        await self.save_runtime_state()
        logger.info("SleepWakeupManager 已关闭")

    async def tick(self, source: str) -> list[str]:
        async with self._state_lock:
            before = self._runtime_state.to_dict()
            before_drowsiness = before.get("drowsiness", 0)
            before_state = before.get("character_state", "awake")
            now = datetime.now()
            self._runtime_state, events = await self._state_machine.tick(
                state=self._runtime_state,
                now=now,
                source=source,
            )
            self._trim_history()
            after = self._runtime_state.to_dict()
            after_drowsiness = after.get("drowsiness", 0)
            after_state = after.get("character_state", "awake")

            if before_drowsiness != after_drowsiness:
                delta = after_drowsiness - before_drowsiness
                logger.info(
                    f"困倦值变化: {before_drowsiness} -> {after_drowsiness} "
                    f"(delta={delta:+d}, source={source})"
                )
            if before_state != after_state:
                logger.info(f"角色状态转换: {before_state} -> {after_state}")
                if after_state == CharacterState.SLEEPING.value:
                    logger.info(f"角色进入睡眠状态 (困倦值达到 {after_drowsiness})")
            if self._config.general.debug_mode:
                logger.debug(
                    f"tick 执行完成: source={source}, drowsiness={after_drowsiness}, "
                    f"state={after_state}, events={events}"
                )
            if before != after:
                await self.save_runtime_state()

            await self._handle_state_events(events)
            return events

    async def handle_private_message_wakeup(self, sender_id: str, platform: str) -> bool:
        if not self._config.guard.enable_private_message_wakeup:
            return False
        if not self._check_user_in_wakeup_list(sender_id, platform):
            logger.debug(f"用户 {platform}:{sender_id} 不满足唤醒名单条件，跳过困倦值降低")
            return False

        delta = max(0, self._config.guard.private_message_wakeup_delta)
        if delta <= 0:
            return False

        async with self._state_lock:
            before = self._runtime_state.to_dict()
            before_drowsiness = before.get("drowsiness", 0)
            now = datetime.now()
            self._runtime_state, events = await self._state_machine.apply_external_adjustment(
                self._runtime_state,
                delta=-delta,
                now=now,
                source="private_message",
                note=f"private_message_wakeup:-{delta}",
            )
            self._trim_history()
            after = self._runtime_state.to_dict()
            after_drowsiness = after.get("drowsiness", 0)
            changed = before != after
            if changed:
                await self.save_runtime_state()
                logger.info(
                    f"私聊消息触发唤醒调整: drowsiness {before_drowsiness} -> {after_drowsiness} "
                    f"(delta={-delta})"
                )
            await self._handle_state_events(events)
            return changed

    def _check_user_in_wakeup_list(self, sender_id: str, platform: str) -> bool:
        list_type = self._config.guard.wakeup_user_list_type
        user_list = [str(item) for item in self._config.guard.wakeup_user_list]
        if list_type == "all":
            return True
        user_key = f"{platform}:{sender_id}"
        if list_type == "whitelist":
            if not user_list:
                logger.debug(f"白名单模式但名单为空，拒绝用户 {user_key}")
                return False
            return user_key in user_list
        if list_type == "blacklist":
            if not user_list:
                return True
            return user_key not in user_list
        logger.warning(f"未知的唤醒名单模式 '{list_type}'，默认允许用户 {user_key}")
        return True

    async def load_runtime_state(self) -> None:
        raw = await storage_api.load_json(self._plugin_name, self._config.storage.state_key)
        if not raw:
            self._runtime_state = RuntimeState()
            logger.info("未检测到历史状态，使用默认初始状态")
            return
        try:
            loaded_state = RuntimeState.from_dict(raw)
            purge, reason = await self._should_purge_stale_state(loaded_state)
            if purge:
                await storage_api.delete_json(self._plugin_name, self._config.storage.state_key)
                self._runtime_state = RuntimeState()
                logger.info(f"已清理旧持久化状态: reason={reason}")
                return
            self._runtime_state = loaded_state
            self._trim_history()
            logger.info("已从 JSON 存储恢复运行状态")
        except Exception as exc:
            logger.warning(f"恢复状态失败，将使用默认状态: {exc}")
            self._runtime_state = RuntimeState()

    async def save_runtime_state(self) -> None:
        payload = self._runtime_state.to_dict()
        await storage_api.save_json(
            self._plugin_name,
            self._config.storage.state_key,
            cast(dict[str, Any], payload),
        )

    def get_runtime_snapshot(self) -> dict[str, Any]:
        return self._runtime_state.to_dict()

    def should_block_messages(self) -> bool:
        if not self._config.general.enabled:
            return False
        if not self._config.guard.block_messages_when_sleeping:
            return False
        return self._runtime_state.character_state == CharacterState.SLEEPING

    async def _start_scheduler(self) -> None:
        if self._schedule_task_id is not None:
            logger.warning("周期任务已存在，跳过启动")
            return
        scheduler = get_unified_scheduler()
        self._schedule_task_id = await scheduler.create_schedule(
            callback=self._scheduled_tick,
            trigger_type=TriggerType.TIME,
            trigger_config={
                "delay_seconds": self._config.timing.update_interval_seconds,
                "interval_seconds": self._config.timing.update_interval_seconds,
            },
            is_recurring=True,
            task_name=f"{self._plugin_name}:drowsiness_tick",
            timeout=60.0,
            max_retries=3,
        )
        logger.info(
            f"周期调度任务已启动: task_id={self._schedule_task_id}, "
            f"interval={self._config.timing.update_interval_seconds}s"
        )

    async def _stop_scheduler(self) -> None:
        if self._schedule_task_id is None:
            return
        scheduler = get_unified_scheduler()
        task_id = self._schedule_task_id
        self._schedule_task_id = None
        try:
            await scheduler.remove_schedule(task_id)
            logger.info(f"周期调度任务已移除: task_id={task_id}")
        except Exception as exc:
            logger.warning(f"移除周期任务失败: task_id={task_id}, error={exc}")

    async def _scheduled_tick(self) -> None:
        await self.tick(source="scheduler")

    def _trim_history(self) -> None:
        limit = self._config.storage.max_history_records
        if len(self._runtime_state.history) > limit:
            self._runtime_state.history = self._runtime_state.history[-limit:]

    async def _should_purge_stale_state(self, state: RuntimeState) -> tuple[bool, str]:
        now = datetime.now()
        today = now.date().isoformat()
        state_date = state.record_date
        if not state_date and state.last_updated_at:
            try:
                state_date = datetime.fromisoformat(state.last_updated_at).date().isoformat()
            except ValueError:
                state_date = None
        if not state_date:
            return False, "no_state_date"
        current_phase = self._state_machine.resolve_phase(now)
        in_sleep_period = current_phase in {DrowsinessPhase.SLEEP, DrowsinessPhase.PRE_WAKE}
        if state_date != today:
            return True, "date_mismatch"
        if not in_sleep_period:
            return True, f"not_in_sleep_period:{current_phase.value}"
        return False, "keep"

    async def _handle_state_events(self, events: list[str]) -> None:
        for event in events:
            if event == "switch_to_sleeping":
                logger.info("状态事件: 切换为 sleeping")
                await self._clear_sleep_report()
            elif event == "guardian_approved":
                logger.info("状态事件: guardian 批准苏醒，切换为 awake")
                await self._inject_sleep_report()
            elif event == "guardian_rejected":
                logger.info("状态事件: guardian 驳回苏醒，保持 sleeping")

    def _format_sleep_report(self, report: dict[str, Any]) -> str:
        wake_time = report.get("wake_time", "未知时间")
        reason = report.get("reason", "未知原因")
        lie_in_count = report.get("lie_in_count", 0)
        guardian_trigger_count = report.get("guardian_trigger_count", 0)
        try:
            dt = datetime.fromisoformat(wake_time)
            wake_time_str = dt.strftime("%Y年%m月%d日 %H:%M")
        except (ValueError, TypeError):
            wake_time_str = str(wake_time)

        lines = [f"你刚刚苏醒过来（{wake_time_str}）。", f"苏醒原因：{reason}"]
        if lie_in_count > 0:
            lines.append(f"赖床次数：{lie_in_count}")
        if guardian_trigger_count > 0:
            lines.append(f"守护检查次数：{guardian_trigger_count}")
        lines.append("\n注：这是你的睡眠记录，你可以根据自己的情况自然地回应或忽略此信息。")
        return "\n".join(lines)

    async def _inject_sleep_report(self) -> None:
        report = self._runtime_state.last_sleep_report
        if not report:
            logger.debug("无睡眠报告，跳过注入")
            return
        formatted = self._format_sleep_report(report)
        store = get_system_reminder_store()
        store.set(bucket=self._REMINDER_BUCKET, name=self._REMINDER_NAME, content=formatted)
        logger.info(f"睡眠报告已注入到 system_reminder (bucket={self._REMINDER_BUCKET})")

    async def _clear_sleep_report(self) -> None:
        store = get_system_reminder_store()
        deleted = store.delete(bucket=self._REMINDER_BUCKET, name=self._REMINDER_NAME)
        if deleted:
            logger.info(f"已清理 system_reminder 中的睡眠报告 (bucket={self._REMINDER_BUCKET})")
        else:
            logger.debug("未找到需要清理的睡眠报告")

    @staticmethod
    def _build_state_machine(config: Config) -> SleepWakeupStateMachine:
        timing = config.timing
        model = config.model
        return SleepWakeupStateMachine(
            sleep_target_time=timing.sleep_target_time,
            wake_target_time=timing.wake_target_time,
            sleep_window_minutes=timing.sleep_window_minutes,
            wake_window_minutes=timing.wake_window_minutes,
            pre_sleep_step=model.pre_sleep_step,
            sleep_phase_step=model.sleep_phase_step,
            pre_wake_step=model.pre_wake_step,
            lie_in_reset_drowsiness=model.lie_in_reset_drowsiness,
            max_lie_in_attempts=model.max_lie_in_attempts,
            guardian_model_task=model.guardian_model_task,
            guardian_timeout_seconds=model.guardian_timeout_seconds,
        )

