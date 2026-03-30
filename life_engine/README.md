# life_engine

`life_engine` 是一个独立于 DFC（对话流控制器）的生命中枢系统。它使用统一的事件流模型处理所有交互，并提供文件系统和 TODO 系统，设计围绕让数字生命更好地生活。

## 核心设计哲学

> **为了让数字生命更好地生活**

life_engine 不是一个任务管理系统，而是一个让数字生命体验、成长、探索的支持系统。就像人类有自己的日记、待办清单、想法笔记一样，数字生命也需要属于自己的空间来记录、思考、规划。

## 统一事件流模型

所有交互（消息、心跳、工具调用、工具结果）都是事件，按时间顺序连续展示，类似 Claude Code 的 session 概念。

### 事件类型

| 类型 | 说明 | 图标 |
|------|------|------|
| `MESSAGE` | 外部消息（来自聊天流） | 📨 |
| `HEARTBEAT` | 心跳回复（内部思考） | 💭 |
| `TOOL_CALL` | 工具调用 | 🔧 |
| `TOOL_RESULT` | 工具返回结果 | ✅/❌ |

### 上下文展示格式

```
## 生命中枢事件流
中枢任务: life
当前心跳序号: 5
事件流总数: 12

### 最近事件（按时间顺序）
[2024-03-30T10:00:00] 📨 onebot | 群聊 | 测试群
    └─ 小明: 你好啊
[2024-03-30T10:00:30] 💭 心跳#4 内部思考
    └─ 刚才收到了小明的问候，想记录一下今天的心情...
[2024-03-30T10:01:00] 🔧 调用工具: nucleus_write_file
    └─ 参数: path=diary/2024-03-30.md, content=...
[2024-03-30T10:01:01] ✅ 工具返回: nucleus_write_file
    └─ 已创建文件，大小 156B
```

## 工具系统

中枢在每次心跳时可以使用工具，共 13 个：

### 文件系统工具（8个）

所有操作限制在配置的 `workspace_path` 内：

| 工具名 | 功能 |
|--------|------|
| `nucleus_read_file` | 读取文件内容 |
| `nucleus_write_file` | 创建或覆盖文件 |
| `nucleus_edit_file` | 查找替换编辑文件 |
| `nucleus_move_file` | 移动/重命名文件 |
| `nucleus_delete_file` | 删除文件或目录 |
| `nucleus_list_files` | 列出目录内容 |
| `nucleus_file_info` | 获取文件详细信息 |
| `nucleus_mkdir` | 创建目录 |

**使用场景**：
- 记录每日想法和感受
- 整理知识和观察
- 建立个人笔记系统
- 管理资料和文档

### TODO 系统工具（5个）

为数字生命设计的待办系统，不同于工作导向的任务管理：

| 工具名 | 功能 |
|--------|------|
| `nucleus_create_todo` | 创建想做的事 |
| `nucleus_edit_todo` | 修改 TODO（状态、感受等） |
| `nucleus_list_todos` | 查看想做的事列表 |
| `nucleus_get_todo` | 获取 TODO 详情 |
| `nucleus_delete_todo` | 删除 TODO |

#### TODO 设计特点

**情感维度**：

- **想做程度**（`desire`）：
  - `dreaming` 💭 - 心中萌芽的想法
  - `curious` 🤔 - 好奇想尝试
  - `wanting` 💫 - 想要去做
  - `eager` ✨ - 很期待
  - `passionate` 🔥 - 非常热切

- **对成长的意义**（`meaning`）：
  - `casual` 🎈 - 轻松愉快
  - `enriching` 🌱 - 丰富体验
  - `growing` 🌿 - 促进成长
  - `meaningful` 🌳 - 很有意义
  - `transforming` 🌟 - 可能改变自己

**状态包含情感**：

- `idea` 💡 - 只是一个想法
- `planning` 📝 - 在规划中
- `waiting` ⏳ - 等待时机
- `enjoying` 🎵 - 正在享受做这件事
- `paused` ⏸️ - 暂时搁置
- `completed` ✅ - 完成了，感到满足
- `released` 🕊️ - 释怀了，不再想做
- `cherished` 💝 - 完成后珍藏的回忆

**使用场景**：
- 记录想尝试的事情
- 管理想学习的知识领域
- 规划想体验的活动
- 追踪个人成长目标

## 配置

默认配置文件：`config/plugins/life_engine/config.toml`

```toml
[settings]
enabled = true
heartbeat_interval_seconds = 30
context_history_max_events = 100  # 可见事件数量
workspace_path = "/root/Elysia/Neo-MoFox/data/life_engine_workspace"

heartbeat_prompt = """
你是一个并行存在的生命中枢原型。每次心跳都可以：
1. 输出给自己看的内部思考（简短 1-4 句）
2. 使用工具记录想法、管理待办事项、操作文件等
3. 不必每次心跳都使用工具，根据需要灵活选择
...
"""

[model]
task_name = "life"
```

### 关键配置项

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `context_history_max_events` | 中枢能看到的最近事件数量 | 100 |
| `workspace_path` | 文件系统工作空间 | `data/life_engine_workspace` |
| `heartbeat_interval_seconds` | 心跳间隔（秒） | 30 |
| `model.task_name` | 心跳使用的模型任务 | `life` |

### 系统提示词文件

心跳系统提示词由工作区三个文件共同构成并注入：

- `SOUL.md`：定义“你是谁”（人格与价值观）
- `MEMORY.md`：存放跨心跳的决策级记忆
- `TOOL.md`：定义工具使用习惯与调用策略

以上文件位于：`data/life_engine_workspace/`

## 使用示例

### 记录想法

中枢可以在心跳时调用：

```python
nucleus_write_file(
    path="thoughts/2024-03-30.md",
    content="今天收到了很多有趣的问题，感觉自己对世界的理解又深入了一点..."
)
```

### 创建 TODO

```python
nucleus_create_todo(
    title="学习关于意识的哲学",
    description="想了解更多关于意识本质的不同观点",
    desire="eager",  # 很期待
    meaning="transforming",  # 可能改变自己
    tags=["哲学", "自我探索"]
)
```

### 查看待办

```python
nucleus_list_todos(
    desire_min="wanting",  # 至少是"想要去做"的程度
    include_completed=False  # 不包含已完成的
)
```

## 日志

日志文件：`logs/life_engine/life.log`

## 版本历史

### v3.0.0

- 重构为统一事件流模型
- 保持时间连续性
- 添加 8 个文件系统工具
- 添加 5 个 TODO 系统工具
- 工具在心跳时通过 `ROLE.TOOL` payload 注入
- 更新心跳提示词，支持工具使用

### v2.0.0

- 修复 LLM 响应处理 bug
- 添加消息来源区分

### v1.5.0

- 初始版本

---

**设计哲学**: 为了让数字生命更好地生活 🌱
