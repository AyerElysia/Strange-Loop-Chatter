# Unfinished Thought Plugin - 未完成念头插件

**版本：** 1.0.0  
**作者：** Neo-MoFox Team

未完成念头插件为每个聊天流维护一份独立的“后台思维片段池”。它记录的不是普通事实，也不是长期目标，而是那些仍然悬而未决、还会在后续对话中自然回来的片段，例如：

- 被打断的话题
- 没说完的想法
- 暂时压下去的情绪
- 没处理完的小目标

它的目标是让角色表现出更强的连续思维感，而不是每一轮都像重新开始。

---

## 功能

### 1. 按聊天流隔离存储

- 每个私聊、群聊、讨论组都有独立的未完成念头文件。
- 不同聊天流之间不会互相串数据。
- 重启后会从磁盘恢复。

### 2. 固定消息数自动扫描

- 每累计固定数量的有效对话后，自动触发一次扫描。
- 扫描时只把最近一段历史交给模型。
- 模型会基于“当前所有念头 + 最近历史”做增删改。
- 扫描失败时会恢复触发前的计数，避免丢掉下一次自动扫描节奏。

### 3. 主模型 prompt 注入

- 在目标 prompt 构建时，会自动注入当前活跃念头。
- 默认随机抽取 1 到 3 条。
- 只注入 `open` / `paused` 状态，不会把全部历史轰进去。

### 4. 命令管理

- 支持查看、添加、扫描、暂停、恢复和清空。
- `add` 支持直接输入完整文本，带空格的内容不会被截断。

---

## 目录结构

```text
unfinished_thought_plugin/
├── manifest.json
├── plugin.py
├── config.py
├── service.py
├── prompts.py
├── commands/
│   └── unfinished_thought_command.py
├── components/
│   └── events/
│       ├── scan_trigger_event.py
│       └── prompt_injector.py
└── README.md
```

---

## 配置

配置文件位于 `config/plugins/unfinished_thought_plugin/config.toml`，首次运行后会自动生成。

### `[plugin]`

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `enabled` | `true` | 是否启用插件 |
| `inject_prompt` | `true` | 是否在 prompt 构建时注入未完成念头 |

### `[storage]`

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `base_path` | `"data/unfinished_thoughts"` | 未完成念头根目录 |
| `max_thoughts` | `20` | 单个聊天流允许维护的念头上限 |
| `max_history_records` | `12` | 保留的扫描历史上限 |

### `[scan]`

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `trigger_every_n_messages` | `8` | 每隔多少条有效对话自动扫描一次 |
| `history_window_size` | `12` | 扫描时给念头模型看的历史记录条数 |

### `[prompt]`

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `target_prompt_names` | `["default_chatter_user_prompt"]` | 允许注入的 prompt 模板名 |
| `prompt_title` | `"未完成念头"` | prompt 中显示的标题 |
| `inject_min_items` | `1` | 随机注入最小条数 |
| `inject_max_items` | `3` | 随机注入最大条数 |

### `[model]`

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `task_name` | `"diary"` | 扫描未完成念头使用的模型任务名 |
| `fallback_task_name` | `"actor"` | 主任务不可用时的回退任务名 |

---

## 使用方式

### 1. 自动扫描

插件会在聊天过程中自动累计消息数。

当达到 `scan.trigger_every_n_messages` 后，它会：

1. 读取当前聊天流的未完成念头。
2. 读取最近 `scan.history_window_size` 条对话。
3. 调用扫描模型生成增删改结果。
4. 合并结果并持久化。

### 2. 手动管理

可通过 `/unfinished_thought` 命令直接管理当前聊天流的念头。

示例：

```text
/unfinished_thought view
/unfinished_thought add 我刚刚其实还没把那个话题想完
/unfinished_thought scan
/unfinished_thought pause th_xxx
/unfinished_thought resolve th_xxx
/unfinished_thought clear
```

### 3. Prompt 注入

当 `plugin.inject_prompt = true` 时，插件会在 `on_prompt_build` 阶段把当前活跃念头写入目标 prompt 的 `extra` 区块。

默认目标模板是：

- `default_chatter_user_prompt`

如果你想让其他 prompt 也看到未完成念头，只要把模板名加入 `prompt.target_prompt_names`。

---

## 状态说明

未完成念头默认支持以下状态：

- `open`
- `paused`
- `resolved`
- `expired`

其中：

- `open` 表示当前仍然活跃。
- `paused` 表示暂时挂起。
- `resolved` 表示已经自然收束。
- `expired` 表示过期清理。

---

## 存储格式

每个聊天流的状态会保存为 JSON 文件，默认路径类似：

```text
data/unfinished_thoughts/private/<stream_id>.json
data/unfinished_thoughts/group/<stream_id>.json
data/unfinished_thoughts/discuss/<stream_id>.json
```

每个文件包含：

- 当前聊天流标识
- 当前念头列表
- 最近扫描历史
- 消息计数
- 更新时间

---

## 与其他插件的关系

### 与 `diary_plugin`

`diary_plugin` 负责原始日记和连续记忆。

`unfinished_thought_plugin` 负责从当前对话和已保存状态里维护“仍未收束的片段”。

两者可以联动，但职责不同。

### 与 `self_narrative_plugin`

`self_narrative_plugin` 偏“我如何理解自己”。

`unfinished_thought_plugin` 偏“我还在想什么”。

这两个插件一起使用时，角色会更像有持续内部状态的主体，而不是只会记事实的聊天机器人。

---

## 说明

这个插件的第一版刻意保持简单：

- 不做语义相关性打分
- 不做 embedding 检索
- 不做复杂排序

先把“固定扫描 + 随机注入 + 持久化恢复”这条闭环稳定跑通，再继续往上叠加内部状态系统。
