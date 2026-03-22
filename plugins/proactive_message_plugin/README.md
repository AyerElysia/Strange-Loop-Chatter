# 主动消息插件 (Proactive Message Plugin)

让 Bot 具有在用户长时间未回复时主动发消息的能力，模拟真实的情感等待过程。

## ✨ 功能特性

### 核心功能

- **时间感知**：自动追踪用户最后消息时间，开始等待计时
- **内心独白**：触发 LLM 生成内心活动，表达对等待的情感反应
- **自主决策**：LLM 决定立即发消息 or 继续等待，等待时长也由 LLM 决定
- **独白历史注入**：之前的内心独白会注入上下文，形成连续的情感心路历程
- **循环等待机制**：支持多轮"等待→独白→决策"循环
- **智能过滤**：可配置忽略群聊等特定聊天类型

### 新增特性（v1.0.0）

- **发送后二次等待** (`post_send_followup_minutes`)：Bot 主动发消息后，若用户仍未回复，会在指定时间后再次触发内心独白，而不是直接结束
- **等待时长累积** (`checkpoint_wait`)：每次 LLM 选择"继续等待"时，会 checkpoint 已等待时长并累加，确保最大等待时间限制准确
- **可配置独白历史数量** (`monologue_history_limit`)：可控制 prompt 中注入多少条之前的内心独白，平衡上下文长度与情感连续性

## 📦 安装

将整个 `proactive_message_plugin` 文件夹复制到 `plugins/` 目录下即可。

## ⚙️ 配置

在 `config/plugins/proactive_message_plugin/config.toml` 中进行配置：

```toml
[settings]
# 启用插件
enabled = true

# 首次触发内心独白的等待时间（分钟）
# Bot 收到用户消息后，超过此时间未收到回复则触发内心独白
first_check_minutes = 10

# 默认最小等待间隔（分钟）
# 防止 LLM 说"等 1 分钟"导致过于频繁地触发独白
min_wait_interval_minutes = 5

# 最大等待时间（分钟）
# 超过此时间后强制触发内心独白，避免无限等待
max_wait_minutes = 180

# 内心独白历史提取数量
# 每次独白时注入多少条之前的独白内容，形成连续情感
monologue_history_limit = 5

# 【新增】主动发送后二次等待时间（分钟）
# Bot 主动发消息后，若用户仍未回复，在此时间后再次触发内心独白
post_send_followup_minutes = 10

# 忽略的聊天类型
# "group" = 群聊，"private" = 私聊
ignored_chat_types = ["group"]
```

## 🧠 核心机制

### 工作流程

```
用户 last_message 后开始计时
        ↓
等待 N 分钟（first_check_minutes）
        ↓
触发内心独白 (LLM 生成心理活动 + 注入之前的独白历史)
        ↓
LLM 自主决定:
  ├─ 发消息 → 发送后启动"二次等待"(post_send_followup_minutes)
  │           ↓
  │       若用户仍不回复 → 再次触发内心独白 → 循环
  │
  └─ 继续等 → checkpoint 累积等待时长 → 指定等待时间 → 循环
```

### 新增机制详解

**1. 发送后二次等待 (`post_send_followup_minutes`)**

旧逻辑：Bot 主动发消息后，本轮结束，等待用户回复。

新逻辑：Bot 主动发消息后，启动一个独立的二次等待计时器。若用户在 `post_send_followup_minutes` 分钟内仍未回复，会再次触发内心独白，让 LLM 决定下一步行动（再次发消息 or 继续等）。

这模拟了真实情感中的"我已经主动了一次，但你还是没理我，我现在是什么感觉？"。

**2. 等待时长累积 (`checkpoint_wait`)**

每次 LLM 选择"继续等待"时，调用 `service.checkpoint_wait()` 将本轮已等待时长累加到 `accumulated_wait_minutes`，并重置计时起点。

这确保 `max_wait_minutes` 限制是基于**累计等待时间**，而不是单次等待时间。

**3. 独白历史注入**

通过 `extract_monologue_history()` 从 `history_messages` 中过滤出 `is_inner_monologue=True` 的消息，取最近 N 条（`monologue_history_limit`）注入 prompt：

```
你之前的内心活动：
独白 1: 他是不是在忙啊，那我先等等吧
独白 2: 已经过了 15 分钟了，有点担心是不是出什么事了
```

### 内心独白注入机制

