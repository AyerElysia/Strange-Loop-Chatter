"""Life Engine 记忆系统工具集。

为中枢提供仿生记忆能力：
- 语义检索 + 联想
- 建立文件关联
- 查看关联图谱
- 主动遗忘
"""

from __future__ import annotations

from typing import Annotated, Any, List, Optional

from src.core.components import BaseTool
from src.app.plugin_system.api import log_api

from .memory_service import EdgeType, LifeMemoryService, SearchResult

logger = log_api.get_logger("life_engine.memory_tools")


# ============================================================
# nucleus_search_memory - 语义检索 + 联想
# ============================================================

class LifeEngineSearchMemoryTool(BaseTool):
    """语义检索 + 联想工具。"""

    tool_name: str = "nucleus_search_memory"
    tool_description: str = """搜索记忆并触发联想。

结合关键词搜索和语义检索，找到相关的记忆。
如果启用联想，会自动沿着关联路径找到更多相关记忆。

参数：
- query: 搜索问题（必填）
- top_k: 返回数量，默认 5
- enable_association: 是否启用联想，默认 True
- file_types: 限定文件类型列表（可选），如 ["diary", "note", "todo"]
- time_range_days: 时间范围，0 表示不限（可选）

返回：
- 直接命中的记忆（source="direct"）
- 联想到的记忆（source="associated"），包含联想路径和原因

示例：
```
nucleus_search_memory(query="我之前想学什么乐器")
→ 返回日记中提到吉他的段落，以及联想到的学习规划笔记
```

注意：
- 搜索会自动增加命中记忆的激活强度
- 经常一起被检索到的记忆，它们之间的关联会自动增强
- 长期不访问的记忆会逐渐被遗忘（激活强度降低）
"""

    async def _get_service(self) -> LifeMemoryService:
        """获取记忆服务实例。"""
        from .service import LifeEngineService
        
        service = LifeEngineService.get_instance()
        if service is None or service._memory_service is None:
            raise RuntimeError("记忆服务未初始化")
        return service._memory_service

    async def execute(
        self,
        query: Annotated[str, "搜索问题"],
        top_k: Annotated[int, "返回数量"] = 5,
        enable_association: Annotated[bool, "是否启用联想"] = True,
        file_types: Annotated[Optional[List[str]], "限定文件类型"] = None,
        time_range_days: Annotated[int, "时间范围（天），0=不限"] = 0,
    ) -> tuple[bool, dict[str, Any]]:
        """执行记忆搜索。"""
        if not query or not query.strip():
            return False, {"error": "query 不能为空"}

        try:
            service = await self._get_service()
            results = await service.search_memory(
                query=query.strip(),
                top_k=top_k,
                enable_association=enable_association,
                file_types=file_types,
                time_range_days=time_range_days
            )

            # 格式化结果
            direct_results = []
            associated_results = []

            for r in results:
                item = {
                    "file_path": r.file_path,
                    "title": r.title,
                    "snippet": r.snippet,
                    "relevance": round(r.relevance, 3)
                }

                if r.source == "direct":
                    direct_results.append(item)
                else:
                    item["association_path"] = r.association_path
                    item["association_reason"] = r.association_reason
                    associated_results.append(item)

            return True, {
                "action": "search_memory",
                "query": query,
                "direct_results": direct_results,
                "associated_results": associated_results,
                "total_found": len(results)
            }

        except Exception as e:
            logger.error(f"记忆搜索失败: {e}", exc_info=True)
            return False, {"error": f"搜索失败: {e}"}


# ============================================================
# nucleus_relate_file - 建立文件关联
# ============================================================

