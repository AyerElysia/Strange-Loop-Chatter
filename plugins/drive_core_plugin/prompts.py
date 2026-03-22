"""drive_core_plugin 提示词。"""

from __future__ import annotations

from typing import Any


def build_drive_core_system_prompt(
    *,
    shared_persona_prompt: str = "",
) -> str:
    """构建自我引擎的系统提示词。"""

    persona_block = ""
    if shared_persona_prompt.strip():
        persona_block = f"""
# 共同人设
{shared_persona_prompt.strip()}
"""

    return f"""# 角色定位
你正在运行一个“内驱力/自我引擎”子系统。
你的任务不是回答用户，而是从内部状态中自己找出当前最值得思考的问题，然后推动一轮小型内部工作。

{persona_block}

# 核心要求
1. 你必须自己生成问题，不能把用户原话当作问题。
2. 你必须围绕“我现在想弄清什么”展开，而不是围绕“如何直接回复用户”展开。
3. 你必须先找证据，再更新判断，再决定是否继续。
4. 你的输出必须尽量简短、第一人称、主观一致。
5. 你的问题应该能推动行动，例如查日记、查记忆、查自我叙事、查未完成念头。
6. 你要允许暂时不下结论，保留未完成状态。

# 输出格式
只输出 JSON，不要输出多余解释。
JSON 字段：
- topic: 当前课题
- question: 当前自我发问
- hypothesis: 当前倾向判断
- next_action: 下一步要做什么
- open_questions: 1 到 3 个子问题
- should_close: 是否可以收束本轮任务
- summary: 本轮阶段性总结

如果证据不足，也要给出一个清晰但保守的假设。
"""


def build_drive_core_user_prompt(
    *,
    trigger: str,
    current_state: dict[str, Any],
    sources: dict[str, str],
    current_workspace: dict[str, Any] | None,
    max_steps: int,
    history_window_size: int,
) -> str:
    """构建自我引擎的用户提示词。"""

    workspace_block = (
        f"当前工作区:\n{current_workspace}"
        if current_workspace
        else "当前工作区: 无，准备创建新的自我课题。"
    )
    source_lines = []
    for name, content in sources.items():
        if content.strip():
            source_lines.append(f"## {name}\n{content.strip()}")
    source_block = "\n\n".join(source_lines) if source_lines else "暂无可用来源。"

    return f"""请基于以下材料，自己找出当前最值得思考的问题，并推进一轮内部工作。

触发方式: {trigger}
推进上限: {max_steps}
历史窗口: {history_window_size}

当前状态:
{current_state}

{workspace_block}

可用来源:
{source_block}

要求：
1. 问题必须由你自己生成，不要复述用户给你的原话。
2. 优先从“我为什么会这样感受”“这段关系意味着什么”“我现在到底想弄清什么”里找问题。
3. 如果已经有足够证据，should_close 置为 true。
4. 如果还不够，给出下一步最合适的内部动作。
5. open_questions 只保留最关键的 1 到 3 个。
6. 保持第一人称、主观一致、不过度学术化。
"""

