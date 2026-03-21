"""目标追踪器 - 事件处理器。

订阅对话事件，检测目标进度，自动更新状态。
"""

from __future__ import annotations

from typing import Any

from src.core.components.base.event_handler import BaseEventHandler
from src.core.components.types import EventType
from src.kernel.event import EventDecision
from src.kernel.logger import get_logger

from .models import Goal
from .intent_engine import IntentEngine, Situation
from .goal_manager import GoalManager
from .config import IntentConfig


logger = get_logger("intent_plugin")


class GoalTracker(BaseEventHandler):
    """目标执行追踪器

    订阅对话事件，检测目标进度，自动更新状态。
    """

    handler_name: str = "intent_goal_tracker"
    handler_description: str = "追踪目标执行进度，更新 System Reminder"
    weight: int = 10  # 高优先级，确保最早执行

    init_subscribe: list[EventType | str] = [
        EventType.ON_CHATTER_STEP,
    ]

    def __init__(self, plugin: Any) -> None:
        """初始化目标追踪器"""
        super().__init__(plugin)

        # 获取配置
        config = None
        if hasattr(plugin, "config") and isinstance(plugin.config, IntentConfig):
            config = plugin.config

        # 初始化引擎和管理器
        self.intent_engine = IntentEngine(config)
        self.goal_manager = GoalManager(self.intent_engine)

        # 消息计数器（按 stream_id 隔离）
        self._message_counts: dict[str, int] = {}

    async def execute(
        self,
        event_name: str,
        params: dict[str, Any],
    ) -> tuple[EventDecision, dict[str, Any]]:
        """执行追踪"""
        # 获取 stream_id
        stream_id = params.get("stream_id")
        if not stream_id:
            return EventDecision.SUCCESS, params

        try:
            # 更新消息计数
            self._message_counts[stream_id] = self._message_counts.get(stream_id, 0) + 1
            current_count = self._message_counts[stream_id]

            # 获取对话上下文
            context = self._get_chat_context(params)

            # 分析情境
            situation = self._analyze_situation(context, params)

            # 检查是否到达意图生成间隔
            should_generate_intents = self._should_generate_intents(stream_id)

            if should_generate_intents:
                # 更新目标列表（可能触发新意图）
                await self.goal_manager.update_goals(
                    situation,
                    max_active=self._get_max_active_intents(),
                )
                # 重置计数器
                self._message_counts[stream_id] = 0
                logger.debug(f"已触发意图生成（第 {current_count} 条消息）")
            else:
                logger.debug(f"意图生成冷却中（{current_count}/{self._get_intent_interval()}）")

            # 检查每个活跃目标的进度
            self._check_goal_progress(context)

            # 更新 System Reminder
            await self._update_system_reminder(stream_id)

        except Exception as e:
            logger.error(f"意图追踪失败：{e}")
            import traceback
            logger.error(f"堆栈追踪：{traceback.format_exc()}")

        return EventDecision.SUCCESS, params

    def _get_chat_context(self, params: dict[str, Any]) -> Any:
        """获取聊天上下文"""
        from src.core.managers import get_stream_manager

        stream_id = params.get("stream_id")
        if not stream_id:
            return None

        stream_manager = get_stream_manager()
        chat_stream = stream_manager._streams.get(stream_id)

        if not chat_stream:
            return None

        return chat_stream.context

    def _analyze_situation(
        self,
        context: Any,
        params: dict[str, Any],
    ) -> Situation:
        """分析当前对话情境"""
        situation = Situation()

        if context is None:
            return situation

        # 获取最近消息
        recent_messages = self._get_recent_messages(context, 5)
        situation.recent_messages = recent_messages

        # 合并消息内容用于分析
        combined_text = " ".join(recent_messages).lower()

        # 检测情境信号
        situation.is_new_user = self._check_new_user(context)
        situation.after_silent = self._check_after_silent(context)

        # 情绪检测
        situation.negative_emotion = self._check_negative_emotion(combined_text)
        situation.tired = self._check_tired(combined_text)
        situation.confused = self._check_confused(combined_text)

        # 话题检测
        situation.user_mention_detail = self._check_user_mention_detail(combined_text)
        situation.user_choice = self._check_user_choice(combined_text)
        situation.user_opinion = self._check_user_opinion(combined_text)
        situation.flat_mood = self._check_flat_mood(combined_text)
        situation.deep_conversation = self._check_deep_conversation(context)

        return situation

    def _get_recent_messages(self, context: Any, limit: int = 5) -> list[str]:
        """获取最近消息"""
        if not hasattr(context, "history_messages"):
            return []

        messages = list(context.history_messages)[-limit:]
        contents = []

        for msg in messages:
            content = getattr(
                msg, "processed_plain_text", str(getattr(msg, "content", ""))
            )
            contents.append(content)

        return contents

    def _check_new_user(self, context: Any) -> bool:
        """检查是否新用户"""
        # 检查对话轮次
        if hasattr(context, "history_messages"):
            msg_count = len(list(context.history_messages))
            return msg_count <= 2
        return False

    def _check_after_silent(self, context: Any) -> bool:
        """检查是否长时间沉默后"""
        # TODO: 需要时间戳支持
        return False

    def _check_negative_emotion(self, text: str) -> bool:
        """检测负面情绪"""
        negative_keywords = ["难过", "伤心", "烦", "累", "困", "烦", "生气", "郁闷"]
        return any(kw in text for kw in negative_keywords)

    def _check_tired(self, text: str) -> bool:
        """检测疲惫"""
        tired_keywords = ["困", "累", "疲惫", "疲倦", "没精神", "想睡"]
        return any(kw in text for kw in tired_keywords)

    def _check_confused(self, text: str) -> bool:
        """检测困惑"""
        confused_keywords = ["不知道", "不懂", "不明白", "困惑", "疑惑", "？？"]
        return any(kw in text for kw in confused_keywords)

    def _check_user_mention_detail(self, text: str) -> bool:
        """检测用户提到具体信息"""
        detail_keywords = ["我叫", "喜欢", "讨厌", "想要", "计划", "打算"]
        return any(kw in text for kw in detail_keywords)

    def _check_user_choice(self, text: str) -> bool:
        """检测用户选择"""
        choice_keywords = ["选择", "选", "更喜欢", "想要", "不要"]
        return any(kw in text for kw in choice_keywords)

    def _check_user_opinion(self, text: str) -> bool:
        """检测用户表达观点"""
        opinion_keywords = ["觉得", "认为", "感觉", "看法", "意见"]
        return any(kw in text for kw in opinion_keywords)

    def _check_flat_mood(self, text: str) -> bool:
        """检测对话氛围平淡"""
        # 简单判断：消息很短且没有情感词
        flat_keywords = ["嗯", "哦", "好的", "是吧", "还行"]
        return text in flat_keywords or len(text.strip()) <= 3

    def _check_deep_conversation(self, context: Any) -> bool:
        """检测深度对话"""
        # TODO: 需要更复杂的分析
        return False

    def _check_goal_progress(self, context: Any) -> None:
        """检查目标进度"""
        recent_messages = self._get_recent_messages(context, 3)
        combined_text = " ".join(recent_messages).lower()

        for goal in self.goal_manager.active_goals:
            if not goal.is_active():
                continue

            current_step = goal.get_current_step()
            if not current_step:
                # 没有更多步骤，标记为完成
                goal.complete()
                continue

            # 关键词匹配检测
            if self._check_step_completed(current_step, combined_text):
                goal.current_step += 1
                goal.updated_at = (
                    goal.updated_at.now() if hasattr(goal.updated_at, "now") else None
                )
                logger.debug(
                    f"目标进度更新：{goal.objective} - "
                    f"步骤 {goal.current_step}/{len(goal.steps)}"
                )

                # 检查是否全部完成
                if goal.current_step >= len(goal.steps):
                    goal.complete()
                    logger.info(f"目标完成：{goal.objective}")

    def _check_step_completed(self, step: Any, text: str) -> bool:
        """检查步骤是否完成"""
        if not step.keywords:
            # 没有关键词，默认完成
            return True

        for keyword in step.keywords:
            if keyword.lower() in text:
                logger.debug(f"检测到关键词 '{keyword}'")
                return True

        return False

    def _get_max_active_intents(self) -> int:
        """获取最大活跃意图数"""
        if self.intent_engine.config and hasattr(self.intent_engine.config, "settings"):
            return self.intent_engine.config.settings.max_active_intents
        return 3

    def _get_intent_interval(self) -> int:
        """获取意图生成间隔"""
        if self.intent_engine.config and hasattr(self.intent_engine.config, "generation"):
            return self.intent_engine.config.generation.intent_generation_interval
        return 3  # 默认值

    def _should_generate_intents(self, stream_id: str) -> bool:
        """检查是否应该生成意图"""
        current_count = self._message_counts.get(stream_id, 0)
        interval = self._get_intent_interval()
        return current_count >= interval

    async def _update_system_reminder(self, stream_id: str) -> None:
        """更新 System Reminder"""
        from src.core.prompt import get_system_reminder_store

        store = get_system_reminder_store()

        # 获取优先级最高的目标
        goal = self.goal_manager.get_priority_goal()

        if goal is None:
            # 没有活跃目标，移除 reminder
            store.delete("actor", "当前小想法")
            return

        # 构建 reminder 内容
        reminder = self._build_reminder(goal)

        # 注入到 actor bucket
        store.set("actor", "当前小想法", content=reminder)
        logger.debug(f"已更新 System Reminder: {goal.objective}")

    def _build_reminder(self, goal: Goal) -> str:
        """构建 System Reminder 内容"""
        current_step = goal.get_current_step()
        step_hint = current_step.action if current_step and current_step.action else ""

        reminder = f"""
## 💭 你心里的小想法

**{goal.objective}**

"""
        if step_hint:
            reminder += f"可以试着：{step_hint}\n\n"
        else:
            reminder += "自然地聊就好，让对话顺着你的想法流动~\n\n"

        reminder += "（不用太刻意，真诚最重要）"

        return reminder

    def get_goal_manager(self) -> GoalManager:
        """获取 GoalManager 实例"""
        return self.goal_manager