插件会将每次内心独白**持久化**到聊天历史中，下次触发时提取并注入 prompt：

```
你上次收到 小星星 的消息已经是 15 分钟了。
他一直没有回复你。

你记得你们之前的对话：
[22:30] 小星星：爱莉希雅，在吗？
[22:31] 爱莉希雅：哎呀，这么急切地想见我吗？♪

你之前的内心活动：
独白 1: 他是不是在忙啊，那我先等等吧
独白 2: 已经过了 15 分钟了，有点担心是不是出什么事了

你现在心里是什么感觉？
你在想他吗？还是担心他在忙？
或者有什么话想对他说？
```

这样 LLM 能看到自己之前的心路历程，形成**连续的情感感知**。

### DFC 可见性确认

内心独白通过 `plugin.py:_inject_inner_monologue()` 方法写入 `chat_stream.context.history_messages`，标记为 `is_inner_monologue=True`。

DFC（Default Flow Chatter）通过 `chat_stream.context.history_messages` 构建提示词，因此能正确读取并注入上下文。

## 🔧 工具

插件暴露以下工具供 LLM 调用：

### `wait_longer`

当你觉得现在还不是发消息的好时机，想再等一段时间时使用。

**参数**：
- `wait_minutes` (int): 想再等的分钟数
- `thought` (str): 等待时的内心想法

**示例调用**：
```json
{
  "name": "wait_longer",
  "args": {
    "wait_minutes": 30,
    "thought": "感觉他现在可能还在忙，再等等吧..."
  }
}
```

### `send_text`（来自 default_chatter）

主动发送一条消息。

**参数**：
- `content` (str): 消息内容

## 🏗️ 项目结构

```
proactive_message_plugin/
├── plugin.py                # 插件主类 + 事件处理器
├── config.py                # 配置定义
├── service.py               # 状态管理 + 调度任务
├── inner_monologue.py       # 内心独白生成 + 决策解析
├── README.md                # 本文档
├── manifest.json            # 插件清单
└── tools/
    └── wait_longer.py       # 等待工具定义
```

### 核心组件说明

| 文件 | 职责 |
|------|------|
| `plugin.py` | 事件订阅、等待计时触发、决策执行 |
| `service.py` | StreamState 管理、调度器集成 |
| `inner_monologue.py` | Prompt 构建、LLM 调用、响应解析 |
| `wait_longer.py` | 工具定义（实际调度由 service 处理） |

## 📝 日志示例

```
[22:36:31] 主动消息插件 | INFO | 开始等待计时：5750ede8...
[22:36:31] 主动消息服务 | INFO | 已调度检查任务：5750ede8... 将在 10 分钟后检查
[22:46:31] 主动消息插件 | INFO | 检查超时，触发内心独白：5750ede8...
[22:46:31] 内心独白 | INFO | 生成内心独白：5750ede8... 已等待 10 分钟
[22:46:43] 内心独白 | INFO | 内心独白内容：他已经 10 分钟没有回复了，是不是刚才的话题让他为难了？还是他在忙别的事情？有点想知道他在做什么...
[22:46:43] 内心独白 | INFO | 内心独白决策：wait_longer(15.0 分钟)
[22:46:43] 主动消息插件 | INFO | 继续等待：5750ede8... 等待 15.0 分钟
```

## 🔍 事件订阅

插件订阅以下事件：

| 事件 | 触发时机 | 处理逻辑 |
|------|----------|----------|
| `ON_MESSAGE_RECEIVED` | 收到用户消息 | 重置等待状态 |
| `ON_CHATTER_STEP` | Chatter 执行一步 | 检查是否进入 Wait 状态，进入则开始等待计时 |

## 🎯 使用场景

1. **情感陪伴型 Bot**：模拟真实的等待情绪，让用户感受到 Bot 的"在乎"
2. **主动关怀**：在用户长时间未回复时主动表达关心
3. **打破沉默**：当对话陷入停滞时，由 Bot 主动重启话题

## ⚠️ 注意事项

1. **需要重启 Bot**：新插件加载或配置修改后需重启 Bot 才能生效
2. **等待时间控制**：`min_wait_interval_minutes` 防止独白触发过于频繁
3. **最大等待限制**：`max_wait_minutes` 避免无限等待，达到后强制触发
4. **群聊默认忽略**：默认配置下群聊不会触发主动消息，可在配置中修改

## 📄 许可证

GPL-v3.0-or-later

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！
