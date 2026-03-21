"""Prompt Logger 插件 — 记录所有发送给 LLM 的完整提示词到日志文件。"""

from .plugin import PromptLoggerPlugin, log_prompt, log_response
from .service import PromptLoggerService
from .service import (
    set_llm_context,
    log_prompt_request,
    log_prompt_response,
    log_llm_interaction,
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
