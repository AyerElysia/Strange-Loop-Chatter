# default_chatter

`default_chatter` 是 Neo-MoFox 的核心对话插件之一。  
从当前代码实现看，它已经不只是“默认聊天组件”，而是一个带有状态机控制、子代理判定、工具调用去重、多模态输入编排与调试观测能力的对话执行框架。

## 你这版改动后，它的定位

这版 `default_chatter` 的关键价值可以概括为 7 点：

1. 稳定人设注入：系统提示词始终注入完整人设字段，降低行为漂移与调参复杂度。
2. 双执行模式：`enhanced`（FSM 连续回合）与 `classical`（单轮更克制）。
3. 工具调用控制流：同轮/跨轮去重、`pass_and_wait` 分支、`action-only` 自动补 `__SUSPEND__`。
4. 子代理决策：群聊先判定“要不要回”，私聊直接放行。
5. 原生多模态：支持把图片/表情包/视频直接编入 LLM payload，并可跳过 VLM 转译。
6. 调试可视化：可输出完整 prompt 面板和 tool call 摘要，便于定位模型行为。
7. 中枢异步桥接：可向 `life_engine` 留言，但不阻塞等待中枢同步返回。

## 目录结构

```text
default_chatter/
├── plugin.py                 # 插件入口、Action、Chatter 主体、模板注册
├── runners.py                # enhanced/classical 两套执行器
├── tool_flow.py              # 工具调用处理、去重、SUSPEND 注入
├── decision_agent.py         # 子代理判定与 token 预算裁剪
├── prompt_builder.py         # system/user prompt 组装
├── multimodal.py             # 图片/表情包/视频提取与 content 组装
├── nucleus_bridge.py         # DFC -> 生命中枢 的异步留言工具
├── config.py                 # 配置模型定义（模式、调试、多模态等）
├── type_defs.py              # 运行时协议与类型约束
├── debug/
│   ├── __init__.py
│   └── log_formatter.py      # prompt / response 调试输出格式化
└── manifest.json             # 插件元信息与 include 组件声明
```

## 核心组件

### 1) Chatter: `DefaultChatter`

- 入口：`plugin.py` 中 `DefaultChatter.execute()`
- 负责：
  - 激活会话流（stream）
  - 注册/反注册多模态场景下的 VLM skip
  - 根据 `mode` 分派到 `run_enhanced()` 或 `run_classical()`

### 2) Action: `send_text`

- 工具名：`action-send_text`
- 能力：
  - 发送文本
  - 支持 `reply_to` 引用回复
  - 自动清洗模型侧漏的 `reason` 片段
- 限制：
  - 仅文本，不直接发送表情包等非文本对象

### 3) Action: `pass_and_wait`

- 工具名：`action-pass_and_wait`
- 语义：本轮不动作，挂起等待下一条用户消息。

### 4) Tool: `message_nucleus`

- 工具名：`tool-message_nucleus`
- 语义：
  - 向 `life_engine` 异步留言
  - 不等待中枢即时回复
  - 由中枢在后续 heartbeat 自己整理，再决定是否调用 `nucleus_wake_dfc`
- 适用场景：
  - “另一个我最近在想什么？”
  - “关于那个人，另一个我有没有什么意见？”
  - “这件事你先替我交给中枢慢慢想”

这个工具只暴露给 `default_chatter`，不会出现在其他 chatter 的工具列表里。

## 执行流（重点）

## `enhanced` 模式（默认）

`runners.py` 使用简化 FSM，显式维护四个相位：

- `WAIT_USER`
- `MODEL_TURN`
- `TOOL_EXEC`
- `FOLLOW_UP`

关键行为：

1. 只在 `WAIT_USER` 接收并合并新未读消息。
2. 若上下文尾部是 `TOOL_RESULT`，强制进入 `FOLLOW_UP`，避免时序错乱。
3. `MODEL_TURN` 发送请求后才 flush 本轮未读，降低“已读但未处理”的风险。
4. 工具执行结束后：
  - 若有非 action 工具结果，进入 `FOLLOW_UP` 继续推理。
  - 若仅 action，补 `__SUSPEND__` 后回到等待态。

## `classical` 模式

`classical` 每次有新未读时重新构建请求，策略更保守：

1. 子代理通过后再发起本轮请求。
2. 允许同一轮多次 `action-send_text` 分段发送。
3. 一旦已成功发送文本，后续非 action 调用可被自动跳过（`break_on_send_text=True`）。
4. 达到“已发送一次”后可强制结束当前对话回合（`Stop(0)`）。

适用场景：希望更稳、更省资源、降低无穷 follow-up 的风险。

## 工具调用控制（`tool_flow.py`）

`process_tool_calls()` 是你这版的关键增强点之一：

1. 去重键：`call_name + normalized_args`（剔除 `reason` 字段干扰）。
2. 同轮重复调用：自动写回 `TOOL_RESULT` 并跳过执行。
3. 跨轮重复调用：基于 `cross_round_seen_signatures` 去重并跳过。
4. `pass_and_wait`：直接写入等待结果并标记 `should_wait=True`。
5. `send_text` 成功标记：供 classical 的“发一次后收敛”策略使用。
6. `has_pending_tool_results` 只对非 action 调用置位，避免 action 触发无意义二次推理。

