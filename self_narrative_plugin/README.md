# Self Narrative Plugin - 自我叙事插件

**版本：** 1.0.0  
**作者：** Neo-MoFox Team  

自我叙事插件为模型提供一套按聊天流隔离的“持续自我理解”缓存系统。它不只是记录事实，而是维护一份稳定的角色内在叙事，包括：

- 我如何理解自己
- 我反复出现的行为模式
- 我还没有解释清楚的张力
- 我需要保持的稳定边界

这套状态可以每日自动更新，也可以手动触发更新，并在需要时注入到主回复的 system prompt 尾部补充区中。

---

## 功能

### 1. 自我叙事状态管理

- 每个聊天流都有独立的自我叙事文件。
- 状态按 `stream_id` 隔离，私聊、群聊、讨论组互不干扰。
- 内部保存当前叙事、更新历史、上次更新时间等信息。

### 2. 自动日更

- 默认每天 00:00 自动更新一次自我叙事。
- 启动时可补跑未执行的日更。
- 更新内容会综合多种来源：
  - 今天的日记摘要
  - 当前聊天流的连续记忆
  - 当前自我叙事状态
  - 睡眠/唤醒状态快照

### 3. 手动更新与查看

- 可以通过命令手动更新当前聊天流的自我叙事。
- 可以查看当前自我叙事摘要。
- 可以查看最近的更新历史。
- 可以重置当前聊天流的自我叙事。

### 4. Prompt 注入

- 可以把当前聊天流的自我叙事摘要注入到目标 system prompt。
- 支持控制是否显示稳定边界。
- 支持控制是否显示近期演化历史。

---

## 目录结构

```text
self_narrative_plugin/
├── manifest.json
├── plugin.py
├── config.py
├── service.py
├── prompts.py
├── commands/
│   └── self_narrative_command.py
├── components/
│   └── events/
│       ├── startup_event.py
│       └── prompt_injector.py
└── README.md
```

---

## 配置

配置文件位于 `config/plugins/self_narrative_plugin/config.toml`，首次运行后会自动生成。

### `[plugin]`

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `enabled` | `true` | 是否启用插件 |
| `inject_prompt` | `true` | 是否在 prompt 构建时注入自我叙事摘要 |
| `include_identity_bounds_in_prompt` | `true` | 是否在 prompt 中显示稳定边界 |
| `include_history_in_prompt` | `false` | 是否在 prompt 中显示近期演化历史 |

### `[storage]`

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `base_path` | `"data/self_narratives"` | 自我叙事存储根目录 |
| `max_history_records` | `12` | 保留的历史更新记录上限 |
| `max_prompt_items_per_section` | `3` | prompt 中每个分区展示的条目数上限 |

### `[schedule]`

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `enabled` | `true` | 是否启用每日自动更新 |
| `update_time` | `"00:00"` | 每日自动更新触发时间 |
| `catch_up_on_startup` | `true` | 启动时是否补跑未执行的日更 |
| `manual_cooldown_seconds` | `300` | 手动更新冷却时间 |

### `[prompt]`

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `target_prompt_names` | `["default_chatter_system_prompt"]` | 允许注入自我叙事的 prompt 模板名 |
| `prompt_title` | `"自我叙事"` | prompt 中显示的标题 |
| `max_history_lines` | `3` | prompt 中显示的历史记录条数 |

### `[model]`

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `task_name` | `"diary"` | 自我叙事更新使用的模型任务名 |
| `fallback_task_name` | `"actor"` | 主任务不可用时的回退任务名 |

### `[narrative]`

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `default_identity_bounds` | `["我更重视真实表达，而不是迎合", "我倾向先理解再判断", "我不希望自己变得过度机械"]` | 初始稳定边界 |
| `default_self_view` | `[]` | 初始自我理解 |
| `default_ongoing_patterns` | `[]` | 初始反复模式 |
| `default_open_loops` | `[]` | 初始未完成问题 |

---

