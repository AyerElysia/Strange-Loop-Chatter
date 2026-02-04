# Scheduler 快速参考卡片

## 一页纸快速参考

### 导入和初始化

```python
from kernel.scheduler import get_unified_scheduler, TriggerType, TaskStatus

# 获取全局调度器
scheduler = get_unified_scheduler()
await scheduler.start()
```

### 创建任务的三种方式

#### 1️⃣ 延迟执行（5秒后）
```python
await scheduler.create_schedule(
    callback=my_task,
    trigger_type=TriggerType.TIME,
    trigger_config={"delay_seconds": 5},
    task_name="delayed_task"
)
```

#### 2️⃣ 周期执行（每10秒）
```python
await scheduler.create_schedule(
    callback=my_task,
    trigger_type=TriggerType.TIME,
    trigger_config={"interval_seconds": 10},
    is_recurring=True,
    task_name="periodic_task"
)
```

#### 3️⃣ 条件触发
```python
async def check_condition():
    return some_flag == True

await scheduler.create_schedule(
    callback=my_task,
    trigger_type=TriggerType.CUSTOM,
    trigger_config={"condition_func": check_condition},
    is_recurring=True,
    task_name="conditional_task"
)
```

### 任务管理

```python
# 查找任务
schedule_id = await scheduler.find_schedule_by_name("my_task")

# 获取任务详情
task = await scheduler.get_schedule(schedule_id)

# 列出所有任务
tasks = await scheduler.list_all_schedules()

# 强制执行任务
await scheduler.trigger_schedule(schedule_id)

# 移除任务
await scheduler.remove_schedule(schedule_id)
```

### 监控和统计

```python
# 获取执行历史
history = await scheduler.get_task_execution_history(schedule_id, limit=10)
for exec in history:
    print(f"{exec.status.value} - {exec.duration:.2f}s")

# 获取统计信息
stats = await scheduler.get_statistics()
print(f"总执行: {stats['total_executions']}")
print(f"失败: {stats['total_failures']}")

# 获取任务统计
task = await scheduler.get_schedule(schedule_id)
print(f"成功率: {task.success_count / task.trigger_count:.1%}")
```

### 高级配置

```python
from kernel.scheduler import SchedulerConfig, UnifiedScheduler

config = SchedulerConfig(
    check_interval=1.0,              # 检查间隔
    task_default_timeout=300.0,      # 默认超时
    max_concurrent_tasks=100,        # 最大并发
    enable_retry=True,               # 启用重试
    max_retries=3,                   # 最大重试次数
    retry_delay=5.0                  # 重试延迟
)

scheduler = UnifiedScheduler(config)
await scheduler.start()
```

### 参数和返回值速查

| 方法 | 主要参数 | 返回值 |
|------|---------|--------|
| `create_schedule` | callback, trigger_type, trigger_config, is_recurring, task_name, timeout, max_retries | `schedule_id: str` |
| `get_schedule` | schedule_id | `ScheduleTask \| None` |
| `find_schedule_by_name` | task_name | `schedule_id: str \| None` |
| `list_all_schedules` | - | `list[ScheduleTask]` |
| `remove_schedule` | schedule_id | `bool` |
| `trigger_schedule` | schedule_id | `bool` |
| `get_task_execution_history` | schedule_id, limit | `list[TaskExecution]` |
| `get_statistics` | - | `dict[str, Any]` |

### 常用的 trigger_config

| 场景 | trigger_config |
|------|---|
| 延迟 5 秒 | `{"delay_seconds": 5}` |
| 每 10 秒 | `{"interval_seconds": 10}` |
| 指定时间 | `{"trigger_at": datetime(...)}` |
| 自定义条件 | `{"condition_func": async_func}` |
| 每日 2 点 | `{"trigger_at": tomorrow_2am, "interval_seconds": 86400}` |

### 任务状态检查

```python
task = await scheduler.get_schedule(schedule_id)

# 检查状态
if task.status == TaskStatus.RUNNING:
    print("任务执行中")
elif task.status == TaskStatus.COMPLETED:
    print("任务完成")
elif task.status == TaskStatus.FAILED:
    print(f"任务失败: {task.last_error}")
elif task.status == TaskStatus.TIMEOUT:
    print("任务超时")
```

### 完整示例

```python
import asyncio
from kernel.scheduler import get_unified_scheduler, TriggerType

async def my_background_task():
    print("任务正在执行...")
    await asyncio.sleep(1)
    print("任务完成")

async def main():
    # 启动调度器
    scheduler = get_unified_scheduler()
    await scheduler.start()
    
    try:
        # 创建每 5 秒执行一次的任务
        await scheduler.create_schedule(
            callback=my_background_task,
            trigger_type=TriggerType.TIME,
            trigger_config={"interval_seconds": 5},
            is_recurring=True,
            task_name="bg_task",
            timeout=10.0,
            max_retries=2
        )
        
        # 运行 30 秒
        await asyncio.sleep(30)
        
    finally:
        await scheduler.stop()

asyncio.run(main())
```

### 关键类和方法

```python
# 获取全局调度器
get_unified_scheduler() -> UnifiedScheduler

# 调度器生命周期
await scheduler.start()      # 启动
await scheduler.stop()       # 停止

# 任务生命周期
schedule_id = await scheduler.create_schedule(...)   # 创建
await scheduler.remove_schedule(schedule_id)        # 删除

# 查询和监控
task = await scheduler.get_schedule(schedule_id)
stats = await scheduler.get_statistics()
history = await scheduler.get_task_execution_history(...)
```

### 枚举值速查

**TriggerType**：
- `TriggerType.TIME` - 时间触发
- `TriggerType.EVENT` - 事件触发
- `TriggerType.CUSTOM` - 自定义条件

**TaskStatus**：
- `TaskStatus.PENDING` - 等待中
- `TaskStatus.RUNNING` - 执行中
- `TaskStatus.COMPLETED` - 已完成
- `TaskStatus.FAILED` - 已失败
- `TaskStatus.TIMEOUT` - 已超时
- `TaskStatus.CANCELLED` - 已取消

### 错误处理

```python
try:
    # 创建任务
    schedule_id = await scheduler.create_schedule(...)
except RuntimeError as e:
    print(f"调度器未运行: {e}")
except ValueError as e:
    print(f"配置错误: {e}")

# 检查执行错误
task = await scheduler.get_schedule(schedule_id)
if task.last_error:
    print(f"最后一次错误: {task.last_error}")
```

### 性能提示

```python
# 高并发场景
config = SchedulerConfig(
    max_concurrent_tasks=50,      # 限制并发
    enable_task_semaphore=True    # 启用信号量
)

# 低延迟场景
config = SchedulerConfig(
    check_interval=0.1,           # 更频繁检查（CPU占用增加）
)

# 关键任务
config = SchedulerConfig(
    task_default_timeout=600.0,   # 更长的超时
    enable_retry=True,
    max_retries=5
)
```

---

**更多信息**：查看 [INDEX.md](./INDEX.md) 获取完整文档导航
