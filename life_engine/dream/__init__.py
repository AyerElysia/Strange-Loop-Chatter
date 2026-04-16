"""life_engine 做梦系统。"""

from __future__ import annotations

from .scheduler import DreamScheduler, DreamPhase
from .residue import (
    DreamReport,
    DreamResidue,
    NREMReport,
    REMReport,
)
from .seeds import (
    DreamSeed,
    DreamSeedType,
)
from .scenes import (
    DreamScene,
    DreamTrace,
)

__all__ = [
    "DreamScheduler",
    "DreamPhase",
    "DreamReport",
    "DreamResidue",
    "NREMReport",
    "REMReport",
    "DreamSeed",
    "DreamSeedType",
    "DreamScene",
    "DreamTrace",
]