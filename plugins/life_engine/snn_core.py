"""SNN 皮层下驱动核心。

纯 numpy 实现的 LIF 神经元 + STDP 突触 + 驱动核网络。
为 life_engine 提供持续运行的状态底座，不依赖 LLM 即可维持连续内在状态。

设计理念：
- LLM 是大脑皮层（高级认知），SNN 是皮层下系统（持续存在）。
- SNN 不输出自然语言，不调用工具，只维护低维驱动状态。
- 膜电位持续衰减 = 时间的流逝留下物理性痕迹。
- STDP 学习 = 系统本身具备适应能力，不依赖外部训练。
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

logger = logging.getLogger("life_engine.snn")


class LIFNeuronGroup:
    """一组 Leaky Integrate-and-Fire 神经元。

    每个神经元维护自己的膜电位 v，接收输入电流后积分，
    达到阈值时发放脉冲（spike）并重置。膜电位在无输入时自然衰减。

    Attributes:
        n: 神经元数量。
        tau: 膜电位时间常数（tick 数）。越大衰减越慢。
        threshold: 发放阈值。
        reset: 发放后重置电位。
        rest: 静息电位。
        v: 当前膜电位向量。
        spikes: 上一步的脉冲向量。
    """

    __slots__ = ("n", "tau", "threshold", "reset", "rest", "v", "spikes")

    def __init__(
        self,
        n: int,
        tau: float = 20.0,
        threshold: float = 1.0,
        reset: float = 0.0,
        rest: float = 0.0,
    ) -> None:
        self.n = n
        self.tau = max(tau, 1.0)
        self.threshold = threshold
        self.reset = reset
        self.rest = rest

        self.v = np.full(n, rest, dtype=np.float64)
        self.spikes = np.zeros(n, dtype=bool)

    def step(self, current: np.ndarray, dt: float = 1.0) -> np.ndarray:
        """单步更新。

        Args:
            current: 输入电流向量（shape = (n,)）。
            dt: 时间步长（通常为 1.0）。

        Returns:
            布尔脉冲向量（shape = (n,)）。
        """
        dv = (-(self.v - self.rest) + current) / self.tau * dt
        self.v += dv

        # 数值安全：裁剪极端值
        np.clip(self.v, -10.0, 10.0, out=self.v)

        self.spikes = self.v >= self.threshold
        self.v[self.spikes] = self.reset

        return self.spikes.copy()

    def get_state(self) -> np.ndarray:
        """获取当前膜电位（用于外部读取）。"""
        return self.v.copy()

    def set_state(self, v: np.ndarray) -> None:
        """恢复膜电位（从持久化恢复）。"""
        if v.shape == self.v.shape:
            self.v = v.copy()
        else:
            logger.warning(
                f"LIF 状态恢复形状不匹配: expected {self.v.shape}, got {v.shape}，使用默认值"
            )


class STDPSynapse:
    """简化 STDP 突触连接。

    实现 Spike-Timing-Dependent Plasticity：
    - 前神经元先于后神经元发放 → 增强连接（因果关系）
    - 后神经元先于前神经元发放 → 减弱连接
    - 叠加奖赏调制：正奖赏放大增强，负奖赏放大减弱。

    Attributes:
        W: 权重矩阵 (n_post, n_pre)。
        trace_pre: 前突触活动痕迹。
        trace_post: 后突触活动痕迹。
    """

    __slots__ = (
        "W",
        "lr_plus",
        "lr_minus",
        "w_min",
        "w_max",
        "trace_pre",
        "trace_post",
        "trace_decay",
    )

    def __init__(
        self,
        n_pre: int,
        n_post: int,
        lr_plus: float = 0.01,
        lr_minus: float = 0.005,
        w_min: float = -1.0,
        w_max: float = 1.0,
    ) -> None:
        # Xavier-like 初始化
        scale = np.sqrt(2.0 / (n_pre + n_post))
        self.W = np.random.uniform(-scale, scale, (n_post, n_pre)).astype(np.float64)

        self.lr_plus = lr_plus
        self.lr_minus = lr_minus
        self.w_min = w_min
        self.w_max = w_max

        self.trace_pre = np.zeros(n_pre, dtype=np.float64)
        self.trace_post = np.zeros(n_post, dtype=np.float64)
        self.trace_decay = 0.95

    def forward(self, pre_activity: np.ndarray) -> np.ndarray:
        """计算突触后电流。

        Args:
            pre_activity: 前突触活动向量（float，可以是 spike 或连续值）。

        Returns:
            突触后电流向量。
        """
        return self.W @ pre_activity

    def update(
        self,
        pre_spikes: np.ndarray,
        post_spikes: np.ndarray,
        reward: float = 0.0,
    ) -> None:
        """STDP + 奖赏调制更新。

        Args:
            pre_spikes: 前突触脉冲向量（float, 0 或 1）。
            post_spikes: 后突触脉冲向量（float, 0 或 1）。
            reward: 奖赏信号（[-1, 1]）。
        """
        # 更新痕迹
        self.trace_pre = self.trace_pre * self.trace_decay + pre_spikes
        self.trace_post = self.trace_post * self.trace_decay + post_spikes

        reward_factor = 1.0 + np.clip(reward, -1.0, 1.0)

        # 前→后增强（LTP）
        if np.any(post_spikes > 0.5):
            dw_plus = self.lr_plus * np.outer(post_spikes, self.trace_pre)
            self.W += dw_plus * max(reward_factor, 0.1)

        # 后→前减弱（LTD）
        if np.any(pre_spikes > 0.5):
            dw_minus = -self.lr_minus * np.outer(self.trace_post, pre_spikes)
            self.W += dw_minus * max(2.0 - reward_factor, 0.1)

        # 权重裁剪
        np.clip(self.W, self.w_min, self.w_max, out=self.W)

    def get_weight_stats(self) -> dict[str, float]:
        """获取权重统计信息（用于监控）。"""
        return {
            "w_mean": float(np.mean(self.W)),
            "w_std": float(np.std(self.W)),
            "w_abs_mean": float(np.mean(np.abs(self.W))),
            "w_min": float(np.min(self.W)),
            "w_max": float(np.max(self.W)),
            "w_norm": float(np.linalg.norm(self.W)),
        }


class DriveCoreNetwork:
    """SNN 驱动核网络。

    结构: 输入层(8) → 隐藏层(16 LIF) → 输出层(6 LIF)
    输出使用膜电位的 EMA 平滑值，提供连续驱动信号。

    输出维度：
        0 - arousal:           整体激活度（短时间尺度）
        1 - valence:           情感正负（短时间尺度）
        2 - social_drive:      社交靠近冲动（中时间尺度）
        3 - task_drive:        推进任务冲动（中时间尺度）
        4 - exploration_drive: 探索新事物冲动（中时间尺度）
        5 - rest_drive:        休息/收束冲动（慢时间尺度）
    """

    INPUT_DIM = 8
    HIDDEN_DIM = 16
    OUTPUT_DIM = 6

    OUTPUT_NAMES: list[str] = [
        "arousal",
        "valence",
        "social_drive",
        "task_drive",
        "exploration_drive",
        "rest_drive",
    ]

    def __init__(self) -> None:
        # 神经元组——不同层使用不同时间常数
        self.hidden = LIFNeuronGroup(self.HIDDEN_DIM, tau=15.0, threshold=0.3)
        self.output = LIFNeuronGroup(self.OUTPUT_DIM, tau=30.0, threshold=0.5)

        # 突触
        self.syn_in_hid = STDPSynapse(
            self.INPUT_DIM, self.HIDDEN_DIM, lr_plus=0.01, lr_minus=0.005
        )
        self.syn_hid_out = STDPSynapse(
            self.HIDDEN_DIM, self.OUTPUT_DIM, lr_plus=0.008, lr_minus=0.004
        )

        # 输出 EMA（指数移动平均）—— 比原始膜电位更平滑
        self._output_ema = np.zeros(self.OUTPUT_DIM, dtype=np.float64)
        self._ema_alpha = 0.1

        self.tick_count: int = 0

    def step(self, input_vec: np.ndarray, reward: float = 0.0) -> np.ndarray:
        """单步前向传播 + 学习更新。

        Args:
            input_vec: 8 维输入特征（归一化到 [-1, 1]）。
            reward: 奖赏信号（[-1, 1]），来自上一轮心跳结果。

        Returns:
            6 维驱动输出（EMA 平滑后）。
        """
        # 输入安全检查
        if input_vec.shape != (self.INPUT_DIM,):
            input_vec = np.zeros(self.INPUT_DIM, dtype=np.float64)

        input_vec = np.clip(input_vec, -2.0, 2.0)

        # 前向传播
        current_hidden = self.syn_in_hid.forward(input_vec)
        spikes_hidden = self.hidden.step(current_hidden)

        current_output = self.syn_hid_out.forward(spikes_hidden.astype(np.float64))
        spikes_output = self.output.step(current_output)

        # 用膜电位作为连续输出（比 spike 更平滑）
        raw_output = self.output.get_state()
        self._output_ema = (
            (1 - self._ema_alpha) * self._output_ema + self._ema_alpha * raw_output
        )

        # STDP 学习（带奖赏调制）
        input_as_spikes = (np.abs(input_vec) > 0.1).astype(np.float64)

        self.syn_in_hid.update(
            input_as_spikes,
            spikes_hidden.astype(np.float64),
            reward=reward,
        )
        self.syn_hid_out.update(
            spikes_hidden.astype(np.float64),
            spikes_output.astype(np.float64),
            reward=reward,
        )

        self.tick_count += 1
        return self._output_ema.copy()

    def get_drive_dict(self) -> dict[str, float]:
        """返回命名的驱动向量。"""
        return {
            name: round(float(val), 4)
            for name, val in zip(self.OUTPUT_NAMES, self._output_ema)
        }

    def get_drive_discrete(self) -> dict[str, str]:
        """返回离散化的驱动描述（用于注入 prompt）。"""
        result: dict[str, str] = {}
        for name, value in zip(self.OUTPUT_NAMES, self._output_ema):
            if value > 0.6:
                level = "高"
            elif value > 0.3:
                level = "中"
            elif value > -0.3:
                level = "低"
            else:
                level = "抑制"
            result[name] = level
        return result

    def get_output_ema(self) -> np.ndarray:
        """获取原始 EMA 输出。"""
        return self._output_ema.copy()

    def get_health(self) -> dict[str, Any]:
        """获取网络健康状态（用于监控和审计）。"""
        return {
            "tick_count": self.tick_count,
            "drives": self.get_drive_dict(),
            "drives_discrete": self.get_drive_discrete(),
            "hidden_v_mean": round(float(np.mean(self.hidden.v)), 4),
            "hidden_v_std": round(float(np.std(self.hidden.v)), 4),
            "output_v_mean": round(float(np.mean(self.output.v)), 4),
            "output_v_std": round(float(np.std(self.output.v)), 4),
            "syn_in_hid": self.syn_in_hid.get_weight_stats(),
            "syn_hid_out": self.syn_hid_out.get_weight_stats(),
        }

    def serialize(self) -> dict[str, Any]:
        """序列化全部状态（用于持久化）。"""
        return {
            "hidden_v": self.hidden.v.tolist(),
            "output_v": self.output.v.tolist(),
            "output_ema": self._output_ema.tolist(),
            "syn_in_hid_W": self.syn_in_hid.W.tolist(),
            "syn_in_hid_trace_pre": self.syn_in_hid.trace_pre.tolist(),
            "syn_in_hid_trace_post": self.syn_in_hid.trace_post.tolist(),
            "syn_hid_out_W": self.syn_hid_out.W.tolist(),
            "syn_hid_out_trace_pre": self.syn_hid_out.trace_pre.tolist(),
            "syn_hid_out_trace_post": self.syn_hid_out.trace_post.tolist(),
            "tick_count": self.tick_count,
        }

    def deserialize(self, data: dict[str, Any]) -> None:
        """从持久化数据恢复。"""
        try:
            if "hidden_v" in data:
                self.hidden.set_state(np.array(data["hidden_v"], dtype=np.float64))
            if "output_v" in data:
                self.output.set_state(np.array(data["output_v"], dtype=np.float64))
            if "output_ema" in data:
                ema = np.array(data["output_ema"], dtype=np.float64)
                if ema.shape == self._output_ema.shape:
                    self._output_ema = ema
            if "syn_in_hid_W" in data:
                w = np.array(data["syn_in_hid_W"], dtype=np.float64)
                if w.shape == self.syn_in_hid.W.shape:
                    self.syn_in_hid.W = w
            if "syn_hid_out_W" in data:
                w = np.array(data["syn_hid_out_W"], dtype=np.float64)
                if w.shape == self.syn_hid_out.W.shape:
                    self.syn_hid_out.W = w
            if "syn_in_hid_trace_pre" in data:
                t = np.array(data["syn_in_hid_trace_pre"], dtype=np.float64)
                if t.shape == self.syn_in_hid.trace_pre.shape:
                    self.syn_in_hid.trace_pre = t
            if "syn_in_hid_trace_post" in data:
                t = np.array(data["syn_in_hid_trace_post"], dtype=np.float64)
                if t.shape == self.syn_in_hid.trace_post.shape:
                    self.syn_in_hid.trace_post = t
            if "syn_hid_out_trace_pre" in data:
                t = np.array(data["syn_hid_out_trace_pre"], dtype=np.float64)
                if t.shape == self.syn_hid_out.trace_pre.shape:
                    self.syn_hid_out.trace_pre = t
            if "syn_hid_out_trace_post" in data:
                t = np.array(data["syn_hid_out_trace_post"], dtype=np.float64)
                if t.shape == self.syn_hid_out.trace_post.shape:
                    self.syn_hid_out.trace_post = t
            if "tick_count" in data:
                self.tick_count = int(data["tick_count"])

            logger.info(f"SNN 状态反序列化成功，tick_count={self.tick_count}")
        except Exception as e:
            logger.error(f"SNN 状态反序列化失败，将从默认状态开始: {e}")
