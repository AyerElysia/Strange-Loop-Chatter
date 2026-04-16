"""life_engine 专属文件日志。"""

from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import Lock
from typing import Any

LOG_DIR = Path("logs/life_engine")
LOG_FILE = LOG_DIR / "life.log"
_LOGGER_NAME = "life_engine.audit"
_LOCK = Lock()
_HANDLER: RotatingFileHandler | None = None


def get_life_log_dir() -> Path:
    """获取 life_engine 日志目录。"""
    return LOG_DIR


def get_life_log_file() -> Path:
    """获取 life_engine 日志文件路径。"""
    return LOG_FILE


def setup_life_audit_logger() -> Path:
    """初始化 life_engine 的文件日志处理器。"""
    global _HANDLER

    with _LOCK:
        LOG_DIR.mkdir(parents=True, exist_ok=True)

        if _HANDLER is not None:
            return LOG_FILE

        logger = logging.getLogger(_LOGGER_NAME)
        logger.setLevel(logging.INFO)
        logger.propagate = False

        handler = RotatingFileHandler(
            LOG_FILE,
            maxBytes=10 * 1024 * 1024,
            backupCount=10,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
        )

        for existing in list(logger.handlers):
            logger.removeHandler(existing)
            try:
                existing.close()
            except Exception:
                pass

        logger.addHandler(handler)
        _HANDLER = handler
        return LOG_FILE


def teardown_life_audit_logger() -> None:
    """释放 life_engine 文件日志处理器。"""
    global _HANDLER

    with _LOCK:
        logger = logging.getLogger(_LOGGER_NAME)
        if _HANDLER is not None:
            try:
                logger.removeHandler(_HANDLER)
            except Exception:
                pass
            try:
                _HANDLER.close()
            finally:
                _HANDLER = None


def _emit(payload: dict[str, Any], *, level: str = "info") -> None:
    """写入一条结构化日志。"""
    setup_life_audit_logger()
    logger = logging.getLogger(_LOGGER_NAME)
    line = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    getattr(logger, level.lower(), logger.info)(line)


def log_lifecycle(event: str, **fields: Any) -> None:
    """记录生命周期事件。"""
    payload = {
        "component": "life_engine",
        "event": event,
        "kind": "lifecycle",
        **fields,
    }
    _emit(payload)


def log_message_received(**fields: Any) -> None:
    """记录收到的聊天消息。"""
    payload = {
        "component": "life_engine",
        "event": "message_received",
        "kind": "message",
        **fields,
    }
    _emit(payload)


def log_wake_context_injected(**fields: Any) -> None:
    """记录一次唤醒上下文注入。"""
    payload = {
        "component": "life_engine",
        "event": "wake_context_injected",
        "kind": "context",
        **fields,
    }
    _emit(payload)


def log_heartbeat(**fields: Any) -> None:
    """记录一次心跳。"""
    payload = {
        "component": "life_engine",
        "event": "heartbeat",
        "kind": "heartbeat",
        **fields,
    }
    _emit(payload)


def log_heartbeat_model_response(**fields: Any) -> None:
    """记录一次心跳模型回复。"""
    payload = {
        "component": "life_engine",
        "event": "heartbeat_model_response",
        "kind": "heartbeat_model",
        **fields,
    }
    _emit(payload)


def log_error(event: str, error: str, **fields: Any) -> None:
    """记录异常。"""
    payload = {
        "component": "life_engine",
        "event": event,
        "kind": "error",
        "error": error,
        **fields,
    }
    _emit(payload, level="error")


def log_snn_tick(**fields: Any) -> None:
    """记录一次 SNN tick 更新。"""
    payload = {
        "component": "life_engine",
        "event": "snn_tick",
        "kind": "snn",
        **fields,
    }
    _emit(payload)


def log_snn_snapshot(**fields: Any) -> None:
    """记录 SNN 状态快照（定期或关键时刻）。"""
    payload = {
        "component": "life_engine",
        "event": "snn_snapshot",
        "kind": "snn",
        **fields,
    }
    _emit(payload)
