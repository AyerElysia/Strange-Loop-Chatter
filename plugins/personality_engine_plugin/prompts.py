"""personality_engine_plugin 提示词。"""

from __future__ import annotations

import json
from typing import Any

FUNCTION_BRIEFS: dict[str, str] = {
    "Ti": "内部逻辑分析、原理拆解、结构化推演",
    "Te": "外部组织优化、效率导向、执行落地",
    "Fi": "个人价值与道德判断、内在一致性",
    "Fe": "群体情绪协调、关系维护、社会规范适配",
    "Ni": "深层模式洞察、趋势预判、长期整合",
    "Ne": "多可能性探索、创意联想、机会发现",
    "Si": "经验记忆调用、细节核验、稳态维持",
    "Se": "当下感知与即时行动、现实环境响应",
}


def build_baseline_hypothesis(*, main_func: str, aux_func: str) -> str:
    """构建初始人格基线假设。"""
    return f"基线人格以 {main_func}-{aux_func} 协同应对任务，优先保持该结构稳定。"


def build_selector_system_prompt() -> str:
    """构建功能选择系统提示词。"""
    return (
        "你是荣格八功能人格动态分析器。"
        "请严格遵循以下机制：\n"
        "1) 先分析任务核心需求（思维/情感/感觉/直觉）；\n"
        "2) 再判断需求偏向内倾还是外倾；\n"
        "3) 检查当前主导-辅助功能是否足够；\n"
        "4) 若不足，从未充分分化功能中选择本轮补偿功能。\n"
        "你必须只输出 JSON，不得输出其它文本。"
    )


def build_selector_user_prompt(
    *,
    trigger: str,
    mbti: str,
    main_func: str,
    aux_func: str,
    weights: dict[str, float],
    recent_messages: str,
) -> str:
    """构建功能选择用户提示词。"""
    messages = recent_messages.strip() or "（无可用近期对话）"
    miss_funcs = [f for f in FUNCTION_BRIEFS if f not in {main_func, aux_func}]
    return (
        "请基于下列状态完成一次“补偿功能识别”。\n"
        "## 八功能说明 ##\n"
        + "\n".join([f"- {func}: {desc}" for func, desc in FUNCTION_BRIEFS.items()])
        + "\n\n"
        "## 当前人格结构 ##\n"
        f"- MBTI: {mbti}\n"
        f"- 主导: {main_func}\n"
        f"- 辅助: {aux_func}\n"
        f"- 未充分分化池: {', '.join(miss_funcs)}\n"
        f"- 权重: {json.dumps(weights, ensure_ascii=False)}\n"
        f"- 触发来源: {trigger}\n\n"
        "## 最近对话 ##\n"
        f"{messages}\n\n"
        "## 输出要求 ##\n"
        "只输出如下 JSON：\n"
        "{\n"
        '  "function": "Ti",\n'
        '  "reason": "为什么该功能最匹配当前任务需求",\n'
        '  "hypothesis": "一句话描述当前心理倾向"\n'
        "}\n\n"
        "可选 function 只能是: Ti, Te, Fi, Fe, Ni, Ne, Si, Se。"
    )


def build_reflection_system_prompt(*, action: str) -> str:
    """构建人格结构反思系统提示词。"""
    return (
        "你是荣格八功能反思分析器。"
        "你要根据心理功能历史使用、权重变化和近期对话，判断是否发生结构变化。"
        f"当前反思任务类型: {action}。"
        "必须只输出 JSON，不输出任何额外文字。"
    )


def build_reflection_user_prompt(
    *,
    action: str,
    trigger: str,
    mbti: str,
    main_func: str,
    aux_func: str,
    selected_function: str,
    base_weights: dict[str, float],
    temp_weights: dict[str, float],
    recent_messages: str,
    recent_changes: list[str] | None,
    aux_candidates: list[str] | None = None,
) -> str:
    """构建人格结构反思用户提示词。"""
    msg = recent_messages.strip() or "（无可用近期对话）"
    changes = "\n".join([f"- {item}" for item in (recent_changes or [])]) or "- 无"

    if action == "swap_main_aux":
        output_spec = (
            "{\n"
            '  "judgment": "yes",\n'
            '  "reason": "为什么发生或未发生变化",\n'
            '  "main_weight": 0.40,\n'
            '  "aux_weight": 0.18\n'
            "}\n"
        )
    elif action == "change_main":
        output_spec = (
            "{\n"
            '  "judgment": "yes",\n'
            '  "reason": "为什么发生或未发生变化",\n'
            '  "main_weight": 0.35,\n'
            '  "ori_main_weight": 0.15\n'
            "}\n"
        )
    elif action == "change_aux":
        output_spec = (
            "{\n"
            '  "judgment": "yes",\n'
            '  "reason": "为什么发生或未发生变化",\n'
            '  "aux_weight": 0.18,\n'
            '  "ori_aux_weight": 0.05\n'
            "}\n"
        )
    else:
        choices = ", ".join(aux_candidates or [])
        output_spec = (
            "{\n"
            '  "judgment": "yes",\n'
            '  "reason": "为什么发生或未发生变化",\n'
            '  "main_weight": 0.35,\n'
            '  "aux_func": "候选之一",\n'
            '  "aux_weight": 0.18,\n'
            '  "ori_main_weight": 0.05,\n'
            '  "ori_aux_weight": 0.03\n'
            "}\n"
            f"aux_func 候选只能是: {choices}\n"
        )

    return (
        "请结合以下信息判断是否发生结构变化，并输出 JSON。\n\n"
        "## 分析依据 ##\n"
        "- 若某功能被高频使用并超过阈值，可能触发结构变化。\n"
        "- judgment 只能是 yes 或 no。\n"
        "- 若 judgment=no，可省略权重字段。\n\n"
        "## 当前状态 ##\n"
        f"- 触发来源: {trigger}\n"
        f"- MBTI: {mbti}\n"
        f"- 主导功能: {main_func}\n"
        f"- 辅助功能: {aux_func}\n"
        f"- 本轮选中功能: {selected_function}\n"
        f"- 反思动作: {action}\n"
        f"- 变更前权重: {json.dumps(base_weights, ensure_ascii=False)}\n"
        f"- 临时权重: {json.dumps(temp_weights, ensure_ascii=False)}\n\n"
        "## 近期对话 ##\n"
        f"{msg}\n\n"
        "## 近期结构变化 ##\n"
        f"{changes}\n\n"
        "## 输出格式 ##\n"
        f"{output_spec}\n"
        "只输出 JSON。"
    )


