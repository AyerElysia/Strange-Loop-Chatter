"""SNN 皮层下驱动核心 v2。

纯 numpy 实现的 LIF 神经元 + 软 STDP 突触 + 驱动核网络。
为 life_engine 提供持续运行的状态底座，不依赖 LLM 即可维持连续内在状态。

v2 关键改进（对照 v1 诊断报告）：
- 分离 decay_only() 与 step()：零输入 tick 不再执行完整 step，避免淹没信号。
- 软 STDP：用 sigmoid(膜电位) 代替二值 spike 参与学习，低活跃时也能触发可塑性。
- 背景噪声：真实输入时叠加微弱高斯噪声，打破不动点。
- 动态离散化：基于运行时 EMA 均值+标准差的 z-score 阈值，而非固定绝对阈值。
- 更温和的自稳态：缩小调节步长，避免 gain/threshold 撞到极限。
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

logger = logging.getLogger("life_engine.snn")


class LIFNeuronGroup:
    """Leaky Integrate-and-Fire 神经元组。"""

    __slots__ = ("n", "tau", "threshold", "reset", "rest", "v", "spikes")

    def __init__(
        self,
        n: int,
        tau: float = 20.0,
        threshold: float = 1.0,
        reset: float = 0.0,
        rest: float = 0.0,
    ) -> None:
        """初始化 LIF 神经元组。

        Args:
            n: 神经元数量
            tau: 膜电位时间常数（毫秒），控制泄漏速度
            threshold: 发放阈值（mV），超过此值触发脉冲
            reset: 发放后重置电位（mV）
            rest: 静息电位（mV），无输入时的稳定电位
        """
        self.n = n
        self.tau = max(tau, 1.0)
        self.threshold = threshold
        self.reset = reset
        self.rest = rest
        self.v = np.full(n, rest, dtype=np.float64)
        self.spikes = np.zeros(n, dtype=bool)

    def step(self, current: np.ndarray, dt: float = 1.0) -> np.ndarray:
        """单步更新：积分 + 发放。"""
        dv = (-(self.v - self.rest) + current) / self.tau * dt
        self.v += dv
        np.clip(self.v, -10.0, 10.0, out=self.v)
        self.spikes = self.v >= self.threshold
        self.v[self.spikes] = self.reset
        return self.spikes.copy()

    def decay_only(self, dt: float = 1.0) -> None:
        """仅膜电位泄漏衰减，不注入电流，不检查发放。"""
        dv = -(self.v - self.rest) / self.tau * dt
        self.v += dv
        np.clip(self.v, -10.0, 10.0, out=self.v)
        self.spikes[:] = False

    def get_state(self) -> np.ndarray:
        """获取当前膜电位状态。

        Returns:
            膜电位数组的副本（单位：mV）
        """
        return self.v.copy()

    def set_state(self, v: np.ndarray) -> None:
        """设置膜电位状态（用于状态恢复）。

        Args:
            v: 新的膜电位数组，形状必须与神经元数量匹配
        """
        if v.shape == self.v.shape:
            self.v = v.copy()
        else:
            logger.warning(
                f"LIF 状态恢复形状不匹配: expected {self.v.shape}, got {v.shape}"
            )


class STDPSynapse:
    """软 STDP 突触连接。

    v2 改进：支持连续活跃度（sigmoid of membrane potential）参与学习，
    而非仅在二值 spike 时才更新。这确保在低放电率下仍有可塑性。
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
        """初始化 STDP 突触连接。

        Args:
            n_pre: 前突触神经元数量
            n_post: 后突触神经元数量
            lr_plus: LTP 学习率（长时程增强）
            lr_minus: LTD 学习率（长时程抑制）
            w_min: 权重下限
            w_max: 权重上限
        """
        scale = np.sqrt(2.0 / (n_pre + n_post))
        self.W = np.random.uniform(-scale, scale, (n_post, n_pre)).astype(np.float64)
        self.lr_plus = lr_plus
        self.lr_minus = lr_minus
        self.w_min = w_min
        self.w_max = w_max
        self.trace_pre = np.zeros(n_pre, dtype=np.float64)
        self.trace_post = np.zeros(n_post, dtype=np.float64)
        self.trace_decay = 0.90

    def forward(self, pre_activity: np.ndarray) -> np.ndarray:
        """前向传播：计算突触后电流。

        Args:
            pre_activity: 前突触神经元活跃度向量

        Returns:
            突触后电流向量（mA）
        """
        return self.W @ pre_activity

    def update_soft(
        self,
        pre_activity: np.ndarray,
        post_activity: np.ndarray,
        reward: float = 0.0,
    ) -> None:
        """软 STDP 更新：用连续活跃度而非二值 spike。"""
        self.trace_pre = self.trace_pre * self.trace_decay + pre_activity
        self.trace_post = self.trace_post * self.trace_decay + post_activity

        reward_factor = 1.0 + np.clip(reward, -1.0, 1.0)

        pre_strength = float(np.sum(pre_activity))
        post_strength = float(np.sum(post_activity))

        if post_strength > 0.05:
            dw_plus = self.lr_plus * np.outer(post_activity, self.trace_pre)
            self.W += dw_plus * max(reward_factor, 0.1)

        if pre_strength > 0.05:
            dw_minus = -self.lr_minus * np.outer(self.trace_post, pre_activity)
            self.W += dw_minus * max(2.0 - reward_factor, 0.1)

        np.clip(self.W, self.w_min, self.w_max, out=self.W)

    # 向后兼容 v1 调用
    def update(
        self,
        pre_spikes: np.ndarray,
        post_spikes: np.ndarray,
        reward: float = 0.0,
    ) -> None:
        """兼容旧接口，内部转发到 update_soft。"""
        self.update_soft(pre_spikes.astype(np.float64), post_spikes.astype(np.float64), reward)

    def decay_traces(self) -> None:
        """纯衰减 tick 时只衰减 trace，不更新权重。

        用于零输入 tick，模拟突触痕迹的自然衰减。
        """
        self.trace_pre *= self.trace_decay
        self.trace_post *= self.trace_decay

    def get_weight_stats(self) -> dict[str, float]:
        """获取权重矩阵统计信息。

        Returns:
            包含权重均值、标准差、绝对值均值、最小/最大值、范数的字典
        """
        return {
            "w_mean": float(np.mean(self.W)),
            "w_std": float(np.std(self.W)),
            "w_abs_mean": float(np.mean(np.abs(self.W))),
            "w_min": float(np.min(self.W)),
            "w_max": float(np.max(self.W)),
            "w_norm": float(np.linalg.norm(self.W)),
        }


