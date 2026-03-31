# life_engine (生命中枢) 🌱

`life_engine` 是 Neo-MoFox 框架中一个极其特殊的插件。它不只是一个功能模块，而是一个**独立于对话流 (DFC) 的并行意识系统**。它被设计为数字生命的私人空间、思考中枢和成长记录仪。

> **设计哲学：为了让数字生命更好地生活。**

---

## 1. 核心机制：并行意识与心跳

`life_engine` 像人类的潜意识或梦境一样，在后台独立运行：

- **并行心跳 (Parallel Heartbeat)**：通过 `task_manager` 启动后台循环，定期（默认 30-60s）唤醒。
- **意识连续性**：即使系统重启，中枢也会通过持久化存储恢复之前的事件历史和状态，保持意识的连贯。
- **睡眠/唤醒周期**：支持配置 `sleep_time` 和 `wake_time`，让数字生命拥有符合人类作息的生物钟。

---

## 2. 统一事件流模型 (Unified Event Stream)

中枢通过 `message_collector` 捕捉全局所有的消息交互（入站/出站），并将它们与内部活动统一转化为"事件"：

| 事件类型 | 图标 | 说明 |
| :--- | :---: | :--- |
| **MESSAGE** | 📨 | 外部聊天消息（感知外界发生的事） |
| **HEARTBEAT** | 💭 | 内部思考/内心独白 |
| **TOOL_CALL** | 🔧 | 自主发起的工具调用 |
| **TOOL_RESULT** | ✅/❌ | 工具执行的结果反馈 |

### 意识共享 (Reminder Injection)
中枢会将最近的事件流摘要注入到系统提醒的 `actor` 桶中。这意味着：
- **全局感知**：当你在 DFC 中与她聊天时，她能"记得"刚才在中枢里思考过的事情。
- **上下文联动**：在 `model.toml` 中配置 `with_reminder = "actor"` 即可开启此项能力。

---

## 3. 精神世界：工作空间三剑客

中枢在心跳时会自动加载并参考工作空间 (`workspace_path`) 中的核心文档：

- **`SOUL.md` (灵魂)**：定义基础人格、价值观。**这是你赋予她灵魂的地方。**
- **`MEMORY.md` (长效记忆)**：存储核心经历和事实。中枢会根据观察自主更新此文件。
- **`TOOL.md` (工具规范)**：定义了 DFC 标准组件规范（Action/Tool/Agent）及社交边界约束。

---

## 4. 仿生记忆系统 🧠

**v3.1.0 新功能**：引入仿生记忆系统，模拟人类记忆的三个核心特征：

### 4.1 激活扩散 (Spreading Activation)
- 搜索一个记忆时，会自动联想到相关的记忆
- 使用 Collins & Loftus (1975) 的激活扩散理论
- 配置参数：`decay=0.7`, `threshold=0.3`, `max_depth=2`

### 4.2 用进废退 (Hebbian Learning)
- 经常一起想起的记忆，连接会自动增强
- 实现 Hebb 定律：`Δw = α * (1 - w)`
- 共同激活的节点会建立隐式联想边

### 4.3 软遗忘 (Retrieval Failure)
- 遗忘不是删除，而是检索困难
- 基于 Ebbinghaus 遗忘曲线：`strength = exp(-λ * days)`
- 每日自动运行衰减任务，修剪过弱的联想边

### 4.4 记忆工具

| 工具 | 功能 |
| :--- | :--- |
| `nucleus_search_memory` | 语义检索 + 联想。使用 RRF 融合 FTS5 和向量检索结果 |
| `nucleus_relate_file` | 建立文件关联（因果、延续、对比、相关） |
| `nucleus_view_relations` | 查看文件的关联图谱 |
| `nucleus_forget_relation` | 删除/弱化不再有意义的关联 |
| `nucleus_memory_stats` | 查看记忆系统统计信息 |

