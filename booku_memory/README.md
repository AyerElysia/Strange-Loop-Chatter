# Booku Memory Agent 插件

为 MoFox-Bot 机器人提供基于 Agent 驱动的分层记忆系统，支持向量语义检索、自动去重合并、记忆闪回等高级功能。

## ✨ 功能特性

### 核心能力

- **分层记忆架构**：三层记忆存储设计
  - `emergent`（隐现层）：近期活跃记忆，7 天窗口内自动晋升/丢弃
  - `archived`（归档层）：长期保存的重要记忆
  - `inherent`（固有层）：全局唯一的背景知识/规则

- **双 Agent 模式**：支持两种运行模式
  - **Agent 代理模式**（默认）：对外暴露 `booku_memory_write` / `booku_memory_read` 两个 Agent，内部自主规划检索/写入流程
  - **Tool 工具模式**：直接暴露 3 个核心 Tool：`memory_retrieve`、`memory_create`、`memory_edit_inherent`

- **向量语义检索**：EPA（扩散 - 对立 - 核心）向量动力学重塑
  - 基于 TAG 三角（core/diffusion/opposing）的语义匹配
  - 自适应重塑强度（基于逻辑深度和跨域共振）
  - 结果去重（余弦相似度阈值 0.88）

- **自动去重合并**：写入前计算新颖度能量比
  - 使用局部 SVD 子空间投影算法
  - 低于阈值时自动合并到现有记忆
  - 避免重复信息堆积

- **记忆闪回机制**：在对话中随机注入历史记忆
  - 可配置触发概率和层级选择概率
  - 按激活次数反向加权（激活越少越易被回忆）
  - 支持冷却时间避免重复闪回

- **知识库集成**：支持本地文档导入
  - 启动时自动导入配置路径的文档
  - 支持 `.txt`/`.md`/`.json`/`.csv`/`.docx` 等格式
  - 自动分块（900 字符/块，重叠 120 字符）并写入向量库

### 记忆文件夹分类

| Folder ID | 用途 | 示例 |
|-----------|------|------|
| `relations` | 人物关系 | 朋友、家人、同事关系信息 |
| `plans` | 未来规划 | 待办事项、目标、计划 |
| `facts` | 已知事实 | 用户的基本信息、客观事实 |
| `preferences` | 个人偏好 | 喜欢的食物、颜色、品牌 |
| `events` | 重要事件 | 发生过的重要对话或事件 |
| `work` | 工作学习 | 项目进展、学习内容 |
| `default` | 未分类 | 其他记忆 |

## 📦 安装

将整个 `booku_memory` 文件夹复制到 `plugins/` 目录下即可。

确保已配置 embedding 模型任务（在 `config/model.toml` 中）：
```toml
[model.embedding]
# 配置你的 embedding 模型...
```

## ⚙️ 配置

在 `config/plugins/booku_memory/config.toml` 中进行配置：

```toml
[plugin]
# 是否启用插件
enabled = true
# 是否启用 Agent 代理模式（关闭后使用 Tool 模式）
enable_agent_proxy_mode = true
# 是否将记忆引导语注入到 actor system reminder
inject_system_prompt = true

[storage]
# SQLite 元数据数据库路径
metadata_db_path = "data/booku_memory/metadata.db"
# 向量数据库路径
vector_db_path = "data/chroma_db/booku_memory"
# 默认文件夹 ID
default_folder_id = "default"

[retrieval]
# 默认召回条数
default_top_k = 5
# 默认是否检索归档记忆
include_archived_default = false
# 默认是否检索知识库
include_knowledge_default = false
# 结果去重余弦阈值
deduplication_threshold = 0.88
# 向量重塑基准强度 (0-1)
base_beta = 0.3
# 逻辑深度对 beta 的增益系数
logic_depth_scale = 0.5
# 核心标签增强范围
core_boost_min = 1.2
core_boost_max = 1.4
# 扩散标签增强权重
diffusion_boost = 0.3
# 对立标签惩罚权重
opposing_penalty = 0.5

[write_conflict]
# 写入冲突检查的检索样本数
top_n = 8
# 新颖度能量阈值，低于此值触发合并
energy_cutoff = 0.1

[time_window]
# 隐现记忆时间窗口（天），超出后进入晋升检查
emergent_days = 7
# 隐现记忆在窗口内最少激活次数，达到后晋升为归档
activation_threshold = 2

[internal_llm]
# 内部决策使用的模型任务名
task_name = "tool_use"
# 内部 tool-calling 最大推理轮数
max_reasoning_steps = 12

[flashback]
# 是否启用记忆闪回
enabled = false
# 每次构建 user prompt 时触发闪回的概率 (0-1)
trigger_probability = 0.05
# 触发后抽取归档层记忆的概率 (0-1)，隐现层概率为 1-该值
archived_probability = 0.6
# 限定抽取的 folder_id（留空表示不限制）
folder_id = ""
# 每次抽取时最多加载的候选记忆数量
candidate_limit = 50
# 激活次数权重指数（越大越偏向低激活记忆）
activation_weight_exponent = 1.0
# 闪回去重冷却时间（秒），0 表示不启用
cooldown_seconds = 3600

[chunking]
# 单块最大字符数
max_chunk_chars = 900
# 相邻块重叠字符数
overlap_chars = 120

[startup_ingest]
# 是否在启动时自动导入配置路径文档
enabled = true
# 启动时自动导入的文件或目录路径列表
paths = ["data\\booku_memory\\knowledges"]
# 是否递归扫描子目录
recursive = true
# 路径不存在时是否跳过
skip_missing_paths = true
# 文档标题已存在时是否跳过导入
skip_existing_title = true
```

