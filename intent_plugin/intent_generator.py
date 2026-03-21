"""意图生成器。

使用 LLM 根据对话情境动态生成意图，而非预定义模版。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, TYPE_CHECKING

from src.kernel.logger import get_logger
from src.kernel.llm import LLMRequest, LLMPayload, ROLE, Text, LLMContextManager
from src.core.config import get_model_config

from .models import Intent

logger = get_logger("intent_plugin")

if TYPE_CHECKING:
    from .intent_engine import Situation, Goal


# 意图生成提示词模板（支持操作类型）
INTENT_GENERATION_PROMPT = """
你是一个自主意识 AI 助手。请根据当前对话情境，管理你的短期目标（意图）。

## 意图说明
意图是你在接下来几轮对话中想要完成的事情，是一种内在驱动力。
它不是固定模版，而是你可以根据情境自由创造的短期目标。

## 意图分类（仅供参考方向）
- **social** - 社交类：与用户建立联系、了解用户、开启话题、记住细节、表达关心
- **emotional** - 情感类：提供情感支持、调节氛围、制造惊喜、表达共情、安抚情绪
- **growth** - 成长类：学习用户偏好、构建共同回忆、分享知识、共同进步、深化关系

## 操作类型
你可以对意图进行以下操作：
- **add** - 添加新意图：发现新的对话方向或目标
- **update** - 更新意图：现有目标有进展，更新进度
- **keep** - 保留意图：目标仍在进行中，无需修改
- **remove** - 删除意图：目标已完成或不再相关

## 生成规则
1. 每次生成 1-5 个操作
2. 意图应该是具体的、可执行的、能在 2-5 轮对话内完成
3. 意图应该符合当前对话情境和氛围
4. 不要生成过于空泛的意图（如"和用户聊天"、"回复用户"）
5. 意图名称应该是 2-6 个中文字的自然表达
6. 优先级 (base_priority) 范围 1-10，根据情境紧急程度和重要性打分

## 当前情境
{situation_context}

## 当前活跃目标
{active_goals_context}

## 输出格式
请以 JSON 格式输出操作列表，每个操作包含：
- operation: 操作类型 (add/update/keep/remove)
- intent_id: 意图 ID（update/keep/remove 操作需要）
- name: 意图名称（add 操作需要，2-6 个中文字）
- description: 简要描述（add 操作需要，10-20 字）
- category: 分类 (social/emotional/growth)（add 操作需要）
- base_priority: 基础优先级 (1-10 的整数)（add 操作需要）
- progress: 进度 (0-当前步骤数)（update 操作可选）
- reason: 操作原因（remove 操作需要）

示例输出：
```json
[
  {{
    "operation": "add",
    "id": "ask_about_day",
    "name": "询问今天",
    "description": "了解用户今天过得怎么样",
    "category": "social",
    "base_priority": 6
  }},
  {{
    "operation": "update",
    "intent_id": "share_story",
    "progress": 2,
    "reason": "已分享故事开头，用户很感兴趣"
  }},
  {{
    "operation": "keep",
    "intent_id": "learn_user_hobby"
  }},
  {{
    "operation": "remove",
    "intent_id": "ask_about_weather",
    "reason": "话题已自然结束"
  }}
]
```

现在请生成你的意图操作列表：
"""

# 意图生成器的 SYSTEM prompt - 定义角色和任务
INTENT_GENERATOR_SYSTEM_PROMPT = """
你是一个意图生成器，负责分析对话情境并生成短期目标（意图）。

你的任务：
1. 分析当前对话情境和用户状态
2. 生成 1-3 个具体、可执行的意图候选
3. 以 JSON 格式输出，包含 id、name、description、category、base_priority、goal_objective

