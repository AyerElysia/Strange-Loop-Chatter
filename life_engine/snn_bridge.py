"""SNN 桥接层：事件流 ↔ SNN 驱动核。

将 life_engine 的事件流转换为 SNN 输入特征，
并从心跳结果中计算奖赏信号反馈给 STDP 学习。

职责：
- 特征提取：从 LifeEngineEvent 列表中提取 8 维归一化输入
- 奖赏计算：从心跳结果中计算奖赏信号
- 状态格式化：将 SNN 输出格式化为可注入 prompt 的文本
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from .service import LifeEngineEvent, LifeEngineService

logger = logging.getLogger("life_engine.snn")


def _event_time_seconds(event: Any) -> float:
    """从事件中提取时间戳（秒）。"""
    ts = getattr(event, "timestamp", None)
    if ts is None:
        return 0.0
    if isinstance(ts, (int, float)):
        return float(ts)
    if isinstance(ts, str):
        try:
            dt = datetime.fromisoformat(ts)
            return dt.timestamp()
        except (ValueError, TypeError):
            return 0.0
    return 0.0


def extract_features(
    events: list[Any],
    window_seconds: float = 600.0,
) -> np.ndarray:
    """从最近的事件中提取 SNN 输入特征。

    Args:
        events: LifeEngineEvent 列表。
        window_seconds: 窗口大小（秒）。

    Returns:
        8 维向量，归一化到 [-1, 1]。
    """
    now = time.time()
    cutoff = now - window_seconds

    # 过滤窗口内事件
    recent: list[Any] = []
    for e in events:
        t = _event_time_seconds(e)
        if t > cutoff or t == 0.0:  # t==0 表示无法解析，仍然计入
            recent.append(e)

    # 统计
    msg_in = 0
    msg_out = 0
    tool_success = 0
    tool_fail = 0
    idle_beats = 0
    tell_dfc_count = 0
    new_content = 0
    last_external_time = 0.0

    for e in recent:
        etype = getattr(e, "event_type", None)
        etype_val = getattr(etype, "value", str(etype)) if etype else ""

        if etype_val == "message":
            source_detail = str(getattr(e, "source_detail", "") or "")
            content_type = str(getattr(e, "content_type", "") or "")
            source = str(getattr(e, "source", "") or "")

            if "入站" in source_detail and source != "life_engine":
                msg_in += 1
                t = _event_time_seconds(e)
                if t > last_external_time:
                    last_external_time = t
            elif "出站" in source_detail:
                msg_out += 1
            elif content_type == "dfc_message":
                # DFC 留言也算入站
                msg_in += 1
            elif content_type == "direct_message":
                msg_in += 1
                t = _event_time_seconds(e)
                if t > last_external_time:
                    last_external_time = t

        elif etype_val == "tool_result":
            success = getattr(e, "tool_success", None)
            tool_name = str(getattr(e, "tool_name", "") or "")
            if success:
                tool_success += 1
                # 新内容创建
                if tool_name in (
                    "nucleus_write_file",
                    "nucleus_create_todo",
                    "nucleus_relate_file",
                ):
                    new_content += 1
            else:
                tool_fail += 1

            if tool_name == "nucleus_tell_dfc":
                tell_dfc_count += 1

        elif etype_val == "tool_call":
            tool_name = str(getattr(e, "tool_name", "") or "")
            if tool_name == "nucleus_tell_dfc":
                tell_dfc_count += 1

        elif etype_val == "heartbeat":
            content = str(getattr(e, "content", "") or "")
            # 仅含默认空转文本的心跳视为 idle
            if "安静" in content or "持续感受" in content or "等待" in content:
                idle_beats += 1

    # 沉默程度
    if last_external_time > 0:
        silence_minutes = (now - last_external_time) / 60.0
    else:
        silence_minutes = window_seconds / 60.0  # 无记录按最大沉默

    silence_factor = min(silence_minutes / 60.0, 1.0)

    raw = np.array(
        [
            msg_in,          # x0: 收到消息数
            msg_out,         # x1: 发送消息数
            tool_success,    # x2: 工具成功数
            tool_fail,       # x3: 工具失败数
            idle_beats,      # x4: 空闲心跳数
            tell_dfc_count,  # x5: 传话次数
            new_content,     # x6: 新内容创建数
            silence_factor,  # x7: 沉默程度 [0, 1]
        ],
        dtype=np.float64,
    )

    # 归一化到 [-1, 1]
    # 前 7 维：tanh 压缩（使得 3 以上基本饱和）
    raw[:7] = np.tanh(raw[:7] / 3.0)
    # 最后一维：[0,1] → [-1,1]
    raw[7] = raw[7] * 2.0 - 1.0

    return raw


def compute_reward(
    tool_event_count: int = 0,
    tool_success_count: int = 0,
    tool_fail_count: int = 0,
    idle_heartbeat_count: int = 0,
    had_text_reply: bool = True,
) -> float:
    """从心跳结果计算奖赏信号。

    Args:
        tool_event_count: 工具事件总数（call + result）。
        tool_success_count: 工具调用成功数。
        tool_fail_count: 工具调用失败数。
        idle_heartbeat_count: 连续空闲心跳数。
        had_text_reply: 是否产生了有意义的文本回复。

    Returns:
        奖赏信号，[-1.0, 1.0]。
    """
    reward = 0.0

    tool_calls = tool_event_count // 2 if tool_event_count > 0 else 0

    # 正向
    if tool_calls > 0:
        reward += 0.3  # 有行动
    if tool_success_count > 0:
        reward += min(tool_success_count * 0.15, 0.4)

    # 负向
    if tool_calls == 0:
        reward -= 0.2
    if tool_fail_count > 0:
        reward -= min(tool_fail_count * 0.2, 0.4)
    if idle_heartbeat_count >= 5:
        reward -= 0.3
    elif idle_heartbeat_count >= 2:
        reward -= 0.15

    return float(np.clip(reward, -1.0, 1.0))


class SNNBridge:
    """SNN 桥接层，连接 life_engine 事件流与 SNN 驱动核。"""

    def __init__(self, service: LifeEngineService) -> None:
        self._service = service
        self._last_reward: float = 0.0
        self._last_features: np.ndarray = np.zeros(8, dtype=np.float64)

    def extract_features_from_events(
        self,
        events: list[Any],
        window_seconds: float = 600.0,
    ) -> np.ndarray:
        """从事件历史中提取特征。"""
        features = extract_features(events, window_seconds=window_seconds)
        self._last_features = features.copy()
        return features

    def get_last_reward(self) -> float:
        """获取上一轮的奖赏信号。"""
        return self._last_reward

    def record_heartbeat_result(
        self,
        tool_event_count: int = 0,
        tool_success_count: int = 0,
        tool_fail_count: int = 0,
        idle_count: int = 0,
        had_text_reply: bool = True,
    ) -> float:
        """记录心跳结果，计算奖赏信号。

        Returns:
            计算得到的奖赏信号。
        """
        self._last_reward = compute_reward(
            tool_event_count=tool_event_count,
            tool_success_count=tool_success_count,
            tool_fail_count=tool_fail_count,
            idle_heartbeat_count=idle_count,
            had_text_reply=had_text_reply,
        )
        return self._last_reward

    def format_drive_for_prompt(self, drive_discrete: dict[str, str]) -> str:
        """将离散化驱动状态格式化为 prompt 注入文本。

        Returns:
            简短的中文状态描述（< 30 tokens）。
        """
        if not drive_discrete:
            return ""

        name_map = {
            "arousal": "激活",
            "valence": "情绪",
            "social_drive": "社交",
            "task_drive": "任务",
            "exploration_drive": "探索",
            "rest_drive": "休息",
        }

        parts = []
        for key, level in drive_discrete.items():
            cn_name = name_map.get(key, key)
            parts.append(f"{cn_name}{level}")

        return "内在驱动态：" + "、".join(parts)

    def get_snapshot(self) -> dict[str, Any]:
        """获取桥接层快照（用于审计）。"""
        return {
            "last_reward": round(self._last_reward, 4),
            "last_features": self._last_features.tolist(),
        }
