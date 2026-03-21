"""
仿生睡眠插件 (Biometric Sleep Plugin)

核心任务：为机器人提供类人的睡眠/清醒周期模拟。
在设定的睡眠时间段内，机器人会进入睡眠状态：
- 只响应私聊和@消息
- 普通群聊消息会被静默处理（仍存入历史记录）
- 持续的呼唤会累积唤醒度，超过阈值后机器人会被"吵醒"
- 被吵醒时会表现出困倦、迷糊的状态

主要特性：
- 3状态机：AWAKE（清醒）、DROWSY（困倦）、SLEEPING（睡眠）
- 唤醒度机制：累计+衰减，模拟"被吵醒"的过程
- 提示词注入：根据状态自动注入相应的角色提示词
- 黑白名单：支持配置用户/群组白名单和黑名单
"""



__plugin_meta__ = dict(
    name="biometric_sleep_plugin",
    description="仿生睡眠插件 - 为机器人提供类人的睡眠/清醒周期模拟，支持唤醒度机制和状态提示词注入",
    usage="在配置文件中设置 start_time 和 end_time 即可，支持随机浮动和唤醒度阈值配置",
    version="2.0.0",
    author="言柒",
    license="GPL-v3.0-or-later",
    repository_url="https://github.com/tt-P607/biometric_sleep_plugin",
    keywords=["sleep", "biometric", "circadian", "personality", "role-play", "interceptor"],
    categories=["utility", "personality"],
    extra={
        "is_built_in": True,
    },
)