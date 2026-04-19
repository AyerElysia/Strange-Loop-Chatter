"""ThoughtStream 工具集。

为中枢提供持久兴趣线索管理能力：
- 创建思考流
- 列出思考流
- 推进思考流
- 结束/休眠思考流
"""

from __future__ import annotations

from typing import Annotated, Any

from src.core.components import BaseTool
from src.app.plugin_system.api import log_api

from .manager import ThoughtStreamManager

logger = log_api.get_logger("life_engine.stream_tools")


def _get_manager() -> ThoughtStreamManager | None:
    """获取 ThoughtStreamManager 实例。"""
    from ..service.registry import get_life_engine_service

    service = get_life_engine_service()
    if service is None or service._thought_manager is None:
        return None
    return service._thought_manager


# ============================================================
# nucleus_create_thought_stream - 创建思考流
# ============================================================


class LifeEngineCreateThoughtStreamTool(BaseTool):
    """创建思考流工具。"""

    tool_name: str = "nucleus_create_thought_stream"
    tool_description: str = (
        "创建一条新的持久思考流——一个你持续在意的兴趣或问题。"
        "这不是待办事项，而是'我最近一直在琢磨这件事'。"
        "当你遇到有趣的话题、未解答的疑问、或反复出现的想法时使用。"
    )
    chatter_allow: list[str] = ["life_engine_internal"]

    def __init__(self, plugin) -> None:
        super().__init__(plugin)

    async def execute(
        self,
        title: Annotated[str, "思考流标题，简短描述你在意的事情"],
        reason: Annotated[str, "为什么这件事引起了你的兴趣（可选）"] = "",
    ) -> tuple[bool, str]:
        """创建新的思考流。"""
        if not title or not title.strip():
            return False, "title 不能为空"

        manager = _get_manager()
        if manager is None:
            return False, "思考流服务未初始化"

        try:
            ts = manager.create(title=title.strip(), reason=reason.strip())
            return True, (
                f"已创建思考流「{ts.title}」({ts.id})，"
                f"当前活跃思考流: {len(manager.list_active())}"
            )
        except Exception as e:
            logger.error(f"创建思考流失败: {e}", exc_info=True)
            return False, f"创建思考流失败: {e}"


# ============================================================
# nucleus_list_thought_streams - 列出思考流
# ============================================================


class LifeEngineListThoughtStreamsTool(BaseTool):
    """列出思考流工具。"""

    tool_name: str = "nucleus_list_thought_streams"
    tool_description: str = (
        "列出你当前活跃的思考流——你持续在意的兴趣。"
        "用于选择接下来想深入哪条线索。"
    )
    chatter_allow: list[str] = ["life_engine_internal"]

    def __init__(self, plugin) -> None:
        super().__init__(plugin)

    async def execute(
        self,
        include_dormant: Annotated[bool, "是否包含休眠中的思考流"] = False,
    ) -> tuple[bool, str]:
        """列出思考流。"""
        manager = _get_manager()
        if manager is None:
            return False, "思考流服务未初始化"

        try:
            if include_dormant:
                streams = manager.list_all()
            else:
                streams = manager.list_active()

            if not streams:
                return True, "当前没有活跃的思考流"

            lines: list[str] = []
            for ts in streams:
                status_tag = f"[{ts.status}]" if ts.status != "active" else ""
                lines.append(
                    f"- {ts.id}: {ts.title} {status_tag}"
                    f" (好奇心: {ts.curiosity_score:.0%}, 推进: {ts.advance_count}次)"
                )
                if ts.last_thought:
                    lines.append(f"  最近想法: {ts.last_thought[:150]}")

            return True, "\n".join(lines)
        except Exception as e:
            logger.error(f"列出思考流失败: {e}", exc_info=True)
            return False, f"列出思考流失败: {e}"


# ============================================================
# nucleus_advance_thought_stream - 推进思考流
# ============================================================


class LifeEngineAdvanceThoughtStreamTool(BaseTool):
    """推进思考流工具。"""

    tool_name: str = "nucleus_advance_thought_stream"
    tool_description: str = (
        "推进一条思考流——记录你对该话题的最新想法。"
        "这是内心独白的核心：围绕你在意的事情深入思考，而不是漫无目的地等待外界刺激。"
    )
    chatter_allow: list[str] = ["life_engine_internal"]

    def __init__(self, plugin) -> None:
        super().__init__(plugin)

    async def execute(
        self,
        stream_id: Annotated[str, "要推进的思考流ID"],
        thought: Annotated[str, "你对该话题的最新想法"],
        curiosity_delta: Annotated[float, "好奇心变化量，正值=更感兴趣，负值=兴趣减退"] = 0.0,
    ) -> tuple[bool, str]:
        """推进一条思考流。"""
        if not stream_id or not stream_id.strip():
            return False, "stream_id 不能为空"
        if not thought or not thought.strip():
            return False, "thought 不能为空"

        manager = _get_manager()
        if manager is None:
            return False, "思考流服务未初始化"

        try:
            success, msg = manager.advance(
                stream_id=stream_id.strip(),
                thought=thought.strip(),
                curiosity_delta=curiosity_delta,
            )
            return success, msg
        except Exception as e:
            logger.error(f"推进思考流失败: {e}", exc_info=True)
            return False, f"推进思考流失败: {e}"


# ============================================================
# nucleus_retire_thought_stream - 结束/休眠思考流
# ============================================================


class LifeEngineRetireThoughtStreamTool(BaseTool):
    """结束或休眠思考流工具。"""

    tool_name: str = "nucleus_retire_thought_stream"
    tool_description: str = (
        "结束或休眠一条思考流。"
        "当你对某个话题有了结论、或者暂时不再感兴趣时使用。"
    )
    chatter_allow: list[str] = ["life_engine_internal"]

    def __init__(self, plugin) -> None:
        super().__init__(plugin)

    async def execute(
        self,
        stream_id: Annotated[str, "要结束的思考流ID"],
        new_status: Annotated[str, "新状态: completed(已得出结论) 或 dormant(暂时搁置)"] = "completed",
        conclusion: Annotated[str, "对该话题的最终结论或搁置原因（可选）"] = "",
    ) -> tuple[bool, str]:
        """结束或休眠一条思考流。"""
        if not stream_id or not stream_id.strip():
            return False, "stream_id 不能为空"

        if new_status not in ("completed", "dormant"):
            return False, "new_status 必须是 'completed' 或 'dormant'"

        manager = _get_manager()
        if manager is None:
            return False, "思考流服务未初始化"

        try:
            success, msg = manager.retire(
                stream_id=stream_id.strip(),
                new_status=new_status,
                conclusion=conclusion.strip() if conclusion else "",
            )
            return success, msg
        except Exception as e:
            logger.error(f"结束思考流失败: {e}", exc_info=True)
            return False, f"结束思考流失败: {e}"


# ============================================================
# 工具注册列表
# ============================================================

STREAM_TOOLS = [
    LifeEngineCreateThoughtStreamTool,
    LifeEngineListThoughtStreamsTool,
    LifeEngineAdvanceThoughtStreamTool,
    LifeEngineRetireThoughtStreamTool,
]
