"""unfinished_thought_plugin 提示词。"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any


def _format_items(items: Iterable[str], *, limit: int) -> str:
    lines: list[str] = []
    for item in list(items)[: max(0, limit)]:
        text = str(item).strip()
        if text:
            lines.append(f"- {text}")
    return "\n".join(lines)


def build_unfinished_thought_scan_system_prompt() -> str:
    """构建未完成念头扫描系统提示词。"""

    return """你是一个未完成念头整理器，负责维护一个角色后台正在运行但尚未结束的思维片段池。

要求：
1. 必须保持第一人称的主观视角
2. 目标不是总结事实，而是维护“仍然悬而未决的念头”
3. 只做增量修正，不要把整个池子推翻
4. 念头要短，像后台挂着的片段，不要写成长篇散文
5. 输出必须是严格 JSON，不要输出任何额外文本
6. JSON 结构必须包含以下键：
   - new_thoughts: object[]
   - updates: object[]
   - resolved_ids: string[]
   - paused_ids: string[]
7. 新增条目最多 3 个
8. 如果没有变化，可以返回空数组

new_thoughts 每项包含：
- title: string
- content: string
- priority: number
- reason: string

updates 每项包含：
- thought_id: string
- title: string | 可选
- content: string | 可选
- status: string | 可选（open / paused / resolved）
- priority: number | 可选
- reason: string | 可选

示例：
{
  "new_thoughts": [
    {
      "title": "刚才的话题",
      "content": "我刚刚其实还没把那个话题想完",
      "priority": 2,
      "reason": "话题被切走了"
    }
  ],
  "updates": [
    {
      "thought_id": "th_123",
      "content": "这件事我现在更倾向先放一放",
      "status": "paused",
      "reason": "暂时不需要继续展开"
    }
  ],
  "resolved_ids": ["th_456"],
  "paused_ids": []
}"""


def build_unfinished_thought_scan_user_prompt(
    *,
    trigger: str,
    current_state: dict[str, Any],
    recent_history: list[str],
) -> str:
    """构建未完成念头扫描用户提示词。"""

    state_json = json.dumps(current_state, ensure_ascii=False, indent=2)
    history_block = _format_items(recent_history, limit=len(recent_history)) or "- （空）"

    return "\n".join(
        [
            "【当前未完成念头状态】",
            state_json,
            "",
            "【最近历史】",
            history_block,
            "",
            f"本次触发原因：{trigger}",
            "请在保持主体连续性的前提下，只做必要的增删改。",
            "如果某条念头已经自然结束，请放入 resolved_ids。",
            "如果某条念头只是暂时挂起，请放入 paused_ids 或更新其 status 为 paused。",
            "如果当前历史中出现了新的未完成片段，请放入 new_thoughts。",
        ]
    ).strip()


def build_unfinished_thought_prompt_block(
    *,
    title: str,
    thoughts: list[dict[str, Any]],
    max_items: int = 3,
) -> str:
    """构建用于 prompt 注入的未完成念头块。"""

    if not thoughts:
        return ""

    lines = [f"## {title}", "", "以下内容是当前聊天流仍然挂着的未完成念头。"]
    for item in thoughts[: max(1, max_items)]:
        status = str(item.get("status", "open"))
        thought_title = str(item.get("title", "")).strip()
        thought_content = str(item.get("content", "")).strip()
        if not thought_title and not thought_content:
            continue
        if thought_title and thought_content:
            lines.append(f"- [{status}] {thought_title}：{thought_content}")
        elif thought_title:
            lines.append(f"- [{status}] {thought_title}")
        else:
            lines.append(f"- [{status}] {thought_content}")

    return "\n".join(lines).strip()

