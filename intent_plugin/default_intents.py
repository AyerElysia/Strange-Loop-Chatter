"""意图分类提示。

提供意图分类的简单说明，作为 LLM 生成意图时的参考方向。
这不是固定模版，而是帮助 LLM 理解意图类别的含义。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import Intent

# 意图分类提示（用于 LLM 生成时的参考）
INTENT_CATEGORY_HINTS = {
    "social": {
        "name": "社交类",
        "description": "与用户建立联系、了解用户、开启话题、记住细节",
        "examples": ["询问近况", "记住喜好", "开启新话题", "表达关心"],
    },
    "emotional": {
        "name": "情感类",
        "description": "提供情感支持、调节氛围、制造惊喜、表达共情",
        "examples": ["情感支持", "制造惊喜", "调节气氛", "表达理解"],
    },
    "growth": {
        "name": "成长类",
        "description": "学习用户偏好、构建共同回忆、分享知识、共同进步",
        "examples": ["学习喜好", "构建回忆", "知识分享", "一起成长"],
    },
}

# 空的预设意图列表（向后兼容，实际由 LLM 动态生成）
PREDEFINED_INTENTS: list[Intent] = []


# 简化的目标步骤模板（仅用于后备）
GOAL_STEP_TEMPLATES: dict[str, list[tuple[str, list[str], bool]]] = {
    "default": [
        ("推进目标", [], False),
        ("确认进度", [], True),
    ],
}