`append_suspend_payload_if_action_only()`：

- 若本轮所有调用均是 `action-*`，自动追加 assistant `__SUSPEND__`，确保对话结构完整，避免上下文里缺 assistant 轮次。

## 子代理判定（`decision_agent.py`）

主逻辑：`decide_should_respond()`。

行为细节：

1. 私聊直接放行（在 `DefaultChatter.sub_agent()` 里短路）。
2. 群聊调用 `sub_actor/sub_agent` 模型进行判定。
3. 为避免判定模型被长文本压垮，会按模型上下文估算 token 预算并裁剪未读文本尾部。
4. 返回 JSON 通过 `json_repair` 解析，解析失败或异常时默认“响应”（保守兜底）。

返回结构：

```json
{
  "reason": "判定理由",
  "should_respond": true
}
```

## 提示词构建（`prompt_builder.py` + `plugin.py`）

### 模板注册

`on_plugin_loaded()` 会注册三套模板：

- `default_chatter_system_prompt`
- `default_chatter_sub_agent_prompt`
- `default_chatter_user_prompt`

### system prompt 关键来源

1. 平台信息：通过 adapter 读取 bot 平台昵称与 ID。
2. 人设字段：`personality_core / personality_side / reply_style / identity / background_story`。
3. 场景引导：按聊天类型选择 `theme_guide.private` 或 `theme_guide.group`。
4. 安全准则与负面行为：来自核心 personality 配置。

### 人设注入策略

系统提示词会稳定注入完整人设字段：

- `personality_core`
- `personality_side`
- `reply_style`
- `identity`
- `background_story`

这能保证角色锚点稳定、提示词行为可预测，也更利于缓存命中。

### user prompt 关键策略

1. 统一消息行格式（时间/角色/ID/昵称/消息ID/内容）。
2. 可注入历史区块、新消息区块、额外约束区块。
3. `extra` 可叠加“负面行为强化提醒”。

## 原生多模态（`multimodal.py` + runner 集成）

启用条件：`native_multimodal=true`。

能力：

1. 支持媒体类型：`image` / `emoji` / `video`。
2. 媒体提取来源：
  - `message.content.media`
  - `message.extra.media`
  - `message.media`
  - emoji 特例（`message_type=emoji` 且 content 为长 base64）
3. payload 组装：
  - 文本 + 图片/视频混合 content
  - 表情包会加 `[表情包]` 标签
  - 视频会加 `[视频]` 标签
4. 配额控制：
  - `max_images_per_payload`
  - `max_videos_per_payload`
  - 可通过 `native_video_multimodal` 一键关闭原生视频
5. 历史媒体补充：
  - 未读媒体优先
  - 剩余额度可回填历史媒体（倒序取近）

同时，`DefaultChatter` 会对当前 stream 调用 `skip_vlm_for_stream()`，退出时再取消，避免重复转译。

## 调试能力（`debug/log_formatter.py`）

开启开关：

- `plugin.debug.show_prompt=true`
- `plugin.debug.show_response=true`

效果：

1. Prompt 面板：按 `SYSTEM(人设)` -> `TOOLS` -> `历史/对话` 的阅读顺序展示。
2. 响应摘要：可按 tool 类型打印核心信息（例如 `send_text` 文本、`pass_and_wait` 等待状态）。

这套输出对排查“为什么模型这么回”很实用。

## 配置项速览（`config.py`）

| 配置项 | 类型 | 说明 |
|---|---|---|
| `plugin.enabled` | bool | 启用插件 |
| `plugin.mode` | `enhanced` / `classical` | 执行模式 |
| `plugin.reinforce_negative_behaviors` | bool | 每轮 user prompt 追加负面行为约束 |
| `plugin.native_multimodal` | bool | 原生多模态开关 |
| `plugin.max_images_per_payload` | int | 单次图片上限 |
| `plugin.max_videos_per_payload` | int | 单次视频上限 |
| `plugin.native_video_multimodal` | bool | 是否原生传视频 |
| `plugin.theme_guide.private` | str | 私聊场景引导 |
| `plugin.theme_guide.group` | str | 群聊场景引导 |
| `plugin.debug.show_prompt` | bool | 打印完整 prompt |
| `plugin.debug.show_response` | bool | 打印响应摘要 |

## 已知行为与注意事项

1. 当前策略强依赖“模型优先返回 tool call”。若模型只回纯文本，会触发停止分支。
2. `classical` 模式偏“快收敛”，适合稳态业务；`enhanced` 更适合复杂多工具链。
3. `send_text` 是文本动作，不承担图片/视频发送。
4. `manifest.json` 与 `plugin.py` 的作者/描述元信息目前仍是默认值；若要在插件列表中展示你的自定义描述，需要同步修改这两处字段。

## 维护建议

如果你准备继续演进这个插件，优先建议沿下面顺序推进：

1. 给 `enhanced/classical` 各补一组行为回归测试（尤其是去重与 `SUSPEND` 分支）。
2. 给子代理判定加可观测指标（命中率、误判率、平均截断长度）。
3. 把“是否允许纯文本回退”做成显式配置，便于不同业务场景切换容错策略。