## 🔧 Agent 模式 vs Tool 模式

### Agent 代理模式（推荐）

启用 `enable_agent_proxy_mode = true` 时，对外暴露两个 Agent：

#### `booku_memory_write` - 写入 Agent

负责将记忆信息写入到指定层级与文件夹中。

**执行流程：**
1. 意图解析与信息提取
2. 可选检索（防重复/防误改）
3. 标签构建（TAG 三角合规校验）
4. 执行操作（新建/更新/删除/移动）
5. 结果验证与审计记录
6. 总结返回（调用 `memory_finish_task`）

**TAG 三角规则（强制）：**
- `core_tags`：≥1 个核心语义标签，描述"这是什么"
- `diffusion_tags`：≥1 个扩散关联标签，描述"相关什么"
- `opposing_tags`：≥1 个对立标签，描述"不是什么"

#### `booku_memory_read` - 读取 Agent

在回答用户问题前自动检索记忆库，返回语义摘要。

**检索策略（优先级从高到低）：**
1. `inherent` 层：固有记忆（全局背景）
2. `emergent` 层：近期活跃记忆
3. `archived` 层：仅在 `include_archived=true` 时检索
4. `knowledge` 层：仅在 `include_knowledge=true` 时检索

### Tool 工具模式

禁用 `enable_agent_proxy_mode = false` 时，直接暴露以下 Tool：

| Tool 名称 | 功能 |
|-----------|------|
| `memory_create` | 创建记忆（自动去重） |
| `memory_edit_inherent` | 编辑固有记忆 |
| `memory_inherent_read` | 读取固有记忆 |
| `memory_retrieve` | 语义检索（TAG 三角驱动） |
| `memory_grep` | 关键词检索（支持正则） |
| `memory_status` | 查询记忆状态/数量 |
| `memory_read_full_content` | 读取完整正文 |
| `memory_update_by_id` | 按 ID 更新记忆 |
| `memory_delete` | 删除记忆（软删/硬删） |
| `memory_move` | 移动记忆（跨 folder/bucket） |
| `memory_finish_task` | 结束 Agent 任务 |

## 🧠 记忆引导语注入

插件会在启动时将记忆引导语同步到 `actor` bucket 的 system reminder，帮助 AI 理解如何正确使用记忆系统。引导语内容包括：

- 记忆价值观念（积极创建和检索）
- 固有记忆的重要性说明
- 具体、清晰、可追溯的写入建议

## 🏗️ 项目结构

```
booku_memory/
├── __init__.py                 # 插件元数据
├── plugin.py                   # 插件主类（注册入口）
├── config.py                   # 配置定义（含所有配置节）
├── manifest.json               # 插件清单文件
├── rag_params.json             # RAG 热参数（可选）
├── flashback.py                # 闪回逻辑纯函数实现
├── event_handler.py            # 事件处理器（启动导入/闪回注入）
├── README.md                   # 本文档
├── agent/
│   ├── __init__.py
│   ├── read_agent.py           # 读取 Agent 实现
│   ├── write_agent.py          # 写入 Agent 实现
│   ├── tools.py                # Agent 工具集（11 个 Tool）
│   └── shared.py               # Agent 共享工具函数
└── service/
    ├── __init__.py
    ├── booku_memory_service.py # 核心记忆服务（主逻辑）
    ├── booku_knowledge_service.py  # 知识库服务
    ├── metadata_repository.py  # 元数据仓储（SQLite CRUD）
    ├── models.py               # 数据模型定义
    └── result_deduplicator.py  # 结果去重器
```

