"""Booku Memory 事件处理器。

订阅 ``on_prompt_build`` 事件，在 DefaultChatter 构建
``default_chatter_system_prompt`` 模板时，将记忆引导语追加到
``extra_info`` 占位符中，使主对话模型感知自身已拥有长期记忆能力。

是否注入可通过配置项 ``plugin.inject_system_prompt`` 控制。
"""

from __future__ import annotations

from typing import Any

from src.app.plugin_system.api.log_api import get_logger
from src.core.components.base import BaseEventHandler
from src.kernel.event import EventDecision

logger = get_logger("booku_memory_event_handler")

# 目标模板：仅对 default_chatter 系统提示注入
_TARGET_PROMPT = "default_chatter_system_prompt"

# 注入到 extra_info 板块的记忆引导语
_MEMORY_HINT = (
    "## 记忆引导语\n"
    "你已经被接入记忆功能，拥有长期记忆。"
    "你的重要能力在于能够记住用户的点点滴滴，并在未来的对话中体现出来。"
    "但是无论如何你都必须实事求是，不记得的你也不能编造\n\n"
    "回复前思考流程：\n"
    "用户这句话是否包含新的重要信息？ -> 是 -> 调用 write_memory。\n"
    "回答这个问题是否需要历史背景？ -> 是 -> 调用 read_memory。\n"
    "重要：创建或检索记忆时，不要使用“用户”、“朋友”等模糊词，记忆中必须具体明确的实体或描述。但是对话中不受此限制。\n"
    "确认记忆内容后，再生成自然流畅的回复。\n\n"
    "请始终保持对记忆的敏感度，及时记录新的有价值信息，并善用记忆检索。"
)


class MemoryPromptInjector(BaseEventHandler):
    """记忆提示注入器。

    订阅 ``on_prompt_build`` 事件，当 ``default_chatter_system_prompt``
    模板即将构建时，将记忆引导语追加到 ``extra_info`` 占位符中。
    已有的 ``extra_info`` 内容会被保留，注入内容追加在其后。

    可通过配置项 ``plugin.inject_system_prompt``（默认 True）在运行时关闭注入。

    Examples:
        配置关闭注入（config/plugins/booku_memory.toml）：

        .. code-block:: toml

            [plugin]
            inject_system_prompt = false
    """

    handler_name: str = "memory_prompt_injector"
    handler_description: str = "在 default_chatter 系统提示 extra_info 板块注入记忆引导语"
    weight: int = 10
    intercept_message: bool = False
    init_subscribe: list[str] = ["on_prompt_build"]

    async def execute(
        self, event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        """处理 on_prompt_build 事件，按需注入记忆引导语。

        仅处理名为 ``default_chatter_system_prompt`` 的模板，其他模板直接透传。
        若配置中 ``plugin.inject_system_prompt`` 为 False，则跳过注入。
        注入后 ``params["values"]["extra_info"]`` 会包含引导语文本。

        Args:
            event_name: 触发本处理器的事件名称（``on_prompt_build``）。
            params: prompt build 事件参数，包含以下字段：
                - ``name``：模板名称
                - ``template``：模板字符串
                - ``values``：当前渲染值 dict（本方法可修改 ``extra_info``）
                - ``policies``：渲染策略 dict
                - ``strict``：是否严格模式

        Returns:
            tuple[EventDecision, dict[str, Any]]:
                始终返回 ``(EventDecision.SUCCESS, params)``，不阻断后续处理器。
                修改仅写入 ``params["values"]["extra_info"]``，不影响其他字段。
        """
        # 仅处理目标模板，其余模板直接透传
        if params.get("name") != _TARGET_PROMPT:
            return EventDecision.SUCCESS, params

        # 读取配置开关
        config = getattr(self.plugin, "config", None)
        if config is not None:
            from .config import BookuMemoryConfig

            if isinstance(config, BookuMemoryConfig) and not config.plugin.inject_system_prompt:
                logger.debug("inject_system_prompt=False，跳过记忆引导语注入")
                return EventDecision.SUCCESS, params

        values: dict[str, Any] = params.get("values", {})
        existing: str = values.get("extra_info", "") or ""
        separator = "\n\n" if existing else ""
        values["extra_info"] = existing + separator + _MEMORY_HINT

        logger.debug("已向 default_chatter_system_prompt.extra_info 注入记忆引导语")
        return EventDecision.SUCCESS, params
