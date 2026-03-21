# Prompt Logger Plugin

**提示词记录插件** — 将所有发送给 LLM 的完整提示词记录到日志文件

## 功能特性

- **自动记录**: 通过 monkey-patch LLMRequest.send() 方法，自动拦截并记录所有 LLM 请求和响应
- **格式化输出**: 使用美观的面板格式记录提示词，包含角色、内容、工具参数等
- **日志轮转**: 支持日志文件大小限制和自动轮转，避免日志文件过大
- **灵活配置**: 可配置是否记录请求/响应、是否显示工具、内容截断长度等
- **过滤功能**: 支持按聊天类型（私聊/群聊）和 Chatter 名称过滤

## 安装

1. 将插件目录复制到 Neo-MoFox 的 plugins 目录
2. 配置 `config/plugins/prompt_logger.toml`
3. 重启 Bot

## 使用方法

### 自动记录（推荐）

插件加载后会自动安装拦截器，无需修改任何代码即可记录所有 LLM 交互。

### 手动记录

如果只想记录特定 chatter 的提示词，可以使用插件提供的 API：

```python
from src.app.plugin_system.api import get_service

# 获取服务实例
service = get_service("prompt_logger", "prompt_logger")

# 记录请求
service.log_request(
    payloads=llm_request.payloads,
    stream_id=stream_id,
    chatter_name="my_chatter",
    request_name="my_request",
)

# 记录响应
service.log_response(
    message=llm_response.message,
    call_list=llm_response.call_list,
    stream_id=stream_id,
    chatter_name="my_chatter",
    request_name="my_request",
)

# 或者一次性记录完整交互
service.log_llm_interaction(
    payloads=llm_request.payloads,
    message=llm_response.message,
    call_list=llm_response.call_list,
    stream_id=stream_id,
    chatter_name="my_chatter",
)
```

### 使用便捷函数

```python
from prompt_logger.service import (
    log_prompt_request,
    log_prompt_response,
    log_llm_interaction,
)

# 记录请求
log_prompt_request(
    payloads=payloads,
    stream_id=stream_id,
    chatter_name="my_chatter",
)

# 记录响应
log_prompt_response(
    message=message,
    call_list=call_list,
    stream_id=stream_id,
    chatter_name="my_chatter",
)
```

## 配置说明

配置文件位于 `config/plugins/prompt_logger.toml`：

### [general] 通用配置

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `enabled` | `true` | 是否启用提示词记录功能 |
| `log_file` | `"logs/prompt_logger/prompt.log"` | 日志文件路径（相对于项目根目录） |
| `max_log_size_mb` | `10` | 单个日志文件的最大大小（MB） |
| `backup_count` | `5` | 保留的备份日志文件数量 |

### [format] 格式配置

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `show_request` | `true` | 是否记录 LLM 请求提示词 |
| `show_response` | `true` | 是否记录 LLM 响应内容 |
| `show_tools` | `true` | 是否在日志中显示工具参数 |
| `show_timestamp` | `true` | 是否在日志中显示时间戳 |
| `truncate_content_length` | `5000` | 单个 payload 内容的最大截断长度（0 表示不限制） |

### [filter] 过滤配置

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `log_private_chat` | `true` | 是否记录私聊聊天流的提示词 |
| `log_group_chat` | `true` | 是否记录群聊聊天流的提示词 |
| `excluded_chatters` | `[]` | 不记录提示词的 Chatter 名称列表 |

## 日志格式示例

### 请求日志

```
══ LLM REQUEST ══ [2026-03-19 10:30:45] | stream=abc12345 | chatter=kokoro_flow_chatter
============================================================

━━ PERSONA / RELATIONSHIP ━━
┌─ SYSTEM ─┐
你是一个友好的 AI 助手...

══ TOOLS (共 3 个) ══
1. kfc_reply
   描述：发送回复消息
   参数:
     - content (string) [必需]: 回复内容

━━ SYSTEM (History) ━━
以下为融合叙事时间线：
[10:25:00] 用户说：你好

━━ CONVERSATION ━━
┌─ USER ─┐
[新消息]
》10:30》<群主> [QQ:123456] 用户：你好
============================================================
```

### 响应日志

```
══ LLM RESPONSE ══ [2026-03-19 10:30:46] | stream=abc12345 | chatter=kokoro_flow_chatter
============================================================

━━ MESSAGE ━━
你好！有什么我可以帮助你的吗？

━━ TOOL CALLS ━━
1. kfc_reply (id=call_123)
   参数：{
     "content": "你好！有什么我可以帮助你的吗？"
   }
============================================================
```

## 注意事项

1. **性能影响**: 日志记录会产生额外的 I/O 开销，建议在开发/调试环境使用
2. **隐私保护**: 日志文件可能包含敏感信息，请妥善保管
3. **磁盘空间**: 请注意日志文件大小，定期清理旧日志
4. **兼容性**: 本插件通过 monkey-patch 实现，理论上与所有 chatter 兼容

## 故障排查

### 日志文件没有内容

1. 检查插件是否启用：`enabled = true`
2. 检查日志级别设置
3. 查看 `logs/` 目录下是否有其他日志文件

### 日志文件过大

1. 调小 `max_log_size_mb` 配置
2. 减少 `backup_count` 数量
3. 调小 `truncate_content_length` 以截断长内容

### 某些 chatter 的提示词没有记录

1. 检查 `excluded_chatters` 配置
2. 检查 `log_private_chat` 和 `log_group_chat` 配置

## 开发者说明

### 拦截器原理

本插件通过 monkey-patch `LLMRequest.send()` 方法实现自动拦截：

```python
# 保存原始方法
_original_send = LLMRequest.send

# 替换为包装后的方法
LLMRequest.send = _patched_send_async
```

### 禁用拦截器

如果不想使用自动拦截，可以在配置中设置 `enabled = false`，或者：

```python
from prompt_logger.interceptor import uninstall_interceptor
uninstall_interceptor()
```

## 版本历史

- **1.0.0** (2026-03-19) - 初始版本
  - 自动拦截 LLM 请求和响应
  - 格式化日志输出
  - 日志轮转支持
  - 灵活的过滤配置
