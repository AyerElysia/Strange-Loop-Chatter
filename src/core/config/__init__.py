"""Core 层配置模块

本模块定义 Core 层所需的配置项，使用 kernel/config 的配置系统。

使用示例：
    ```python
    from src.core.config import init_core_config, get_core_config

    # 初始化配置（在使用前必须调用一次）
    init_core_config("config/core.toml")

    # 获取配置实例
    config = get_core_config()
    print(config.database.database_type)
    ```
"""

from .core_config import (
    CoreConfig,
    get_core_config,
    init_core_config,
)

__all__ = [
    "CoreConfig",
    "get_core_config",
    "init_core_config",
]