def build_prompt_block(
    *,
    title: str,
    mbti: str,
    main_func: str,
    aux_func: str,
    selected_function: str,
    hypothesis: str,
    weights: dict[str, float],
    detail: bool,
    mode: str,
    recent_changes: list[str] | None = None,
    include_function_catalog: bool = True,
) -> str:
    """构建 prompt 注入块。"""
    mode_lc = str(mode or "compact").strip().lower()
    if mode_lc not in {"compact", "paper_strict"}:
        mode_lc = "compact"

    lines = [
        f"【{title}】",
        f"- 当前类型：{mbti}（{main_func}-{aux_func}）",
        f"- 当前补偿：{selected_function or '暂无'}",
        f"- 当前假设：{hypothesis or '暂无'}",
    ]
    if mode_lc == "compact":
        if detail:
            lines.append(
                "- 权重："
                + " ".join(
                    [
                        f"Ti{weights.get('Ti', 0.0):.2f}",
                        f"Te{weights.get('Te', 0.0):.2f}",
                        f"Fi{weights.get('Fi', 0.0):.2f}",
                        f"Fe{weights.get('Fe', 0.0):.2f}",
                        f"Ni{weights.get('Ni', 0.0):.2f}",
                        f"Ne{weights.get('Ne', 0.0):.2f}",
                        f"Si{weights.get('Si', 0.0):.2f}",
                        f"Se{weights.get('Se', 0.0):.2f}",
                    ]
                )
            )
        return "\n".join(lines)

    # paper_strict 模式：尽量贴近原论文 prompt 结构
    miss_funcs = [f for f in FUNCTION_BRIEFS if f not in {main_func, aux_func}]
    lines.extend(
        [
            "",
            "##Compensation Mechanism##",
            "1. When the dominant and auxiliary functions cannot effectively cope with the current situation, trigger compensation.",
            "2. The compensation process must match task demands with the most appropriate Jungian function.",
            "3. Frequent compensation may lead to long-term structural personality change through reflection.",
            "",
            "##Following the steps##",
            "1. Task Demand Analysis: identify the core requirements and constraints in the current user request.",
            "2. Current Function Evaluation: evaluate whether dominant/auxiliary functions can handle the task.",
            "3. Compensatory Function Identification: if insufficient, choose the best function from the undifferentiated pool.",
            "4. Response Generation: keep final response natural, concise, and helpful without exposing chain-of-thought.",
            "",
            "##Psychological Type Characteristics##",
            f"- Dominant Function: {main_func} (high differentiation)",
            f"- Auxiliary Function: {aux_func} (medium differentiation)",
            f"- Undifferentiated Function Pool: {', '.join(miss_funcs)}",
            f"- Current Compensation Focus: {selected_function or 'none'}",
            f"- Current Hypothesis: {hypothesis or 'none'}",
        ]
    )
    if detail:
        lines.append(
            "- Current Weights: "
            + " ".join(
                [
                    f"Ti{weights.get('Ti', 0.0):.2f}",
                    f"Te{weights.get('Te', 0.0):.2f}",
                    f"Fi{weights.get('Fi', 0.0):.2f}",
                    f"Fe{weights.get('Fe', 0.0):.2f}",
                    f"Ni{weights.get('Ni', 0.0):.2f}",
                    f"Ne{weights.get('Ne', 0.0):.2f}",
                    f"Si{weights.get('Si', 0.0):.2f}",
                    f"Se{weights.get('Se', 0.0):.2f}",
                ]
            )
        )
    if include_function_catalog:
        lines.append("")
        lines.append("##Jungian Function Mapping##")
        lines.extend([f"- {func}: {desc}" for func, desc in FUNCTION_BRIEFS.items()])
    if recent_changes:
        lines.append("")
        lines.append("##Recent Structural Changes##")
        lines.extend([f"- {item}" for item in recent_changes if item.strip()])
    return "\n".join(lines)


def build_reflection_reason(
    *,
    action: str,
    old_mbti: str,
    new_mbti: str,
    selected_function: str,
    extra: dict[str, Any] | None = None,
) -> str:
    """构建结构变更原因文本。"""
    detail = ""
    if extra:
        detail = f" | extra={json.dumps(extra, ensure_ascii=False)}"
    return (
        f"{action}: {old_mbti}->{new_mbti}, selected={selected_function}{detail}"
    )
