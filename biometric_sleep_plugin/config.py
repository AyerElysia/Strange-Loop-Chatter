"""
Biometric Sleep System - 配置定义 v2

简化后的配置结构：
- basic: 基础作息配置
- drowsy: 困倦/延时期配置
- sleeping: 睡眠状态配置（唤醒度机制）
- prompt: 提示词注入配置
- filter: 过滤配置
"""

from src.core.components.base.config import Field

CONFIG_SCHEMA = {
    "basic": {
        "enabled": Field(
            type=bool,
            default=True,
            description="是否启用睡眠系统",
        ),
        "start_time": Field(
            type=str,
            default="23:30",
            description="基础入睡时间 (HH:MM)",
        ),
        "end_time": Field(
            type=str,
            default="07:30",
            description="基础醒来时间 (HH:MM)",
        ),
        "random_offset": Field(
            type=int,
            default=30,
            description="随机浮动范围（分钟）",
        ),
    },
    "drowsy": {
        "max_delay_count": Field(
            type=int,
            default=3,
            description="最大延时入睡次数",
        ),
        "delay_duration": Field(
            type=int,
            default=15,
            description="每次延时增加的时长（分钟）",
        ),
    },
    "sleeping": {
        "wake_threshold": Field(
            type=float,
            default=50.0,
            description="唤醒度阈值（达到此值被吵醒）",
        ),
        "wake_increment": Field(
            type=float,
            default=20.0,
            description="每次有效交互增加的唤醒度",
        ),
        "wake_max": Field(
            type=float,
            default=80.0,
            description="唤醒度上限",
        ),
        "decay_rate": Field(
            type=float,
            default=5.0,
            description="唤醒度每分钟衰减值",
        ),
    },
    "prompt": {
        "enable_injection": Field(
            type=bool,
            default=True,
            description="是否开启提示词注入",
        ),
        "drowsy_prompt": Field(
            type=str,
            default="你现在非常困倦，准备睡觉了，回复语气应带有倦意。",
            description="困倦状态下的提示词",
        ),
        "sleeping_prompt": Field(
            type=str,
            default="你现在正在睡觉，语气慵懒迷糊。",
            description="睡眠状态下的提示词",
        ),
        "woken_prompt": Field(
            type=str,
            default="你刚被吵醒，有点迷糊，可能带点小脾气。",
            description="被吵醒状态下的提示词",
        ),
    },
    "filter": {
        "mode": Field(
            type=str,
            default="blacklist",
            description="过滤模式: blacklist-黑名单 / whitelist-白名单",
            choices=["blacklist", "whitelist"],
        ),
        "ignored_ids": Field(
            type=list,
            default=[],
            description="忽略睡眠逻辑的群组或用户ID列表",
            example='["123456789", "987654321"]',
        ),
    },
}