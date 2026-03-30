# life_engine

`life_engine` 是一个并行存在的生命中枢最小原型，目前已经接上三件事：

- 保持一个独立的后台心跳服务
- 旁路收集所有聊天流的消息
- 在心跳时把待处理消息整理成上下文，注入到系统提醒里
- 同时把中枢相关事件写入 `logs/life_engine/`

## 当前已经做了什么

- 新增了 `life_engine` 插件入口，继续遵循现有插件框架的 `BasePlugin` + `BaseService` 写法。
- 新增了消息收集事件处理器，订阅 `ON_MESSAGE_RECEIVED`，所以平台上的私聊、群聊消息都会进入中枢队列。
- 收到的每条消息都会保留来源信息，包括：
  - 平台
  - 聊天类型
  - 群聊名称或私聊对象
  - 群 ID 或用户 ID
  - `stream_id`
  - 发送者信息
- 心跳到来时，会把当前积压的消息批量整理成一段上下文，并写入 `system reminder` 的 `actor` bucket。
- 暴露了一个独立的模型任务名：
  - `model.task_name = "life"`
  - 对应 `config/model.toml` 中的 `[model_tasks.life]`
- 保留了基础开关：
  - `enabled`: 是否启用插件
  - `heartbeat_interval_seconds`: 心跳间隔，单位秒
  - `heartbeat_prompt`: 心跳提示词
  - `log_heartbeat`: 是否输出每次心跳日志

## 当前明确没有做什么

- 不接管正常聊天回复
- 不做决策
- 不主动调用工具
- 不注入 system prompt 到聊天主链路
- 不做记忆、TODO、自主探索或情绪决策

## 运行方式

插件加载后会自动启动心跳；卸载时会停止后台任务并清空中枢上下文。默认配置文件路径为：

```text
config/plugins/life_engine/config.toml
```

如果你想调心跳节奏、提示词或模型任务，直接改配置即可。例如：

```toml
[settings]
enabled = true
heartbeat_interval_seconds = 30
heartbeat_prompt = "你是一个并行存在的生命中枢原型。当前阶段只需要记录自己的状态、等待下一次心跳，不要接管正常聊天流程。"
log_heartbeat = true

[model]
task_name = "life"
```

## 日志目录

中枢相关日志会写到：

```text
logs/life_engine/life.log
```

内容包括：

- 收到的消息来源信息
- 每次心跳状态
- 每次唤醒上下文注入
- 生命周期事件，例如加载、禁用、卸载

终端仍然会继续输出 `life_engine` 的运行日志，方便你实时观察。

## 模型任务

`life_engine` 现在不直接调用 LLM，但已经把任务路由预留好了。你可以在 `config/model.toml` 里维护一个独立的 `life` 任务，让中枢后续的唤醒、整理、回顾、探索都走这条路，而不是和普通聊天的 `actor` 混用。

## 后续扩展方向

这个版本只负责“活着”和“收消息”。后面可以继续往下扩展：

- 按时间窗口批量整理消息
- 把消息按来源压缩成更短的唤醒摘要
- 在空闲时主动检查待办
- 把中枢自己的行动和想法也纳入事件流
