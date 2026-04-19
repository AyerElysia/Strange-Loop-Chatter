"""life_engine 工具集。"""

from __future__ import annotations

from .file_tools import (
    ALL_TOOLS as FILE_TOOLS,
    LifeEngineReadFileTool,
    LifeEngineWriteFileTool,
    LifeEngineEditFileTool,
    LifeEngineMoveFileTool,
    LifeEngineDeleteFileTool,
    LifeEngineListFilesTool,
    LifeEngineFileInfoTool,
    LifeEngineMakeDirectoryTool,
    LifeEngineWakeDFCTool,
    LifeEngineRunAgentTool,
    FetchLifeMemoryTool,
)
from .chat_history_tools import (
    CHAT_HISTORY_TOOLS,
    LifeEngineFetchChatHistoryTool,
)
from .todo_tools import TODO_TOOLS
from .grep_tools import GREP_TOOLS
from .web_tools import WEB_TOOLS

ALL_TOOLS = [
    *FILE_TOOLS,
    *CHAT_HISTORY_TOOLS,
]

__all__ = [
    "ALL_TOOLS",
    "TODO_TOOLS",
    "GREP_TOOLS",
    "WEB_TOOLS",
    "LifeEngineFetchChatHistoryTool",
    "LifeEngineReadFileTool",
    "LifeEngineWriteFileTool",
    "LifeEngineEditFileTool",
    "LifeEngineMoveFileTool",
    "LifeEngineDeleteFileTool",
    "LifeEngineListFilesTool",
    "LifeEngineFileInfoTool",
    "LifeEngineMakeDirectoryTool",
    "LifeEngineWakeDFCTool",
    "LifeEngineRunAgentTool",
    "FetchLifeMemoryTool",
]
