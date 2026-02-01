"""数据库核心层 (core)

包含引擎、会话与异常定义。
"""

from src.kernel.db.core.engine import (
    close_engine,
    get_engine,
    get_engine_info,
)
from src.kernel.db.core.exceptions import (
    DatabaseConnectionError,
    DatabaseError,
    DatabaseInitializationError,
    DatabaseQueryError,
    DatabaseTransactionError,
)
from src.kernel.db.core.session import (
    get_db_session,
    get_session_factory,
    reset_session_factory,
)

# TODO: Base 模型需要从 core/models 导入
# 目前暂时定义为 Any，待 models.py 完成后再导入
from typing import Any

Base = Any

__all__ = [
    # 引擎
    "get_engine",
    "close_engine",
    "get_engine_info",
    # 会话
    "get_session_factory",
    "get_db_session",
    "reset_session_factory",
    # 异常
    "DatabaseError",
    "DatabaseInitializationError",
    "DatabaseConnectionError",
    "DatabaseQueryError",
    "DatabaseTransactionError",
    # 模型基类
    "Base",
]
