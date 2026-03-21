"""
Biometric Sleep System - 管理命令 v2
"""

from src.kernel.logger import get_logger
from src.core.components.base import BaseCommand

from ..core.sleep_manager import SleepState, SleepStateManager

logger = get_logger("sleep_admin_command")


class SleepAdminCommand(BaseCommand):
    command_name = "sleep"
    command_description = "睡眠系统管理命令"
    command_usage = "/sleep <status|set|wake> [参数]"
    
    manager: SleepStateManager = None  # type: ignore

    def __init__(self, params, plugin_config: dict):
        super().__init__(params, plugin_config)

    async def execute(self) -> str:
        args = self.params.get("args", [])
        if not args:
            return self._get_help()

        sub_cmd = args[0].lower()
        
        if sub_cmd == "status":
            return self._get_status()
        elif sub_cmd == "set":
            if len(args) < 2:
                return "用法: /sleep set <awake|drowsy|sleeping>"
            return self._set_state(args[1])
        elif sub_cmd == "wake":
            session_id = args[1] if len(args) > 1 else None
            return self._force_wake(session_id)
        elif sub_cmd == "reset":
            return self._reset()
        else:
            return self._get_help()

    def _get_help(self) -> str:
        return """睡眠系统管理命令:
/sleep status - 查看当前状态
/sleep set <awake|drowsy|sleeping> - 强制设置状态
/sleep wake [session_id] - 强制唤醒指定会话
/sleep reset - 重置所有状态"""

    def _get_status(self) -> str:
        state = self.manager.get_current_state()
        target_time = self.manager.target_sleep_time
        delay_count = self.manager.delay_count
        
        status = f"当前状态: {state.value}\n"
        status += f"目标入睡时间: {target_time.strftime('%H:%M') if target_time else 'N/A'}\n"
        status += f"延时次数: {delay_count}\n"
        
        # 显示各会话的唤醒度
        if self.manager.wake_values:
            status += "\n唤醒度:\n"
            for session_id, (val, last_time) in self.manager.wake_values.items():
                current_val = self.manager.get_wake_value(session_id)
                is_woken = self.manager.is_woken(session_id)
                status += f"  {session_id}: {current_val:.1f} {'(被吵醒)' if is_woken else ''}\n"
        
        return status

    def _set_state(self, state_str: str) -> str:
        state_map = {
            "awake": SleepState.AWAKE,
            "drowsy": SleepState.DROWSY,
            "sleeping": SleepState.SLEEPING,
        }
        
        state = state_map.get(state_str.lower())
        if not state:
            return f"无效状态: {state_str}，可选: awake, drowsy, sleeping"
        
        self.manager.state = state
        self.manager._save_state()
        return f"状态已设置为: {state.value}"

    def _force_wake(self, session_id: str | None) -> str:
        if not session_id:
            session_id = "private_admin"
        
        # 将唤醒度设置到阈值以上
        threshold = self.manager.config.get("sleeping.wake_threshold", 50.0)
        from datetime import datetime
        self.manager.wake_values[session_id] = (threshold + 10, datetime.now())
        
        return f"已强制唤醒会话: {session_id} (唤醒度: {threshold + 10})"

    def _reset(self) -> str:
        self.manager._reset_manager()
        return "睡眠系统已重置"