class LifeEngineRelateFileTool(BaseTool):
    """建立文件关联工具。"""

    tool_name: str = "nucleus_relate_file"
    tool_description: str = """建立两个文件之间的关联。

当你发现两个文件之间有联系时，使用此工具记录关联关系。
这会帮助未来的联想检索找到相关记忆。

参数：
- source_path: 源文件路径（workspace 相对路径，必填）
- target_path: 目标文件路径（必填）
- relation_type: 关联类型（必填），可选值：
  - "relates": 相关（默认双向）
  - "causes": 因果（A 导致/启发了 B）
  - "continues": 延续（A 是 B 的后续）
  - "contrasts": 对比（A 和 B 观点不同）
- reason: 关联原因（必填！请具体描述为什么关联）
- strength: 关联强度 0.1-1.0，默认 0.5

示例：
```
nucleus_relate_file(
    source_path="diaries/2026-03-30.md",
    target_path="todos/学吉他.md",
    relation_type="causes",
    reason="日记中提到想学吉他的想法，启发了这个 TODO"
)
```

注意：
- reason 必须具体，不要写"可能相关"这种模糊原因
- 关联会随着共同检索而自动增强
- 长期不用的关联会自动衰减
"""

    def __init__(self, plugin: Any):
        super().__init__()
        self.plugin = plugin
        self._memory_service: Optional[LifeMemoryService] = None

    async def _get_service(self) -> LifeMemoryService:
        """获取记忆服务实例。"""
        from .service import LifeEngineService
        
        service = LifeEngineService.get_instance()
        if service is None or service._memory_service is None:
            raise RuntimeError("记忆服务未初始化")
        return service._memory_service

    async def execute(
        self,
        source_path: Annotated[str, "源文件路径"],
        target_path: Annotated[str, "目标文件路径"],
        relation_type: Annotated[str, "关联类型: relates/causes/continues/contrasts"],
        reason: Annotated[str, "关联原因（必填，请具体描述）"],
        strength: Annotated[float, "关联强度 0.1-1.0"] = 0.5,
    ) -> tuple[bool, dict[str, Any]]:
        """建立文件关联。"""
        # 参数验证
        if not source_path or not target_path:
            return False, {"error": "source_path 和 target_path 不能为空"}

        if not reason or len(reason.strip()) < 5:
            return False, {"error": "reason 必填且至少 5 个字符，请具体描述关联原因"}

        # 检查模糊原因
        vague_patterns = ["可能", "也许", "大概", "不确定", "或许"]
        if any(p in reason for p in vague_patterns):
            return False, {"error": f"reason 不够具体，避免使用模糊词汇：{vague_patterns}"}

        # 验证关联类型
        valid_types = ["relates", "causes", "continues", "contrasts"]
        if relation_type not in valid_types:
            return False, {"error": f"relation_type 必须是 {valid_types} 之一"}

        # 验证强度
        strength = max(0.1, min(1.0, strength))

        try:
            service = await self._get_service()

            # 获取或创建节点
            source_node = await service.get_or_create_file_node(source_path)
            target_node = await service.get_or_create_file_node(target_path)

            # 创建边
            edge_type = EdgeType(relation_type)
            edge = await service.create_or_update_edge(
                source_id=source_node.node_id,
                target_id=target_node.node_id,
                edge_type=edge_type,
                reason=reason.strip(),
                strength=strength,
                bidirectional=(relation_type == "relates")
            )

            logger.info(f"建立关联: {source_path} --[{relation_type}]--> {target_path}")

            return True, {
                "action": "relate_file",
                "source_path": source_path,
                "target_path": target_path,
                "relation_type": relation_type,
                "reason": reason,
                "strength": strength,
                "edge_id": edge.edge_id
            }

        except Exception as e:
            logger.error(f"建立关联失败: {e}", exc_info=True)
            return False, {"error": f"建立关联失败: {e}"}


# ============================================================
# nucleus_view_relations - 查看关联图谱
# ============================================================

class LifeEngineViewRelationsTool(BaseTool):
    """查看文件关联图谱工具。"""

    tool_name: str = "nucleus_view_relations"
    tool_description: str = """查看文件的关联图谱。

显示指定文件与其他文件的关联关系。

参数：
- file_path: 文件路径（必填）
- depth: 遍历深度 1-3，默认 1
- min_strength: 最小关联强度阈值，默认 0.2

返回：
- center: 中心文件信息
- outgoing: 从此文件指向其他文件的关联
- incoming: 从其他文件指向此文件的关联

示例：
```
nucleus_view_relations(file_path="diaries/2026-03-30.md")
→ 显示这篇日记与哪些文件有关联
```
"""

    def __init__(self, plugin: Any):
        super().__init__()
        self.plugin = plugin
        self._memory_service: Optional[LifeMemoryService] = None

    async def _get_service(self) -> LifeMemoryService:
        """获取记忆服务实例。"""
        from .service import LifeEngineService
        
        service = LifeEngineService.get_instance()
        if service is None or service._memory_service is None:
            raise RuntimeError("记忆服务未初始化")
        return service._memory_service

    async def execute(
        self,
        file_path: Annotated[str, "文件路径"],
        depth: Annotated[int, "遍历深度 1-3"] = 1,
        min_strength: Annotated[float, "最小关联强度阈值"] = 0.2,
    ) -> tuple[bool, dict[str, Any]]:
        """查看关联图谱。"""
        if not file_path:
            return False, {"error": "file_path 不能为空"}

        depth = max(1, min(3, depth))
        min_strength = max(0.0, min(1.0, min_strength))

        try:
            service = await self._get_service()
            relations = await service.get_file_relations(
                file_path=file_path,
                depth=depth,
                min_strength=min_strength
            )

            if "error" in relations:
                return False, relations

            return True, {
                "action": "view_relations",
                "file_path": file_path,
                **relations
            }

        except Exception as e:
            logger.error(f"查看关联失败: {e}", exc_info=True)
            return False, {"error": f"查看关联失败: {e}"}


# ============================================================
# nucleus_forget_relation - 删除/弱化关联
# ============================================================

