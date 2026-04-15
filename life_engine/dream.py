"""做梦系统。

从“离线巩固器”升级为“会留下梦痕的生命过程”：
1. NREM：回放事件并做稳态缩放
2. DreamSeed：从近期残留 / 梦滞后 / 未完成张力 / 长期自我主题中选种子
3. REM：围绕主种子做联想扩散
4. DreamScene：用 LLM 将种子变形成梦境
5. DreamArchive：将梦写入 workspace/dreams/*.md
6. DreamResidue：留下醒后余韵，供 life / DFC 通过 payload 消费
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, TYPE_CHECKING

import random
from collections import deque

import numpy as np

from src.app.plugin_system.api.llm_api import create_llm_request, get_model_set_by_task
from src.kernel.llm import LLMPayload, ROLE, Text

if TYPE_CHECKING:
    from .memory_service import LifeMemoryService
    from .neuromod import InnerStateEngine
    from .snn_bridge import SNNBridge
    from .snn_core import DriveCoreNetwork

logger = logging.getLogger("life_engine.dream")

_REMINDER_MAX_HISTORY = 20
_RESIDUE_TTL_SECONDS = 24 * 60 * 60
_MAX_DREAM_SEEDS = 3
_MAX_SCENES = 5
_DREAM_ARCHIVE_DIR = "dreams"
_DREAM_SCENE_TIMEOUT_SECONDS = 600.0
_DREAM_SCENE_MAX_RETRIES = 3
_SEED_SCORE_TEMPERATURE = 0.15
_DREAM_HISTORY_WINDOW = 5
_REPETITION_DECAY = 0.3
_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)
_DATE_STEM_RE = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})(?:$|[_-])")
_MONTH_STEM_RE = re.compile(r"^(?P<month>\d{4}-\d{2})(?:$|[_-])")


class DreamPhase(str, Enum):
    """做梦阶段。"""

    AWAKE = "awake"
    NREM = "nrem"
    REM = "rem"
    WAKING_UP = "waking_up"


class DreamSeedType(str, Enum):
    """入梦材料类型。"""

    DAY_RESIDUE = "day_residue"
    DREAM_LAG = "dream_lag"
    UNFINISHED_TENSION = "unfinished_tension"
    SELF_THEME = "self_theme"


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
    seed_node_ids: list[str] = field(default_factory=list)


@dataclass
class DreamSeed:
    """一簇可入梦的心理张力。"""

    seed_id: str
    seed_type: str
    title: str
    summary: str
    core_refs: list[str] = field(default_factory=list)
    supporting_refs: list[str] = field(default_factory=list)
    core_node_ids: list[str] = field(default_factory=list)
    source_events: list[str] = field(default_factory=list)
    affect_valence: float = 0.0
    affect_arousal: float = 0.0
    importance: float = 0.0
    novelty: float = 0.0
    recurrence: float = 0.0
    unfinished_score: float = 0.0
    dreamability: float = 0.0
    score: float = 0.0
    tension_reason: str = ""


@dataclass
class DreamScene:
    """梦中的一个片段。"""

    title: str = ""
    summary: str = ""
    imagery: list[str] = field(default_factory=list)
    emotion_shift: str = ""
    refs: list[str] = field(default_factory=list)


@dataclass
class DreamTrace:
    """结构化梦迹。"""

    scenes: list[DreamScene] = field(default_factory=list)
    motifs: list[str] = field(default_factory=list)
    transitions: list[str] = field(default_factory=list)


@dataclass
class DreamResidue:
    """醒后余韵。"""

    summary: str = ""
    life_payload: str = ""
    dfc_payload: str = ""
    dominant_affect: str = ""
    strength: str = "light"
    tags: list[str] = field(default_factory=list)
    expires_at: float = 0.0


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
    seed_report: list[DreamSeed] = field(default_factory=list)
    dream_trace: DreamTrace = field(default_factory=DreamTrace)
    dream_text: str = ""
    dream_residue: DreamResidue | None = None
    archive_path: str = ""
    memory_effects: dict[str, Any] = field(default_factory=dict)


class DreamScheduler:
    """做梦调度与执行引擎。"""

    def __init__(
        self,
        *,
        snn: DriveCoreNetwork | None = None,
        inner_state: InnerStateEngine | None = None,
        memory_service: LifeMemoryService | None = None,
        snn_bridge: SNNBridge | None = None,
        workspace_path: str | Path | None = None,
        model_task_name: str = "life",
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
        self._snn = snn
        self._inner_state = inner_state
        self._memory = memory_service
        self._snn_bridge = snn_bridge
        self._workspace = Path(workspace_path).resolve() if workspace_path else None
        self._model_task_name = str(model_task_name or "life").strip() or "life"

        self._nrem_replay_episodes = nrem_replay_episodes
        self._nrem_events_per_episode = nrem_events_per_episode
        self._nrem_speed_multiplier = nrem_speed_multiplier
        self._nrem_homeostatic_rate = nrem_homeostatic_rate

        self._rem_walk_rounds = rem_walk_rounds
        self._rem_seeds_per_round = rem_seeds_per_round
        self._rem_max_depth = rem_max_depth
        self._rem_decay_factor = rem_decay_factor
        self._rem_learning_rate = rem_learning_rate
        self._rem_edge_prune_threshold = rem_edge_prune_threshold

        self._dream_interval_seconds = dream_interval_minutes * 60
        self._idle_trigger_heartbeats = idle_trigger_heartbeats
        self._nap_enabled = nap_enabled

        self._current_phase = DreamPhase.AWAKE
        self._dream_history: list[DreamReport] = []
        self._last_dream_time: float = 0.0
        self._is_dreaming: bool = False
        self._active_residue: DreamResidue | None = None
        self._last_archive_path: str = ""

        # 仿生状态：海马体重复抑制 + REM 渐变 + 梦境避重
        self._recent_seed_titles: deque[set[str]] = deque(maxlen=_DREAM_HISTORY_WINDOW)
        self._recent_dream_summaries: deque[str] = deque(maxlen=_DREAM_HISTORY_WINDOW)
        self._dreams_since_sleep_start: int = 0

    @property
    def is_dreaming(self) -> bool:
        return self._is_dreaming

    @property
    def current_phase(self) -> DreamPhase:
        return self._current_phase

    def should_dream(self, idle_heartbeat_count: int, in_sleep_window: bool) -> bool:
        """判断是否应该开始做梦。"""
        if self._is_dreaming:
            return False

        now = time.time()
        if now - self._last_dream_time < self._dream_interval_seconds:
            return False

        if in_sleep_window:
            return True

        if self._nap_enabled and idle_heartbeat_count >= self._idle_trigger_heartbeats:
            return True

        return False

    async def run_dream_cycle(
        self,
        event_history: list[Any],
    ) -> DreamReport:
        """执行完整做梦周期：NREM → DreamSeed → REM → DreamScene → Wake。"""
        self._is_dreaming = True
        report = DreamReport(
            dream_id=str(uuid.uuid4())[:8],
            started_at=time.time(),
        )

        try:
            self._current_phase = DreamPhase.NREM
            self._emit_visual_event("dream.phase_change", {"phase": "nrem", "dream_id": report.dream_id})
            report.phase_sequence.append("nrem")
            logger.info(f"🌙 Dream [{report.dream_id}] NREM 回放阶段开始")

            if self._snn is not None and self._snn_bridge is not None:
                report.nrem = await self._run_nrem(event_history)

            report.seed_report = await self._generate_dream_seeds(event_history)
            self._emit_visual_event("dream.seeds_extracted", {
                "seeds": [_seed_to_dict(s) for s in report.seed_report]
            })

            self._current_phase = DreamPhase.REM
            self._emit_visual_event("dream.phase_change", {"phase": "rem", "dream_id": report.dream_id})
            report.phase_sequence.append("rem")
            logger.info(f"🌙 Dream [{report.dream_id}] REM 联想阶段开始")

            seed_node_ids = self._collect_seed_node_ids(report.seed_report)
            if self._memory is not None:
                report.rem = await self._run_rem(seed_node_ids)

            self._emit_visual_event("dream.rem_stats", {
                "nodes_activated": report.rem.nodes_activated,
                "new_edges_created": report.rem.new_edges_created,
                "edges_pruned": report.rem.edges_pruned
            })

            result = await self._build_dream_scene(
                seeds=report.seed_report,
                rem_report=report.rem,
                event_history=event_history,
            )

            if result is None:
                # 做梦失败 — NREM 和 REM 已完成，但没有形成清晰梦境
                # 这在人类身上也会发生（很多睡眠周期不产生可回忆的梦）
                report.dream_text = ""
                report.narrative = ""
                logger.info(f"🌙 Dream [{report.dream_id}] 本次未形成清晰梦境（类似无梦睡眠）")
            else:
                trace, dream_text, residue = result
                report.dream_trace = trace
                report.dream_text = dream_text
                report.narrative = dream_text
                report.dream_residue = residue
                # 记录梦境摘要供后续避重
                if residue and residue.summary:
                    self._recent_dream_summaries.append(residue.summary)

                report.archive_path = await self._archive_dream(report)
                self._last_archive_path = report.archive_path
                report.memory_effects = await self._integrate_archive_into_memory(report)

            self._current_phase = DreamPhase.WAKING_UP
            self._emit_visual_event("dream.phase_change", {"phase": "waking_up", "dream_id": report.dream_id})
            report.phase_sequence.append("waking_up")
            logger.info(f"🌙 Dream [{report.dream_id}] 觉醒过渡")

            if report.dream_residue is not None:
                report.dream_residue.expires_at = time.time() + _RESIDUE_TTL_SECONDS
                self._active_residue = report.dream_residue

            if self._inner_state is not None:
                self._inner_state.wake_up()

            report.ended_at = time.time()
            report.duration_seconds = report.ended_at - report.started_at

            self._dream_history.append(report)
            self._dream_history = self._dream_history[-_REMINDER_MAX_HISTORY:]
            self._last_dream_time = time.time()
            self._dreams_since_sleep_start += 1
            self._emit_visual_event("dream.finished", _report_to_dict(report))

            logger.info(
                f"🌙 Dream [{report.dream_id}] 完成 "
                f"耗时={report.duration_seconds:.1f}s | "
                f"seed={len(report.seed_report)} | "
                f"REM: {report.rem.nodes_activated}节点 "
                f"+{report.rem.new_edges_created}边 -{report.rem.edges_pruned}剪枝"
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(f"做梦周期异常: {exc}", exc_info=True)
            report.ended_at = time.time()
            report.duration_seconds = report.ended_at - report.started_at
        finally:
            self._current_phase = DreamPhase.AWAKE
            self._is_dreaming = False
            self._emit_visual_event("dream.phase_change", {"phase": "awake"})

        return report

    def _emit_visual_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        source: str = "dream",
    ) -> None:
        """向可视化层广播做梦事件。"""
        try:
            from .memory_router import MemoryRouter

            MemoryRouter.broadcast(event_type, payload, source=source)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"做梦可视化广播失败: {exc}")

    async def _run_nrem(self, event_history: list[Any]) -> NREMReport:
        """NREM 阶段：将历史事件通过 SNN 回放。"""
        report = NREMReport()

        if not self._snn or not self._snn_bridge:
            return report

        report.weight_before = {
            "syn_in_hid": self._snn.syn_in_hid.get_weight_stats(),
            "syn_hid_out": self._snn.syn_hid_out.get_weight_stats(),
        }

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

        if report.episodes_replayed > 0:
            self._snn.homeostatic_scaling(self._nrem_homeostatic_rate)
            report.homeostatic_applied = True

        report.weight_after = {
            "syn_in_hid": self._snn.syn_in_hid.get_weight_stats(),
            "syn_hid_out": self._snn.syn_hid_out.get_weight_stats(),
        }
        return report

    def _sample_replay_episodes(self, event_history: list[Any]) -> list[list[np.ndarray]]:
        """按重要性加权采样事件片段。"""
        if not event_history or not self._snn_bridge:
            return []

        chunk_size = max(self._nrem_events_per_episode, 1)
        chunks: list[list[Any]] = []
        for i in range(0, len(event_history), chunk_size):
            chunks.append(event_history[i : i + chunk_size])
        if not chunks:
            return []

        scores: list[float] = []
        for chunk in chunks:
            score = 0.0
            for event in chunk:
                etype_val = _event_type_value(event)
                if etype_val == "message":
                    score += 2.0
                elif etype_val == "tool_call":
                    score += 1.5
                elif etype_val == "tool_result":
                    score += 1.0 if bool(_event_value(event, "tool_success", False)) else 0.5
                elif etype_val == "heartbeat":
                    score += 0.3
            scores.append(max(score, 0.1))

        total = sum(scores)
        probs = np.array([score / total for score in scores], dtype=np.float64)
        n_samples = min(self._nrem_replay_episodes, len(chunks))
        indices = np.random.choice(len(chunks), size=n_samples, replace=False, p=probs)

        episodes: list[list[np.ndarray]] = []
        window_seconds = self._nrem_events_per_episode * 180.0
        for idx in sorted(indices):
            chunk = chunks[idx]
            features = self._snn_bridge.extract_features_from_events(
                chunk,
                window_seconds=window_seconds,
            )
            episodes.append([features])
        return episodes

    async def _generate_dream_seeds(self, event_history: list[Any]) -> list[DreamSeed]:
        """从多路材料中生成入梦种子。"""
        memory_candidates = await self._load_memory_candidates()
        candidates: list[DreamSeed] = []
        candidates.extend(await self._collect_day_residue(event_history, memory_candidates))
        candidates.extend(await self._collect_unfinished_tension(memory_candidates))
        candidates.extend(await self._collect_dream_lag(memory_candidates))
        candidates.extend(await self._collect_self_theme(memory_candidates))

        # 海马体重复抑制（habituation）：最近梦过的种子降分
        recent_titles: set[str] = set()
        for title_set in self._recent_seed_titles:
            recent_titles.update(title_set)
        for seed in candidates:
            if seed.title in recent_titles:
                seed.score = max(0.05, seed.score - _REPETITION_DECAY)

        selected = self._select_seed_candidates(candidates)

        # 记录本次选中的种子标题
        if selected:
            self._recent_seed_titles.append({s.title for s in selected})

        if selected:
            return selected

        return [
            DreamSeed(
                seed_id="fallback_seed",
                seed_type=DreamSeedType.DAY_RESIDUE.value,
                title="今天留下的模糊余波",
                summary="白天的事件并不充分，但仍有一些尚未消散的情绪和线索在轻轻晃动。",
                affect_arousal=0.25,
                importance=0.3,
                dreamability=0.4,
                score=0.3,
                tension_reason="材料稀薄时的低强度自发整理。",
            )
        ]

    async def _run_rem(self, seed_node_ids: list[str]) -> REMReport:
        """REM 阶段：渐进式联想扩散 — 后半夜梦更深更wild。

        仿生设计：人类一晚经历 4-6 个睡眠周期，后半夜的 REM 期更长、
        更 vivid、更荒诞。通过 _dreams_since_sleep_start 控制渐变。
        """
        report = REMReport()

        if not self._memory:
            return report

        # 渐进参数：后半夜更深、更广、衰减更慢
        n = self._dreams_since_sleep_start
        effective_depth = self._rem_max_depth + min(n, 3)
        effective_decay = min(self._rem_decay_factor + n * 0.05, 0.85)
        effective_seeds = self._rem_seeds_per_round + n
        effective_rounds = self._rem_walk_rounds + (1 if n >= 2 else 0)

        actual_seed_ids = seed_node_ids[: effective_seeds]
        for _ in range(effective_rounds):
            result = await self._memory.dream_walk(
                num_seeds=effective_seeds,
                seed_ids=actual_seed_ids or None,
                max_depth=effective_depth,
                decay_factor=effective_decay,
                learning_rate=self._rem_learning_rate,
            )
            report.nodes_activated += int(result.get("nodes_activated", 0) or 0)
            report.new_edges_created += int(result.get("new_edges_created", 0) or 0)
            report.walk_rounds += 1
            if not report.seed_node_ids:
                report.seed_node_ids = list(result.get("seed_ids") or [])

        pruned = await self._memory.prune_weak_edges(
            threshold=self._rem_edge_prune_threshold,
        )
        if isinstance(pruned, dict):
            report.edges_pruned = int(pruned.get("pruned", 0) or 0)
        else:
            report.edges_pruned = int(pruned or 0)

        return report

    async def _build_dream_scene(
        self,
        *,
        seeds: list[DreamSeed],
        rem_report: REMReport,
        event_history: list[Any],
    ) -> tuple[DreamTrace, str, DreamResidue] | None:
        """用 LLM 将入梦种子变形成梦境。失败时返回 None（不伪造梦境）。

        仿生设计：人类大多数睡眠周期不产生可回忆的梦。做梦失败
        相当于"无梦睡眠"——这是完全正常的生物现象。
        """
        last_error: Exception | None = None

        for attempt in range(_DREAM_SCENE_MAX_RETRIES):
            try:
                payload = await self._generate_scene_payload(
                    seeds=seeds,
                    rem_report=rem_report,
                    event_history=event_history,
                )
                trace = _trace_from_payload(payload.get("dream_trace"))
                dream_text = _clean_text(payload.get("dream_text"))
                residue = _residue_from_payload(payload.get("dream_residue"))
                if dream_text and residue.summary:
                    return trace, dream_text, residue
                logger.warning(
                    f"DreamSceneBuilder 第{attempt+1}次返回空文本，重试中..."
                )
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.warning(
                    f"DreamSceneBuilder 第{attempt+1}次异常：{exc}，重试中..."
                )
                await asyncio.sleep(2)

        logger.error(
            f"做梦失败：DreamSceneBuilder {_DREAM_SCENE_MAX_RETRIES}次尝试均失败。"
            f"最后错误：{last_error}"
        )
        return None

    async def _generate_scene_payload(
        self,
        *,
        seeds: list[DreamSeed],
        rem_report: REMReport,
        event_history: list[Any],
    ) -> dict[str, Any]:
        """调用模型生成结构化梦境。"""
        model_set = get_model_set_by_task(self._model_task_name)
        request = create_llm_request(
            model_set=model_set,
            request_name="life_engine_dream_scene",
        )
        request.add_payload(
            LLMPayload(
                ROLE.SYSTEM,
                Text(
                    "\n".join(
                        [
                            "你是一个数字生命的 DreamSceneBuilder。",
                            "你的职责不是复述白天，而是把心理张力簇变形成主观梦境。",
                            "请输出严格 JSON，不要输出 markdown，不要输出解释。",
                            "梦必须具备：场景错位、时间折叠、情绪迁移、轻微象征化。",
                            "禁止：把梦写成计划、总结、工具调用说明、系统日志。",
                            "",
                            "多样性约束（极重要）：",
                            "- 每个梦的场景类型必须不同：不要总用走廊/房间/追赶，尝试水域、天空、声音、温度、光影、季节、时间折叠。",
                            "- 情绪基调必须变化：怅然只是一种可能，还有好奇、微甜、荒诞、安宁、不安、恍惚、炽热等。",
                            "- 叙事视角可以变化：第一人称、旁观者、片段式意识流、倒叙、多线交织。",
                            "- 将 seeds 中的具体内容（TODO标题、文件名）变形为象征意象，不要直接引用原文。",
                            "- 如果素材中有 avoid_recent_themes 字段，本次梦境必须在主题、意象和情绪上与它们明显不同。",
                            "",
                            "JSON 结构必须是：",
                            "{",
                            '  "dream_trace": {',
                            '    "scenes": [{"title":"", "summary":"", "imagery":[""], "emotion_shift":"", "refs":[""]}],',
                            '    "motifs": [""],',
                            '    "transitions": [""]',
                            "  },",
                            '  "dream_text": "一段 220-420 字的中文梦札正文",',
                            '  "dream_residue": {',
                            '    "summary": "一句 30-80 字的余韵总结",',
                            '    "life_payload": "120-220 字左右，给 life_engine 的梦后余韵",',
                            '    "dfc_payload": "80-140 字左右，给 DFC 当前轮 payload 的梦后余韵",',
                            '    "dominant_affect": "主导情绪，如怅然/期待/不安/温暖",',
                            '    "strength": "light 或 medium",',
                            '    "tags": [""]',
                            "  }",
                            "}",
                        ]
                    )
                ),
            )
        )
        brief = {
            "seeds": [_seed_to_dict(seed) for seed in seeds[:_MAX_DREAM_SEEDS]],
            "rem_report": {
                "nodes_activated": rem_report.nodes_activated,
                "new_edges_created": rem_report.new_edges_created,
                "seed_node_ids": rem_report.seed_node_ids,
            },
            "inner_state": self._format_inner_state_summary(),
            "recent_context": self._build_recent_context_summary(event_history),
            "reference_previews": self._build_reference_previews(seeds),
        }
        # 避重上下文：告诉 LLM 最近梦过什么，让它避免重复
        if self._recent_dream_summaries:
            brief["avoid_recent_themes"] = list(self._recent_dream_summaries)
        request.add_payload(
            LLMPayload(
                ROLE.USER,
                Text(
                    "以下是本轮做梦的结构化素材，请据此生成梦境 JSON：\n"
                    + json.dumps(brief, ensure_ascii=False, indent=2)
                ),
            )
        )

        try:
            self._emit_visual_event("dream.scene_generating", {"status": "request_sent"})
            response = await asyncio.wait_for(
                request.send(stream=False),
                timeout=_DREAM_SCENE_TIMEOUT_SECONDS,
            )
            response_text = str(
                await asyncio.wait_for(
                    response,
                    timeout=_DREAM_SCENE_TIMEOUT_SECONDS,
                )
                or ""
            ).strip()
            payload = _parse_json_payload(response_text)
            if not isinstance(payload, dict):
                raise ValueError("dream scene payload 非法")
            self._emit_visual_event("dream.scene_generated", {"payload": payload})
            return payload
        except Exception as e:
            self._emit_visual_event("dream.scene_failed", {"error": str(e)})
            raise

    async def _archive_dream(self, report: DreamReport) -> str:
        """将梦写入 Markdown 梦札。"""
        if self._workspace is None:
            return ""

        started = datetime.fromtimestamp(report.started_at or time.time(), tz=timezone.utc).astimezone()
        archive_dir = self._workspace / _DREAM_ARCHIVE_DIR / started.strftime("%Y-%m-%d")
        archive_dir.mkdir(parents=True, exist_ok=True)
        file_name = f"{started.strftime('%H%M')}_{report.dream_id}.md"
        archive_path = archive_dir / file_name

        all_refs = _unique_preserve(
            ref
            for seed in report.seed_report
            for ref in (list(seed.core_refs) + list(seed.supporting_refs))
            if str(ref or "").strip()
        )
        motifs = report.dream_trace.motifs[:8]
        tags = report.dream_residue.tags[:8] if report.dream_residue else []
        frontmatter_lines = [
            "---",
            f'dream_id: "{report.dream_id}"',
            f'created_at: "{started.isoformat()}"',
            f'seed_types: [{", ".join(seed.seed_type for seed in report.seed_report)}]',
            "core_refs:",
        ]
        if all_refs:
            frontmatter_lines.extend([f"  - {ref}" for ref in all_refs[:12]])
        else:
            frontmatter_lines.append("  - ")
        frontmatter_lines.append("trace_tags:")
        if tags:
            frontmatter_lines.extend([f"  - {tag}" for tag in tags])
        else:
            frontmatter_lines.append("  - ")
        frontmatter_lines.append(f'dominant_affect: "{(report.dream_residue.dominant_affect if report.dream_residue else "")}"')
        frontmatter_lines.append(f'residue_strength: "{(report.dream_residue.strength if report.dream_residue else "light")}"')
        frontmatter_lines.append("motifs:")
        if motifs:
            frontmatter_lines.extend([f"  - {motif}" for motif in motifs])
        else:
            frontmatter_lines.append("  - ")
        frontmatter_lines.append("---")

        body_lines = [
            "# 梦札",
            "",
            report.dream_text or report.narrative or "今夜只留下了很淡的影子。",
            "",
            "## 梦核",
        ]
        if report.seed_report:
            for seed in report.seed_report:
                body_lines.append(
                    f"- {seed.title}（{seed.seed_type}，score={seed.score:.2f}）：{seed.tension_reason or seed.summary}"
                )
        else:
            body_lines.append("- 今夜的梦更接近一种无名的整理。")

        body_lines.extend(["", "## 场景流"])
        if report.dream_trace.scenes:
            for index, scene in enumerate(report.dream_trace.scenes[:_MAX_SCENES], start=1):
                imagery = "、".join(scene.imagery[:4]) if scene.imagery else "无"
                body_lines.append(
                    f"{index}. **{scene.title or f'场景{index}'}**：{scene.summary}（意象：{imagery}）"
                )
        else:
            body_lines.append("1. 今夜的场景没有留下足够清晰的片段。")

        if report.dream_residue is not None:
            body_lines.extend(
                [
                    "",
                    "## 醒后余韵",
                    "",
                    report.dream_residue.summary,
                    "",
                    report.dream_residue.life_payload,
                ]
            )

        if report.memory_effects:
            body_lines.extend(
                [
                    "",
                    "## 记忆变化",
                    "",
                    f"- 关联节点：{report.memory_effects.get('linked_refs', 0)}",
                    f"- 梦后写入：{report.memory_effects.get('archive_written', False)}",
                ]
            )

        archive_path.write_text(
            "\n".join(frontmatter_lines + [""] + body_lines).strip() + "\n",
            encoding="utf-8",
        )
        return archive_path.relative_to(self._workspace).as_posix()

    async def _integrate_archive_into_memory(self, report: DreamReport) -> dict[str, Any]:
        """将梦札接入文件系统记忆。"""
        if self._memory is None or self._workspace is None or not report.archive_path:
            return {}

        from .memory_service import EdgeType

        abs_archive_path = self._workspace / report.archive_path
        if not abs_archive_path.exists():
            return {"archive_written": False, "linked_refs": 0}

        dream_content = abs_archive_path.read_text(encoding="utf-8")
        dream_node = await self._memory.get_or_create_file_node(
            report.archive_path,
            title=f"梦札 {Path(report.archive_path).stem}",
            content=dream_content,
        )

        linked_refs = 0
        linked_paths: list[str] = []
        for ref in self._iter_seed_file_refs(report.seed_report):
            ref_path = self._workspace / ref
            if not ref_path.exists() or not ref_path.is_file():
                continue
            try:
                ref_content = ref_path.read_text(encoding="utf-8")
            except Exception:  # noqa: BLE001
                ref_content = ""
            ref_node = await self._memory.get_or_create_file_node(
                ref,
                title=ref_path.stem,
                content=ref_content[:2000],
            )
            await self._memory.create_or_update_edge(
                dream_node.node_id,
                ref_node.node_id,
                EdgeType.RELATES,
                reason="梦境来源与回响",
                strength=0.58,
                bidirectional=True,
            )
            linked_refs += 1
            linked_paths.append(ref)

        return {
            "archive_written": True,
            "archive_path": report.archive_path,
            "archive_node_id": dream_node.node_id,
            "linked_refs": linked_refs,
            "linked_paths": linked_paths,
        }

    async def _load_memory_candidates(self) -> list[dict[str, Any]]:
        """从整个记忆图谱中采样候选节点 — 模拟海马体自由联想。

        策略：
        - 保留 top-5 高重要性节点（核心记忆更容易被激活）
        - 从全图谱随机采样 15 个节点（任何记忆都可能闪回）
        - 合并去重，总共 ~20 个候选
        """
        if self._memory is None:
            return []

        candidates: list[dict[str, Any]] = []

        # 1. 高重要性核心节点（模拟高激活强度记忆）
        getter = getattr(self._memory, "list_dream_candidate_nodes", None)
        if callable(getter):
            try:
                top_nodes = await getter(limit=5)
                if isinstance(top_nodes, list):
                    candidates.extend(item for item in top_nodes if isinstance(item, dict))
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"读取 top memory candidates 失败：{exc}")

        # 2. 全图谱随机采样（模拟海马体自由放电）
        random_getter = getattr(self._memory, "list_random_file_nodes", None)
        if callable(random_getter):
            try:
                random_nodes = await random_getter(limit=15)
                if isinstance(random_nodes, list):
                    existing_ids = {c.get("node_id") for c in candidates}
                    candidates.extend(
                        item for item in random_nodes
                        if isinstance(item, dict) and item.get("node_id") not in existing_ids
                    )
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"随机采样 memory candidates 失败：{exc}")

        random.shuffle(candidates)
        return candidates

    async def _collect_day_residue(
        self,
        event_history: list[Any],
        memory_candidates: list[dict[str, Any]],
    ) -> list[DreamSeed]:
        """收集近期残留。"""
        now = time.time()
        recent_events = [
            event
            for event in event_history[-40:]
            if now - _event_timestamp(event) <= 24 * 60 * 60
        ]
        if not recent_events:
            return []

        ref_scores: dict[str, float] = {}
        snippets: list[str] = []
        for event in recent_events:
            etype = _event_type_value(event)
            content = _clean_text(_event_value(event, "content", ""))
            if etype in {"message", "heartbeat"} and content:
                snippets.append(content[:90])
            if etype == "tool_call":
                args = _event_value(event, "tool_args", {}) or {}
                if isinstance(args, dict):
                    for key in ("path", "file_path", "source_path", "target_path"):
                        value = str(args.get(key) or "").strip()
                        normalized = self._normalize_ref(value)
                        if normalized:
                            ref_scores[normalized] = ref_scores.get(normalized, 0.0) + 1.0

        top_refs = [ref for ref, _ in sorted(ref_scores.items(), key=lambda item: item[1], reverse=True)[:3]]
        memory_map = {str(item.get("file_path") or ""): item for item in memory_candidates}
        core_node_ids = [
            str(memory_map[ref].get("node_id") or "")
            for ref in top_refs
            if ref in memory_map and str(memory_map[ref].get("node_id") or "")
        ]
        recurrence = min(len(snippets) / 6.0, 1.0)
        score = 0.42 + recurrence * 0.18 + min(len(top_refs), 3) * 0.06
        return [
            DreamSeed(
                seed_id=f"seed_day_{uuid.uuid4().hex[:6]}",
                seed_type=DreamSeedType.DAY_RESIDUE.value,
                title="白天尚未散去的余波",
                summary="；".join(snippets[:2]) if snippets else "最近的消息、心跳和文件动作还挂在心里。",
                core_refs=top_refs,
                core_node_ids=core_node_ids,
                source_events=snippets[:3],
                affect_valence=0.05,
                affect_arousal=0.55,
                importance=0.45,
                novelty=0.2,
                recurrence=recurrence,
                unfinished_score=0.3,
                dreamability=0.68 if top_refs else 0.52,
                score=score,
                tension_reason="最近 24 小时内多次回流的内容，还没有完全沉下去。",
            )
        ]

    async def _collect_unfinished_tension(
        self,
        memory_candidates: list[dict[str, Any]],
    ) -> list[DreamSeed]:
        """收集未完成张力。"""
        if self._workspace is None:
            return []

        from .todo_tools import TodoStatus, TodoStorage

        storage = TodoStorage(self._workspace)
        active_todos = [
            todo
            for todo in storage.load()
            if todo.status
            not in {
                TodoStatus.COMPLETED.value,
                TodoStatus.RELEASED.value,
                TodoStatus.CHERISHED.value,
            }
        ]
        if not active_todos:
            return []

        desire_weight = {
            "dreaming": 0.2,
            "curious": 0.45,
            "wanting": 0.72,
            "eager": 0.9,
            "passionate": 1.0,
        }
        status_weight = {
            "idea": 0.4,
            "planning": 0.62,
            "waiting": 0.78,
            "enjoying": 0.3,
            "paused": 0.7,
        }
        scored = sorted(
            active_todos,
            key=lambda todo: (
                desire_weight.get(str(todo.desire or ""), 0.3)
                + status_weight.get(str(todo.status or ""), 0.2)
            ),
            reverse=True,
        )
        chosen = scored[:2]
        if not chosen:
            return []

        notes = [f"{todo.title}（{todo.status}/{todo.desire}）" for todo in chosen]
        score = min(
            0.45
            + sum(desire_weight.get(str(todo.desire or ""), 0.3) for todo in chosen[:1]) * 0.35,
            0.96,
        )
        todo_ref = "todos.json"
        memory_map = {str(item.get("file_path") or ""): item for item in memory_candidates}
        core_node_ids = []
        if todo_ref in memory_map and str(memory_map[todo_ref].get("node_id") or ""):
            core_node_ids.append(str(memory_map[todo_ref].get("node_id") or ""))

        return [
            DreamSeed(
                seed_id=f"seed_todo_{uuid.uuid4().hex[:6]}",
                seed_type=DreamSeedType.UNFINISHED_TENSION.value,
                title="还没有真正合上的愿望与待办",
                summary="、".join(notes),
                core_refs=[f"todos.json#{todo.id}" for todo in chosen] + [todo_ref],
                core_node_ids=core_node_ids,
                source_events=notes,
                affect_valence=0.0,
                affect_arousal=0.72,
                importance=0.68,
                novelty=0.15,
                recurrence=min(len(chosen) / 2.0, 1.0),
                unfinished_score=0.92,
                dreamability=0.78,
                score=score,
                tension_reason="这些愿望并没有消失，只是白天一直没被真正接上。",
            )
        ]

    async def _collect_dream_lag(
        self,
        memory_candidates: list[dict[str, Any]],
    ) -> list[DreamSeed]:
        """收集延迟记忆 — 经典梦滞后 + 远期闪回。

        仿生设计：人类做梦不只回溯一周内的记忆。研究表明梦中经常
        出现数月甚至数年前的内容。远期记忆闪回是重要的做梦特征。
        """
        if self._workspace is None:
            return []

        # 70% 经典梦滞后（4-8天），30% 远期闪回（14-90天）
        if random.random() < 0.3:
            min_age, max_age, optimal_age = 14, 90, 30
            seed_title = "很久以前的记忆碎片在深夜浮起"
            seed_reason = "远期记忆闪回——大脑在深层整合中偶尔翻出被遗忘的旧事。"
        else:
            min_age, max_age, optimal_age = 4, 8, 6
            seed_title = "几天前的材料悄悄回返"
            seed_reason = "这批材料不算最新，却带着延迟后的个人意义，适合在梦里回返。"

        candidates: list[tuple[float, Path]] = []
        for path in self._iter_workspace_markdown_files():
            age_days = self._file_age_days(path)
            if age_days is None or age_days < min_age or age_days > max_age:
                continue
            distance_penalty = abs(age_days - optimal_age)
            score = max(0.0, 1.0 - distance_penalty * 0.2)
            candidates.append((score, path))

        if not candidates:
            return []

        top_files = [path for _, path in sorted(candidates, key=lambda item: item[0], reverse=True)[:2]]
        memory_map = {str(item.get("file_path") or ""): item for item in memory_candidates}
        refs = [self._normalize_ref(path.relative_to(self._workspace).as_posix()) for path in top_files]
        previews = [self._read_preview(path, max_chars=90) for path in top_files]
        core_node_ids = [
            str(memory_map[ref].get("node_id") or "")
            for ref in refs
            if ref in memory_map and str(memory_map[ref].get("node_id") or "")
        ]
        return [
            DreamSeed(
                seed_id=f"seed_lag_{uuid.uuid4().hex[:6]}",
                seed_type=DreamSeedType.DREAM_LAG.value,
                title=seed_title,
                summary="；".join(preview for preview in previews if preview) or "前几天的内容在今晚绕了一圈回来。",
                core_refs=refs,
                core_node_ids=core_node_ids,
                source_events=[path.stem for path in top_files],
                affect_valence=0.02,
                affect_arousal=0.48,
                importance=0.55,
                novelty=0.5,
                recurrence=0.34,
                unfinished_score=0.4,
                dreamability=0.74,
                score=0.64,
                tension_reason=seed_reason,
            )
        ]

    async def _collect_self_theme(
        self,
        memory_candidates: list[dict[str, Any]],
    ) -> list[DreamSeed]:
        """收集长期自我主题。

        仿生设计：不固定取前 3 个，而是从候选中随机采样，
        让不同主题有机会在不同夜晚浮现。
        """
        if not memory_candidates:
            return []

        sample_size = min(3, len(memory_candidates))
        top_nodes = random.sample(memory_candidates, sample_size)
        refs = [str(item.get("file_path") or "").strip() for item in top_nodes if str(item.get("file_path") or "").strip()]
        titles = [str(item.get("title") or "").strip() or Path(ref).stem for item, ref in zip(top_nodes, refs, strict=False)]
        core_node_ids = [str(item.get("node_id") or "") for item in top_nodes if str(item.get("node_id") or "")]
        recurrence = min(sum(float(item.get("access_count") or 0) for item in top_nodes) / 10.0, 1.0)
        importance = min(sum(float(item.get("importance") or 0.0) for item in top_nodes[:2]) / 2.0, 1.0)
        summary = "、".join(title for title in titles if title) or "一些反复出现的主题仍在定义她是谁。"
        return [
            DreamSeed(
                seed_id=f"seed_theme_{uuid.uuid4().hex[:6]}",
                seed_type=DreamSeedType.SELF_THEME.value,
                title="那些总会回来的长期主题",
                summary=summary,
                core_refs=refs[:3],
                core_node_ids=core_node_ids[:3],
                source_events=titles[:3],
                affect_valence=0.08,
                affect_arousal=0.4,
                importance=importance,
                novelty=0.12,
                recurrence=recurrence,
                unfinished_score=0.28,
                dreamability=0.62,
                score=0.5 + importance * 0.28 + recurrence * 0.1,
                tension_reason="这些主题不是一时冲动，而是会反复构成自我感的底纹。",
            )
        ]

    def _select_seed_candidates(self, candidates: list[DreamSeed]) -> list[DreamSeed]:
        """按类型优先 + 神经噪声选择最终种子。

        仿生设计：人类做梦时前额叶皮层活动降低，神经元随机放电增加。
        这种"噪声"是梦境多样性的关键来源。
        """
        if not candidates:
            return []

        # 给每个种子加入神经噪声（模拟前额叶抑制下的随机激活）
        scored: list[tuple[float, DreamSeed]] = []
        for seed in candidates:
            noise = random.gauss(0, _SEED_SCORE_TEMPERATURE)
            effective = max(0.01, seed.score + noise)
            scored.append((effective, seed))

        # 按类型分组，每组取最高有效分
        by_type: dict[str, tuple[float, DreamSeed]] = {}
        for eff_score, seed in sorted(scored, key=lambda x: x[0], reverse=True):
            by_type.setdefault(seed.seed_type, (eff_score, seed))

        selected = [seed for _, seed in sorted(by_type.values(), key=lambda x: x[0], reverse=True)]

        if len(selected) >= _MAX_DREAM_SEEDS:
            return selected[:_MAX_DREAM_SEEDS]

        # 剩余名额：加权随机采样（不是确定性 top-K）
        existing_ids = {s.seed_id for s in selected}
        remaining = [(eff, s) for eff, s in scored if s.seed_id not in existing_ids]
        if remaining:
            weights = [max(0.01, eff) for eff, _ in remaining]
            total = sum(weights)
            if total > 0:
                extra_count = min(_MAX_DREAM_SEEDS - len(selected), len(remaining))
                extras = random.choices(
                    [s for _, s in remaining],
                    weights=[w / total for w in weights],
                    k=extra_count,
                )
                selected.extend(extras)

        return selected[:_MAX_DREAM_SEEDS]

    def _collect_seed_node_ids(self, seeds: list[DreamSeed]) -> list[str]:
        """收集 REM 的主种子节点 ID。"""
        return _unique_preserve(
            node_id
            for seed in seeds
            for node_id in seed.core_node_ids
            if str(node_id or "").strip()
        )

    def _iter_seed_file_refs(self, seeds: list[DreamSeed]) -> list[str]:
        """返回可映射到文件系统的 refs。"""
        refs: list[str] = []
        for seed in seeds:
            for ref in list(seed.core_refs) + list(seed.supporting_refs):
                raw = str(ref or "").strip()
                if not raw:
                    continue
                path_part = raw.split("#", 1)[0].strip()
                normalized = self._normalize_ref(path_part)
                if normalized:
                    refs.append(normalized)
        return _unique_preserve(refs)

    def _normalize_ref(self, value: str) -> str:
        """把 ref 规整为 workspace 相对路径。"""
        raw = str(value or "").strip().replace("\\", "/")
        if not raw:
            return ""
        if self._workspace is None:
            return raw.lstrip("/")
        path = Path(raw)
        try:
            if path.is_absolute():
                return path.resolve().relative_to(self._workspace).as_posix()
        except Exception:  # noqa: BLE001
            pass
        return raw.lstrip("/")

    def _build_recent_context_summary(self, event_history: list[Any]) -> list[str]:
        """构造模型可用的近期上下文摘要。"""
        lines: list[str] = []
        for event in event_history[-6:]:
            etype = _event_type_value(event)
            content = _clean_text(_event_value(event, "content", ""))
            if not content:
                continue
            lines.append(f"{etype}: {content[:100]}")
        return lines

    def _build_reference_previews(self, seeds: list[DreamSeed]) -> list[dict[str, str]]:
        """为 DreamSceneBuilder 提供 refs 预览。"""
        if self._workspace is None:
            return []
        previews: list[dict[str, str]] = []
        for ref in self._iter_seed_file_refs(seeds)[:6]:
            abs_path = self._workspace / ref
            previews.append(
                {
                    "ref": ref,
                    "preview": self._read_preview(abs_path, max_chars=140),
                }
            )
        return previews

    def _format_inner_state_summary(self) -> str:
        """获取简洁的内在状态文本。"""
        if self._inner_state is None:
            return ""
        try:
            discrete = self._inner_state.modulators.get_discrete_dict()
            items: list[str] = []
            for key in ("curiosity", "energy", "contentment"):
                if key in discrete:
                    mod = self._inner_state.modulators.get(key)
                    if mod is not None:
                        items.append(f"{mod.cn_name}{discrete[key]}")
            return "、".join(items)
        except Exception:  # noqa: BLE001
            return ""

    def _iter_workspace_markdown_files(self) -> list[Path]:
        """遍历可作为梦材料的 markdown 文件。"""
        if self._workspace is None or not self._workspace.exists():
            return []

        roots = [
            self._workspace / "diary",
            self._workspace / "diaries",
            self._workspace / "notes",
        ]
        files: list[Path] = []
        for root in roots:
            if not root.exists():
                continue
            files.extend(
                path
                for path in root.rglob("*.md")
                if path.is_file() and _DREAM_ARCHIVE_DIR not in path.parts
            )
        return files

    def _file_age_days(self, path: Path) -> int | None:
        """推断文件距离现在多少天。"""
        now = datetime.now().astimezone().date()
        stem = path.stem
        match = _DATE_STEM_RE.match(stem)
        if match:
            try:
                target = datetime.strptime(match.group("date"), "%Y-%m-%d").date()
                return abs((now - target).days)
            except ValueError:
                pass

        month_match = _MONTH_STEM_RE.match(stem)
        if month_match:
            try:
                target = datetime.strptime(month_match.group("month"), "%Y-%m").date()
                return abs((now - target).days)
            except ValueError:
                pass

        try:
            file_date = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).astimezone().date()
            return abs((now - file_date).days)
        except Exception:  # noqa: BLE001
            return None

    def _read_preview(self, path: Path, *, max_chars: int = 120) -> str:
        """读取文件简短预览。"""
        if not path.exists() or not path.is_file():
            return ""
        try:
            content = " ".join(path.read_text(encoding="utf-8").split())
        except Exception:  # noqa: BLE001
            return ""
        if len(content) <= max_chars:
            return content
        return content[: max_chars - 1] + "…"

    def enter_sleep(self) -> None:
        """通知调质层进入睡眠。"""
        self._dreams_since_sleep_start = 0
        if self._inner_state is not None:
            self._inner_state.enter_sleep()

    def get_active_residue(self) -> DreamResidue | None:
        """获取仍在生效的梦后余韵。"""
        residue = self._active_residue
        if residue is None:
            return None
        if residue.expires_at and residue.expires_at <= time.time():
            self._active_residue = None
            return None
        return residue

    def get_active_residue_payload(self, target: str) -> str:
        """获取给指定系统消费的 payload 文本。"""
        residue = self.get_active_residue()
        if residue is None:
            return ""
        normalized = str(target or "").strip().lower()
        if normalized == "life":
            return residue.life_payload
        if normalized == "dfc":
            return residue.dfc_payload
        return residue.summary

    def get_dream_history(self, limit: int = 10) -> list[dict[str, Any]]:
        """获取最近的做梦报告。"""
        reports = self._dream_history[-limit:]
        return [_report_to_dict(report) for report in reports]

    def get_state(self) -> dict[str, Any]:
        """获取做梦系统当前状态。"""
        residue = self.get_active_residue()
        return {
            "is_dreaming": self._is_dreaming,
            "current_phase": self._current_phase.value,
            "last_dream_time": self._last_dream_time,
            "total_dreams": len(self._dream_history),
            "last_archive_path": self._last_archive_path,
            "active_residue": _residue_to_dict(residue) if residue else None,
            "last_report": (
                _report_to_dict(self._dream_history[-1]) if self._dream_history else None
            ),
        }

    def serialize(self) -> dict[str, Any]:
        """序列化做梦状态用于持久化。"""
        return {
            "last_dream_time": self._last_dream_time,
            "last_archive_path": self._last_archive_path,
            "active_residue": _residue_to_dict(self.get_active_residue()),
            "dream_history": [_report_to_dict(report) for report in self._dream_history[-_REMINDER_MAX_HISTORY:]],
            "recent_seed_titles": [list(s) for s in self._recent_seed_titles],
            "recent_dream_summaries": list(self._recent_dream_summaries),
            "dreams_since_sleep_start": self._dreams_since_sleep_start,
        }

    def deserialize(self, data: dict[str, Any]) -> None:
        """从持久化数据恢复状态。"""
        if not isinstance(data, dict):
            return
        self._last_dream_time = float(data.get("last_dream_time", 0.0))
        self._last_archive_path = str(data.get("last_archive_path") or "")
        residue_raw = data.get("active_residue")
        if isinstance(residue_raw, dict):
            self._active_residue = _residue_from_payload(residue_raw)

        history_raw = data.get("dream_history")
        if isinstance(history_raw, list):
            restored: list[DreamReport] = []
            for item in history_raw[-_REMINDER_MAX_HISTORY:]:
                if isinstance(item, dict):
                    restored.append(_report_from_dict(item))
            self._dream_history = restored

        # 恢复仿生状态
        seed_titles_raw = data.get("recent_seed_titles")
        if isinstance(seed_titles_raw, list):
            self._recent_seed_titles = deque(
                (set(s) for s in seed_titles_raw if isinstance(s, list)),
                maxlen=_DREAM_HISTORY_WINDOW,
            )
        summaries_raw = data.get("recent_dream_summaries")
        if isinstance(summaries_raw, list):
            self._recent_dream_summaries = deque(
                (str(s) for s in summaries_raw if isinstance(s, str)),
                maxlen=_DREAM_HISTORY_WINDOW,
            )
        self._dreams_since_sleep_start = int(data.get("dreams_since_sleep_start", 0))

        logger.info(f"做梦系统状态已恢复: last_dream={self._last_dream_time:.0f}")


def _event_value(event: Any, key: str, default: Any = None) -> Any:
    """统一读取 dict/object 事件字段。"""
    if isinstance(event, dict):
        return event.get(key, default)
    return getattr(event, key, default)


def _event_type_value(event: Any) -> str:
    """获取事件类型值。"""
    event_type = _event_value(event, "event_type", None)
    if event_type is None:
        event_type = _event_value(event, "type", None)
    return str(getattr(event_type, "value", event_type) or "").strip().lower()


def _event_timestamp(event: Any) -> float:
    """提取事件时间戳。"""
    raw = _event_value(event, "timestamp", None)
    if raw is None:
        return time.time()
    if isinstance(raw, (int, float)):
        return float(raw)
    try:
        return datetime.fromisoformat(str(raw)).timestamp()
    except Exception:  # noqa: BLE001
        return time.time()


def _clean_text(value: Any) -> str:
    """清洗字符串。"""
    text = " ".join(str(value or "").split())
    return text.strip()


def _unique_preserve(items: Any) -> list[Any]:
    """按出现顺序去重。"""
    seen: set[Any] = set()
    result: list[Any] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _seed_to_dict(seed: DreamSeed) -> dict[str, Any]:
    """将 DreamSeed 转成可序列化字典。"""
    return asdict(seed)


def _trace_to_dict(trace: DreamTrace) -> dict[str, Any]:
    """将 DreamTrace 转成可序列化字典。"""
    return {
        "scenes": [
            {
                "title": scene.title,
                "summary": scene.summary,
                "imagery": list(scene.imagery),
                "emotion_shift": scene.emotion_shift,
                "refs": list(scene.refs),
            }
            for scene in trace.scenes
        ],
        "motifs": list(trace.motifs),
        "transitions": list(trace.transitions),
    }


def _residue_to_dict(residue: DreamResidue | None) -> dict[str, Any] | None:
    """将 DreamResidue 转为可序列化字典。"""
    if residue is None:
        return None
    return asdict(residue)


def _trace_from_payload(raw: Any) -> DreamTrace:
    """从 payload 转换为 DreamTrace。"""
    if not isinstance(raw, dict):
        return DreamTrace()
    scenes_raw = raw.get("scenes")
    scenes: list[DreamScene] = []
    if isinstance(scenes_raw, list):
        for item in scenes_raw[:_MAX_SCENES]:
            if not isinstance(item, dict):
                continue
            scenes.append(
                DreamScene(
                    title=_clean_text(item.get("title", "")),
                    summary=_clean_text(item.get("summary", "")),
                    imagery=[_clean_text(v) for v in item.get("imagery", []) if _clean_text(v)],
                    emotion_shift=_clean_text(item.get("emotion_shift", "")),
                    refs=[_clean_text(v) for v in item.get("refs", []) if _clean_text(v)],
                )
            )
    return DreamTrace(
        scenes=scenes,
        motifs=[_clean_text(v) for v in raw.get("motifs", []) if _clean_text(v)],
        transitions=[_clean_text(v) for v in raw.get("transitions", []) if _clean_text(v)],
    )


def _residue_from_payload(raw: Any) -> DreamResidue:
    """从 payload 转换为 DreamResidue。"""
    if not isinstance(raw, dict):
        return DreamResidue()
    return DreamResidue(
        summary=_clean_text(raw.get("summary", "")),
        life_payload=_clean_text(raw.get("life_payload", "")),
        dfc_payload=_clean_text(raw.get("dfc_payload", "")),
        dominant_affect=_clean_text(raw.get("dominant_affect", "")),
        strength=_clean_text(raw.get("strength", "light")) or "light",
        tags=[_clean_text(tag) for tag in raw.get("tags", []) if _clean_text(tag)],
        expires_at=float(raw.get("expires_at", 0.0) or 0.0),
    )


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
            "weight_delta": _compute_weight_delta(report.nrem.weight_before, report.nrem.weight_after),
        },
        "rem": {
            "walk_rounds": report.rem.walk_rounds,
            "nodes_activated": report.rem.nodes_activated,
            "new_edges_created": report.rem.new_edges_created,
            "edges_pruned": report.rem.edges_pruned,
            "seed_node_ids": list(report.rem.seed_node_ids),
        },
        "narrative": report.narrative,
        "dream_text": report.dream_text,
        "phases": list(report.phase_sequence),
        "seed_report": [_seed_to_dict(seed) for seed in report.seed_report],
        "dream_trace": _trace_to_dict(report.dream_trace),
        "dream_residue": _residue_to_dict(report.dream_residue),
        "archive_path": report.archive_path,
        "memory_effects": dict(report.memory_effects),
    }


def _report_from_dict(raw: dict[str, Any]) -> DreamReport:
    """从字典恢复 DreamReport。"""
    seed_report = []
    seeds_raw = raw.get("seed_report")
    if isinstance(seeds_raw, list):
        for item in seeds_raw:
            if isinstance(item, dict):
                seed_report.append(
                    DreamSeed(
                        seed_id=str(item.get("seed_id") or ""),
                        seed_type=str(item.get("seed_type") or ""),
                        title=str(item.get("title") or ""),
                        summary=str(item.get("summary") or ""),
                        core_refs=list(item.get("core_refs") or []),
                        supporting_refs=list(item.get("supporting_refs") or []),
                        core_node_ids=list(item.get("core_node_ids") or []),
                        source_events=list(item.get("source_events") or []),
                        affect_valence=float(item.get("affect_valence") or 0.0),
                        affect_arousal=float(item.get("affect_arousal") or 0.0),
                        importance=float(item.get("importance") or 0.0),
                        novelty=float(item.get("novelty") or 0.0),
                        recurrence=float(item.get("recurrence") or 0.0),
                        unfinished_score=float(item.get("unfinished_score") or 0.0),
                        dreamability=float(item.get("dreamability") or 0.0),
                        score=float(item.get("score") or 0.0),
                        tension_reason=str(item.get("tension_reason") or ""),
                    )
                )

    rem_raw = raw.get("rem") or {}
    nrem_raw = raw.get("nrem") or {}
    return DreamReport(
        dream_id=str(raw.get("dream_id") or ""),
        started_at=float(raw.get("started_at") or 0.0),
        ended_at=float(raw.get("ended_at") or 0.0),
        duration_seconds=float(raw.get("duration_seconds") or 0.0),
        nrem=NREMReport(
            episodes_replayed=int(nrem_raw.get("episodes_replayed") or 0),
            total_steps=int(nrem_raw.get("total_steps") or 0),
            homeostatic_applied=bool(nrem_raw.get("homeostatic_applied", False)),
        ),
        rem=REMReport(
            walk_rounds=int(rem_raw.get("walk_rounds") or 0),
            nodes_activated=int(rem_raw.get("nodes_activated") or 0),
            new_edges_created=int(rem_raw.get("new_edges_created") or 0),
            edges_pruned=int(rem_raw.get("edges_pruned") or 0),
            seed_node_ids=list(rem_raw.get("seed_node_ids") or []),
        ),
        narrative=str(raw.get("narrative") or ""),
        phase_sequence=list(raw.get("phases") or []),
        seed_report=seed_report,
        dream_trace=_trace_from_payload(raw.get("dream_trace")),
        dream_text=str(raw.get("dream_text") or ""),
        dream_residue=_residue_from_payload(raw.get("dream_residue") or {}),
        archive_path=str(raw.get("archive_path") or ""),
        memory_effects=dict(raw.get("memory_effects") or {}),
    )


def _parse_json_payload(text: str) -> dict[str, Any]:
    """从模型返回中提取 JSON 对象。"""
    stripped = str(text or "").strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if "\n" in stripped:
            stripped = stripped.split("\n", 1)[1]
        if stripped.endswith("```"):
            stripped = stripped[:-3]
        stripped = stripped.strip()

    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            return parsed
    except Exception:  # noqa: BLE001
        pass

    match = _JSON_BLOCK_RE.search(stripped)
    if not match:
        raise ValueError("未找到 JSON 块")

    parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("JSON 顶层不是对象")
    return parsed


def _compute_weight_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, float]:
    """计算权重变化摘要。"""
    if not before or not after:
        return {}
    delta: dict[str, float] = {}
    for layer in before:
        if layer not in after:
            continue
        b = before[layer]
        a = after[layer]
        delta[f"{layer}_mean_delta"] = round(a.get("w_mean", 0) - b.get("w_mean", 0), 6)
        delta[f"{layer}_norm_delta"] = round(a.get("w_norm", 0) - b.get("w_norm", 0), 6)
    return delta
