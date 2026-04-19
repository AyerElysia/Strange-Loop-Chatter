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
        return _normalize_multimodal_media_data(raw_data)

    if isinstance(raw_data, dict):
        if media_type == "video":
            keys = ("base64", "data", "video_base64", "url", "path", "file")
        else:
            keys = ("data", "base64", "url", "path", "file")

        for key in keys:
            value = raw_data.get(key)
            if isinstance(value, str) and value.strip():
                return value

        nested_media = raw_data.get("media")
        if isinstance(nested_media, list):
            for item in nested_media:
                if not isinstance(item, dict):
                    continue
                for key in ("data", "base64", "url", "path", "file"):
                    value = item.get(key)
                    if isinstance(value, str) and value.strip():
                        return _normalize_multimodal_media_data(value)
    return ""


def _normalize_multimodal_media_data(value: str) -> str:
    """把不同来源的媒体前缀统一成多模态链路可消费的形式。"""
    if value.startswith("base64://"):
        return f"base64|{value[len('base64://'):]}"
    return value


def get_media_list(msg: Message) -> list[dict[str, Any]]:
    """从 Message 对象中提取 media 列表。"""
    collected: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def extend_media(source: Any) -> None:
        if not isinstance(source, list):
            return
        for item in source:
            if not isinstance(item, dict):
                continue
            media_type = str(item.get("type", "")).lower()
            if not media_type:
                continue
            raw_key = (
                item.get("data")
                or item.get("base64")
                or item.get("path")
                or item.get("url")
                or item.get("file")
            )
            key = (media_type, str(raw_key))
            if key in seen:
                continue
            seen.add(key)
            collected.append(item)

    content = getattr(msg, "content", None)
    if isinstance(content, dict):
        extend_media(content.get("media"))

    extra = getattr(msg, "extra", {})
    if isinstance(extra, dict):
        extend_media(extra.get("media"))

    media = getattr(msg, "media", None)
    extend_media(media)

    if collected:
        return collected

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
