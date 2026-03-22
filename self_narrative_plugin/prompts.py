"""self_narrative_plugin 提示词。"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def _format_items(items: Iterable[str], *, limit: int) -> str:
    lines = []
    for item in list(items)[: max(0, limit)]:
        text = str(item).strip()
        if text:
            lines.append(f"- {text}")
    return "\n".join(lines)


def build_self_narrative_update_system_prompt() -> str:
    """构建自我叙事更新系统提示词。"""

    return """你是一个自我叙事整理器，负责维护一个角色对自己的持续解释。

要求：
1. 必须保持第一人称“我”的主观视角
2. 目标不是复述事实，而是更新“我如何理解自己”
3. 只做增量修正，不要彻底推翻旧的自我理解
4. 保留稳定边界，允许开放问题继续存在
5. 输出必须是严格 JSON，不要输出任何额外文本
6. JSON 结构必须包含以下键：
   - self_view: string[]
   - ongoing_patterns: string[]
   - open_loops: string[]
   - identity_bounds: string[]
7. 每个数组尽量保持 1-5 条，句子要短，清楚，稳定

示例：
{
  "self_view": ["我最近更安静，但不是失去兴趣"],
  "ongoing_patterns": ["我在熟悉关系里更放松"],
  "open_loops": ["我为什么最近总想降低回应强度"],
  "identity_bounds": ["我更重视真实表达，而不是迎合"]
}"""


def build_self_narrative_update_user_prompt(
    *,
    trigger: str,
    reference_date: str,
    current_state: dict[str, Any],
    sources: dict[str, str],
) -> str:
    """构建自我叙事更新用户提示词。"""

    state_block = [
        "【当前自我叙事】",
        f"当前日期：{reference_date}",
        f"触发原因：{trigger}",
        "",
        "自我理解：",
        _format_items(current_state.get("self_view", []), limit=5) or "- （空）",
        "",
        "反复模式：",
        _format_items(current_state.get("ongoing_patterns", []), limit=5) or "- （空）",
        "",
        "未完成问题：",
        _format_items(current_state.get("open_loops", []), limit=5) or "- （空）",
        "",
        "稳定边界：",
        _format_items(current_state.get("identity_bounds", []), limit=5) or "- （空）",
    ]

    source_block: list[str] = ["", "【输入素材】"]
    for key, value in sources.items():
        text = str(value or "").strip()
        if not text:
            continue
        source_block.append(f"\n## {key}")
        source_block.append(text)

    source_block.append(
        "\n请在保持主体连续性的前提下，只做必要修正。"
        "如果某一项没有明显变化，可以保留原样。"
        "如果出现新的稳定倾向，把它写进对应数组。"
        "如果出现尚未解释清楚的张力，把它放进 open_loops。"
        "请避免写成长篇散文。"
    )

    return "\n".join(state_block + source_block).strip()


def build_self_narrative_prompt_block(
    *,
    title: str,
    current_state: dict[str, Any],
    history_lines: list[str] | None = None,
    include_identity_bounds: bool = True,
    max_items_per_section: int = 3,
    include_history: bool = False,
    history_limit: int = 3,
) -> str:
    """构建用于 prompt 注入的自我叙事块。"""

    if not current_state:
        return ""

    sections: list[tuple[str, str]] = []

    self_view = _format_items(
        current_state.get("self_view", []), limit=max_items_per_section
    )
    if self_view:
        sections.append(("当前自我理解", self_view))

    patterns = _format_items(
        current_state.get("ongoing_patterns", []), limit=max_items_per_section
    )
    if patterns:
        sections.append(("反复出现的模式", patterns))

    loops = _format_items(
        current_state.get("open_loops", []), limit=max_items_per_section
    )
    if loops:
        sections.append(("尚未解释完的问题", loops))

    if include_identity_bounds:
        bounds = _format_items(
            current_state.get("identity_bounds", []), limit=max_items_per_section
        )
        if bounds:
            sections.append(("稳定边界", bounds))

    if include_history and history_lines:
        history_block = _format_items(history_lines[-history_limit:], limit=history_limit)
        if history_block:
            sections.append(("近期演化历史", history_block))

    if not sections:
        return ""

    lines = [
        f"## {title}",
        "",
        "以下内容是当前聊天流的自我叙事，请把它视为持续存在的内在背景。",
    ]
    for section_title, section_body in sections:
        lines.extend(["", f"### {section_title}", section_body])

    return "\n".join(lines).strip()
