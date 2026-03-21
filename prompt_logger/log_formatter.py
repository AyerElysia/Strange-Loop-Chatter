"""Prompt Logger 日志格式化工具。

将 LLM 请求/响应的 payload 列表格式化为人类可读的日志输出。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from src.kernel.llm import ROLE


def truncate_text(text: str, max_length: int) -> str:
    """截断文本到指定长度。

    Args:
        text: 要截断的文本
        max_length: 最大长度，0 表示不限制

    Returns:
        截断后的文本
    """
    if max_length <= 0 or len(text) <= max_length:
        return text
    return text[:max_length] + f"\n... [已截断，超出 {len(text) - max_length} 字符]"


def extract_payload_text(content: Any, truncate_length: int = 0) -> str:
    """从 payload 内容中提取文本。

    Args:
        content: payload 内容
        truncate_length: 截断长度，0 表示不限制

    Returns:
        提取的文本
    """
    if not isinstance(content, list):
        content = [content]

    text_parts = []
    for item in content:
        if hasattr(item, 'text'):
            text_parts.append(item.text)
        elif hasattr(item, 'value'):
            # Image 对象
            data_preview = str(item.value)[:40] if item.value else ""
            text_parts.append(f"[图片：{data_preview}...]")
        elif hasattr(item, 'to_text'):
            text_parts.append(item.to_text())
        elif hasattr(item, 'name') and hasattr(item, 'args'):
            # ToolCall 对象
            name = getattr(item, 'name', '?')
            args = getattr(item, 'args', {})
            try:
                args_str = json.dumps(args, ensure_ascii=False)
            except Exception:
                args_str = str(args)
            if truncate_length > 0 and len(args_str) > truncate_length:
                args_str = args_str[:truncate_length] + "..."
            text_parts.append(f"ToolCall(name={name!r}, args={args_str})")
        else:
            raw = str(item)
            if truncate_length > 0 and len(raw) > truncate_length:
                raw = raw[:truncate_length] + "..."
            text_parts.append(raw)

    return "\n".join(text_parts) if text_parts else "（空）"


def format_tools_for_log(tools: list[dict[str, Any]], truncate_length: int = 0) -> str:
    """格式化工具列表为日志文本。

    Args:
        tools: 工具 schema 列表
        truncate_length: 截断长度

    Returns:
        格式化后的工具列表文本
    """
    if not tools:
        return ""

    lines = []
    lines.append(f"══ TOOLS (共 {len(tools)} 个) ══")

    for i, schema in enumerate(tools, 1):
        func_info = schema.get("function", schema)
        name = func_info.get("name", "unknown")
        desc = func_info.get("description", "（无描述）")

        if truncate_length > 0 and len(desc) > truncate_length:
            desc = desc[:truncate_length] + "..."

        lines.append(f"\n{i}. {name}")
        lines.append(f"   描述：{desc}")

        params = func_info.get("parameters", {})
        if params and isinstance(params, dict):
            required = params.get("required", [])
            properties = params.get("properties", {})
            if properties:
                lines.append("   参数:")
                for param_name, param_info in properties.items():
                    param_desc = param_info.get("description", "")
                    param_type = param_info.get("type", "unknown")
                    is_required = "必需" if param_name in required else "可选"

                    if truncate_length > 0 and len(param_desc) > truncate_length:
                        param_desc = param_desc[:truncate_length] + "..."

                    lines.append(f"     - {param_name} ({param_type}) [{is_required}]: {param_desc}")

    return "\n".join(lines)


def format_request_for_log(
    payloads: list[Any],
    stream_id: str = "",
    chatter_name: str = "",
    show_tools: bool = True,
    truncate_length: int = 0,
) -> str:
    """格式化 LLM 请求为日志文本。

    格式：
    ══ 请求头 ══
    ├── SYSTEM: 人设/关系
    ├── TOOLS: 工具参数
    ├── SYSTEM: 历史叙事
    ├── USER/ASSISTANT: 对话内容

    Args:
        payloads: LLM 请求的 payloads 列表
        stream_id: 聊天流 ID
        chatter_name: Chatter 名称
        show_tools: 是否显示工具列表
        truncate_length: 单个 payload 的截断长度

    Returns:
        格式化后的日志文本
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    sections = []
    header = f"══ LLM REQUEST ══ [{timestamp}]"
    header += f" | stream={stream_id[:8] if stream_id else 'N/A'}"
    header += f" | chatter={chatter_name}"
    sections.append(header)
    sections.append("=" * 60)

    system_parts = []
    convo_parts = []
    all_tool_schemas = []

    for i, payload in enumerate(payloads):
        role = getattr(payload, 'role', None)
        content = getattr(payload, 'content', [])
        text = extract_payload_text(content, truncate_length)

        if role == ROLE.TOOL:
            # 收集工具 schema
            for item in content:
                if hasattr(item, 'to_schema'):
                    all_tool_schemas.append(item.to_schema())
            continue

        role_name = str(role.value).upper() if hasattr(role, 'value') else str(role)

        if role == ROLE.SYSTEM:
            system_parts.append((role_name, text))
        else:
            convo_parts.append((role_name, text, i))

    # 拆分 SYSTEM：最后一条通常是历史叙事
    if len(system_parts) >= 2:
        persona_parts = system_parts[:-1]
        history_part = system_parts[-1]
    else:
        persona_parts = system_parts
        history_part = None

    # 1. 人设/关系
    if persona_parts:
        sections.append("\n━━ PERSONA / RELATIONSHIP ━━")
        for role_name, text in persona_parts:
            sections.append(f"┌─ {role_name} ─┐")
            sections.append(text)
            sections.append("")

    # 2. 工具列表
    if show_tools and all_tool_schemas:
        tools_text = format_tools_for_log(all_tool_schemas, truncate_length)
        sections.append(f"\n{tools_text}")

    # 3. 历史叙事
    if history_part:
        sections.append(f"\n━━ {history_part[0]} (History) ━━")
        sections.append(history_part[1])

    # 4. 对话内容
    if convo_parts:
        sections.append("\n━━ CONVERSATION ━━")
        for role_name, text, _ in convo_parts:
            sections.append(f"┌─ {role_name} ─┐")
            sections.append(text)
            sections.append("")

    sections.append("=" * 60)
    return "\n".join(sections)


