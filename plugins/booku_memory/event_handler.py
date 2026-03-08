"""Booku Memory 事件处理器。"""

from __future__ import annotations

import random
import time
from typing import TYPE_CHECKING, Any

from src.app.plugin_system.api.log_api import get_logger
from src.core.components.base import BaseEventHandler
from src.kernel.event import EventDecision

logger = get_logger("booku_memory_event_handler")

if TYPE_CHECKING:
    from .service.metadata_repository import BookuMemoryMetadataRepository

# 目标模板：仅对 default_chatter user prompt 闪回注入
_FLASHBACK_TARGET_PROMPT = "default_chatter_user_prompt"


class MemoryFlashbackInjector(BaseEventHandler):
    """记忆闪回注入器。

    订阅 ``on_prompt_build`` 事件，当 ``default_chatter_user_prompt``
    模板即将构建时，按配置概率触发“记忆闪回”，并在 ``values.extra``
    中追加一个 markdown 小节。

    闪回抽取规则：
    - 触发概率由 ``flashback.trigger_probability`` 决定；
    - 归档层/隐现层选择由 ``flashback.archived_probability`` 决定；
    - 在目标层中按 activation_count 反向加权抽取（激活次数低更易被抽到）。
    """

    handler_name: str = "memory_flashback_injector"
    handler_description: str = "在 default_chatter user prompt extra 板块注入记忆闪回"
    weight: int = 10
    intercept_message: bool = False
    init_subscribe: list[str] = ["on_prompt_build"]

    def __init__(self, plugin: Any) -> None:
        super().__init__(plugin)
        self._repo = None
        self._repo_initialized = False
        self._recent_flashbacks: dict[str, float] = {}

    def _prune_recent_flashbacks(self, now: float, cooldown_seconds: int) -> None:
        """清理过期的近期闪回记录。"""

        if cooldown_seconds <= 0:
            self._recent_flashbacks.clear()
            return

        expired: list[str] = []
        for memory_id, ts in self._recent_flashbacks.items():
            if now - ts >= cooldown_seconds:
                expired.append(memory_id)

        for memory_id in expired:
            self._recent_flashbacks.pop(memory_id, None)

    async def _get_repo(self) -> "BookuMemoryMetadataRepository":
        from .config import BookuMemoryConfig
        from .service.metadata_repository import BookuMemoryMetadataRepository

        config = self.plugin.config if isinstance(self.plugin.config, BookuMemoryConfig) else BookuMemoryConfig()
        if self._repo is None:
            self._repo = BookuMemoryMetadataRepository(db_path=config.storage.metadata_db_path)
        if not self._repo_initialized:
            await self._repo.initialize()
            self._repo_initialized = True
        return self._repo

    @staticmethod
    def _format_flashback_block(memory_text: str) -> str:
        """将闪回内容格式化为注入块。"""

        text = (memory_text or "").strip()
        return (
            "## 记忆闪回\n"
            "就在刚才，你突然回忆起了一些事情：\n"
            f"{text}\n"
            "- 这是你无征兆的回忆起的东西，你可以按实际情况处理，可以选择忽视，也可以选择其他做法。\n"
            "- 注：这是你记忆中已经存在的内容，不需要重新写入。"
        )

    async def execute(
        self, event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        """处理 on_prompt_build 事件，按需向 extra 注入闪回内容。"""

        if params.get("name") != _FLASHBACK_TARGET_PROMPT:
            return EventDecision.SUCCESS, params

        from .config import BookuMemoryConfig
        from .flashback import (
            activation_weight,
            pick_layer,
            should_trigger,
            weighted_choice,
        )

        config_obj = self.plugin.config if isinstance(self.plugin.config, BookuMemoryConfig) else BookuMemoryConfig()
        fb = config_obj.flashback
        if not fb.enabled:
            return EventDecision.SUCCESS, params

        if not should_trigger(trigger_probability=float(fb.trigger_probability), u=random.random()):
            return EventDecision.SUCCESS, params

        bucket = pick_layer(archived_probability=float(fb.archived_probability), u=random.random())
        repo = await self._get_repo()

        folder_id = fb.folder_id
        if isinstance(folder_id, str) and not folder_id.strip():
            folder_id = None

        records = await repo.list_records_by_bucket(
            bucket=bucket,
            folder_id=folder_id,
            limit=int(fb.candidate_limit),
            include_deleted=False,
        )

        cooldown_seconds = int(getattr(fb, "cooldown_seconds", 0) or 0)
        now = time.time()
        self._prune_recent_flashbacks(now=now, cooldown_seconds=cooldown_seconds)
        if cooldown_seconds > 0 and records:
            before_count = len(records)
            records = [
                r
                for r in records
                if str(getattr(r, "memory_id", "") or "") not in self._recent_flashbacks
            ]
            if not records:
                logger.info(
                    "flashback 已触发但候选均处于冷却期（"
                    f"bucket={bucket}, folder_id={folder_id}, cooldown_seconds={cooldown_seconds}, candidates={before_count}）"
                )
                return EventDecision.SUCCESS, params

        if not records:
            logger.info(
                f"flashback 已触发但无候选记忆（bucket={bucket}, folder_id={folder_id}, limit={int(fb.candidate_limit)}）"
            )
            return EventDecision.SUCCESS, params

        weights = [
            activation_weight(
                activation_count=int(getattr(r, "activation_count", 0)),
                exponent=float(fb.activation_weight_exponent),
            )
            for r in records
        ]
        picked = weighted_choice(records, weights, u=random.random())
        if picked is None:
            return EventDecision.SUCCESS, params

        picked_id = str(getattr(picked, "memory_id", "") or "")
        if cooldown_seconds > 0 and picked_id:
            self._recent_flashbacks[picked_id] = now

        values: dict[str, Any] = params.get("values", {})
        existing_extra: str = values.get("extra", "") or ""
        block = self._format_flashback_block(getattr(picked, "content", ""))
        separator = "\n\n" if existing_extra else ""
        values["extra"] = existing_extra + separator + block

        # 显式写回，确保上层读取到变更
        params["values"] = values

        logger.info(
            f"已注入记忆闪回（bucket={bucket}, memory_id={picked_id}）"
        )
        return EventDecision.SUCCESS, params
