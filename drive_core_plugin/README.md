# drive_core_plugin - 内驱力 / 自我引擎

`drive_core_plugin` 为角色提供一层持续推进的内部工作区。它不是普通记忆，也不是对话偏好，而是负责把内部张力转成“当前我想弄清什么”的系统。

## 作用

- 维护按聊天流隔离的内驱力状态
- 自动生成自我发问
- 在连续聊天中持续推进一轮内部工作
- 将当前工作区注入到主回复 prompt
- 提供命令查看、推进和重置状态

## 目标工作流

1. 先从内部状态里生成当前课题。
2. 再由系统自己找出问题。
3. 然后查最近日记、连续记忆、自我叙事和未完成念头。
4. 边查边更新假设。
5. 在证据足够时收束成阶段性理解。

## 配置概览

- `trigger_every_n_messages`: 每隔多少次有效对话推进一次
- `history_window_size`: 推进时看多少条最近历史
- `max_inquiry_steps`: 单个课题允许推进几轮
- `target_prompt_names`: 哪些 prompt 允许注入

当目标模板名以 `_system_prompt` 结尾时，注入内容会进入 `extra_info`，避免堆到用户历史信息里；如果保留旧式 `_user_prompt`，则仍写入 `extra` 以兼容。

## 命令

- `/drive_core view`
- `/drive_core history`
- `/drive_core advance`
- `/drive_core reset`

## 存储

默认持久化在：

- `data/drive_core/private/<stream_id>.json`
- `data/drive_core/group/<stream_id>.json`
- `data/drive_core/discuss/<stream_id>.json`