def format_response_for_log(
    message: str,
    call_list: list[Any] | None = None,
    stream_id: str = "",
    chatter_name: str = "",
    truncate_length: int = 0,
) -> str:
    """格式化 LLM 响应为日志文本。

    Args:
        message: LLM 响应消息
        call_list: 工具调用列表
        stream_id: 聊天流 ID
        chatter_name: Chatter 名称
        truncate_length: 截断长度

    Returns:
        格式化后的日志文本
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    sections = []
    header = f"══ LLM RESPONSE ══ [{timestamp}]"
    header += f" | stream={stream_id[:8] if stream_id else 'N/A'}"
    header += f" | chatter={chatter_name}"
    sections.append(header)
    sections.append("=" * 60)

    # 响应消息
    sections.append("\n━━ MESSAGE ━━")
    message_text = truncate_text(message or "（空）", truncate_length)
    sections.append(message_text)

    # 工具调用
    if call_list:
        sections.append("\n━━ TOOL CALLS ━━")
        for i, call in enumerate(call_list, 1):
            name = getattr(call, 'name', getattr(call, 'function_name', 'unknown'))
            args = getattr(call, 'args', getattr(call, 'arguments', {}))
            call_id = getattr(call, 'id', 'N/A')

            try:
                args_str = json.dumps(args, ensure_ascii=False, indent=2)
            except Exception:
                args_str = str(args)

            if truncate_length > 0 and len(args_str) > truncate_length:
                args_str = args_str[:truncate_length] + "..."

            sections.append(f"\n{i}. {name} (id={call_id})")
            sections.append(f"   参数：{args_str}")

    sections.append("=" * 60)
    return "\n".join(sections)