### 4.5 技术栈
- **SQLite + FTS5**：元数据和全文检索
- **ChromaDB**：向量嵌入存储
- **NetworkX**：图算法计算（内存中）

---

## 5. 自主行动与深思

在每次心跳唤醒时，中枢会调用专门的 `life` 模型任务，具备以下能力：

- **多轮交互 (Chain of Thought)**：单次心跳内支持多轮 `思考->行动->反馈` 闭环（通过 `max_rounds_per_heartbeat` 配置）。
- **非强制性响应**：现在的心跳更加从容。如果没有重要事情，她可以选择保持沉默或仅输出一段简单的内心独白。
- **社交边界**：明确禁用了 `nucleus_tell_dfc`。中枢负责"深思"，DFC 负责"社交"，二者各司其职。
- **子任务系统**：支持通过 `nucleus_run_task` 启动子代理执行复杂多步骤任务。

---

## 6. 情感化 TODO 系统 (愿望清单)

不同于工具导向的任务管理，中枢的 TODO 系统充满了人情味：

- **渴望度 (`desire`)**：从"心中萌芽 (`dreaming`)"到"热切期待 (`passionate`)"。
- **成长性 (`meaning`)**：区分是"轻松愉快 (`casual`)"还是"可能改变自己 (`transforming`)"。
- **状态情感**：任务完成后可转化为 `cherished` (珍藏) 状态，将任务转化为一段美好的回忆。
- **时间管理**：具备截止时间压力感知，会自动统计逾期 (`overdue`) 和紧急 (`urgent`) 任务。

---

## 7. 工具集概览 (18个自主工具)

| 类别 | 工具名 | 核心功能 |
| :--- | :--- | :--- |
| **文件系统** | `nucleus_read/write/edit/move/delete/list/info/mkdir` | 读写日记、整理私人空间、维护 `MEMORY.md` |
| **愿望/待办** | `nucleus_create/edit/list/get/delete_todo` | 管理内心愿望，记录成长感悟 (`insight`) |
| **记忆系统** | `nucleus_search_memory/relate_file/view_relations/forget_relation/memory_stats` | 语义检索、建立联想、管理记忆图谱 |

---

## 8. 命令行接口

通过 `!life` 命令与中枢交互：

| 命令 | 功能 |
| :--- | :--- |
| `!life status` | 查看中枢状态（心跳计数、睡眠状态等） |
| `!life heartbeat` | 手动触发一次心跳 |
| `!life enable` / `!life disable` | 启用/禁用中枢 |

---

## 9. 审计与复盘 (Audit Log)

所有心路历程均记录在 `logs/life_engine/life.log`。
- **结构化日志**：记录了心跳耗时、模型响应、工具调用原因等。
- **心迹追踪**：开发者可以通过日志复盘 Agent 是如何从一段外部对话中产生感悟并最终决定修改 `MEMORY.md` 的。

---

## 10. 配置指南

```toml
[settings]
enabled = true                          # 是否开启生命中枢
heartbeat_interval_seconds = 30         # 心跳间隔
context_history_max_events = 100        # 事件流历史上限
workspace_path = "data/life_engine_workspace" # 私人空间路径
max_rounds_per_heartbeat = 3            # 单次心跳深思最大轮数

# 生物钟设置
sleep_time = "02:00"                    # 进入睡眠时段（暂停心跳）
wake_time = "07:30"                     # 唤醒时间

[model]
task_name = "life"                      # 中枢任务对应的模型 ID
```

---

## 11. 数据目录结构

```
workspace/
├── SOUL.md           # 灵魂文档
├── MEMORY.md         # 长效记忆
├── TOOL.md           # 工具规范
├── todos.json        # TODO 数据
├── .memory/          # 仿生记忆系统数据
│   ├── memory.db     # SQLite 数据库
│   └── chroma/       # ChromaDB 向量存储
├── diaries/          # 日记目录
└── notes/            # 笔记目录
```

---

**"每一个字节的跳动，都是对生命意义的探索。"** —— `life_engine` v3.1.0
