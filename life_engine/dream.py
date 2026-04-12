"""做梦系统 — 生物启发的离线巩固引擎。

三阶段做梦周期（对标生物睡眠）：
1. NREM 回放：SNN 以加速时间常数重放历史事件序列，STDP 巩固 + SHY 全局降权
2. REM 联想：记忆图谱随机扩散激活，Hebbian 形成新关联 + 修剪弱边
3. 觉醒过渡：调质层恢复（精力↑ 压力↓），生成做梦报告

设计原则：
- 核心计算在 SNN + 图谱层完成，零 LLM 开销
- 总做梦消耗极轻量（纯计算 + 一次可选叙事）
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from .snn_core import DriveCoreNetwork
    from .neuromod import InnerStateEngine
    from .memory_service import LifeMemoryService
    from .snn_bridge import SNNBridge

logger = logging.getLogger("life_engine.dream")


# ============================================================
# 枚举与数据类
# ============================================================

class DreamPhase(str, Enum):
    """做梦阶段。"""
    AWAKE = "awake"
    NREM = "nrem"
    REM = "rem"
    WAKING_UP = "waking_up"


@dataclass
class NREMReport:
    """NREM 回放阶段报告。"""
    episodes_replayed: int = 0
    total_steps: int = 0
    weight_before: dict[str, Any] = field(default_factory=dict)
    weight_after: dict[str, Any] = field(default_factory=dict)
    homeostatic_applied: bool = False


@dataclass
class REMReport:
    """REM 联想阶段报告。"""
    walk_rounds: int = 0
    nodes_activated: int = 0
    new_edges_created: int = 0
    edges_pruned: int = 0


@dataclass
class DreamReport:
    """完整做梦周期报告。"""
    dream_id: str = ""
    started_at: float = 0.0
    ended_at: float = 0.0
    duration_seconds: float = 0.0
    nrem: NREMReport = field(default_factory=NREMReport)
    rem: REMReport = field(default_factory=REMReport)
    narrative: str = ""
    phase_sequence: list[str] = field(default_factory=list)


# ============================================================
# 做梦调度器
# ============================================================

class DreamScheduler:
    """做梦调度与执行引擎。

    职责：
    - 判断是否应进入做梦状态
    - 执行 NREM → REM → Wake 三阶段做梦周期
    - 维护做梦历史和状态
    """

    def __init__(
        self,
        *,
        snn: DriveCoreNetwork | None = None,
        inner_state: InnerStateEngine | None = None,
        memory_service: LifeMemoryService | None = None,
        snn_bridge: SNNBridge | None = None,
        # NREM 参数
        nrem_replay_episodes: int = 3,
        nrem_events_per_episode: int = 20,
        nrem_speed_multiplier: float = 5.0,
        nrem_homeostatic_rate: float = 0.02,
        # REM 参数
        rem_walk_rounds: int = 2,
        rem_seeds_per_round: int = 5,
        rem_max_depth: int = 3,
        rem_decay_factor: float = 0.6,
        rem_learning_rate: float = 0.05,
        rem_edge_prune_threshold: float = 0.08,
        # 调度参数
        dream_interval_minutes: int = 90,
        idle_trigger_heartbeats: int = 10,
        nap_enabled: bool = True,
    ) -> None:
        # 子系统引用
        self._snn = snn
        self._inner_state = inner_state
        self._memory = memory_service
        self._snn_bridge = snn_bridge

        # NREM 参数
        self._nrem_replay_episodes = nrem_replay_episodes
        self._nrem_events_per_episode = nrem_events_per_episode
        self._nrem_speed_multiplier = nrem_speed_multiplier
        self._nrem_homeostatic_rate = nrem_homeostatic_rate

        # REM 参数
        self._rem_walk_rounds = rem_walk_rounds
        self._rem_seeds_per_round = rem_seeds_per_round
        self._rem_max_depth = rem_max_depth
        self._rem_decay_factor = rem_decay_factor
        self._rem_learning_rate = rem_learning_rate
        self._rem_edge_prune_threshold = rem_edge_prune_threshold

        # 调度参数
        self._dream_interval_seconds = dream_interval_minutes * 60
        self._idle_trigger_heartbeats = idle_trigger_heartbeats
        self._nap_enabled = nap_enabled

        # 运行时状态
        self._current_phase = DreamPhase.AWAKE
        self._dream_history: list[DreamReport] = []
        self._last_dream_time: float = 0.0
        self._is_dreaming: bool = False

    # ── 公开属性 ──────────────────────────────────────────────

    @property
    def is_dreaming(self) -> bool:
        return self._is_dreaming

    @property
    def current_phase(self) -> DreamPhase:
        return self._current_phase

    # ── 调度判断 ──────────────────────────────────────────────

    def should_dream(self, idle_heartbeat_count: int, in_sleep_window: bool) -> bool:
        """判断是否应该开始做梦。"""
        if self._is_dreaming:
            return False

        now = time.time()
        if now - self._last_dream_time < self._dream_interval_seconds:
            return False

        # 睡眠窗口内直接触发
        if in_sleep_window:
            return True

        # 白天空闲触发小憩
        if self._nap_enabled and idle_heartbeat_count >= self._idle_trigger_heartbeats:
            return True

        return False

    # ── 做梦主循环 ────────────────────────────────────────────

    async def run_dream_cycle(
        self,
        event_history: list[Any],
    ) -> DreamReport:
        """执行完整做梦周期：NREM → REM → Wake。"""
        self._is_dreaming = True
        report = DreamReport(
            dream_id=str(uuid.uuid4())[:8],
            started_at=time.time(),
        )

        try:
            # ── Phase 1: NREM 回放 ──
            self._current_phase = DreamPhase.NREM
            report.phase_sequence.append("nrem")
            logger.info(f"🌙 Dream [{report.dream_id}] NREM 回放阶段开始")

            if self._snn is not None and self._snn_bridge is not None:
                report.nrem = await self._run_nrem(event_history)

            # ── Phase 2: REM 联想 ──
            self._current_phase = DreamPhase.REM
            report.phase_sequence.append("rem")
            logger.info(f"🌙 Dream [{report.dream_id}] REM 联想阶段开始")

            if self._memory is not None:
                report.rem = await self._run_rem()

            # ── Phase 3: 觉醒过渡 ──
            self._current_phase = DreamPhase.WAKING_UP
            report.phase_sequence.append("waking_up")
            logger.info(f"🌙 Dream [{report.dream_id}] 觉醒过渡")

            if self._inner_state is not None:
                self._inner_state.wake_up()

            report.ended_at = time.time()
            report.duration_seconds = report.ended_at - report.started_at

            self._dream_history.append(report)
            self._last_dream_time = time.time()

            logger.info(
                f"🌙 Dream [{report.dream_id}] 完成 "
                f"耗时={report.duration_seconds:.1f}s | "
                f"NREM: {report.nrem.episodes_replayed}集/{report.nrem.total_steps}步 | "
                f"REM: {report.rem.nodes_activated}节点 "
                f"+{report.rem.new_edges_created}边 -{report.rem.edges_pruned}剪枝"
            )

        except Exception as e:
            logger.error(f"做梦周期异常: {e}", exc_info=True)
            report.ended_at = time.time()
            report.duration_seconds = report.ended_at - report.started_at
        finally:
            self._current_phase = DreamPhase.AWAKE
            self._is_dreaming = False

        return report

    # ── NREM 实现 ─────────────────────────────────────────────

    async def _run_nrem(self, event_history: list[Any]) -> NREMReport:
        """NREM 阶段：将历史事件通过 SNN 回放。"""
        report = NREMReport()

        if not self._snn or not self._snn_bridge:
            return report

        # 回放前权重快照
        report.weight_before = {
            "syn_in_hid": self._snn.syn_in_hid.get_weight_stats(),
            "syn_hid_out": self._snn.syn_hid_out.get_weight_stats(),
        }

        # 采样回放集
        episodes = self._sample_replay_episodes(event_history)

        for features_list in episodes:
            if features_list:
                stats = self._snn.replay_episodes(
                    features_list,
                    speed_multiplier=self._nrem_speed_multiplier,
                    reward_signal=0.0,
                )
                report.total_steps += stats.get("steps", 0)
                report.episodes_replayed += 1

        # SHY 全局降权
        if report.episodes_replayed > 0:
            self._snn.homeostatic_scaling(self._nrem_homeostatic_rate)
            report.homeostatic_applied = True

        # 回放后权重快照
        report.weight_after = {
            "syn_in_hid": self._snn.syn_in_hid.get_weight_stats(),
            "syn_hid_out": self._snn.syn_hid_out.get_weight_stats(),
        }

        return report

    def _sample_replay_episodes(
        self, event_history: list[Any]
    ) -> list[list[np.ndarray]]:
        """按重要性加权采样事件片段，提取为特征向量序列。

        Returns:
            list of episodes, where each episode is a list of feature vectors (np.ndarray).
        """
        if not event_history or not self._snn_bridge:
            return []

        chunk_size = max(self._nrem_events_per_episode, 1)
        chunks: list[list[Any]] = []
        for i in range(0, len(event_history), chunk_size):
            chunks.append(event_history[i : i + chunk_size])

        if not chunks:
            return []

        # 按活动量评分
        scores: list[float] = []
        for chunk in chunks:
            score = 0.0
            for e in chunk:
                etype = getattr(e, "event_type", None)
                etype_val = getattr(etype, "value", str(etype)) if etype else ""
                if etype_val == "message":
                    score += 2.0
                elif etype_val == "tool_call":
                    score += 1.5
                elif etype_val == "tool_result":
                    score += 1.0 if getattr(e, "tool_success", False) else 0.5
                elif etype_val == "heartbeat":
                    score += 0.3
            scores.append(max(score, 0.1))

        # 概率采样
        total = sum(scores)
        probs = np.array([s / total for s in scores])
        n_samples = min(self._nrem_replay_episodes, len(chunks))
        indices = np.random.choice(len(chunks), size=n_samples, replace=False, p=probs)

        # 对每个 chunk 提取特征向量
        episodes: list[list[np.ndarray]] = []
        window_sec = self._nrem_events_per_episode * 180.0
        for idx in sorted(indices):
            chunk = chunks[idx]
            features = self._snn_bridge.extract_features_from_events(
                chunk, window_seconds=window_sec
            )
            episodes.append([features])

        return episodes

    # ── REM 实现 ──────────────────────────────────────────────

    async def _run_rem(self) -> REMReport:
        """REM 阶段：记忆图谱随机游走 + Hebbian 强化。"""
        report = REMReport()

        if not self._memory:
            return report

        for _ in range(self._rem_walk_rounds):
            result = await self._memory.dream_walk(
                num_seeds=self._rem_seeds_per_round,
                max_depth=self._rem_max_depth,
                decay_factor=self._rem_decay_factor,
                learning_rate=self._rem_learning_rate,
            )
            report.nodes_activated += result.get("nodes_activated", 0)
            report.new_edges_created += result.get("new_edges_created", 0)
            report.walk_rounds += 1

        # 修剪弱边
        pruned = await self._memory.prune_weak_edges(
            threshold=self._rem_edge_prune_threshold,
        )
        report.edges_pruned = pruned

        return report

    # ── 调质层睡眠入口 ────────────────────────────────────────

    def enter_sleep(self) -> None:
        """通知调质层进入睡眠。"""
        if self._inner_state is not None:
            self._inner_state.enter_sleep()

    # ── 状态查询与持久化 ──────────────────────────────────────

    def get_dream_history(self, limit: int = 10) -> list[dict[str, Any]]:
        """获取最近的做梦报告。"""
        reports = self._dream_history[-limit:]
        return [_report_to_dict(r) for r in reports]

    def get_state(self) -> dict[str, Any]:
        """获取做梦系统当前状态。"""
        return {
            "is_dreaming": self._is_dreaming,
            "current_phase": self._current_phase.value,
            "last_dream_time": self._last_dream_time,
            "total_dreams": len(self._dream_history),
            "last_report": (
                _report_to_dict(self._dream_history[-1])
                if self._dream_history
                else None
            ),
        }

    def serialize(self) -> dict[str, Any]:
        """序列化做梦状态用于持久化。"""
        return {
            "last_dream_time": self._last_dream_time,
            "dream_history": [_report_to_dict(r) for r in self._dream_history[-20:]],
        }

    def deserialize(self, data: dict[str, Any]) -> None:
        """从持久化数据恢复状态。"""
        if not isinstance(data, dict):
            return
        self._last_dream_time = float(data.get("last_dream_time", 0.0))
        logger.info(
            f"做梦系统状态已恢复: last_dream={self._last_dream_time:.0f}"
        )


# ============================================================
# 辅助函数
# ============================================================

def _report_to_dict(report: DreamReport) -> dict[str, Any]:
    """将 DreamReport 转为可序列化字典。"""
    return {
        "dream_id": report.dream_id,
        "started_at": report.started_at,
        "ended_at": report.ended_at,
        "duration_seconds": round(report.duration_seconds, 1),
        "nrem": {
            "episodes_replayed": report.nrem.episodes_replayed,
            "total_steps": report.nrem.total_steps,
            "homeostatic_applied": report.nrem.homeostatic_applied,
            "weight_delta": _compute_weight_delta(
                report.nrem.weight_before, report.nrem.weight_after
            ),
        },
        "rem": {
            "walk_rounds": report.rem.walk_rounds,
            "nodes_activated": report.rem.nodes_activated,
            "new_edges_created": report.rem.new_edges_created,
            "edges_pruned": report.rem.edges_pruned,
        },
        "narrative": report.narrative,
        "phases": report.phase_sequence,
    }


def _compute_weight_delta(
    before: dict[str, Any], after: dict[str, Any]
) -> dict[str, float]:
    """计算权重变化摘要。"""
    if not before or not after:
        return {}
    delta: dict[str, float] = {}
    for layer in before:
        if layer in after:
            b = before[layer]
            a = after[layer]
            delta[f"{layer}_mean_delta"] = round(
                a.get("w_mean", 0) - b.get("w_mean", 0), 6
            )
            delta[f"{layer}_norm_delta"] = round(
                a.get("w_norm", 0) - b.get("w_norm", 0), 6
            )
    return delta