## 更新流程

### 自动更新

每日自动更新大致会走下面的流程：

1. 读取当前聊天流的自我叙事状态。
2. 收集输入素材：
   - 日记摘要
   - 连续记忆
   - 当前状态
   - 睡眠状态
   - 聊天元数据
3. 调用模型生成新的自我叙事增量。
4. 合并到现有状态中，并保留历史记录。
5. 写入对应聊天流的 JSON 文件。

### 手动更新

手动更新通过命令触发，和自动更新使用同一套更新逻辑，但会额外受冷却时间限制。

### 重置

重置会清空当前聊天流的自我叙事内容，并用 `narrative.default_*` 里的默认值重新初始化。

---

## 命令

插件提供 `/self_narrative` 命令，常用子命令如下：

### `update`

立即更新当前聊天流的自我叙事。

### `view`

查看当前聊天流的自我叙事摘要。

### `history`

查看最近的更新历史。

### `reset`

重置当前聊天流的自我叙事状态。

示例：

```text
/self_narrative update
/self_narrative view
/self_narrative history
/self_narrative reset
```

---

## Prompt 注入

当 `plugin.inject_prompt = true` 时，插件会在 `on_prompt_build` 阶段把自我叙事块注入到目标 system prompt 中。

默认目标模板是：

- `default_chatter_system_prompt`

注入内容一般包括：

- 当前自我理解
- 反复出现的模式
- 尚未解释完的问题
- 稳定边界
- 可选的近期演化历史

实际注入位置是 system prompt 末尾的补充区，而不是 history 或 user prompt 的 extra 区块。

这能帮助主回复模型在长对话中保持更稳定的自我连续性，而不是每轮都像重新开始。

---

## 存储格式

每个聊天流的状态会保存为 JSON 文件，默认路径类似：

```text
data/self_narratives/private/<stream_id>.json
data/self_narratives/group/<stream_id>.json
data/self_narratives/discuss/<stream_id>.json
```

保存内容包括：

- 当前自我叙事条目
- 更新历史
- 最近一次自动更新日期
- 最近一次手动更新时间

---

## 适用场景

### 1. 让模型更稳定地“记得自己是谁”

如果你发现模型在长对话里开始失去角色连续性，自我叙事可以作为一种内部稳定层。

### 2. 让 prompt 更像“持续成长中的人格”

这套状态不是客观数据库，而更像一个随时间变化的自我理解总结。

### 3. 作为连续记忆之上的更高层抽象

连续记忆偏“经历了什么”，自我叙事偏“这些经历让我怎么看待自己”。

---

## 常见问题

### 为什么会有每日自动更新？

因为这个插件的目标不是只做静态记忆，而是让自我理解持续演化。每日更新可以把最近的变化慢慢沉淀下来。

### 为什么要分聊天流？

不同聊天场景可能会形成不同的关系结构和表达习惯，按 `stream_id` 隔离可以避免互相污染。

### 为什么 prompt 注入是可选的？

因为不是所有场景都适合额外注入自我叙事。某些任务可能更需要轻量上下文，所以提供开关控制。

### 为什么主任务失败时还有回退任务？

为了提高可用性。主任务不可用时，系统仍然可以尝试用回退模型完成更新。

---

## 安装

把 `self_narrative_plugin/` 目录放入 Neo-MoFox 的 `plugins/` 目录即可。

首次启动后会自动生成配置文件，并在启用自动调度时建立日更任务。

---

## 相关文件

- 插件入口：`plugins/self_narrative_plugin/plugin.py`
- 服务实现：`plugins/self_narrative_plugin/service.py`
- 提示词：`plugins/self_narrative_plugin/prompts.py`
- 命令：`plugins/self_narrative_plugin/commands/self_narrative_command.py`
- 启动事件：`plugins/self_narrative_plugin/components/events/startup_event.py`
- Prompt 注入：`plugins/self_narrative_plugin/components/events/prompt_injector.py`
