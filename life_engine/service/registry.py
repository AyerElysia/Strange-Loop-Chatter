"""服务注册表，替代全局变量。

本模块提供线程安全的服务注册表，用于管理 life_engine 服务单例。
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Generic, TypeVar

if TYPE_CHECKING:
    from .core import LifeEngineService

T = TypeVar("T")


class ServiceRegistry(Generic[T]):
    """线程安全的服务注册表。

    使用双重检查锁定模式确保线程安全的单例访问。
    """

    def __init__(self) -> None:
        """初始化注册表。"""
        self._instance: T | None = None
        self._lock = threading.Lock()

    def register(self, instance: T) -> None:
        """注册服务实例。

        Args:
            instance: 要注册的服务实例

        Raises:
            RuntimeError: 如果服务已经注册
        """
        with self._lock:
            if self._instance is not None:
                raise RuntimeError(f"Service already registered: {type(instance)}")
            self._instance = instance

    def unregister(self) -> None:
        """注销服务实例。"""
        with self._lock:
            self._instance = None

    def get(self) -> T | None:
        """获取服务实例。

        Returns:
            服务实例，如果未注册则返回 None
        """
        with self._lock:
            return self._instance


# 全局注册表实例
_life_engine_registry: ServiceRegistry[LifeEngineService] = ServiceRegistry()


def get_life_engine_service() -> LifeEngineService | None:
    """获取 LifeEngineService 单例。

    Returns:
        LifeEngineService 实例，如果未注册则返回 None
    """
    return _life_engine_registry.get()


def register_life_engine_service(service: LifeEngineService) -> None:
    """注册 LifeEngineService 单例。

    Args:
        service: LifeEngineService 实例

    Raises:
        RuntimeError: 如果服务已经注册
    """
    _life_engine_registry.register(service)


def unregister_life_engine_service() -> None:
    """注销 LifeEngineService 单例。"""
    _life_engine_registry.unregister()
