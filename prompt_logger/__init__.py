"""Prompt Logger 插件 — 记录所有发送给 LLM 的完整提示词到日志文件。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .plugin import PromptLoggerPlugin, log_prompt, log_response
    from .service import PromptLoggerService
    from .service import (
        log_llm_interaction,
        log_prompt_request,
        log_prompt_response,
        set_llm_context,
    )

__all__ = [
    "PromptLoggerPlugin",
    "PromptLoggerService",
    "log_prompt",
    "log_response",
    "set_llm_context",
    "log_prompt_request",
    "log_prompt_response",
    "log_llm_interaction",
]

_EXPORTS: dict[str, tuple[str, str]] = {
    "PromptLoggerPlugin": (".plugin", "PromptLoggerPlugin"),
    "log_prompt": (".plugin", "log_prompt"),
    "log_response": (".plugin", "log_response"),
    "PromptLoggerService": (".service", "PromptLoggerService"),
    "set_llm_context": (".service", "set_llm_context"),
    "log_prompt_request": (".service", "log_prompt_request"),
    "log_prompt_response": (".service", "log_prompt_response"),
    "log_llm_interaction": (".service", "log_llm_interaction"),
}


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module_name, attr_name = target
    if module_name == ".plugin":
        from .plugin import PromptLoggerPlugin, log_prompt, log_response

        return {
            "PromptLoggerPlugin": PromptLoggerPlugin,
            "log_prompt": log_prompt,
            "log_response": log_response,
        }[attr_name]

    from .service import (
        PromptLoggerService,
        log_llm_interaction,
        log_prompt_request,
        log_prompt_response,
        set_llm_context,
    )

    return {
        "PromptLoggerService": PromptLoggerService,
        "set_llm_context": set_llm_context,
        "log_prompt_request": log_prompt_request,
        "log_prompt_response": log_prompt_response,
        "log_llm_interaction": log_llm_interaction,
    }[attr_name]
