"""
Biometric Sleep System - 核心状态管理器 v2

简化版状态机：
- AWAKE（清醒）：正常时段
- DROWSY（困倦）：到达睡眠时间后的延时期
- SLEEPING（睡眠）：基于唤醒度的动态睡眠状态
"""

import random
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, Optional, Tuple

from src.kernel.logger import get_logger
from src.app.plugin_system.api.storage_api import JSONStore

logger = get_logger("sleep_manager")


class SleepState(Enum):
    """睡眠状态枚举 - 简化为3个核心状态"""
    AWAKE = "AWAKE"       # 清醒
    DROWSY = "DROWSY"     # 困倦/延时期
    SLEEPING = "SLEEPING" # 睡眠中


class SleepStateManager:
    """
    睡眠状态管理器
    
    核心机制：
    1. 时间驱动的状态切换（AWAKE <-> DROWSY -> SLEEPING）
    2. 基于唤醒度的动态唤醒/入睡判断
    """
    
    def __init__(self, config: dict):
        self.config = config
        self.storage = JSONStore("biometric_sleep_plugin")
        
        # 基础状态
        self.state = SleepState.AWAKE
        self.target_sleep_time: Optional[datetime] = None
        self.last_active_time = datetime.now()
        self.delay_count = 0
        
        # 唤醒度计数器 {session_id: (value, last_update_time)}
        self.wake_values: Dict[str, Tuple[float, datetime]] = {}
        
        self._load_state()

    def _save_state(self):
        """持久化关键状态"""
        state_data = {
            "target_sleep_time": self.target_sleep_time.isoformat() if self.target_sleep_time else None,
            "delay_count": self.delay_count,
            "last_active_time": self.last_active_time.isoformat(),
            "state": self.state.value,
            "save_date": datetime.now().date().isoformat()
        }
        self.storage.save("core_state", state_data)

    def _load_state(self):
        """加载持久化状态"""
        data = self.storage.load("core_state")
        now = datetime.now()
        
        if data and data.get("save_date") == now.date().isoformat():
            try:
                if data.get("target_sleep_time"):
                    self.target_sleep_time = datetime.fromisoformat(data["target_sleep_time"])
                self.delay_count = data.get("delay_count", 0)
                self.last_active_time = datetime.fromisoformat(data["last_active_time"])
                
                # 兼容旧状态名称
                saved_state = data.get("state", "AWAKE")
                if saved_state == "TIRED":
                    saved_state = "DROWSY"
                elif saved_state in ("DEEP_SLEEP", "WOKEN_UP"):
                    saved_state = "SLEEPING"
                    
                self.state = SleepState(saved_state)
                logger.info(f"[SleepManager] 已恢复今日状态: {self.state.value}, 目标入睡时间: {self.target_sleep_time.strftime('%H:%M') if self.target_sleep_time else 'N/A'}")
                return
            except Exception as e:
                logger.error(f"[SleepManager] 恢复状态失败: {e}")

        # 如果没有今日数据或加载失败，初始化新的一天
        self._init_daily_schedule()

    def _init_daily_schedule(self):
        """初始化每日作息"""
        now = datetime.now()
        basic_config = self.config.get("basic", {})
        start_str = basic_config.get("start_time", "23:30")
        offset = basic_config.get("random_offset", 30)
        
        try:
            h, m = map(int, start_str.split(":"))
            base_time = now.replace(hour=h, minute=m, second=0, microsecond=0)
            
            # 随机浮动
            random_min = random.randint(-offset, offset)
            self.target_sleep_time = base_time + timedelta(minutes=random_min)
            
            logger.info(f"[SleepManager] 今日目标入睡时间: {self.target_sleep_time.strftime('%H:%M')}")
            self._save_state()
        except Exception as e:
            logger.error(f"[SleepManager] 初始化作息失败: {e}")
            self.target_sleep_time = now.replace(hour=23, minute=30)

    def get_current_state(self, session_id: Optional[str] = None) -> SleepState:
        """获取当前基础状态"""
        now = datetime.now()
        
        # 基础状态逻辑
        start_time = self.target_sleep_time
        if start_time is None:
            return SleepState.AWAKE

        basic_config = self.config.get("basic", {})
        end_str = basic_config.get("end_time", "07:30")
        h_end, m_end = map(int, end_str.split(":"))
        
        # 构造结束时间
        end_time = now.replace(hour=h_end, minute=m_end, second=0, microsecond=0)
        calc_start = start_time
        
        # 判断时间跨度是否跨天
        is_cross_day = end_time < calc_start

        if is_cross_day:
            if now >= calc_start:
                end_time += timedelta(days=1)
            elif now < end_time:
                calc_start -= timedelta(days=1)
        
        # 判定是否在睡眠时段
        if calc_start <= now < end_time:
            # 检查是否处于 DROWSY 延时期
            if self.state == SleepState.DROWSY:
                if self.target_sleep_time and now >= self.target_sleep_time:
                    self.state = SleepState.SLEEPING
                    self._save_state()
                    logger.info(f"[SleepManager] 延时结束，进入睡眠状态")
                    return SleepState.SLEEPING
                return SleepState.DROWSY
            
            # 如果当前状态是 AWAKE 但时间在睡眠区间内
            if self.state == SleepState.AWAKE:
                self.state = SleepState.SLEEPING
                self._save_state()
                logger.info(f"[SleepManager] 检测到处于睡眠时间段，进入睡眠状态")

            return SleepState.SLEEPING
        
        # 不在睡眠时段
        if self.state != SleepState.AWAKE:
            self._reset_manager()
            
        return SleepState.AWAKE

    def _reset_manager(self):
        """重置管理器状态（醒来时）"""
        self.state = SleepState.AWAKE
        self.delay_count = 0
        self.wake_values.clear()
        self._init_daily_schedule()
        self._save_state()

    def update_activity(self, is_effective: bool = False):
        """更新活跃状态（DROWSY 延时逻辑）"""
        now = datetime.now()
        self.last_active_time = now
        
        # 如果快到入睡时间了，且有有效交互，尝试延时
        drowsy_config = self.config.get("drowsy", {})
        if self.target_sleep_time and now >= self.target_sleep_time - timedelta(minutes=10):
            max_delay = drowsy_config.get("max_delay_count", 3)
            if is_effective and self.delay_count < max_delay:
                self.state = SleepState.DROWSY
                self.delay_count += 1
                delay_min = drowsy_config.get("delay_duration", 15)
                self.target_sleep_time += timedelta(minutes=delay_min)
                logger.info(f"[SleepManager] 触发延时入睡，次数: {self.delay_count}/{max_delay}, 新入睡时间: {self.target_sleep_time.strftime('%H:%M')}")
                self._save_state()
            elif now >= self.target_sleep_time:
                if self.state != SleepState.SLEEPING:
                    self.state = SleepState.SLEEPING
                    self._save_state()
                    logger.info(f"[SleepManager] 到达入睡时间，进入睡眠状态")

    def get_wake_value(self, session_id: str) -> float:
        """
        获取指定会话的当前唤醒度（应用衰减后）
        """
        now = datetime.now()
        sleeping_config = self.config.get("sleeping", {})
        decay_rate = sleeping_config.get("decay_rate", 5.0)
        
        if session_id not in self.wake_values:
            return 0.0
        
        current_val, last_time = self.wake_values[session_id]
        
        # 计算时间差（分钟）并应用衰减
        time_diff = (now - last_time).total_seconds() / 60.0
        decay_amount = time_diff * decay_rate
        new_val = max(0.0, current_val - decay_amount)
        
        return new_val

    def is_woken(self, session_id: str) -> bool:
        """
        判断指定会话是否处于"被吵醒"状态
        
        规则：唤醒度 >= 阈值 时被吵醒
        """
        sleeping_config = self.config.get("sleeping", {})
        threshold = sleeping_config.get("wake_threshold", 50.0)
        current_val = self.get_wake_value(session_id)
        return current_val >= threshold

    def add_wake_value(self, session_id: str) -> Tuple[float, bool]:
        """
        增加唤醒度，返回 (新唤醒度, 是否刚刚被吵醒)
        
        逻辑：
        1. 获取当前唤醒度（应用衰减）
        2. 增加唤醒度（不超过上限）
        3. 判断是否达到阈值
        """
        now = datetime.now()
        sleeping_config = self.config.get("sleeping", {})
        threshold = sleeping_config.get("wake_threshold", 50.0)
        increment = sleeping_config.get("wake_increment", 20.0)
        max_val = sleeping_config.get("wake_max", 80.0)
        
        # 获取当前值（已应用衰减）
        current_val = self.get_wake_value(session_id)
        was_woken = current_val >= threshold
        
        # 增加唤醒度，但不超过上限
        new_val = min(current_val + increment, max_val)
        
        # 保存新值
        self.wake_values[session_id] = (new_val, now)
        
        is_now_woken = new_val >= threshold
        just_woken = is_now_woken and not was_woken
        
        logger.info(f"[SleepManager] 会话 {session_id} 唤醒度: {current_val:.1f} -> {new_val:.1f} (阈值: {threshold}, 上限: {max_val})")
        
        if just_woken:
            logger.info(f"[SleepManager] 会话 {session_id} 被吵醒了！")
        
        return new_val, just_woken

    def is_ignored(self, user_id: str, group_id: Optional[str] = None) -> bool:
        """检查是否在忽略名单中"""
        filter_config = self.config.get("filter", {})
        ignored_ids = filter_config.get("ignored_ids", [])
        mode = filter_config.get("mode", "blacklist")
        
        target_ids = [user_id]
        if group_id:
            target_ids.append(group_id)
            
        is_in_list = any(tid in ignored_ids for tid in target_ids)
        
        if mode == "blacklist":
            return is_in_list
        else:  # whitelist
            return not is_in_list