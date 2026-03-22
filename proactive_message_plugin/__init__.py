"""Proactive Message Plugin - 主动发消息插件"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .plugin import ProactiveMessagePlugin

__all__ = ["ProactiveMessagePlugin"]


def __getattr__(name: str) -> Any:
    if name == "ProactiveMessagePlugin":
        from .plugin import ProactiveMessagePlugin

        return ProactiveMessagePlugin
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
