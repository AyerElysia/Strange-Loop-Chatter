"""Time Awareness Plugin - 时间感知插件。

为爱莉希雅提供时间感知能力，注入中式时间描述到 system reminder。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .plugin import TimeAwarenessPlugin

__all__ = ["TimeAwarenessPlugin"]


def __getattr__(name: str) -> Any:
    if name == "TimeAwarenessPlugin":
        from .plugin import TimeAwarenessPlugin

        return TimeAwarenessPlugin
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
