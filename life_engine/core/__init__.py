"""life_engine 核心模块。"""

from __future__ import annotations

from .config import LifeEngineConfig

# Plugin should be imported directly from .plugin when needed
# to avoid circular import with service module
# from .plugin import LifeEnginePlugin

__all__ = ["LifeEngineConfig"]