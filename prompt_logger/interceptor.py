"""LLM 请求拦截器。

通过 monkey-patch LLMRequest.send() 方法，自动记录所有 LLM 请求和响应。
这是实现无侵入式提示词记录的核心模块。

支持两种模式：
1. 自动拦截 - 记录所有 LLMRequest.send() 调用
2. 上下文感知 - 通过 set_current_context() 设置 stream_id/chatter_name
"""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from typing import Any, Callable

from src.kernel.llm import LLMRequest, LLMResponse
from src.kernel.logger import get_logger

logger = get_logger("prompt_logger.interceptor", display="提示词拦截")

# 保存原始的 send 方法
_original_send: Callable | None = None

# 当前上下文（stream_id, chatter_name）- 通过 contextvar 在 asyncio 任务间传递
_current_context: ContextVar[dict[str, str] | None] = ContextVar(
    "prompt_logger_context", default=None
)


def set_current_context(stream_id: str = "", chatter_name: str = "") -> None:
    """设置当前 LLM 请求的上下文信息。

    在调用 LLMRequest.send() 之前调用此函数，可以让日志记录包含 stream_id。

    Args:
        stream_id: 聊天流 ID
        chatter_name: Chatter 名称
    """
    _current_context.set({
        "stream_id": stream_id,
        "chatter_name": chatter_name,
    })


def get_current_context() -> dict[str, str]:
    """获取当前上下文信息。

    Returns:
        包含 stream_id 和 chatter_name 的字典
    """
    ctx = _current_context.get()
    return ctx if ctx else {"stream_id": "", "chatter_name": ""}


def _try_extract_payload_info(payloads: list[Any]) -> list[dict[str, Any]]:
    """尝试从 payloads 中提取简化信息用于日志记录。

    Args:
        payloads: payloads 列表

    Returns:
        简化后的 payload 信息列表
    """
    result = []
    for p in payloads:
        role = getattr(p, "role", "unknown")
        content = getattr(p, "content", [])

        # 提取文本内容
        text_parts = []
        for item in content:
            if hasattr(item, "text"):
                text_parts.append(item.text)
            elif hasattr(item, "value"):
                text_parts.append(f"[Image: {str(item.value)[:50]}...]")
            else:
                text_parts.append(str(item)[:200])

        result.append({
            "role": str(role),
            "content_preview": "\n".join(text_parts)[:500] if text_parts else "",
        })
    return result


async def _patched_send_async(
    self: LLMRequest,
    auto_append_response: bool = True,
    *,
    stream: bool = True,
) -> LLMResponse:
    """包装后的异步 send 方法。

    在调用原始 send 方法前后记录请求和响应。
    """
    from .service import PromptLoggerService

    # 获取当前上下文（可能由 set_current_context 设置）
    context = get_current_context()
    stream_id = context.get("stream_id", "")
    chatter_name = context.get("chatter_name", "") or self.request_name or "unknown"
    service = None

    # 在调用前记录请求
    try:
        service = PromptLoggerService.get_instance()
        if service:
            logger.info(
                f"LLM Request: {chatter_name}, "
                f"stream={stream_id[:8] if stream_id else 'N/A'}, "
                f"payloads_count={len(self.payloads)}, "
                f"model={self.model_set[0].get('model_identifier') if self.model_set else 'unknown'}"
            )

            # 记录完整 payloads 到文件日志
            plugin = service.plugin
            if hasattr(plugin, "log_prompt"):
                plugin.log_prompt(
                    payloads=self.payloads,
                    stream_id=stream_id,
                    chatter_name=chatter_name,
                    is_response=False,
                )
    except Exception as e:
        logger.debug(f"记录 LLM 请求失败：{e}")

    # 调用原始方法
    assert _original_send is not None
    response = await _original_send(self, auto_append_response, stream=stream)

    # 记录响应
    try:
        if service:
            plugin = service.plugin
            if hasattr(plugin, "log_prompt"):
                message = getattr(response, "message", "")
                call_list = getattr(response, "call_list", None)

                plugin.log_prompt(
                    payloads=[],
                    stream_id=stream_id,
                    chatter_name=chatter_name,
                    is_response=True,
                    message=message,
                    call_list=call_list,
                )
                logger.info(
                    f"LLM Response: stream={stream_id[:8] if stream_id else 'N/A'}, "
                    f"message_len={len(message) if message else 0}, "
                    f"tool_calls={len(call_list) if call_list else 0}"
                )
    except Exception as e:
        logger.debug(f"记录 LLM 响应失败：{e}")

    return response


def install_interceptor() -> bool:
    """安装 LLM 请求拦截器。

    Monkey-patch LLMRequest.send() 方法以自动记录所有 LLM 交互。

    Returns:
        bool: 是否安装成功
    """
    global _original_send

    if _original_send is not None:
        logger.debug("拦截器已安装，跳过")
        return True

    try:
        # 保存原始方法
        _original_send = LLMRequest.send

        # 替换为包装后的方法
        LLMRequest.send = _patched_send_async  # type: ignore[method-assign]

        logger.info("LLM 请求拦截器安装成功")
        return True
    except Exception as e:
        logger.error(f"安装拦截器失败：{e}")
        return False


def uninstall_interceptor() -> bool:
    """卸载 LLM 请求拦截器。

    恢复原始的 LLMRequest.send() 方法。

    Returns:
        bool: 是否卸载成功
    """
    global _original_send

    if _original_send is None:
        logger.debug("拦截器未安装，跳过")
        return True

    try:
        # 恢复原始方法
        LLMRequest.send = _original_send  # type: ignore[method-assign]
        _original_send = None

        logger.info("LLM 请求拦截器已卸载")
        return True
    except Exception as e:
        logger.error(f"卸载拦截器失败：{e}")
        return False


def is_interceptor_installed() -> bool:
    """检查拦截器是否已安装。

    Returns:
        bool: 是否已安装
    """
    return _original_send is not None
