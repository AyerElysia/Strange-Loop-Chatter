"""人格 prompt 注入器。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.app.plugin_system.base import BaseEventHandler
from src.kernel.event import EventDecision
from src.kernel.logger import get_logger

from ...service import get_personality_engine_service

if TYPE_CHECKING:
    from ...plugin import PersonalityEnginePlugin


logger = get_logger("personality_engine_plugin.events.prompt")


class PersonalityPromptInjector(BaseEventHandler):
    """在 prompt 构建时注入人格态摘要。"""

    handler_name = "personality_prompt_injector"
    handler_description = "在目标 system prompt 尾部注入人格态"
    weight = 12
    intercept_message = False
    init_subscribe = ["on_prompt_build"]

    def __init__(self, plugin: "PersonalityEnginePlugin") -> None:
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

        service = get_personality_engine_service()
        if service is None:
            return EventDecision.SUCCESS, params

        chat_type_value = values.get("chat_type")
        normalized_chat_type: str | None = None
        if chat_type_value is not None:
            text = str(chat_type_value).strip()
            normalized_chat_type = text if text else None
        block = service.render_prompt_block(
            stream_id=stream_id,
            chat_type=normalized_chat_type,
        )
        if not block:
            return EventDecision.SUCCESS, params

        target_field = self._resolve_target_field(prompt_name)
        if not target_field:
            return EventDecision.SUCCESS, params

        current_text = str(values.get(target_field, "") or "").strip()
        values[target_field] = (
            f"{current_text}\n\n{block}".strip() if current_text else block
        )
        logger.debug(f"已向 prompt 注入人格态: stream={stream_id[:8]}")
        return EventDecision.SUCCESS, params

    def _resolve_target_field(self, prompt_name: str) -> str:
        if prompt_name.endswith("_system_prompt"):
            return "extra_info"
        if prompt_name.endswith("_user_prompt"):
            return "extra"
        return ""
