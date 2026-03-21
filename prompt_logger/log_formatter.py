"""Prompt Logger 日志格式化工具。

将 LLM 请求/响应的 payload 列表格式化为人类可读的日志输出。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from src.kernel.llm import ROLE


def truncate_text(text: str, max_length: int) -> str:
    """截断文本到指定长度。"""
    if max_length <= 0 or len(text) <= max_length:
        return text
    return text[:max_length] + f"\n... [已截断，超出 {len(text) - max_length} 字符]"


def _extract_item_text(item: Any, truncate_length: int = 0) -> tuple[list[str], list[dict[str, Any]]]:
    """从 payload 内容项中提取文本和工具 schema。"""
    text_parts: list[str] = []
    tool_schemas: list[dict[str, Any]] = []

    if hasattr(item, "text"):
        text_parts.append(getattr(item, "text"))
    elif hasattr(item, "value") and hasattr(item, "__class__") and item.__class__.__name__ == "Image":
        data_preview = str(getattr(item, "value", ""))[:40]
        text_parts.append(f"[图片: {data_preview}...]")
    elif hasattr(item, "to_text"):
        text_parts.append(item.to_text())
    elif hasattr(item, "to_schema"):
        schema = item.to_schema()
        tool_schemas.append(schema)
    elif hasattr(item, "name") and hasattr(item, "args") and hasattr(item, "id"):
        # ToolCall 对象
        name = getattr(item, "name", "?")
        args = getattr(item, "args", {})
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

    return text_parts, tool_schemas


def _extract_payload_text(content: Any, truncate_length: int = 0) -> tuple[str, list[dict[str, Any]]]:
    """从 payload 内容中提取文本和工具 schema。"""
    if not isinstance(content, list):
        content = [content]

    text_parts: list[str] = []
    tool_schemas: list[dict[str, Any]] = []

    for item in content:
        item_text_parts, item_tool_schemas = _extract_item_text(item, truncate_length)
        text_parts.extend(item_text_parts)
        tool_schemas.extend(item_tool_schemas)

    text = "\n".join(text_parts) if text_parts else "（空）"
    return text, tool_schemas


def _format_tools_for_log(tools: list[dict[str, Any]], truncate_length: int = 0) -> str:
    """格式化工具列表为日志文本。"""
    if not tools:
        return ""

    lines: list[str] = [f"━━ TOOLS (共 {len(tools)} 个) ━━"]

    for i, schema in enumerate(tools, 1):
        func_info = schema.get("function", schema)
        name = func_info.get("name", "unknown")
        desc = func_info.get("description", "（无描述）")

        if truncate_length > 0 and len(desc) > truncate_length:
            desc = desc[:truncate_length] + "..."

        lines.append(f"{i}. {name}")
        lines.append(f"   描述: {desc}")

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

                    lines.append(
                        f"     - {param_name} ({param_type}) [{is_required}]: {param_desc}"
                    )

    return "\n".join(lines)


def _format_metadata_line(
    *,
    stream_id: str = "",
    plugin_name: str = "",
    chatter_name: str = "",
    request_name: str = "",
    model_name: str = "",
    chat_type: str = "",
    extra_fields: dict[str, Any] | None = None,
) -> str:
    """格式化来源元信息行。"""
    parts: list[str] = []

    if plugin_name:
        parts.append(f"plugin={plugin_name}")
    if chatter_name:
        parts.append(f"chatter={chatter_name}")
    if request_name:
        parts.append(f"request={request_name}")
    if model_name:
        parts.append(f"model={model_name}")
    if chat_type:
        parts.append(f"chat_type={chat_type}")
    if stream_id:
        parts.append(f"stream={stream_id[:8]}")

    if extra_fields:
        for key, value in extra_fields.items():
            if value in ("", None):
                continue
            parts.append(f"{key}={value}")

    if not parts:
        return "source: unknown"

    return "source: " + " | ".join(parts)


def _build_header(
    *,
    kind: str,
    include_timestamp: bool,
    stream_id: str = "",
    plugin_name: str = "",
    chatter_name: str = "",
    request_name: str = "",
    model_name: str = "",
    chat_type: str = "",
    extra_fields: dict[str, Any] | None = None,
) -> list[str]:
    """构建日志头部。"""
    title = f"══ LLM {kind.upper()} ══"
    if include_timestamp:
        title += f" [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]"

    lines = [title]
    lines.append(_format_metadata_line(
        stream_id=stream_id,
        plugin_name=plugin_name,
        chatter_name=chatter_name,
        request_name=request_name,
        model_name=model_name,
        chat_type=chat_type,
        extra_fields=extra_fields,
    ))
    lines.append("=" * 72)
    return lines


def _format_payload_sections(
    payloads: list[Any],
    *,
    show_tools: bool,
    truncate_length: int,
) -> list[str]:
    """将 payloads 分解为人类可读的区块。"""
    system_parts: list[str] = []
    convo_parts: list[str] = []
    all_tool_schemas: list[dict[str, Any]] = []

    for payload in payloads:
        role = getattr(payload, "role", None)
        content = getattr(payload, "content", [])
        text, tool_schemas = _extract_payload_text(content, truncate_length)

        if role == ROLE.TOOL:
            all_tool_schemas.extend(tool_schemas)
            continue

        role_name = str(role.value).upper() if hasattr(role, "value") else str(role)
        block = f"── {role_name} ──\n{text}"

        if role == ROLE.SYSTEM:
            system_parts.append(block)
        else:
            convo_parts.append(block)

    if len(system_parts) >= 2:
        persona_parts = system_parts[:-1]
        history_part = system_parts[-1]
    else:
        persona_parts = system_parts
        history_part = None

    sections: list[str] = []

    if persona_parts:
        sections.append("━━ PERSONA / RELATIONSHIP ━━")
        sections.extend(persona_parts)

    if show_tools and all_tool_schemas:
        sections.append(_format_tools_for_log(all_tool_schemas, truncate_length))

    if history_part:
        sections.append("━━ SYSTEM (History) ━━")
        sections.append(history_part)

    if convo_parts:
        sections.append("━━ CONVERSATION ━━")
        sections.extend(convo_parts)

    return sections


def format_request_for_log(
    payloads: list[Any],
    stream_id: str = "",
    chatter_name: str = "",
    request_name: str = "",
    plugin_name: str = "",
    model_name: str = "",
    chat_type: str = "",
    show_tools: bool = True,
    truncate_length: int = 0,
    include_timestamp: bool = True,
    extra_fields: dict[str, Any] | None = None,
) -> str:
    """格式化 LLM 请求为日志文本。"""
    sections: list[str] = _build_header(
        kind="REQUEST",
        include_timestamp=include_timestamp,
        stream_id=stream_id,
        plugin_name=plugin_name,
        chatter_name=chatter_name,
        request_name=request_name,
        model_name=model_name,
        chat_type=chat_type,
        extra_fields=extra_fields,
    )

    if not payloads:
        sections.append("（无 payload）")
        sections.append("=" * 72)
        return "\n".join(sections)

    sections.extend(
        _format_payload_sections(
            payloads,
            show_tools=show_tools,
            truncate_length=truncate_length,
        )
    )
    sections.append("=" * 72)
    return "\n".join(sections)


def format_response_for_log(
    message: str,
    call_list: list[Any] | None = None,
    stream_id: str = "",
    chatter_name: str = "",
    request_name: str = "",
    plugin_name: str = "",
    model_name: str = "",
    chat_type: str = "",
    truncate_length: int = 0,
    include_timestamp: bool = True,
    extra_fields: dict[str, Any] | None = None,
) -> str:
    """格式化 LLM 响应为日志文本。"""
    sections: list[str] = _build_header(
        kind="RESPONSE",
        include_timestamp=include_timestamp,
        stream_id=stream_id,
        plugin_name=plugin_name,
        chatter_name=chatter_name,
        request_name=request_name,
        model_name=model_name,
        chat_type=chat_type,
        extra_fields=extra_fields,
    )

    sections.append("━━ MESSAGE ━━")
    sections.append(truncate_text(message or "（空）", truncate_length))

    if call_list:
        sections.append("━━ TOOL CALLS ━━")
        for i, call in enumerate(call_list, 1):
            name = getattr(call, "name", getattr(call, "function_name", "unknown"))
            args = getattr(call, "args", getattr(call, "arguments", {}))
            call_id = getattr(call, "id", "N/A")

            try:
                args_str = json.dumps(args, ensure_ascii=False, indent=2)
            except Exception:
                args_str = str(args)

            if truncate_length > 0 and len(args_str) > truncate_length:
                args_str = args_str[:truncate_length] + "..."

            sections.append(f"{i}. {name} (id={call_id})")
            sections.append(f"   参数: {args_str}")

    sections.append("=" * 72)
    return "\n".join(sections)
