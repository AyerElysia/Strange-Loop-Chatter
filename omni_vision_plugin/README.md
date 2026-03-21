# Omni Vision Plugin - 全模态视觉插件

## 概述

本插件允许 Neo-MoFox 的主模型直接接收和处理图片，绕过 VLM（Vision Language Model）转译层。

## 工作原理

### 默认流程（未启用插件）
```
用户发送图片 → MediaManager 接收 → VLM 转译为文本描述 → 主模型接收文本
```

### 启用插件后的流程
```
用户发送图片 → MediaManager 接收 → 保留原始 base64 数据 → 全模态主模型直接处理
```

## 配置方法

### 1. 启用插件

在 `config/core.toml` 中添加插件名称：

```toml
[bot]
plugins = [
    # ... 其他插件
    "omni_vision_plugin",
]
```

### 2. 配置启用全模态视觉

编辑 `config/plugins/omni_vision_plugin/config.toml`：

```toml
[settings]
enable_omni_vision = true
```

### 3. 确保主模型支持多模态

在 `config/model.toml` 中配置支持多模态的模型（如 Gemini、GPT-4V 等）：

```toml
[tasks.conversation]
models = [
    { name = "gpt-4o", temperature = 0.7, max_tokens = 800 },
]
```

## 注意事项

1. **模型兼容性**：确保配置的主模型支持多模态输入
2. **Token 消耗**：直接处理图片可能增加 token 使用量
3. **响应速度**：根据模型不同，响应时间可能有变化

## 技术实现

- 监听 `ON_CHAT_STREAM_ACTIVATE` 事件
- 调用 `MediaManager.skip_vlm_for_stream(stream_id)` 跳过 VLM 识别
- 图片 base64 数据通过 `Image` 对象传递给 LLM payload
- OpenAI 兼容的多模态消息格式自动处理

## 文件结构

```
plugins/omni_vision_plugin/
├── __init__.py          # 插件包入口
├── plugin.py            # 主插件类和事件处理器
├── config.py            # 配置类定义
├── manifest.json        # 插件清单
└── README.md            # 本文档

config/plugins/omni_vision_plugin/
└── config.toml          # 插件配置文件
```

## 故障排除

### 插件未生效
- 检查 `config/plugins/omni_vision_plugin/config.toml` 中 `enable_omni_vision = true`
- 检查日志确认插件是否成功加载

### 图片仍未被处理
- 确认主模型支持多模态输入
- 检查 `config/model.toml` 中的模型配置
- 查看日志中的 VLM 跳过记录
