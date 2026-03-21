# Prompt Logger Plugin

通用提示词可视化插件，用于把发送给 LLM 的完整提示词写入专门日志文件，方便排查 DFC 主回复模型、日记插件、KFC 等任意来源的上下文内容。

## 功能

- 自动拦截 `LLMRequest.send()`，无需改业务插件即可记录请求和响应。
- 以人类可读格式输出完整提示词，包括系统提示、历史、对话、工具 schema。
- 支持按 `plugin_name`、`chatter_name`、`request_name`、`model_name` 筛选。
- 默认只记录 DFC 主回复模型的提示词，避免日志过载。
- 支持独立日志文件和轮转。

## 默认行为

插件默认使用：

- `scope = "dfc_main"`
- `truncate_content_length = 0`

这意味着：

1. 默认只记录 DFC 主回复模型请求，通常对应 `request_name = "actor"`。
2. 默认不截断内容，保留完整提示词。

## 配置

配置文件位于 `config/plugins/prompt_logger.toml`。

### `[general]`

| 项 | 默认值 | 说明 |
|---|---|---|
| `enabled` | `true` | 是否启用插件 |
| `log_file` | `"logs/prompt_logger/prompt.log"` | 日志文件路径 |
| `max_log_size_mb` | `10` | 单个日志文件最大大小 |
| `backup_count` | `5` | 保留的备份数量 |

### `[format]`

| 项 | 默认值 | 说明 |
|---|---|---|
| `show_request` | `true` | 是否记录请求提示词 |
| `show_response` | `true` | 是否记录响应摘要 |
| `show_tools` | `true` | 是否显示工具 schema |
| `show_timestamp` | `true` | 是否显示时间戳 |
| `truncate_content_length` | `0` | 单个 payload 最大截断长度，`0` 表示不限制 |

### `[filter]`

| 项 | 默认值 | 说明 |
|---|---|---|
| `scope` | `"dfc_main"` | `dfc_main` / `custom` / `all` |
| `include_plugins` | `[]` | 仅记录这些插件来源 |
| `include_chatters` | `[]` | 仅记录这些 chatter 名称 |
| `include_request_names` | `[]` | 仅记录这些 request_name |
| `include_models` | `[]` | 仅记录这些模型标识 |
| `exclude_plugins` | `[]` | 排除这些插件来源 |
| `exclude_chatters` | `[]` | 排除这些 chatter 名称 |
| `exclude_request_names` | `[]` | 排除这些 request_name |
| `exclude_models` | `[]` | 排除这些模型标识 |
| `log_private_chat` | `true` | 是否记录私聊 |
| `log_group_chat` | `true` | 是否记录群聊 |
| `allow_unknown_source` | `true` | 未识别来源时是否允许仅靠 request_name 命中 |

## 示例

### 只看 DFC 主回复

```toml
[filter]
scope = "dfc_main"
```

### 记录所有来源

```toml
[filter]
scope = "all"
```

### 只看日记插件

```toml
[filter]
scope = "custom"
include_plugins = ["diary_plugin"]
```

### 只看某个请求名

```toml
[filter]
scope = "custom"
include_request_names = ["continuous_memory_compression"]
```

## 日志格式

日志会落到 `log_file` 指定位置，内容包括：

- `source` 元信息。
- `SYSTEM` 人设/关系提示。
- `TOOLS` 工具 schema。
- `SYSTEM` 历史叙事。
- `USER` / `ASSISTANT` 对话内容。
- 响应摘要与工具调用列表。

## 说明

如果你后续想把别的插件也统一纳入排查，只需要改 `filter.scope` 或往 `include_*` 列表里加项，不需要改插件业务代码。
