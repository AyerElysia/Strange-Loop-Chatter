"""DFC 向生命中枢同步查询信息的工具。"""

from __future__ import annotations

from typing import Annotated, Any

from src.core.components.base.tool import BaseTool
from src.core.managers import get_plugin_manager
from src.kernel.logger import Logger, get_logger

logger: Logger = get_logger("consult_nucleus")


def _get_life_service() -> Any:
    """获取 life_engine service。"""
    life_plugin = get_plugin_manager().get_plugin("life_engine")
    if life_plugin is None:
        raise RuntimeError("life_engine 未加载，无法查询中枢")

    service = getattr(life_plugin, "service", None)
    if service is None:
        raise RuntimeError("life_engine 服务不可用")
    return service


class ConsultNucleusTool(BaseTool):
    """同步查询当前状态层信息。"""

    tool_name = "consult_nucleus"
    tool_description = (
        "向生命中枢同步查询当前状态层信息。"
        "适合查询：最近在想什么、当前内在状态、活跃 TODO、最近日记等。"
        "这个工具不做深层记忆检索；如果需要翻过去的记忆文件，请改用 search_life_memory。"
    )
    chatter_allow: list[str] = ["default_chatter"]

    async def execute(
        self,
        query: Annotated[
            str,
            "想问中枢的状态问题，例如“最近在想什么”“现在有什么 TODO”“最近写了什么日记”",
        ],
    ) -> tuple[bool, str]:
        query_text = str(query or "").strip()
        if not query_text:
            return False, "query 不能为空"

        try:
            service = _get_life_service()
            result = await service.query_actor_context(query_text)
            if not result:
                return True, "暂时没有找到相关状态信息"
            return True, result
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"查询生命中枢状态失败: {exc}")
            return False, f"查询失败: {exc}"


class SearchLifeMemoryTool(BaseTool):
    """同步检索 life_engine 深层记忆。"""

    tool_name = "search_life_memory"
    tool_description = (
        "深度检索生命中枢的记忆文件与联想结果。"
        "适合查询过去聊过的事、旧计划、历史文件记录、被记住的线索。"
        "这个工具会做真正的记忆检索，不是当前状态摘要。"
    )
    chatter_allow: list[str] = ["default_chatter"]

    async def execute(
        self,
        query: Annotated[str, "要检索的记忆主题或关键词"],
        top_k: Annotated[int, "最多返回多少条主结果，默认 5"] = 5,
    ) -> tuple[bool, str]:
        query_text = str(query or "").strip()
        if not query_text:
            return False, "query 不能为空"

        resolved_top_k = max(1, min(int(top_k), 10))
        try:
            service = _get_life_service()
            result = await service.search_actor_memory(query_text, top_k=resolved_top_k)
            if not result:
                return True, "暂时没有检索到相关记忆"
            return True, result
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"检索 life memory 失败: {exc}")
            return False, f"检索失败: {exc}"
