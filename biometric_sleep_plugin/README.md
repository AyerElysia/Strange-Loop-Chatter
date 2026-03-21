# 仿生睡眠插件 (Biometric Sleep Plugin)

为 MoFox-Bot 机器人提供类人的睡眠/清醒周期模拟。

## ✨ 功能特性

- **3状态机设计**：清醒(AWAKE) → 困倦(DROWSY) → 睡眠(SLEEPING)
- **唤醒度机制**：累计+衰减，模拟"被吵醒"的过程
- **智能消息拦截**：睡眠期间只响应私聊和@消息
- **提示词注入**：根据状态自动注入相应的角色提示词
- **黑白名单**：支持配置用户/群组白名单和黑名单
- **历史记录保留**：被拦截的消息仍会存入历史记录

## 📦 安装

将整个 `biometric_sleep_plugin` 文件夹复制到 `plugins/` 目录下即可。

## ⚙️ 配置

在 `config/plugins/biometric_sleep_plugin/config.toml` 中进行配置：

```toml
[sleep_schedule]
# 入睡时间 (24小时制)
start_time = "00:00"
# 起床时间 (24小时制)
end_time = "08:00"
# 入睡时间随机浮动范围 (分钟)
random_offset_minutes = 30
# 入睡前困倦期持续时间 (分钟)
drowsy_duration_minutes = 30

[awakening]
# 唤醒阈值 (超过此值会被吵醒)
threshold = 50.0
# 每次有效交互增加的唤醒度
increment = 20.0
# 唤醒度上限
max_value = 80.0
# 每分钟衰减值
decay_per_minute = 5.0

[whitelist]
# 白名单用户 (永远不被拦截)
user_ids = []
# 白名单群组 (永远不被拦截)
group_ids = []

[blacklist]
# 黑名单用户 (永远被拦截，即使在清醒时段)
user_ids = []
# 黑名单群组
group_ids = []

[prompts]
# 各状态下的提示词
awake = ""
drowsy = "你现在有点困了，语气会变得慵懒一些。"
sleeping = "你现在正在睡觉，语气慵懒迷糊。"
woken_up = "你刚被吵醒，有点迷糊，可能带点小脾气。"
```

## 🔧 状态机说明

```
AWAKE (清醒)
    │
    │ 到达入睡时间 - drowsy_duration
    ▼
DROWSY (困倦)
    │
    │ 无交互超过 drowsy_duration / 有交互则延时
    ▼
SLEEPING (睡眠)
    │
    │ 唤醒度 >= threshold
    ▼
WOKEN_UP (被吵醒) ──回落──▶ SLEEPING
    │
    │ 到达起床时间
    ▼
AWAKE (清醒)
```

### 状态详解

| 状态 | 描述 | 消息处理 |
|------|------|----------|
| AWAKE | 清醒状态 | 全部放行 |
| DROWSY | 困倦状态 | 只放行私聊/@消息 |
| SLEEPING | 睡眠状态 | 基于唤醒度判断 |

### 唤醒度机制

- **累计**：每次有效交互（私聊/@消息）增加 `increment` 点唤醒度
- **衰减**：每分钟衰减 `decay_per_minute` 点
- **阈值**：唤醒度 ≥ `threshold` 时，机器人被"吵醒"，放行消息
- **上限**：唤醒度最高不超过 `max_value`

## 🎯 提示词注入

插件会根据当前状态自动向 LLM 提示词中注入状态描述：

- **AFC 群聊**：注入到 `s4u_style_prompt`
- **KFC 私聊**：注入到 `kfc_main`、`kfc_replyer`、`kfc_unified_prompt`

示例注入内容：
```
[状态感应: 你刚被吵醒，有点迷糊，可能带点小脾气。]
```

## 📝 管理命令

插件提供 `/sleep` 命令用于管理和调试：

```
/sleep status          - 查看当前睡眠状态
/sleep set <状态>      - 强制设置状态 (awake/drowsy/sleeping)
/sleep wake <会话ID>   - 手动唤醒指定会话
```

## 🏗️ 项目结构

```
biometric_sleep_plugin/
├── __init__.py              # 插件元数据
├── plugin.py                # 插件主类
├── config.py                # 配置定义
├── README.md                # 本文档
└── src/
    ├── core/
    │   └── sleep_manager.py # 状态机核心
    ├── handlers/
    │   ├── sleep_interceptor.py  # 消息拦截器
    │   └── sleep_prompt.py       # 提示词注入
    └── commands/
        └── sleep_admin_command.py  # 管理命令
```

## 📄 许可证

GPL-v3.0-or-later

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📮 联系

- GitHub: [@tt-P607](https://github.com/tt-P607)
- Repository: [biometric_sleep_plugin](https://github.com/tt-P607/biometric_sleep_plugin)