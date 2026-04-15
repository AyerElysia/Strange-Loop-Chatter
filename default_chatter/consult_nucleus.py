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
        "这个工具不做深层记忆检索，也不要拿它反复追问同一个历史主题；"
        "如果需要翻过去的记忆文件或旧计划，请改用 search_life_memory。"
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
        "这个工具会做真正的记忆检索，不是当前状态摘要；"
        "同一主题如果已经搜过一次且没有新线索，就不要继续换词重搜。"
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

            # 记录工具调用和返回结果，方便调试
            logger.info(
                f"[search_life_memory] DFC 调用记忆检索工具:\n"
                f"  query: {query_text}\n"
                f"  top_k: {resolved_top_k}\n"
                f"  返回结果长度: {len(result) if result else 0} 字符\n"
                f"  返回内容:\n{result if result else '(空结果)'}"
            )

            if not result:
                return True, "暂时没有检索到相关记忆"
            return True, result
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"检索 life memory 失败: {exc}")
            return False, f"检索失败: {exc}"


class IntelligentMemoryRetrievalTool(BaseTool):
    """智能记忆检索工具（内部使用 sub agent）。"""

    tool_name = "retrieve_memory"
    tool_description = (
        "智能检索生命中枢的记忆，自动决定检索策略和详细程度。"
        "\n\n"
        "**何时使用：**\n"
        "- ✓ 需要回忆过去的对话、事件、想法\n"
        "- ✓ 用户询问「你还记得...」「之前我们聊过...」\n"
        "- ✓ 需要查找相关的历史信息\n"
        "\n"
        "**优势：**\n"
        "- 自动决定是否需要完整内容（你不需要再调用 fetch_life_memory）\n"
        "- 智能整合多个记忆源\n"
        "- 返回最相关和有用的信息\n"
        "- 一次调用完成，减少延迟\n"
        "\n"
        "**参数说明：**\n"
        "- query: 要检索的主题或关键词\n"
        "- detail_level: 详细程度（brief/normal/detailed/auto）\n"
        "  - brief: 只返回摘要\n"
        "  - normal: 返回中等详细程度（推荐）\n"
        "  - detailed: 返回完整内容\n"
        "  - auto: 让 agent 自动判断\n"
        "- max_results: 最多返回几条记忆（默认 3）"
    )
    chatter_allow: list[str] = ["default_chatter"]

    async def execute(
        self,
        query: Annotated[str, "要检索的记忆主题或关键词"],
        detail_level: Annotated[str, "详细程度：brief/normal/detailed/auto"] = "normal",
        max_results: Annotated[int, "最多返回几条记忆"] = 3,
    ) -> tuple[bool, str]:
        """启动 sub agent 进行智能记忆检索。"""
        query_text = str(query or "").strip()
        if not query_text:
            return False, "query 不能为空"

        detail_level = str(detail_level or "normal").strip().lower()
        if detail_level not in {"brief", "normal", "detailed", "auto"}:
            detail_level = "normal"

        max_results = max(1, min(int(max_results), 5))

        try:
            # 导入必要的 API
            from src.app.plugin_system.api.llm_api import create_llm_request, get_model_set_by_task
            from src.kernel.llm import LLMPayload, ROLE, Text, ToolRegistry, ToolResult

            # 获取 life_engine 插件
            life_plugin = get_plugin_manager().get_plugin("life_engine")
            if not life_plugin:
                return False, "life_engine 未加载"

            # 创建工具注册表，只包含需要的工具
            registry = ToolRegistry()
            search_tool_cls = None
            fetch_tool_cls = None

            for tool in life_plugin.tools:
                if tool.tool_name == "search_life_memory":
                    search_tool_cls = tool.__class__
                    registry.register(search_tool_cls)
                elif tool.tool_name == "fetch_life_memory":
                    fetch_tool_cls = tool.__class__
                    registry.register(fetch_tool_cls)

            if not search_tool_cls:
                return False, "search_life_memory 工具未找到"

            # 构建 agent prompt
            agent_prompt = f"""你是一个记忆检索专家。你的任务是帮助主人格（DFC）检索相关记忆。

**检索需求**：
- 主题/关键词：{query_text}
- 详细程度：{detail_level}
- 最多返回：{max_results} 条

**可用工具**：
- search_life_memory: 检索记忆摘要
- fetch_life_memory: 获取完整文件内容

**你的任务**：
1. 使用 search_life_memory 检索相关记忆
2. 根据 detail_level 决定是否需要调用 fetch_life_memory：
   - brief: 只返回 search 的摘要
   - normal: 对最相关的 1-2 条记忆调用 fetch（限制 500 字符）
   - detailed: 对所有相关记忆调用 fetch（限制 1000 字符）
   - auto: 根据摘要质量自动判断
3. 整合结果，返回清晰的摘要

**返回格式**：
【记忆1】标题
相关度：0.XX
内容：...

【记忆2】标题
相关度：0.XX
内容：...

**注意**：
- 如果没有找到相关记忆，明确说明
- 控制返回长度，避免过长
- 优先返回最相关的记忆
- 直接返回整合后的结果，不要说"我已经检索完成"之类的元信息
"""

            # 创建 LLM 请求
            model_set = get_model_set_by_task("sub_actor")
            request = create_llm_request(
                model_set=model_set,
                request_name="memory_retrieval_agent",
            )

            # 添加 payload
            request.add_payload(LLMPayload(ROLE.SYSTEM, Text(agent_prompt)))
            request.add_payload(LLMPayload(ROLE.TOOL, list(registry.get_all())))
            request.add_payload(LLMPayload(ROLE.USER, Text(f"请检索关于「{query_text}」的记忆")))

            # 执行多轮工具调用
            max_rounds = 5
            response = await request.send(stream=False)
            final_result = ""

            for round_num in range(max_rounds):
                response_text = await response
                call_list = list(getattr(response, "call_list", []) or [])

                if not call_list:
                    # 没有工具调用，返回最终结果
                    final_result = response_text
                    break

                # 执行工具调用
                for call in call_list:
                    tool_name = getattr(call, "name", "")
                    args = dict(getattr(call, "args", {}) or {})

                    usable_cls = registry.get(tool_name)
                    if usable_cls:
                        tool_instance = usable_cls(plugin=life_plugin)
                        success, result = await tool_instance.execute(**args)
                        result_text = str(result) if success else f"失败: {result}"
                    else:
                        result_text = f"未知工具: {tool_name}"

                    # 返回结果给 LLM
                    call_id = getattr(call, "id", None)
                    response.add_payload(
                        LLMPayload(
                            ROLE.TOOL_RESULT,
                            ToolResult(value=result_text, call_id=call_id, name=tool_name),
                        )
                    )

                response = await response.send(stream=False)
            else:
                final_result = f"Agent 在 {max_rounds} 轮内未完成检索，部分结果：\n{response_text}"

            # 记录工具调用，方便调试
            logger.info(
                f"[retrieve_memory] DFC 调用智能记忆检索工具:\n"
                f"  query: {query_text}\n"
                f"  detail_level: {detail_level}\n"
                f"  max_results: {max_results}\n"
                f"  执行轮数: {round_num + 1}\n"
                f"  返回结果长度: {len(final_result)} 字符"
            )

            return True, final_result

        except Exception as exc:  # noqa: BLE001
            logger.error(f"智能记忆检索失败: {exc}", exc_info=True)
            return False, f"检索失败: {exc}"
