"""DFC 向生命中枢同步查询信息的工具。

与 MessageNucleusTool（异步留言）不同，ConsultNucleusTool 提供同步查询：
DFC 在回复用户前可以向 Life Engine 查询记忆、日记、TODO、内心状态等信息，
立即获得结果并融入当前对话。
"""

from __future__ import annotations

from typing import Annotated

from src.core.components.base.tool import BaseTool
from src.core.managers import get_plugin_manager
from src.kernel.logger import Logger, get_logger

logger: Logger = get_logger("consult_nucleus")


class ConsultNucleusTool(BaseTool):
    """向生命中枢查询信息，立即返回结果。"""

    tool_name = "consult_nucleus"
    tool_description = (
        "向你的生命中枢/内心查询信息，并立即获得回答。"
        "可以查询：最近在想什么、相关的记忆、日记里写了什么、"
        "TODO进度如何、当前的内心状态和情绪等。"
        "与 message_nucleus（异步留言）不同，这个工具会同步返回结果。"
        "适合在回复用户前先翻一翻自己的记忆和日记。"
    )
    chatter_allow: list[str] = ["default_chatter"]

    async def execute(
        self,
        query: Annotated[
            str,
            "你想问中枢的问题，比如'我最近有没有关于XX的记忆'、"
            "'我的日记里有关于XX的内容吗'、'我的TODO列表里有什么'",
        ],
    ) -> tuple[bool, str]:
        query = str(query or "").strip()
        if not query:
            return False, "query 不能为空"

        life_plugin = get_plugin_manager().get_plugin("life_engine")
        if life_plugin is None:
            return False, "life_engine 未加载，无法查询中枢"

        service = getattr(life_plugin, "service", None)
        if service is None:
            return False, "life_engine 服务不可用"

        try:
            result = await _query_life_engine(service, query)
            if not result:
                return True, "暂时没有找到相关信息"
            return True, result
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"查询生命中枢失败: {exc}")
            return False, f"查询失败: {exc}"


async def _query_life_engine(service: object, query: str) -> str:
    """从 Life Engine 的多个数据源聚合查询结果。

    不调用 LLM，纯本地检索，确保低延迟。
    """
    results: list[str] = []

    # 1. 记忆检索
    try:
        memory_service = getattr(service, "_memory_service", None)
        if memory_service is not None and hasattr(memory_service, "search"):
            memories = await memory_service.search(query, top_k=3)
            if memories:
                mem_lines = []
                for m in memories[:3]:
                    title = getattr(m, "title", "") or ""
                    snippet = getattr(m, "snippet", "") or getattr(m, "content", "")
                    if snippet:
                        snippet = str(snippet)[:150]
                    if title or snippet:
                        mem_lines.append(f"- {title}: {snippet}" if title else f"- {snippet}")
                if mem_lines:
                    results.append("【记忆检索】\n" + "\n".join(mem_lines))
    except Exception:
        pass

    # 2. 最近日记
    try:
        from pathlib import Path
        cfg = getattr(service, "_cfg", lambda: None)()
        if cfg is not None:
            workspace = Path(cfg.settings.workspace_path)
            diary_dir = workspace / "diary"
            if diary_dir.exists():
                diary_files = sorted(diary_dir.glob("*.md"), reverse=True)[:3]
                for f in diary_files:
                    content = f.read_text(encoding="utf-8")[:300]
                    # 检查是否与查询相关（简单包含匹配）
                    query_keywords = [w for w in query if len(w) > 1]
                    if any(kw in content for kw in query.split() if len(kw) > 1):
                        results.append(f"【日记 {f.stem}】\n{content[:200]}")
                        break
    except Exception:
        pass

    # 3. TODO 状态
    try:
        from plugins.life_engine.todo_tools import TodoStorage, TodoStatus
        from pathlib import Path
        cfg = getattr(service, "_cfg", lambda: None)()
        if cfg is not None:
            workspace = Path(cfg.settings.workspace_path)
            storage = TodoStorage(workspace)
            all_todos = storage.load()
            inactive = {
                TodoStatus.COMPLETED.value,
                TodoStatus.RELEASED.value,
                TodoStatus.CHERISHED.value,
            }
            active = [t for t in all_todos if t.status not in inactive]
            if active:
                todo_lines = []
                for t in active[:5]:
                    emoji = {
                        "idea": "💡", "planning": "📝", "waiting": "⏳",
                        "enjoying": "🎵", "paused": "⏸️",
                    }.get(t.status, "·")
                    todo_lines.append(f"{emoji} {t.title} ({t.status})")
                results.append("【活跃TODO】\n" + "\n".join(todo_lines))
    except Exception:
        pass

    # 4. 当前内心状态
    try:
        state = getattr(service, "_state", None)
        snn = getattr(service, "_snn_network", None)
        inner = getattr(service, "_inner_state", None)

        state_parts = []
        if snn is not None and hasattr(snn, "get_drive_discrete"):
            drives = snn.get_drive_discrete()
            label_map = {
                "arousal": "唤醒", "valence": "情绪效价",
                "social_drive": "社交欲", "task_drive": "做事欲",
                "exploration_drive": "探索欲", "rest_drive": "休息欲",
            }
            drive_text = "、".join(f"{label_map.get(k,k)}={v}" for k, v in drives.items())
            state_parts.append(f"SNN驱动: {drive_text}")

        if inner is not None and hasattr(inner, "format_for_prompt"):
            state_parts.append(inner.format_for_prompt())

        if state is not None:
            last_reply = getattr(state, "last_model_reply", "")
            if last_reply:
                state_parts.append(f"最近独白: {str(last_reply)[:150]}")

        if state_parts:
            results.append("【当前状态】\n" + "\n".join(state_parts))
    except Exception:
        pass

    return "\n\n".join(results)
