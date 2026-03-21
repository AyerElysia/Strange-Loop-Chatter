"""未完成念头 prompt 注入器。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.app.plugin_system.base import BaseEventHandler
from src.kernel.event import EventDecision
from src.kernel.logger import get_logger

from ...service import get_unfinished_thought_service

if TYPE_CHECKING:
    from ...plugin import UnfinishedThoughtPlugin


logger = get_logger("unfinished_thought_plugin.events.prompt")


class UnfinishedThoughtPromptInjector(BaseEventHandler):
    """在 prompt 构建时注入随机挑选的未完成念头。"""

    handler_name = "unfinished_thought_prompt_injector"
    handler_description = "在目标 prompt 的 extra 板块注入未完成念头"
    weight = 11
    intercept_message = False
    init_subscribe = ["on_prompt_build"]

    def __init__(self, plugin: "UnfinishedThoughtPlugin") -> None:
        super().__init__(plugin)

    async def execute(
        self,
        event_name: str,
        params: dict[str, Any],
    ) -> tuple[EventDecision, dict[str, Any]]:
        del event_name

        config = getattr(self.plugin, "config", None)
        if not config or not getattr(config.plugin, "enabled", True):
            return EventDecision.SUCCESS, params
        if not getattr(config.plugin, "inject_prompt", True):
            return EventDecision.SUCCESS, params

        prompt_name = str(params.get("name", ""))
        if prompt_name not in config.prompt.target_prompt_names:
            return EventDecision.SUCCESS, params

        values = params.get("values")
        if not isinstance(values, dict):
            return EventDecision.SUCCESS, params

        stream_id = str(values.get("stream_id", "")).strip()
        if not stream_id:
            return EventDecision.SUCCESS, params

        service = get_unfinished_thought_service()
        if service is None:
            return EventDecision.SUCCESS, params

        block = service.render_prompt_block(stream_id, values.get("chat_type"))
        if not block:
            return EventDecision.SUCCESS, params

        current_extra = str(values.get("extra", "") or "")
        values["extra"] = (
            f"{current_extra}\n\n{block}".strip() if current_extra else block
        )
        logger.debug(f"已向 prompt 注入未完成念头: stream={stream_id[:8]}")
        return EventDecision.SUCCESS, params

