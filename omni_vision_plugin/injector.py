"""Omni Vision 多模态注入器。

在聊天流激活时，将图片直接注入到 LLM 上下文，
允许主模型直接接收和处理图片，绕过 VLM 转译。
"""

from __future__ import annotations

from typing import Any

from src.kernel.logger import get_logger
from src.kernel.llm import LLMPayload, ROLE, Text, Image
from src.kernel.event import EventDecision

logger = get_logger("omni_vision_injector")


async def inject_multimodal_images(
    params: dict[str, Any],
) -> tuple[EventDecision, dict[str, Any]]:
    """在聊天流激活时注入图片到上下文。

    当启用全模态视觉时，从消息中提取图片并注入到
    上下文中，使主模型能够直接接收图片。

    Args:
        params: 事件参数，包含 message 和 stream_id

    Returns:
        事件决策和更新后的参数
    """
    message = params.get("message")
    if not message:
        return EventDecision.SUCCESS, params

    stream_id = getattr(message, "stream_id", None)
    if not stream_id:
        return EventDecision.SUCCESS, params

    # 检查消息是否包含媒体
    media_list = getattr(message, "media", [])
    if not media_list:
        return EventDecision.SUCCESS, params

    # 过滤出图片和表情包
    images_to_inject = [
        m for m in media_list
        if m.get("type") in ("image", "emoji") and m.get("data")
    ]

    if not images_to_inject:
        return EventDecision.SUCCESS, params

    logger.info(
        f"[多模态注入] 聊天流 {stream_id[:8]} - "
        f"准备注入 {len(images_to_inject)} 张图片/表情包"
    )

    # 将图片数据注入到消息的 extra 中，供后续聊天器使用
    extra = getattr(message, "extra", {})
    if not isinstance(extra, dict):
        extra = {}

    # 保存原始媒体数据，供聊天器读取
    extra["omni_vision_images"] = images_to_inject

    # 标记已注入，避免重复处理
    extra["omni_vision_injected"] = True

    message.extra = extra

    logger.debug(
        f"[多模态注入] 已将图片数据写入 message.extra, "
        f"数量：{len(images_to_inject)}"
    )

    return EventDecision.SUCCESS, params
