# Scheduler 模块文档索引

## 📚 文档导航

### 快速入门
- **[README.md](./README.md)** - 模块概述、快速开始、核心概念
  - 适合新手快速了解模块功能
  - 包含基础示例和常见问题

### API 参考
- **[core.md](./core.md)** - 核心类和方法的详细说明
  - `UnifiedScheduler` - 调度器主类
  - `ScheduleTask` - 调度任务
  - `SchedulerConfig` - 配置选项
  - 所有 API 的参数说明和示例

- **[types.md](./types.md)** - 类型定义
  - `TriggerType` - 触发类型枚举
  - `TaskStatus` - 任务状态枚举
  - `TaskExecution` - 执行记录模型

- **[time_utils.md](./time_utils.md)** - 时间工具函数
  - `next_after()` - 计算下一次触发时间
  - 时间计算原理和边界情况

### 高级用法
- **[advanced.md](./advanced.md)** - 高级特性和最佳实践
  - 并发控制
  - 错误处理
  - 性能优化
  - 监控和日志
  - 实战案例

---

## 🎯 按场景查找

### 我想...

#### 创建一个简单的定时任务
👉 [README.md - 周期性任务](./README.md#周期性任务)

#### 创建一个延迟执行的任务
👉 [README.md - 基础示例](./README.md#基础示例---延迟执行)

#### 创建一个条件驱动的任务
👉 [README.md - 自定义条件触发](./README.md#自定义条件触发)

#### 了解所有配置选项
👉 [core.md - SchedulerConfig](./core.md#schedulerconfig---调度器配置)

#### 监控和统计任务执行
👉 [advanced.md - 监控和日志](./advanced.md#监控和日志)

#### 限制并发执行的任务数
👉 [advanced.md - 并发控制](./advanced.md#并发控制)

#### 处理任务失败和超时
👉 [advanced.md - 错误处理](./advanced.md#错误处理)

#### 优化性能
👉 [advanced.md - 性能优化](./advanced.md#性能优化)

#### 查看实战案例
👉 [advanced.md - 实战案例](./advanced.md#实战案例)

#### 理解时间计算原理
👉 [time_utils.md](./time_utils.md)

---

## 📖 文档结构

```
docs/scheduler/
├── README.md              # 主文档（推荐从这里开始）
├── core.md               # API 参考
├── types.md              # 类型定义
├── time_utils.md         # 时间工具
├── advanced.md           # 高级用法
├── INDEX.md              # 本文件
└── examples/             # 示例代码（暂无，可自行补充）
```

---

## 🔑 关键概念

### TriggerType（触发类型）
- **TIME**：时间触发（延迟、周期、指定时间）
- **EVENT**：事件触发（预留）
- **CUSTOM**：自定义条件触发

### TaskStatus（任务状态）
- **PENDING**：等待触发
- **RUNNING**：执行中
- **COMPLETED**：已完成
- **FAILED**：执行失败
- **CANCELLED**：已取消
- **TIMEOUT**：执行超时

### SchedulerConfig 重要参数
- `check_interval`：检查间隔（秒）
- `task_default_timeout`：默认超时时间
- `max_concurrent_tasks`：最大并发数
- `enable_retry`：是否启用重试
- `max_retries`：最大重试次数

---

## 💡 最佳实践速查

| 需求 | 建议 |
|------|------|
| 一次性延迟任务 | 使用 TIME + delay_seconds，is_recurring=False |
| 周期性任务 | 使用 TIME + interval_seconds，is_recurring=True |
| 指定时间执行 | 使用 TIME + trigger_at |
| 条件驱动 | 使用 CUSTOM + condition_func |
| 关键任务 | 设置较长的 timeout 和 max_retries |
| 快速任务 | 可以禁用 enable_task_semaphore |
| 资源密集型 | 降低 max_concurrent_tasks |
| 高精度需求 | 减小 check_interval，但会增加 CPU 占用 |

---

## ⚠️ 常见陷阱

1. **未启动调度器**
   - ❌ `schedule_id = await scheduler.create_schedule(...)`
   - ✅ `await scheduler.start()` 然后再创建任务

2. **同名任务冲突**
   - ❌ 创建两个同名任务
   - ✅ 使用 `force_overwrite=True` 或检查任务是否存在

3. **同步任务阻塞**
   - ❌ 在同步函数中进行大量阻塞操作
   - ✅ 使用异步函数或将阻塞操作分散

4. **忘记设置超时**
   - ❌ 创建任务而不设置 timeout
   - ✅ 为所有任务设置合理的超时时间

5. **过度使用重试**
   - ❌ `max_retries=100`
   - ✅ 根据具体情况设置，通常 2-3 次足够

---

## 🚀 快速参考

### 创建任务的最小代码
```python
scheduler = get_unified_scheduler()
await scheduler.start()

await scheduler.create_schedule(
    callback=my_task,
    trigger_type=TriggerType.TIME,
    trigger_config={"delay_seconds": 5}
)
```

### 获取统计信息
```python
stats = await scheduler.get_statistics()
print(stats['total_executions'])
```

### 查看任务详情
```python
task = await scheduler.get_schedule(schedule_id)
print(task.execution_history)
```

### 停止调度器
```python
await scheduler.stop()
```

---

## 📞 获取帮助

- 查看 [README.md](./README.md) 的常见问题部分
- 阅读 [advanced.md](./advanced.md) 的实战案例
- 查阅 [core.md](./core.md) 的详细 API 说明

---

## 版本信息

- **当前版本**：1.0.0
- **最后更新**：2026-02-04
- **兼容 Python**：3.10+
