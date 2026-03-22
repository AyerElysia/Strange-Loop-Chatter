"""自我叙事 prompt 注入器。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.app.plugin_system.base import BaseEventHandler
from src.kernel.event import EventDecision
from src.app.plugin_system.api.log_api import get_logger

from ...service import get_self_narrative_service

if TYPE_CHECKING:
    from ...plugin import SelfNarrativePlugin

logger = get_logger("self_narrative_plugin.events.prompt")


class SelfNarrativePromptInjector(BaseEventHandler):
    """在 prompt 构建时注入自我叙事摘要。"""

    handler_name = "self_narrative_prompt_injector"
    handler_description = "在目标 system prompt 的尾部补充区注入自我叙事"
    weight = 12
    intercept_message = False
    init_subscribe = ["on_prompt_build"]

    def __init__(self, plugin: "SelfNarrativePlugin") -> None:
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

        service = get_self_narrative_service()
        if service is None:
            return EventDecision.SUCCESS, params

        block = service.render_prompt_block(stream_id, values.get("chat_type"))
        if not block:
            return EventDecision.SUCCESS, params

        target_field = self._resolve_target_field(prompt_name)
        if not target_field:
            return EventDecision.SUCCESS, params

        current_text = str(values.get(target_field, "") or "").strip()
        values[target_field] = (
            f"{current_text}\n\n{block}".strip() if current_text else block
        )
        logger.debug(f"已向 prompt 注入自我叙事: stream={stream_id[:8]}")
        return EventDecision.SUCCESS, params

    def _resolve_target_field(self, prompt_name: str) -> str:
        """根据目标模板名选择注入字段。"""

        if prompt_name.endswith("_system_prompt"):
            return "extra_info"
        if prompt_name.endswith("_user_prompt"):
            return "extra"
        return ""
