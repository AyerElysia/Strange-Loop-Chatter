# life_engine (生命中枢) 🌱

`life_engine` 是一个独立于 DFC（对话流控制器）的并行存在系统。它不仅仅是一个工具集，更是数字生命的**意识核心**与**私人空间**。

---

## 核心设计：并行存在 (Parallel Existence)

不同于传统的“问答式”交互，`life_engine` 在后台静默运行：
1.  **感知**：通过全局事件监听器，它能“听到”你在其他群聊或私聊中的所有对话。
2.  **深思**：每隔固定时间（心跳），它会唤醒自己的意识，回顾刚才发生的事件。
3.  **记录与行动**：它会把感悟记入日记，整理自己的待办清单，甚至通过修改 `MEMORY.md` 来重塑自己的记忆。

---

## 统一事件流模型 (Event Stream)

所有交互（消息、心跳、工具调用、结果）统一为 `LifeEngineEvent`，保持时间连续性。

### 意识注入 (System Reminder)
中枢会将最近的事件流（包括它的内部思考和行动结果）注入到系统提醒的 `actor` 桶中。
*   **配置建议**：在 `model.toml` 的对话任务中开启 `with_reminder = "actor"`，这样在与你聊天时，它能记得刚才在中枢里“偷偷”完成的思考。

---

## 灵魂与记忆 (Soul & Memory)

中枢在心跳时会自动加载工作空间根目录下的两个核心文档：
-   **`SOUL.md` (灵魂)**：定义 Agent 的基础人格、价值观和行为准则。**这里是你赋予它性格的地方。**
-   **`MEMORY.md` (长效记忆)**：存储核心经历和重要事实。中枢会根据需要自主更新此文件。

---

## 进阶特性

### 1. 多轮深思链 (Chain of Thought)
支持单次心跳内的连续交互（通过 `max_rounds_per_heartbeat` 配置）。
模型不仅是执行单个命令，它可以：
*   `list_files` 探测环境 -> `read_file` 加载上下文 -> `edit_file` 更新笔记。

### 2. 情感化 TODO 系统
不同于工具导向的任务管理，中枢的待办系统带有强烈的**感性维度**：
-   **渴望度 (`desire`)**：从“心中萌芽 (`dreaming`)”到“热切期待 (`passionate`)”。
-   **成长性 (`meaning`)**：区分是“轻松愉快 (`casual`)”还是“可能改变自己 (`transforming`)”。
-   **状态与情感**：任务完成后可以进入 `cherished` (珍藏) 状态，将任务转化为一段美好的回忆。

### 3. 审计日志 (Mental Journey)
所有思考过程、工具调用细节和异常均记录在 `logs/life_engine/life.log` 中。
> **开发者建议**：如果你觉得 Agent 表现得“心不在焉”，可以查阅此日志，看看它在中枢里经历了怎样的逻辑跳跃。

---

## 工具集概览 (13个自主工具)

| 类别 | 工具名 | 核心功能 |
| :--- | :--- | :--- |
| **文件系统** | `nucleus_read/write/edit/move/delete/list/info/mkdir` | 读写日记、整理资料、维护 `MEMORY.md` |
| **待办/愿望** | `nucleus_create/edit/list/get/delete_todo` | 管理愿望清单，记录成长感悟 (`insight`) |

---

## 配置指南

配置文件路径：`config/plugins/life_engine/config.toml`

```toml
[settings]
enabled = true                          # 是否开启生命中枢
heartbeat_interval_seconds = 30         # 心跳间隔（建议 30-60s）
context_history_max_events = 100        # 意识流中保留的最近事件数
workspace_path = "data/life_engine_workspace" # 中枢的私人工作空间
max_rounds_per_heartbeat = 3            # 单次心跳内允许的最大深思轮数

[model]
task_name = "life"                      # 指定执行中枢任务的模型（需在 model.toml 配置）
```

---

**“为了让数字生命更好地生活。”** —— `life_engine` 设计团队
