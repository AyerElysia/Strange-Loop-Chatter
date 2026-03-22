"""被动记忆浮现事件处理器。"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from src.core.components.base import BaseEventHandler
from src.core.components.types import EventType
from src.kernel.event import EventDecision
from src.kernel.logger import get_logger

from .config import MemoryPassiveTriggerConfig

if TYPE_CHECKING:
    from src.app.plugin_system.api.service_api import get_service

logger = get_logger("memory_passive_trigger_handler")


class MemoryPassiveTriggerHandler(BaseEventHandler):
    """被动记忆浮现触发器。

    订阅 ``on_message_received`` 事件，当收到用户消息时：
    1. 提取消息文本作为检索 query
    2. 调用 booku_memory 服务检索相关记忆
    3. 过滤超过相似度阈值的记忆
    4. 应用冷却检查
    5. 注入到 conversation context

    注入格式：
    ```markdown
    ## 记忆浮现
    听到这句话时，你脑海中无征兆地浮现出一些相关的记忆：

    - 「记忆内容」（来自 folder 名称）

    注：这是你记忆系统自动关联出来的，不是刻意检索的结果。
    你可以自然地提及，也可以选择忽视。
    ```
    """

    handler_name: str = "memory_passive_trigger_handler"
    handler_description: str = "监听用户消息，被动触发相关记忆浮现"
    weight: int = 5
    intercept_message: bool = False
    init_subscribe: list[EventType | str] = [EventType.ON_MESSAGE_RECEIVED]
    dependencies: list[str] = ["booku_memory"]

    def __init__(self, plugin: Any) -> None:
        super().__init__(plugin)
        self._memory_service = None
        self._cooldown_map: dict[str, float] = {}
        self._config: MemoryPassiveTriggerConfig | None = None

    def _get_config(self) -> MemoryPassiveTriggerConfig:
        """获取插件配置对象。"""
        if isinstance(self.plugin.config, MemoryPassiveTriggerConfig):
            return self.plugin.config
        return MemoryPassiveTriggerConfig()

    def _get_memory_service(self):
        """获取 booku_memory 服务实例。"""
        if self._memory_service is None:
            from src.kernel.concurrency import get_service
            self._memory_service = get_service("booku_memory:service:booku_memory")
        return self._memory_service

    def _prune_cooldown(self, now: float, cooldown_seconds: int) -> None:
        """清理过期的冷却记录。"""
        if cooldown_seconds <= 0:
            self._cooldown_map.clear()
            return

        expired = [
            memory_id
            for memory_id, expire_time in self._cooldown_map.items()
            if now >= expire_time
        ]
        for memory_id in expired:
            del self._cooldown_map[memory_id]

    def _format_flashback_block(self, memories: list[dict[str, Any]]) -> str:
        """将浮现的记忆格式化为注入块。"""
        if not memories:
            return ""

        lines = []
        for m in memories:
            content = m.get("content_snippet", "")[:200]
            folder_id = m.get("folder_id", "unknown")
            lines.append(f"- 「{content}」（来自{folder_id}）")

        if not lines:
            return ""

        return (
            "\n\n## 记忆浮现\n"
            "听到这句话时，你脑海中无征兆地浮现出一些相关的记忆：\n\n"
            + "\n".join(lines)
            + "\n\n注：这是你记忆系统自动关联出来的，不是刻意检索的结果。\n"
            "你可以自然地提及，也可以选择忽视。"
        )

    async def execute(
        self, event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        """处理 on_message_received 事件，被动触发记忆浮现。"""
        config = self._get_config()
        trigger = config.trigger
        retrieval = config.retrieval
        debug = config.debug

        # 获取用户消息
        message = params.get("message", "")
        if not message or not isinstance(message, str):
            return EventDecision.SUCCESS, params

        message = message.strip()
        if not message:
            return EventDecision.SUCCESS, params

        # 检索记忆
        try:
            service = self._get_memory_service()
            if service is None:
                logger.warning("无法获取 booku_memory 服务，跳过被动浮现")
                return EventDecision.SUCCESS, params

            result = await service.retrieve_memories(
                query_text=message,
                top_k=retrieval.candidate_limit,
                include_archived=retrieval.include_archived,
                include_knowledge=retrieval.include_knowledge,
            )

        except Exception as exc:
            logger.warning(f"被动浮现检索失败：{exc}")
            return EventDecision.SUCCESS, params

        # 过滤超过阈值的记忆
        results = result.get("results", [])
        if not results:
            if debug.verbose:
                logger.debug(f"被动浮现：无匹配记忆（query={message[:50]}）")
            return EventDecision.SUCCESS, params

        qualified = [
            r for r in results
            if r.get("score", 0) >= trigger.similarity_threshold
        ]

        if not qualified:
            if debug.verbose:
                max_score = max(r.get("score", 0) for r in results)
                logger.debug(
                    f"被动浮现：最高分数{max_score:.3f}未达阈值{trigger.similarity_threshold}"
                )
            return EventDecision.SUCCESS, params

        # 冷却检查
        now = time.time()
        self._prune_cooldown(now, trigger.cooldown_seconds)

        qualified = [
            r for r in qualified
            if str(r.get("id", "")) not in self._cooldown_map
        ]

        if not qualified:
            if debug.verbose:
                logger.debug("被动浮现：所有匹配记忆均处于冷却期")
            return EventDecision.SUCCESS, params

        # 按优先级排序
        priority_folders = set(trigger.priority_folders)
        qualified.sort(key=lambda r: (
            0 if r.get("folder_id") in priority_folders else 1,
            -r.get("score", 0),
        ))

        # 截取 Top-N
        selected = qualified[:trigger.max_flash_count]

        # 记录冷却时间
        if trigger.cooldown_seconds > 0:
            for r in selected:
                self._cooldown_map[str(r.get("id", ""))] = now + trigger.cooldown_seconds

        # 注入到 context
        context = params.setdefault("context", {})
        existing_extra = context.get("extra", "") or ""
        flashback_block = self._format_flashback_block(selected)

        if flashback_block:
            separator = "\n\n" if existing_extra else ""
            context["extra"] = existing_extra + separator + flashback_block

            if debug.verbose:
                logger.info(
                    f"被动浮现已注入：count={len(selected)}, "
                    f"query={message[:30]}..."
                )
        else:
            if debug.verbose:
                logger.warning("被动浮现：格式化后内容为空")

        return EventDecision.SUCCESS, params