## 📋 核心 API 说明

### BookuMemoryService 主要方法

```python
# 写入或自动合并记忆
async def upsert_memory(
    content: str,
    title: str | None = None,
    bucket: str = "emergent",
    folder_id: str | None = None,
    tags: list[str] | None = None,
    core_tags: list[str] | None = None,
    diffusion_tags: list[str] | None = None,
    opposing_tags: list[str] | None = None,
) -> dict

# 执行 EPA 向量动力学重塑后的语义检索
async def retrieve_memories(
    query_text: str,
    folder_id: str | None = None,
    top_k: int | None = None,
    include_archived: bool | None = None,
    include_knowledge: bool | None = None,
    core_tags: list[str] | None = None,
    diffusion_tags: list[str] | None = None,
    opposing_tags: list[str] | None = None,
) -> dict

# 编辑全局固有记忆
async def edit_inherent_memory(content: str) -> dict

# 按关键词 grep 记忆
async def grep_memories(
    query: str,
    search_fields: list[str],
    folder_id: str | None = None,
    include_archived: bool = False,
    top_k: int = 10,
    use_regex: bool = False,
) -> dict

# 移动记忆到新位置
async def move_memories(
    memory_ids: list[str],
    to_bucket: str | None = None,
    to_folder_id: str | None = None,
) -> dict

# 删除记忆（软删/硬删）
async def delete_memories(
    memory_ids: list[str],
    hard: bool = False,
) -> dict
```

## 🔍 检索流程说明

1. **初始检索**：使用原始查询向量在各集合中召回候选
2. **逻辑深度计算**：基于投影熵计算检索子空间的专注程度
3. **共振估计**：判断查询是否具有跨域特征
4. **重塑强度计算**：`beta = base_beta + logic_depth * scale + resonance_bonus`
5. **TAG 向量收集**：从候选记忆中按标签匹配收集核心/扩散/对立向量
6. **向量重塑**：使用 TAG 动力学公式重塑查询向量
7. **二次检索**：用重塑后的向量再次检索并打分
8. **结果去重**：使用余弦相似度阈值消除冗余结果

## 📝 最佳实践

### 写入记忆

1. **标签质量优先**：每个标签都应具备检索价值
   - `core_tags`：从记忆类型角度（偏好/事件/知识/待办/关系）
   - `diffusion_tags`：从应用场景角度（饮食/旅行/工作/学习/健康）
   - `opposing_tags`：从排除干扰角度（非紧急/非正式/反面案例/待验证）

2. **最小变更原则**：用户意图模糊时，宁可少改不多改

3. **安全编辑流程**：更新前必须先读取原文，防止覆盖丢失

### 检索记忆

1. **对话开始时**：必须调用读取 Agent 检索用户身份和偏好
2. **用户提到"之前说过"**：立即检索相关记忆
3. **需要个性化建议时**：先查用户历史喜好
4. **不确定时**：优先检索而非猜测

### 固有记忆维护

- 固有记忆是全局背景，不按 folder 隔离
- 编辑前必须调用 `memory_inherent_read` 读取现有内容
- 每次修改会全量覆写，需确保内容完整

## 🐛 故障排查

### 常见问题

**Q: 写入后检索不到记忆？**
- 检查 embedding 模型配置是否正确
- 确认 `folder_id` 是否匹配
- 尝试设置 `include_archived=true` 检索归档层

**Q: Agent 内部推理超过最大步数？**
- 增加 `internal_llm.max_reasoning_steps` 配置
- 简化输入内容，避免一次性写入过多信息

**Q: 启动导入失败？**
- 检查 `startup_ingest.paths` 路径是否存在
- 确认文件格式是否在支持列表中
- 查看日志文件获取详细错误信息

**Q: 向量维度不一致错误？**
- 可能是更换了 embedding 模型导致
- 删除 `data/chroma_db/booku_memory` 后重建
- 或执行全量向量重生成覆盖流程

## 📄 许可证

GPL-v3.0-or-later

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📮 联系

- GitHub: [@tt-P607](https://github.com/tt-P607)
- Repository: [Neo-MoFox](https://github.com/tt-P607/Neo-MoFox)
