"""sleep_wakeup_plugin 配置定义。"""

from typing import ClassVar

from src.app.plugin_system.base import BaseConfig, Field, SectionBase, config_section


class Config(BaseConfig):
    """睡眠/苏醒插件配置。"""

    config_name: ClassVar[str] = "config"
    config_description: ClassVar[str] = "睡眠/苏醒离散状态机插件配置"

    @config_section("general", title="通用设置", tag="general", order=0)
    class GeneralSection(SectionBase):
        enabled: bool = Field(default=True, description="是否启用 sleep_wakeup_plugin")
        debug_mode: bool = Field(default=False, description="是否输出调试日志")

    @config_section("timing", title="时间参数", tag="timer", order=10)
    class TimingSection(SectionBase):
        sleep_target_time: str = Field(
            default="23:30",
            description="预计入睡时间点（HH:MM）",
        )
        wake_target_time: str = Field(
            default="07:30",
            description="预计苏醒时间点（HH:MM）",
        )
        sleep_window_minutes: int = Field(
            default=90,
            ge=1,
            le=720,
            description="入睡窗口（分钟）",
        )
        wake_window_minutes: int = Field(
            default=120,
            ge=1,
            le=720,
            description="苏醒窗口（分钟）",
        )
        update_interval_seconds: int = Field(
            default=30,
            ge=10,
            le=3600,
            description="困倦值更新周期（秒）",
        )

    @config_section("model", title="状态机参数", tag="model", order=20)
    class ModelSection(SectionBase):
        guardian_model_task: str = Field(
            default="actor",
            description="守护决策使用的模型任务名",
        )
        guardian_timeout_seconds: int = Field(
            default=20,
            ge=5,
            le=120,
            description="守护决策调用大模型的超时时间（秒）",
        )
        pre_sleep_step: int = Field(
            default=2,
            ge=1,
            le=30,
            description="预计入睡阶段每次更新增加的困倦值",
        )
        sleep_phase_step: int = Field(
            default=6,
            ge=1,
            le=50,
            description="预计睡眠阶段每次更新增加的困倦值",
        )
        pre_wake_step: int = Field(
            default=3,
            ge=1,
            le=30,
            description="预计苏醒阶段每次更新降低的困倦值",
        )
        lie_in_reset_drowsiness: int = Field(
            default=10,
            ge=1,
            le=99,
            description="守护驳回苏醒时重置的困倦值",
        )
        max_lie_in_attempts: int = Field(
            default=1,
            ge=0,
            le=10,
            description="守护驳回最大次数，超过后强制批准苏醒",
        )

    @config_section("guard", title="消息拦截", tag="guard", order=30)
    class GuardSection(SectionBase):
        block_messages_when_sleeping: bool = Field(
            default=True,
            description="角色处于 sleeping 时是否阻挡消息事件",
        )
        enable_private_message_wakeup: bool = Field(
            default=True,
            description="检测到私聊消息时是否降低困倦值",
        )
        private_message_wakeup_delta: int = Field(
            default=12,
            ge=1,
            le=100,
            description="每次检测到私聊消息时降低的困倦值",
        )
        wakeup_user_list_type: str = Field(
            default="all",
            description="私聊唤醒用户名单模式",
            input_type="select",
            choices=["whitelist", "blacklist", "all"],
        )
        wakeup_user_list: list[str] = Field(
            default_factory=list,
            description="私聊唤醒用户列表（格式：platform:user_id）",
            input_type="list",
            item_type="str",
        )

    @config_section("storage", title="持久化", tag="storage", order=40)
    class StorageSection(SectionBase):
        state_key: str = Field(default="runtime_state", description="JSON 存储键名")
        max_history_records: int = Field(
            default=500,
            ge=50,
            le=5000,
            description="保留的最大历史记录数量",
        )

    general: GeneralSection = Field(default_factory=GeneralSection)
    timing: TimingSection = Field(default_factory=TimingSection)
    model: ModelSection = Field(default_factory=ModelSection)
    guard: GuardSection = Field(default_factory=GuardSection)
    storage: StorageSection = Field(default_factory=StorageSection)

