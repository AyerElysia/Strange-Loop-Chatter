"""Default Chatter 多模态辅助模块。

负责提取消息中的图片/表情包/视频，并组装为 LLM 原生多模态 payload 内容。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.kernel.llm import Content, Image, Text, Video

if TYPE_CHECKING:
    from src.core.models.message import Message


@dataclass
class MediaItem:
    """从消息中提取的媒体条目。"""

    media_type: str
    raw_data: str
    source_message_id: str


class ImageBudget:
    """图片预算追踪器。"""

    def __init__(self, total_max: int = 4) -> None:
        self._total_max = max(0, total_max)
        self._used = 0

    @property
    def remaining(self) -> int:
        return max(0, self._total_max - self._used)

    def consume(self, count: int) -> None:
        self._used += max(0, count)

    def is_exhausted(self) -> bool:
        return self._used >= self._total_max

    def reset(self) -> None:
        self._used = 0


def extract_media_from_messages(
    messages: list[Message],
    max_images: int = 4,
    max_videos: int = 1,
) -> list[MediaItem]:
    """从消息列表中提取图片/表情包/视频媒体。"""
    items: list[MediaItem] = []
    image_like_count = 0
    video_count = 0

    for msg in messages:
        if image_like_count >= max_images and video_count >= max_videos:
            break

        media_list = get_media_list(msg)
        if not media_list:
            continue

        msg_id = getattr(msg, "message_id", "")
        for media in media_list:
            media_type = str(media.get("type", "image")).lower()
            if media_type not in ("image", "emoji", "video"):
                continue

            data = _extract_media_data(media_type, media.get("data", ""))
            if not data:
                continue

            if media_type in ("image", "emoji"):
                if image_like_count >= max_images:
                    continue
                image_like_count += 1
            elif media_type == "video":
                if video_count >= max_videos:
                    continue
                video_count += 1

            items.append(
                MediaItem(
                    media_type=media_type,
                    raw_data=data,
                    source_message_id=str(msg_id),
                )
            )

    return items


def build_multimodal_content(
    text: str,
    media_items: list[MediaItem],
) -> list[Content]:
    """构建 Text + Image/Video 混合 content 列表。"""
    content_list: list[Content] = [Text(text)]
    for item in media_items:
        if item.media_type == "emoji":
            content_list.append(Text("[表情包]"))
            content_list.append(Image(item.raw_data))
            continue
        if item.media_type == "image":
            content_list.append(Image(item.raw_data))
            continue
        if item.media_type == "video":
            content_list.append(Text("[视频]"))
            content_list.append(Video(item.raw_data))
    return content_list


def _extract_media_data(media_type: str, raw_data: Any) -> str:
    """提取媒体原始数据（base64/data-url/path）。"""
    if isinstance(raw_data, str):
        return raw_data

    if isinstance(raw_data, dict):
        if media_type == "video":
            for key in ("base64", "data", "video_base64"):
                value = raw_data.get(key)
                if isinstance(value, str) and value.strip():
                    return value
        else:
            value = raw_data.get("data")
            if isinstance(value, str) and value.strip():
                return value
            value = raw_data.get("base64")
            if isinstance(value, str) and value.strip():
                return value
    return ""


def get_media_list(msg: Message) -> list[dict[str, Any]]:
    """从 Message 对象中提取 media 列表。"""
    content = getattr(msg, "content", None)
    if isinstance(content, dict):
        media = content.get("media")
        if isinstance(media, list) and media:
            return media

    extra = getattr(msg, "extra", {})
    if isinstance(extra, dict):
        media = extra.get("media")
        if isinstance(media, list) and media:
            return media

    media = getattr(msg, "media", None)
    if isinstance(media, list) and media:
        return media

    msg_type = getattr(msg, "message_type", None)
    if (
        msg_type is not None
        and str(msg_type).lower() == "emoji"
        and isinstance(content, str)
        and len(content) > 100
    ):
        data = content if content.startswith("base64|") else f"base64|{content}"
        return [{"type": "emoji", "data": data}]

    return []
