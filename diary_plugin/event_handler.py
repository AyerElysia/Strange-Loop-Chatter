"""自动写日记与连续记忆注入事件处理器。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.core.components.base.event_handler import BaseEventHandler
from src.core.components.types import EventType
from src.kernel.event import EventDecision
from src.kernel.logger import get_logger

from .config import DiaryConfig
from .prompts import build_auto_diary_system_prompt, build_shared_persona_prompt
from .service import DiaryService


logger = get_logger("diary_plugin")


class AutoDiaryEventHandler(BaseEventHandler):
    """自动写日记事件处理器。"""

    handler_name: str = "auto_diary_handler"
    handler_description: str = (
        "自动写日记事件处理器 - 当对话达到一定数量时自动总结并写入日记"
    )
    weight: int = 5
    init_subscribe: list[EventType | str] = [EventType.ON_CHATTER_STEP]

    _message_counts: dict[str, int] = {}

    def __init__(self, plugin: Any) -> None:
        super().__init__(plugin)
        self._message_counts = {}

    async def execute(
        self,
        event_name: str,
        params: dict[str, Any],
    ) -> tuple[EventDecision, dict[str, Any]]:
        """执行自动写日记检查。"""

        if not self._is_enabled():
            return EventDecision.SUCCESS, params

        stream_id = params.get("stream_id")
        if not stream_id:
            logger.debug("未找到 stream_id，跳过自动写日记检查")
            return EventDecision.SUCCESS, params

        config = self._get_config()
        if config is None:
            return EventDecision.SUCCESS, params

        if not self._allow_group_chat(params.get("chat_type"), config):
            logger.debug(f"[{stream_id[:8]}] 群聊不触发自动写日记，跳过")
            return EventDecision.SUCCESS, params

        threshold = config.auto_diary.message_threshold
        current_count = self._message_counts.get(stream_id, 0) + 1
        self._message_counts[stream_id] = current_count

        logger.debug(f"[{stream_id[:8]}] 消息计数：{current_count}/{threshold}")

        if current_count >= threshold:
            logger.info(
                f"[{stream_id[:8]}] 达到写日记阈值 ({current_count}/{threshold})，执行自动总结"
            )
            await self._auto_summary(stream_id, config.auto_diary.message_threshold)
            self._message_counts[stream_id] = 0

        return EventDecision.SUCCESS, params

    async def _auto_summary(self, stream_id: str, summary_count: int) -> None:
        """执行自动总结并写入日记。"""

        from src.app.plugin_system.api.service_api import get_service
        from src.core.managers import get_stream_manager

        logger.info(f"[{stream_id[:8]}] 开始自动总结最近 {summary_count} 条对话")

        try:
            stream_manager = get_stream_manager()
            chat_stream = stream_manager._streams.get(stream_id)
            if not chat_stream:
                logger.warning(f"无法获取聊天流：{stream_id}")
                return

            context = chat_stream.context
            all_messages = list(context.history_messages) + list(context.unread_messages)
            recent_messages = (
                all_messages[-summary_count:]
                if len(all_messages) > summary_count
                else all_messages
            )
            if not recent_messages:
                logger.warning("没有可用的对话历史")
                return

            history_lines = []
            bot_id = str(getattr(chat_stream, "bot_id", "") or "")
            bot_nickname = str(getattr(chat_stream, "bot_nickname", "") or "")
            for msg in recent_messages:
                sender = getattr(msg, "sender_name", "未知")
                sender_id = str(getattr(msg, "sender_id", "") or "")
                if bot_id and sender_id == bot_id:
                    sender = f"{bot_nickname or sender}（自己）"
                content = getattr(
                    msg,
                    "processed_plain_text",
                    str(getattr(msg, "content", "")),
                )
                history_lines.append(f"{sender}: {content}")

            service = get_service("diary_plugin:service:diary_service")
            if not isinstance(service, DiaryService):
                logger.warning("diary_service 未加载")
                return

            today_content = service.read_today()
            today_events = [event.content for event in today_content.events]
            summary = await self._llm_summarize(chat_stream, history_lines, today_events)
            if not summary:
                logger.warning("LLM 总结失败")
                return

            if self._is_duplicate(summary):
                logger.info("检测到重复内容，跳过写入")
                return

            success, message = await self._write_diary(summary)
            if not success:
                logger.warning(f"自动日记写入失败：{message}")
                return

            logger.info(f"自动日记已写入：{summary[:30]}...")

            continuous_ok, continuous_msg = await service.append_continuous_memory_entry(
                stream_id=chat_stream.stream_id,
                chat_type=chat_stream.chat_type,
                platform=chat_stream.platform,
                stream_name=chat_stream.stream_name,
                content=summary,
                section=self._get_current_section(),
                bot_id=bot_id,
                bot_nickname=bot_nickname,
                diary_date=datetime.now().strftime("%Y-%m-%d"),
            )
            if not continuous_ok:
                logger.warning(
                    f"[{stream_id[:8]}] 连续记忆同步失败，但自动日记已成功：{continuous_msg}"
                )

        except Exception as exc:
            logger.error(f"自动总结失败：{exc}", exc_info=True)

    async def _llm_summarize(
        self,
        chat_stream: Any,
        chat_history: list[str],
        today_events: list[str] | None = None,
    ) -> str | None:
        """调用 LLM 总结对话历史为第一人称日记。"""

        from src.core.config import get_model_config
        from src.kernel.llm import LLMRequest, LLMPayload, ROLE, Text

        history_text = "\n".join(chat_history)

        try:
            config = self._get_config()
            if config is None:
                logger.warning("无法获取日记插件配置")
                return None

            task_name = config.model.task_name
            model_set = get_model_config().get_task(task_name)
        except KeyError:
            logger.warning(f"未找到模型配置：{task_name}")
            return None

        if not model_set:
            return None

        request = LLMRequest(model_set, "auto_diary_summary")
        shared_persona_prompt = ""
        if config.plugin.inherit_default_chatter_persona_prompt:
            shared_persona_prompt = await build_shared_persona_prompt(chat_stream)
        request.add_payload(
            LLMPayload(
                ROLE.SYSTEM,
                Text(
                    build_auto_diary_system_prompt(
                        today_events,
                        shared_persona_prompt=shared_persona_prompt,
                        strict_identity_name_lock=config.plugin.strict_identity_name_lock,
                    )
                ),
            )
        )
        request.add_payload(
            LLMPayload(
                ROLE.USER,
                Text(
                    f"请把以下对话内容写成一篇简短的日记（以“{getattr(chat_stream, 'bot_nickname', '') or '你本人'}”的口吻，避免混成用户视角）：\n\n{history_text}"
                ),
            )
        )

        try:
            response = await request.send()
            summary = await response if not response.message else response.message
            if summary:
                text = str(summary).strip().replace("**", "").replace("*", "")
                return text if text else None
        except Exception as exc:
            logger.error(f"LLM 总结失败：{exc}")

        return None

    def _is_duplicate(self, content: str, threshold: float = 0.5) -> bool:
        """检查总结内容是否与已有日记重复。"""

        from src.app.plugin_system.api.service_api import get_service

        service = get_service("diary_plugin:service:diary_service")
        if not isinstance(service, DiaryService):
            return False

        today_content = service.read_today()
        if not today_content.events:
            return False

        content_lower = content.lower().strip()
        if len(content_lower) < 5:
            return False

        for event in today_content.events:
            existing_lower = event.content.lower().strip()
            if not existing_lower:
                continue

            similarity = self._calculate_similarity(content_lower, existing_lower)
            if similarity > threshold:
                logger.debug(f"检测到重复内容，相似度：{similarity}")
                return True

        return False

    def _calculate_similarity(self, text1: str, text2: str) -> float:
        """计算 Jaccard 相似度。"""

        set1 = set(text1)
        set2 = set(text2)
        if not set1 or not set2:
            return 0.0

        intersection = len(set1 & set2)
        union = len(set1 | set2)
        return intersection / union if union > 0 else 0.0

    async def _write_diary(self, content: str) -> tuple[bool, str]:
        """写入日记。"""

        from src.app.plugin_system.api.service_api import get_service

        service = get_service("diary_plugin:service:diary_service")
        if not isinstance(service, DiaryService):
            return False, "diary_service 未加载"

        section = self._get_current_section()
        return service.append_entry(content=content, section=section)

    def _get_current_section(self) -> str:
        """根据当前时间获取时间段分类。"""

        hour = datetime.now().hour
        if 5 <= hour < 12:
            return "上午"
        if 12 <= hour < 18:
            return "下午"
        if 18 <= hour < 23:
            return "晚上"
        return "其他"

    def _is_enabled(self) -> bool:
        """检查自动写日记功能是否启用。"""

        config = self._get_config()
        return config.auto_diary.enabled if config else False

    def _get_config(self) -> DiaryConfig | None:
        """获取插件配置。"""

        if isinstance(self.plugin.config, DiaryConfig):
            return self.plugin.config
        return None

    def reset_count(self, stream_id: str) -> None:
        """重置指定 stream_id 的计数器。"""

        if stream_id in self._message_counts:
            self._message_counts[stream_id] = 0
            logger.debug(f"[{stream_id[:8]}] 计数器已重置")

    def _allow_group_chat(
        self,
        chat_type: str | None,
        config: DiaryConfig,
    ) -> bool:
        """检查是否允许群聊触发自动写日记。"""

        chat_type_raw = str(chat_type or "").lower()
        if chat_type_raw == "group":
            return config.auto_diary.allow_group_chat
        return True


class ContinuousMemoryPromptInjector(BaseEventHandler):
    """在 prompt 构建时注入连续记忆。"""

    handler_name: str = "continuous_memory_prompt_injector"
    handler_description: str = "在目标 prompt 的 extra 板块中注入当前聊天流连续记忆"
    weight: int = 10
    init_subscribe: list[str] = ["on_prompt_build"]

    async def execute(
        self,
        event_name: str,
        params: dict[str, Any],
    ) -> tuple[EventDecision, dict[str, Any]]:
        """处理 on_prompt_build 事件。"""

        config = self._get_config()
        if (
            config is None
            or not config.continuous_memory.enabled
            or not config.continuous_memory.inject_prompt
        ):
            return EventDecision.SUCCESS, params

        prompt_name = str(params.get("name", ""))
        if prompt_name not in config.continuous_memory.target_prompt_names:
            return EventDecision.SUCCESS, params

        values = params.get("values")
        if not isinstance(values, dict):
            return EventDecision.SUCCESS, params

        stream_id = str(values.get("stream_id", "")).strip()
        if not stream_id:
            return EventDecision.SUCCESS, params

        service = self._get_service()
        if service is None:
            return EventDecision.SUCCESS, params

        memory_block = service.render_continuous_memory_for_prompt(
            stream_id,
            values.get("chat_type"),
        )
        if not memory_block:
            return EventDecision.SUCCESS, params

        values["continuous_memory"] = memory_block

        return EventDecision.SUCCESS, params

    def _get_service(self) -> DiaryService | None:
        """获取 DiaryService 实例。"""

        from src.app.plugin_system.api.service_api import get_service

        service = get_service("diary_plugin:service:diary_service")
        if isinstance(service, DiaryService):
            return service
        return None

    def _get_config(self) -> DiaryConfig | None:
        """获取插件配置。"""

        if isinstance(self.plugin.config, DiaryConfig):
            return self.plugin.config
        return None
