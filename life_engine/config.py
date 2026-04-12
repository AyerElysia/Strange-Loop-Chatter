"""life_engine 插件配置。"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from src.core.components.base.config import BaseConfig, Field, SectionBase, config_section


# 默认工作空间路径
_DEFAULT_WORKSPACE = str(Path(__file__).parent.parent.parent / "data" / "life_engine_workspace")


class LifeEngineConfig(BaseConfig):
    """life_engine 插件配置。"""

    config_name: ClassVar[str] = "config"
    config_description: ClassVar[str] = "生命中枢最小原型配置"

    @config_section("settings")
    class SettingsSection(SectionBase):
        """基础设置。"""

        enabled: bool = Field(
            default=True,
            description="是否启用 life_engine。设为 false 时不启动心跳。",
        )

        heartbeat_interval_seconds: int = Field(
            default=30,
            description="心跳间隔（秒）。",
        )

        sleep_time: str = Field(
            default="",
            description="睡觉时间，格式 HH:MM（24小时制）。与 wake_time 同时配置后生效。",
        )

        wake_time: str = Field(
            default="",
            description="苏醒时间，格式 HH:MM（24小时制）。与 sleep_time 同时配置后生效。",
        )

        log_heartbeat: bool = Field(
            default=True,
            description="是否在每次心跳时输出日志。",
        )

        context_history_max_events: int = Field(
            default=100,
            ge=1,
            description="滚动事件流最多保留的事件条数（包括心跳、消息、工具调用等）。",
        )

        workspace_path: str = Field(
            default=_DEFAULT_WORKSPACE,
            description="中枢文件系统操作的工作空间路径。中枢只能在此目录下进行文件操作。",
        )

        max_rounds_per_heartbeat: int = Field(
            default=3,
            ge=1,
            description="单次心跳内允许模型连续进行工具调用的最大轮数（防止死循环）。",
        )

    @config_section("model")
    class ModelSection(SectionBase):
        """中枢模型任务设置。"""

        task_name: str = Field(
            default="life",
            description="中枢任务使用的模型任务名称，对应 config/model.toml 中的 [model_tasks.life]。",
        )

    @config_section("web")
    class WebSection(SectionBase):
        """网络搜索与网页提取能力配置（Tavily）。"""

        tavily_api_key: str = Field(
            default="",
            description="Tavily API Key。请在 config/plugins/life_engine/config.toml 的 [web] 中配置。",
        )

        tavily_api_keys: list[str] = Field(
            default_factory=list,
            description="多个 Tavily API Key。配置后 web_tools 会按轮询方式选择，用于负载均衡。",
        )

        tavily_base_url: str = Field(
            default="https://api.tavily.com",
            description="Tavily API 基础地址。",
        )

        tavily_base_urls: list[str] = Field(
            default_factory=list,
            description="多个 Tavily API 基础地址。配置后 web_tools 会按轮询方式选择，用于负载均衡。",
        )

        search_timeout_seconds: int = Field(
            default=30,
            ge=1,
            le=120,
            description="网络搜索超时（秒）。",
        )

        extract_timeout_seconds: int = Field(
            default=60,
            ge=1,
            le=180,
            description="网页提取超时（秒）。",
        )

        default_search_max_results: int = Field(
            default=5,
            ge=1,
            le=20,
            description="网络搜索默认返回条数。",
        )

        default_fetch_max_chars: int = Field(
            default=12000,
            ge=500,
            le=50000,
            description="网页提取默认最大返回字符数。",
        )

    @config_section("snn")
    class SNNSection(SectionBase):
        """SNN 皮层下状态层配置。"""

        enabled: bool = Field(
            default=False,
            description="是否启用 SNN 状态层。启用后 life_engine 将运行一个持续的 SNN 驱动核。",
        )

        shadow_only: bool = Field(
            default=True,
            description="影子模式：只记录 SNN 状态变化，不注入心跳 prompt。用于初期验证。",
        )

        tick_interval_seconds: float = Field(
            default=10.0,
            ge=1.0,
            description="SNN 独立 tick 间隔（秒）。SNN 以此频率独立更新衰减，不绑定 LLM 心跳。",
        )

        inject_to_heartbeat: bool = Field(
            default=False,
            description="是否将 SNN 驱动状态注入心跳 prompt。需要 shadow_only=false 才生效。",
        )

        feature_window_seconds: float = Field(
            default=600.0,
            ge=60.0,
            description="特征提取窗口大小（秒）。决定 SNN 从多长时间的事件中提取输入。",
        )

    @config_section("neuromod")
    class NeuromodSection(SectionBase):
        """神经调质层配置。"""

        enabled: bool = Field(
            default=True,
            description="是否启用神经调质层。调质层在 SNN 之上提供慢时间尺度的驱动调节。",
        )

        inject_to_heartbeat: bool = Field(
            default=True,
            description="是否将调质状态注入心跳 prompt。",
        )

        habit_tracking: bool = Field(
            default=True,
            description="是否启用习惯追踪。",
        )

    @config_section("dream")
    class DreamSection(SectionBase):
        """做梦系统配置。三阶段做梦周期：NREM 回放 → REM 联想 → 觉醒过渡。"""

        enabled: bool = Field(
            default=True,
            description="是否启用做梦系统。",
        )

        # NREM 参数
        nrem_replay_episodes: int = Field(
            default=3,
            ge=1, le=10,
            description="每次做梦 NREM 阶段回放的事件集数。",
        )

        nrem_events_per_episode: int = Field(
            default=20,
            ge=5, le=100,
            description="每集回放包含的事件数量。",
        )

        nrem_speed_multiplier: float = Field(
            default=5.0,
            ge=1.0, le=20.0,
            description="NREM 回放加速倍率（缩短 SNN tau）。",
        )

        nrem_homeostatic_rate: float = Field(
            default=0.02,
            ge=0.001, le=0.1,
            description="SHY 突触稳态缩减比例（每次做梦全局权重缩减百分比）。",
        )

        # REM 参数
        rem_walk_rounds: int = Field(
            default=2,
            ge=1, le=10,
            description="REM 阶段记忆图谱随机游走轮数。",
        )

        rem_seeds_per_round: int = Field(
            default=5,
            ge=1, le=20,
            description="每轮 REM 游走的随机种子数。",
        )

        rem_max_depth: int = Field(
            default=3,
            ge=1, le=5,
            description="REM 游走激活扩散最大深度。",
        )

        rem_decay_factor: float = Field(
            default=0.6,
            ge=0.1, le=0.95,
            description="REM 游走激活扩散衰减因子。",
        )

        rem_learning_rate: float = Field(
            default=0.05,
            ge=0.01, le=0.3,
            description="REM 阶段 Hebbian 学习率（低于清醒时 0.1）。",
        )

        rem_edge_prune_threshold: float = Field(
            default=0.08,
            ge=0.01, le=0.3,
            description="REM 阶段弱边修剪阈值（仅 ASSOCIATES 边）。",
        )

        # 调度参数
        dream_interval_minutes: int = Field(
            default=90,
            ge=10, le=480,
            description="两次做梦之间的最小间隔（分钟）。",
        )

        idle_trigger_heartbeats: int = Field(
            default=10,
            ge=3, le=50,
            description="白天连续空闲心跳数触发小憩做梦。",
        )

        nap_enabled: bool = Field(
            default=True,
            description="是否启用白天小憩做梦（空闲触发）。",
        )

    settings: SettingsSection = Field(default_factory=SettingsSection)
    model: ModelSection = Field(default_factory=ModelSection)
    web: WebSection = Field(default_factory=WebSection)
    snn: SNNSection = Field(default_factory=SNNSection)
    neuromod: NeuromodSection = Field(default_factory=NeuromodSection)
    dream: DreamSection = Field(default_factory=DreamSection)