def _sigmoid(x: np.ndarray, center: float = 0.0, steepness: float = 8.0) -> np.ndarray:
    """Sigmoid 激活，将膜电位映射为 [0, 1] 连续活跃度。"""
    return 1.0 / (1.0 + np.exp(-steepness * (x - center)))


class DriveCoreNetwork:
    """SNN 驱动核网络 v2。

    结构: 输入层(8) -> 隐藏层(16 LIF) -> 输出层(6 LIF)

    输出维度：
        0 - arousal:           整体激活度
        1 - valence:           情感正负
        2 - social_drive:      社交冲动
        3 - task_drive:        推进任务冲动
        4 - exploration_drive: 探索冲动
        5 - rest_drive:        休息冲动
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
        """初始化 SNN 驱动核网络。

        构建三层网络结构：输入层(8) -> 隐藏层(16 LIF) -> 输出层(6 LIF)。
        输出维度对应6种驱动：arousal、valence、social_drive、task_drive、
        exploration_drive、rest_drive。
        """
        self.hidden = LIFNeuronGroup(self.HIDDEN_DIM, tau=12.0, threshold=0.15)
        self.output = LIFNeuronGroup(self.OUTPUT_DIM, tau=25.0, threshold=0.20)

        self.syn_in_hid = STDPSynapse(
            self.INPUT_DIM, self.HIDDEN_DIM, lr_plus=0.015, lr_minus=0.007
        )
        self.syn_hid_out = STDPSynapse(
            self.HIDDEN_DIM, self.OUTPUT_DIM, lr_plus=0.012, lr_minus=0.006
        )

        self._output_ema = np.zeros(self.OUTPUT_DIM, dtype=np.float64)
        self._ema_alpha = 0.15

        self._input_gain = 2.0
        self._hidden_spike_gain = 1.5
        self._hidden_cont_gain = 0.4
        self._noise_std = 0.08

        # 自稳态
        self._target_hidden_rate = 0.10
        self._target_output_rate = 0.06
        self._homeo_alpha = 0.03
        self._homeo_threshold_lr = 0.005
        self._homeo_gain_lr = 0.08
        self._hidden_rate_ema = 0.05
        self._output_rate_ema = 0.03

        # 运行时统计（动态离散化）
        self._output_running_mean = np.zeros(self.OUTPUT_DIM, dtype=np.float64)
        self._output_running_var = np.ones(self.OUTPUT_DIM, dtype=np.float64) * 0.01
        self._stats_alpha = 0.01

        self.tick_count: int = 0
        self._real_step_count: int = 0

    def decay_only(self) -> np.ndarray:
        """零输入衰减 tick：只泄漏膜电位和 trace，不学习。"""
        self.hidden.decay_only()
        self.output.decay_only()
        self.syn_in_hid.decay_traces()
        self.syn_hid_out.decay_traces()
        self.tick_count += 1
        return self._output_ema.copy()

    def step(self, input_vec: np.ndarray, reward: float = 0.0) -> np.ndarray:
        """真实输入步：前向传播 + 噪声 + 软 STDP 学习。"""
        if input_vec.shape != (self.INPUT_DIM,):
            input_vec = np.zeros(self.INPUT_DIM, dtype=np.float64)
        input_vec = np.clip(input_vec, -2.0, 2.0)

        # 前向 + 噪声
        input_scaled = input_vec * self._input_gain
        current_hidden = self.syn_in_hid.forward(input_scaled)
        noise = np.random.normal(0, self._noise_std, size=self.HIDDEN_DIM)
        current_hidden += noise
        input_strength = float(np.mean(np.abs(input_vec)))
        current_hidden += 0.08 * input_strength
        spikes_hidden = self.hidden.step(current_hidden)

        hidden_spike_signal = spikes_hidden.astype(np.float64) * self._hidden_spike_gain
        hidden_cont_signal = np.clip(self.hidden.get_state(), 0.0, 1.0)
        current_output = self.syn_hid_out.forward(hidden_spike_signal)
        current_output += self._hidden_cont_gain * self.syn_hid_out.forward(hidden_cont_signal)
        current_output += np.random.normal(0, self._noise_std * 0.5, size=self.OUTPUT_DIM)
        spikes_output = self.output.step(current_output)

        # 自稳态
        hidden_rate = float(np.mean(spikes_hidden.astype(np.float64)))
        output_rate = float(np.mean(spikes_output.astype(np.float64)))
        self._hidden_rate_ema = (1.0 - self._homeo_alpha) * self._hidden_rate_ema + self._homeo_alpha * hidden_rate
        self._output_rate_ema = (1.0 - self._homeo_alpha) * self._output_rate_ema + self._homeo_alpha * output_rate

        self.hidden.threshold += self._homeo_threshold_lr * (self._hidden_rate_ema - self._target_hidden_rate)
        self.output.threshold += self._homeo_threshold_lr * (self._output_rate_ema - self._target_output_rate)
        self._input_gain += self._homeo_gain_lr * (self._target_hidden_rate - self._hidden_rate_ema)
        self._hidden_spike_gain += self._homeo_gain_lr * (self._target_output_rate - self._output_rate_ema)

        self.hidden.threshold = float(np.clip(self.hidden.threshold, 0.05, 0.5))
        self.output.threshold = float(np.clip(self.output.threshold, 0.05, 0.5))
        self._input_gain = float(np.clip(self._input_gain, 0.8, 3.5))
        self._hidden_spike_gain = float(np.clip(self._hidden_spike_gain, 0.8, 3.5))
        self._hidden_cont_gain = float(np.clip(self._hidden_cont_gain, 0.15, 0.8))

        # EMA 输出
        raw_output = self.output.get_state()
        self._output_ema = (1 - self._ema_alpha) * self._output_ema + self._ema_alpha * raw_output

        # 更新运行时统计
        self._output_running_mean = (
            (1 - self._stats_alpha) * self._output_running_mean
            + self._stats_alpha * self._output_ema
        )
        diff = self._output_ema - self._output_running_mean
        self._output_running_var = (
            (1 - self._stats_alpha) * self._output_running_var
            + self._stats_alpha * (diff ** 2)
        )

        # 软 STDP
        hidden_v = self.hidden.get_state()
        output_v = self.output.get_state()
        soft_hidden = _sigmoid(hidden_v, center=self.hidden.threshold * 0.5, steepness=10.0)
        soft_output = _sigmoid(output_v, center=self.output.threshold * 0.5, steepness=10.0)
        input_activity = np.abs(input_vec) / max(float(np.max(np.abs(input_vec))), 0.01)

        self.syn_in_hid.update_soft(input_activity, soft_hidden, reward=reward)
        self.syn_hid_out.update_soft(soft_hidden, soft_output, reward=reward)

        self.tick_count += 1
        self._real_step_count += 1
        return self._output_ema.copy()

    def get_drive_dict(self) -> dict[str, float]:
        """获取当前驱动值的字典表示。

        Returns:
            驱动名称到 EMA 平滑值的映射字典
        """
        return {
            name: round(float(val), 4)
            for name, val in zip(self.OUTPUT_NAMES, self._output_ema)
        }

    def get_drive_discrete(self) -> dict[str, str]:
        """动态离散化：z-score。"""
        result: dict[str, str] = {}
        std = np.sqrt(np.maximum(self._output_running_var, 1e-6))
        for i, name in enumerate(self.OUTPUT_NAMES):
            z = (self._output_ema[i] - self._output_running_mean[i]) / std[i]
            if z > 1.0:
                level = "高"
            elif z > 0.3:
                level = "中"
            elif z > -0.5:
                level = "低"
            else:
                level = "抑制"
            result[name] = level
        return result

    def get_output_ema(self) -> np.ndarray:
        """获取输出层的 EMA 平滑值。

        Returns:
            6维驱动向量的副本
        """
        return self._output_ema.copy()

    def get_health(self) -> dict[str, Any]:
        """获取网络健康状态报告。

        Returns:
            包含 tick 计数、驱动值、膜电位统计、突触统计等信息的字典
        """
        return {
            "tick_count": self.tick_count,
            "real_step_count": self._real_step_count,
            "drives": self.get_drive_dict(),
            "drives_discrete": self.get_drive_discrete(),
            "hidden_v_mean": round(float(np.mean(self.hidden.v)), 4),
            "hidden_v_std": round(float(np.std(self.hidden.v)), 4),
            "output_v_mean": round(float(np.mean(self.output.v)), 4),
            "output_v_std": round(float(np.std(self.output.v)), 4),
            "hidden_threshold": round(float(self.hidden.threshold), 4),
            "output_threshold": round(float(self.output.threshold), 4),
            "input_gain": round(float(self._input_gain), 4),
            "hidden_spike_gain": round(float(self._hidden_spike_gain), 4),
            "hidden_cont_gain": round(float(self._hidden_cont_gain), 4),
            "hidden_rate_ema": round(float(self._hidden_rate_ema), 4),
            "output_rate_ema": round(float(self._output_rate_ema), 4),
            "output_running_mean": [round(float(v), 4) for v in self._output_running_mean],
            "output_running_std": [round(float(np.sqrt(max(v, 0))), 4) for v in self._output_running_var],
            "noise_std": round(self._noise_std, 4),
            "syn_in_hid": self.syn_in_hid.get_weight_stats(),
            "syn_hid_out": self.syn_hid_out.get_weight_stats(),
        }

    # ── 做梦系统接口 ────────────────────────────────────────

    def replay_episodes(
        self,
        features_list: list[np.ndarray],
        speed_multiplier: float = 5.0,
        reward_signal: float = 0.0,
    ) -> dict[str, Any]:
        """NREM 回放：以加速时间常数重放特征序列，执行 STDP 学习。

        与 step() 的区别：
        - 使用缩短的 tau（加速回放）
        - 不更新 EMA / 运行时统计（不影响实时驱动输出）
        - 不执行自稳态阈值调整

        Returns:
            {"steps": int, "hidden_spikes": int, "output_spikes": int}
        """
        # 缩短 tau 实现加速回放
        orig_hidden_tau = self.hidden.tau
        orig_output_tau = self.output.tau
        self.hidden.tau = max(orig_hidden_tau / speed_multiplier, 1.0)
        self.output.tau = max(orig_output_tau / speed_multiplier, 1.0)

        total_hidden_spikes = 0
        total_output_spikes = 0
        steps = 0

        try:
            for feat in features_list:
                if feat.shape != (self.INPUT_DIM,):
                    feat = np.zeros(self.INPUT_DIM, dtype=np.float64)
                feat = np.clip(feat, -2.0, 2.0)

                input_scaled = feat * self._input_gain
                current_hidden = self.syn_in_hid.forward(input_scaled)
                noise = np.random.normal(0, self._noise_std * 0.5, size=self.HIDDEN_DIM)
                current_hidden += noise
                spikes_hidden = self.hidden.step(current_hidden)

                hidden_spike_signal = spikes_hidden.astype(np.float64) * self._hidden_spike_gain
                current_output = self.syn_hid_out.forward(hidden_spike_signal)
                spikes_output = self.output.step(current_output)

                # 软 STDP（与 step() 相同的学习规则）
                hidden_v = self.hidden.get_state()
                output_v = self.output.get_state()
                soft_hidden = _sigmoid(hidden_v, center=self.hidden.threshold * 0.5, steepness=10.0)
                soft_output = _sigmoid(output_v, center=self.output.threshold * 0.5, steepness=10.0)
                input_activity = np.abs(feat) / max(float(np.max(np.abs(feat))), 0.01)

                self.syn_in_hid.update_soft(input_activity, soft_hidden, reward=reward_signal)
                self.syn_hid_out.update_soft(soft_hidden, soft_output, reward=reward_signal)

                total_hidden_spikes += int(np.sum(spikes_hidden))
                total_output_spikes += int(np.sum(spikes_output))
                steps += 1
        finally:
            # 恢复原始 tau
            self.hidden.tau = orig_hidden_tau
            self.output.tau = orig_output_tau

        return {
            "steps": steps,
            "hidden_spikes": total_hidden_spikes,
            "output_spikes": total_output_spikes,
        }

    def homeostatic_scaling(self, rate: float = 0.02) -> dict[str, Any]:
        """SHY (突触稳态假说)：全局等比例缩减突触权重。

        模拟慢波睡眠中的突触下调：
        - 弱连接衰减至更弱（被清理）
        - 强连接相对保留（信噪比提升）

        Args:
            rate: 缩减比例，0.02 意味着所有权重绝对值缩减 2%。

        Returns:
            {"before": weight_stats, "after": weight_stats, "scale_factor": float}
        """
        scale = 1.0 - np.clip(rate, 0.0, 0.1)
        before = {
            "syn_in_hid": self.syn_in_hid.get_weight_stats(),
            "syn_hid_out": self.syn_hid_out.get_weight_stats(),
        }

        self.syn_in_hid.W *= scale
        self.syn_hid_out.W *= scale
        np.clip(self.syn_in_hid.W, self.syn_in_hid.w_min, self.syn_in_hid.w_max, out=self.syn_in_hid.W)
        np.clip(self.syn_hid_out.W, self.syn_hid_out.w_min, self.syn_hid_out.w_max, out=self.syn_hid_out.W)

        after = {
            "syn_in_hid": self.syn_in_hid.get_weight_stats(),
            "syn_hid_out": self.syn_hid_out.get_weight_stats(),
        }

        logger.info(
            f"SHY 突触缩减完成: scale={scale:.4f} | "
            f"in_hid norm {before['syn_in_hid']['w_norm']:.4f} → {after['syn_in_hid']['w_norm']:.4f} | "
            f"hid_out norm {before['syn_hid_out']['w_norm']:.4f} → {after['syn_hid_out']['w_norm']:.4f}"
        )

        return {"before": before, "after": after, "scale_factor": scale}

    def serialize(self) -> dict[str, Any]:
        """序列化网络状态用于持久化存储。

        Returns:
            包含所有可恢复状态的字典（膜电位、权重、统计量等）
        """
        return {
            "version": 2,
            "hidden_v": self.hidden.v.tolist(),
            "output_v": self.output.v.tolist(),
            "output_ema": self._output_ema.tolist(),
            "syn_in_hid_W": self.syn_in_hid.W.tolist(),
            "syn_in_hid_trace_pre": self.syn_in_hid.trace_pre.tolist(),
            "syn_in_hid_trace_post": self.syn_in_hid.trace_post.tolist(),
            "syn_hid_out_W": self.syn_hid_out.W.tolist(),
            "syn_hid_out_trace_pre": self.syn_hid_out.trace_pre.tolist(),
            "syn_hid_out_trace_post": self.syn_hid_out.trace_post.tolist(),
            "hidden_threshold": float(self.hidden.threshold),
            "output_threshold": float(self.output.threshold),
            "input_gain": float(self._input_gain),
            "hidden_spike_gain": float(self._hidden_spike_gain),
            "hidden_cont_gain": float(self._hidden_cont_gain),
            "hidden_rate_ema": float(self._hidden_rate_ema),
            "output_rate_ema": float(self._output_rate_ema),
            "tick_count": self.tick_count,
            "real_step_count": self._real_step_count,
            "output_running_mean": self._output_running_mean.tolist(),
            "output_running_var": self._output_running_var.tolist(),
        }

    def deserialize(self, data: dict[str, Any]) -> None:
        """从字典恢复网络状态。

        Args:
            data: serialize() 生成的状态字典

        注意：状态恢复失败时会记录错误日志，但不会抛出异常。
        """
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
            for key, arr, attr_path in [
                ("syn_in_hid_trace_pre", self.syn_in_hid.trace_pre, "syn_in_hid.trace_pre"),
                ("syn_in_hid_trace_post", self.syn_in_hid.trace_post, "syn_in_hid.trace_post"),
                ("syn_hid_out_trace_pre", self.syn_hid_out.trace_pre, "syn_hid_out.trace_pre"),
                ("syn_hid_out_trace_post", self.syn_hid_out.trace_post, "syn_hid_out.trace_post"),
            ]:
                if key in data:
                    t = np.array(data[key], dtype=np.float64)
                    if t.shape == arr.shape:
                        parts = attr_path.split(".")
                        setattr(getattr(self, parts[0]), parts[1], t)

            if "tick_count" in data:
                self.tick_count = int(data["tick_count"])
            if "real_step_count" in data:
                self._real_step_count = int(data["real_step_count"])
            if "hidden_threshold" in data:
                self.hidden.threshold = float(data["hidden_threshold"])
            if "output_threshold" in data:
                self.output.threshold = float(data["output_threshold"])
            if "input_gain" in data:
                self._input_gain = float(data["input_gain"])
            if "hidden_spike_gain" in data:
                self._hidden_spike_gain = float(data["hidden_spike_gain"])
            if "hidden_cont_gain" in data:
                self._hidden_cont_gain = float(data["hidden_cont_gain"])
            if "hidden_rate_ema" in data:
                self._hidden_rate_ema = float(data["hidden_rate_ema"])
            if "output_rate_ema" in data:
                self._output_rate_ema = float(data["output_rate_ema"])
            if "output_running_mean" in data:
                m = np.array(data["output_running_mean"], dtype=np.float64)
                if m.shape == self._output_running_mean.shape:
                    self._output_running_mean = m
            if "output_running_var" in data:
                v = np.array(data["output_running_var"], dtype=np.float64)
                if v.shape == self._output_running_var.shape:
                    self._output_running_var = v

            self.hidden.threshold = float(np.clip(self.hidden.threshold, 0.05, 0.5))
            self.output.threshold = float(np.clip(self.output.threshold, 0.05, 0.5))
            self._input_gain = float(np.clip(self._input_gain, 0.8, 3.5))
            self._hidden_spike_gain = float(np.clip(self._hidden_spike_gain, 0.8, 3.5))
            self._hidden_cont_gain = float(np.clip(self._hidden_cont_gain, 0.15, 0.8))

            logger.info(f"SNN v2 状态反序列化成功，tick={self.tick_count}, real_steps={self._real_step_count}")
        except Exception as e:
            logger.error(f"SNN 状态反序列化失败: {e}")