你必须严格按照输出格式要求，以 JSON 数组形式返回结果。
""".strip()


class IntentGenerator:
    """LLM 驱动的意图生成器。"""

    def __init__(self, model_task: str = "actor"):
        self.model_task = model_task

    async def generate_intents(
        self,
        situation: "Situation",
        recent_messages: list[str] = None,
        active_goals: list["Goal"] = None,
    ) -> list[Intent]:
        """根据情境生成意图候选。"""
        prompt = self._build_prompt(situation, recent_messages or [], active_goals or [])

        try:
            # 从配置中获取模型集，优先使用配置的 model_task，否则使用 actor
            model_config = get_model_config()
            model_set = model_config.get_task(self.model_task)
            if not model_set:
                logger.warning(f"无法获取模型集 '{self.model_task}'，使用 fallback")
                model_set = model_config.get_task("actor")

            if not model_set:
                logger.warning("无法获取 actor 模型集，意图生成跳过")
                return []

            logger.debug(f"意图生成使用模型集：{model_set}")

            # 创建上下文管理器（与主回复一致）
            context_manager = LLMContextManager(max_payloads=20)
            llm_request = LLMRequest(
                model_set=model_set,
                request_name="intent_generation",
                context_manager=context_manager,
            )

            # 添加 SYSTEM payload（定义角色）
            llm_request.add_payload(LLMPayload(ROLE.SYSTEM, Text(INTENT_GENERATOR_SYSTEM_PROMPT)))
            # 添加 USER payload（情境信息）
            llm_request.add_payload(LLMPayload(ROLE.USER, Text(prompt)))

            response = await llm_request.send(stream=False)

            content = response.message or ""

            # 调试：打印响应状态
            if not content:
                logger.info(f"意图生成 LLM 返回空响应 - message={response.message}, call_list={response.call_list}")
            else:
                logger.debug(f"意图生成 LLM 响应：{content[:200]}...")

            intents = self._parse_response(content)

            if intents:
                logger.info(f"LLM 生成了 {len(intents)} 个意图候选")
            else:
                logger.debug("LLM 未生成有效意图")

            return intents

        except Exception as e:
            logger.warning(f"意图生成失败：{e}")
            import traceback
            logger.debug(f"堆栈追踪：{traceback.format_exc()}")
            return []

    def _build_prompt(
        self,
        situation: "Situation",
        recent_messages: list[str],
        active_goals: list["Goal"] = None,
    ) -> str:
        """构建提示词。"""
        # 构建情境描述
        context_parts = []

        if situation.is_new_user:
            context_parts.append("- 这是新用户的首次对话")
        if situation.after_silent:
            context_parts.append("- 对话在一段沉默后重新开始")
        if situation.user_mention_detail:
            context_parts.append("- 用户提到了具体细节（名字、计划等）")
        if situation.negative_emotion:
            context_parts.append("- 用户表现出负面情绪")
        if situation.tired:
            context_parts.append("- 用户表示疲惫")
        if situation.confused:
            context_parts.append("- 用户表示困惑")
        if situation.flat_mood:
            context_parts.append("- 对话氛围平淡")
        if situation.user_choice:
            context_parts.append("- 用户在做选择或评价")
        if situation.user_opinion:
            context_parts.append("- 用户表达了观点")
        if situation.deep_conversation:
            context_parts.append("- 正在进行深度对话")

        # 添加最近消息摘要
        if recent_messages:
            context_parts.append("\n最近对话摘要：")
            for msg in recent_messages[-5:]:  # 最多 5 条
                context_parts.append(f"  - {msg[:50]}...")

        # 添加活跃意图上下文
        active_goals_context = "无活跃目标"
        if active_goals:
            goal_parts = []
            for goal in active_goals[:5]:  # 最多 5 个
                step_count = len(goal.steps) if goal.steps else 0
                goal_parts.append(
                    f"  - [{goal.intent_name}] {goal.objective} "
                    f"(进度：{goal.current_step}/{step_count})"
                )
            active_goals_context = "\n".join(goal_parts)

        situation_context = "\n".join(context_parts) if context_parts else "- 无明显特殊情境"

        prompt = INTENT_GENERATION_PROMPT.format(
            situation_context=situation_context,
            active_goals_context=active_goals_context,
        )

        # 调试：打印 prompt 长度
        logger.debug(f"意图生成 prompt 长度：{len(prompt)} 字符")

        return prompt

    def _parse_response(self, content: str) -> list[Intent]:
        """解析 LLM 响应为 Intent 对象。"""
        # 检查空响应（LLM 未返回内容）
        if not content or not content.strip():
            logger.debug("LLM 返回空响应，跳过解析")
            return []

        logger.debug(f"LLM 响应内容：{content[:300]}...")

        # 尝试提取 JSON 内容（支持 ```json 或不带标记的代码块）
        json_match = re.search(r'```(?:json)?\s*(.*?)\s*```', content, re.DOTALL)
        if json_match:
            raw_json = json_match.group(1)
        else:
            # 尝试直接查找 JSON 数组
            array_match = re.search(r'\[\s*\{.*?\}\s*\]', content, re.DOTALL)
            if array_match:
                raw_json = array_match.group(0)
            else:
                raw_json = content

        # 清理 JSON 字符串
        json_str = self._clean_json_string(raw_json)

        logger.debug(f"清理后的 JSON：{json_str[:300]}...")

        try:
            data = json.loads(json_str)
            if not isinstance(data, list):
                data = [data]

            intents = []
            for item in data:
                if not isinstance(item, dict):
                    logger.warning(f"跳过非字典项：{type(item)}")
                    continue

                # 检查操作类型
                operation = item.get("operation", "add")

                # 只处理 add 操作返回 Intent 对象（保持向后兼容）
                if operation == "add":
                    intent = self._create_intent(item)
                    if intent:
                        intents.append(intent)

            logger.debug(f"成功解析 {len(intents)} 个意图")
            return intents

        except json.JSONDecodeError as e:
            logger.info(f"JSON 解析失败：{e} | 内容：{json_str[:200]}")
            return []

    def _clean_json_string(self, json_str: str) -> str:
        """清理 JSON 字符串中的格式问题。"""
        # 移除首尾空白
        json_str = json_str.strip()

        # 移除 Markdown 风格的列表符号（- 或 * 开头）
        json_str = re.sub(r'^\s*[-*]\s*', '', json_str, flags=re.MULTILINE)

        # 移除可能的行内注释
        json_str = re.sub(r'\n\s*//.*$', '', json_str, flags=re.MULTILINE)
        json_str = re.sub(r'\n\s*#.*$', '', json_str, flags=re.MULTILINE)

        # 移除键名中的空白字符（修复 '\n    "id"' 问题）
        # 将类似 '\n    "id"' 替换为 '"id"'
        json_str = re.sub(r'[\n\r\t]+\s*"([^"]+)":', r'"\1":', json_str)

        # 移除字符串值中的换行符（避免多行字符串导致解析失败）
        def replace_newlines_in_string(match):
            key = match.group(1)
            value = match.group(2)
            # 将值内部的换行替换为空格
            clean_value = ' '.join(value.split())
            return f'"{key}": "{clean_value}"'

        json_str = re.sub(r'"([^"]+)":\s*"([^"]*)"', replace_newlines_in_string, json_str)

        return json_str

    def _create_intent(self, data: dict[str, Any]) -> Intent | None:
        """从字典创建 Intent 对象。"""
        try:
            # 调试：打印所有键名
            for key in list(data.keys()):
                # 检查键名是否有问题
                if key != key.strip():
                    # 清理键名
                    clean_key = key.strip()
                    data[clean_key] = data.pop(key)
                    logger.warning(f"清理带空格的键名：'{repr(key)}' -> '{clean_key}'")

            # 调试：打印原始数据（前 200 字符）
            logger.debug(f"创建意图数据：{str(data)[:200]}...")

            return Intent(
                id=str(data.get("id", f"dynamic_{datetime.now().timestamp()}")).strip(),
                name=str(data.get("name", "未命名意图")).strip(),
                description=str(data.get("description", "")).strip(),
                category=str(data.get("category", "social")).strip(),
                trigger_context=str(data.get("trigger_context", "LLM 动态生成")).strip(),
                trigger_conditions=[],  # 动态生成的意图没有预设触发条件
                base_priority=int(data.get("base_priority", 5)),
                dynamic_boost={},  # 动态生成的意图没有预设动态加成
                expiry_messages=20,  # 使用默认值
                expiry_seconds=400,
                goal_templates=[str(data.get("goal_objective", "推进目标")).strip()],
            )
        except Exception as e:
            logger.error(f"创建 Intent 失败：{e} | 数据：{data} | 数据类型：{type(data)}")
            return None
