"""LLM 请求拦截器。

通过 monkey-patch LLMRequest.send() 方法，自动记录所有 LLM 请求和响应。
这是实现无侵入式提示词记录的核心模块。
"""

from __future__ import annotations

import inspect
from contextvars import ContextVar
from pathlib import Path
from typing import Callable

from src.kernel.llm import LLMRequest, LLMResponse
from src.kernel.logger import get_logger

logger = get_logger("prompt_logger.interceptor", display="提示词拦截")

# 保存原始的 send 方法
_original_send: Callable | None = None

# 当前上下文（通过 contextvar 在 asyncio 任务间传递）
_current_context: ContextVar[dict[str, str] | None] = ContextVar(
    "prompt_logger_context", default=None
)


def set_current_context(
    stream_id: str = "",
    chatter_name: str = "",
    plugin_name: str = "",
    request_name: str = "",
    model_name: str = "",
    chat_type: str = "",
) -> None:
    """设置当前 LLM 请求的上下文信息。"""
    _current_context.set(
        {
            "stream_id": stream_id,
            "chatter_name": chatter_name,
            "plugin_name": plugin_name,
            "request_name": request_name,
            "model_name": model_name,
            "chat_type": chat_type,
        }
    )


def get_current_context() -> dict[str, str]:
    """获取当前上下文信息。"""
    ctx = _current_context.get()
    return ctx if ctx else {
        "stream_id": "",
        "chatter_name": "",
        "plugin_name": "",
        "request_name": "",
        "model_name": "",
        "chat_type": "",
    }


def _extract_model_name(request: LLMRequest) -> str:
    """尽量提取模型标识。"""
    try:
        model_set = getattr(request, "model_set", None) or []
        if not model_set:
            return ""
        first = model_set[0]
        if isinstance(first, dict):
            for key in ("model_identifier", "name", "model_name"):
                value = first.get(key)
                if isinstance(value, str) and value:
                    return value
    except Exception:
        pass
    return ""


def _detect_plugin_from_stack() -> dict[str, str]:
    """从调用栈中尝试识别插件来源。"""
    frame = inspect.currentframe()
    if frame is None:
        return {}

    try:
        outer = frame.f_back
        while outer is not None:
            filename = (outer.f_code.co_filename or "").replace("\\", "/")
            if "/plugins/" in filename:
                parts = Path(filename).parts
                if "plugins" in parts:
                    idx = parts.index("plugins")
                    if idx + 1 < len(parts):
                        plugin_name = parts[idx + 1]
                        return {
                            "plugin_name": plugin_name,
                            "caller_path": filename,
                        }
            outer = outer.f_back
    finally:
        del frame

    return {}


def _merge_context(request: LLMRequest) -> dict[str, str]:
    """合并显式上下文、调用栈与请求本身的信息。"""
    ctx = get_current_context()
    stack_meta = _detect_plugin_from_stack()
    model_name = ctx.get("model_name") or _extract_model_name(request)
    request_name = ctx.get("request_name") or getattr(request, "request_name", "")
    plugin_name = ctx.get("plugin_name") or stack_meta.get("plugin_name", "")
    chatter_name = ctx.get("chatter_name") or plugin_name or request_name
    chat_type = ctx.get("chat_type", "")

    return {
        "stream_id": ctx.get("stream_id", ""),
        "chatter_name": chatter_name,
        "plugin_name": plugin_name,
        "request_name": request_name,
        "model_name": model_name,
        "chat_type": chat_type,
    }


async def _patched_send_async(
    self: LLMRequest,
    auto_append_response: bool = True,
    *,
    stream: bool = True,
) -> LLMResponse:
    """包装后的异步 send 方法。"""
    from .service import PromptLoggerService

    meta = _merge_context(self)
    service = None

    # 在调用前记录请求
    try:
        service = PromptLoggerService.get_instance()
        if service:
            logger.info(
                f"LLM Request: {meta['chatter_name'] or meta['request_name'] or 'unknown'}, "
                f"stream={meta['stream_id'][:8] if meta['stream_id'] else 'N/A'}, "
                f"payloads_count={len(self.payloads)}, "
                f"model={meta['model_name'] or 'unknown'}"
            )

            plugin = service.plugin
            if hasattr(plugin, "log_prompt"):
                plugin.log_prompt(
                    payloads=self.payloads,
                    stream_id=meta["stream_id"],
                    chatter_name=meta["chatter_name"],
                    request_name=meta["request_name"],
                    plugin_name=meta["plugin_name"],
                    model_name=meta["model_name"],
                    chat_type=meta["chat_type"],
                    is_response=False,
                )
    except Exception as e:
        logger.debug(f"记录 LLM 请求失败：{e}")

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
                    stream_id=meta["stream_id"],
                    chatter_name=meta["chatter_name"],
                    request_name=meta["request_name"],
                    plugin_name=meta["plugin_name"],
                    model_name=meta["model_name"],
                    chat_type=meta["chat_type"],
                    is_response=True,
                    message=message,
                    call_list=call_list,
                )
                logger.info(
                    f"LLM Response: stream={meta['stream_id'][:8] if meta['stream_id'] else 'N/A'}, "
                    f"message_len={len(message) if message else 0}, "
                    f"tool_calls={len(call_list) if call_list else 0}"
                )
    except Exception as e:
        logger.debug(f"记录 LLM 响应失败：{e}")

    return response


def install_interceptor() -> bool:
    """安装 LLM 请求拦截器。"""
    global _original_send

    if _original_send is not None:
        logger.debug("拦截器已安装，跳过")
        return True

    try:
        _original_send = LLMRequest.send
        LLMRequest.send = _patched_send_async  # type: ignore[method-assign]
        logger.info("LLM 请求拦截器安装成功")
        return True
    except Exception as e:
        logger.error(f"安装拦截器失败：{e}")
        return False


def uninstall_interceptor() -> bool:
    """卸载 LLM 请求拦截器。"""
    global _original_send

    if _original_send is None:
        logger.debug("拦截器未安装，跳过")
        return True

    try:
        LLMRequest.send = _original_send  # type: ignore[method-assign]
        _original_send = None
        logger.info("LLM 请求拦截器已卸载")
        return True
    except Exception as e:
        logger.error(f"卸载拦截器失败：{e}")
        return False


def is_interceptor_installed() -> bool:
    """检查拦截器是否已安装。"""
    return _original_send is not None