class LifeEngineForgetRelationTool(BaseTool):
    """删除或弱化关联工具。"""

    tool_name: str = "nucleus_forget_relation"
    tool_description: str = """删除或弱化两个文件之间的关联。

当关联不再有意义时，可以选择删除或弱化它。

参数：
- source_path: 源文件路径（必填）
- target_path: 目标文件路径（必填）
- mode: 操作模式，"delete" 删除 或 "weaken" 弱化，默认 "weaken"

示例：
```
nucleus_forget_relation(
    source_path="diaries/2026-03-30.md",
    target_path="notes/废弃的想法.md",
    mode="delete"
)
```

注意：
- weaken 模式会将关联强度降低 50%
- delete 模式会完全删除关联
- 自动建立的 ASSOCIATES 类型关联会随时间自动衰减，通常无需手动删除
"""

    def __init__(self, plugin: Any):
        super().__init__()
        self.plugin = plugin
        self._memory_service: Optional[LifeMemoryService] = None

    async def _get_service(self) -> LifeMemoryService:
        """获取记忆服务实例。"""
        from .service import LifeEngineService
        
        service = LifeEngineService.get_instance()
        if service is None or service._memory_service is None:
            raise RuntimeError("记忆服务未初始化")
        return service._memory_service

    async def execute(
        self,
        source_path: Annotated[str, "源文件路径"],
        target_path: Annotated[str, "目标文件路径"],
        mode: Annotated[str, "操作模式: delete/weaken"] = "weaken",
    ) -> tuple[bool, dict[str, Any]]:
        """删除或弱化关联。"""
        if not source_path or not target_path:
            return False, {"error": "source_path 和 target_path 不能为空"}

        if mode not in ("delete", "weaken"):
            return False, {"error": "mode 必须是 'delete' 或 'weaken'"}

        try:
            service = await self._get_service()

            if mode == "delete":
                deleted = await service.delete_edge(source_path, target_path)
                if deleted:
                    return True, {
                        "action": "forget_relation",
                        "mode": "delete",
                        "source_path": source_path,
                        "target_path": target_path,
                        "result": "关联已删除"
                    }
                else:
                    return False, {"error": "未找到关联"}
            else:
                # weaken 模式：降低强度
                source_node = await service.get_node_by_file_path(source_path)
                target_node = await service.get_node_by_file_path(target_path)

                if not source_node or not target_node:
                    return False, {"error": "文件节点不存在"}

                # 获取并更新边
                edges = await service.get_edges_from(source_node.node_id)
                weakened = 0
                for edge in edges:
                    if edge.target_id == target_node.node_id:
                        new_strength = edge.weight * 0.5
                        await service.create_or_update_edge(
                            source_id=edge.source_id,
                            target_id=edge.target_id,
                            edge_type=edge.edge_type,
                            reason=edge.reason,
                            strength=new_strength,
                            bidirectional=edge.bidirectional
                        )
                        weakened += 1

                if weakened > 0:
                    return True, {
                        "action": "forget_relation",
                        "mode": "weaken",
                        "source_path": source_path,
                        "target_path": target_path,
                        "result": f"已弱化 {weakened} 条关联"
                    }
                else:
                    return False, {"error": "未找到关联"}

        except Exception as e:
            logger.error(f"遗忘关联失败: {e}", exc_info=True)
            return False, {"error": f"遗忘关联失败: {e}"}


# ============================================================
# nucleus_memory_stats - 记忆系统统计
# ============================================================

class LifeEngineMemoryStatsTool(BaseTool):
    """记忆系统统计工具。"""

    tool_name: str = "nucleus_memory_stats"
    tool_description: str = """获取记忆系统的统计信息。

返回：
- file_nodes: 文件节点数量
- concept_nodes: 概念节点数量
- total_edges: 总关联数量
- avg_activation: 平均激活强度

用于了解记忆系统的整体状态。
"""

    def __init__(self, plugin: Any):
        super().__init__()
        self.plugin = plugin
        self._memory_service: Optional[LifeMemoryService] = None

    async def _get_service(self) -> LifeMemoryService:
        """获取记忆服务实例。"""
        from .service import LifeEngineService
        
        service = LifeEngineService.get_instance()
        if service is None or service._memory_service is None:
            raise RuntimeError("记忆服务未初始化")
        return service._memory_service

    async def execute(self) -> tuple[bool, dict[str, Any]]:
        """获取统计信息。"""
        try:
            service = await self._get_service()
            stats = await service.get_stats()

            return True, {
                "action": "memory_stats",
                **stats
            }

        except Exception as e:
            logger.error(f"获取统计失败: {e}", exc_info=True)
            return False, {"error": f"获取统计失败: {e}"}


# ============================================================
# 工具注册列表
# ============================================================

MEMORY_TOOLS = [
    LifeEngineSearchMemoryTool,
    LifeEngineRelateFileTool,
    LifeEngineViewRelationsTool,
    LifeEngineForgetRelationTool,
    LifeEngineMemoryStatsTool,
]
