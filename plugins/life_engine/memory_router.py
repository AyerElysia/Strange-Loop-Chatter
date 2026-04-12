"""记忆系统可视化 Web 端点。"""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from src.core.components.base.router import BaseRouter

if TYPE_CHECKING:
    from .plugin import LifeEnginePlugin
    from .memory_service import MemoryNode, MemoryEdge


class ActivateRequest(BaseModel):
    seed_ids: List[str]
    max_depth: int = 2
    max_results: int = 20


class MemoryRouter(BaseRouter):
    """仿生记忆系统可视化路由。"""

    router_name = "memory"
    router_description = "Bionic Memory System Visualization"
    custom_route_path = "/memory"

    def register_endpoints(self) -> None:
        @self.app.get("/", response_class=HTMLResponse)
        async def get_dashboard() -> Any:
            """返回记忆系统面板 HTML。"""
            static_dir = os.path.join(os.path.dirname(__file__), "static")
            dashboard_path = os.path.join(static_dir, "memory_dashboard.html")
            if not os.path.exists(dashboard_path):
                return HTMLResponse(content="<h1>Memory Dashboard HTML not found!</h1>", status_code=404)
            with open(dashboard_path, "r", encoding="utf-8") as f:
                html_content = f.read()
            return HTMLResponse(content=html_content)

        @self.app.get("/api/stats")
        async def get_stats() -> Any:
            """获取记忆系统全局统计。"""
            plugin: "LifeEnginePlugin" = self.plugin  # type: ignore
            service = plugin.service
            memory = service.memory_service
            
            if not memory:
                return JSONResponse(content={"status": "disabled"}, status_code=503)
            
            stats = await memory.get_stats()
            return stats

        @self.app.get("/api/graph")
        async def get_graph(limit_nodes: int = 100) -> Any:
            """返回记忆图谱数据（节点与边）。"""
            plugin: "LifeEnginePlugin" = self.plugin  # type: ignore
            service = plugin.service
            memory = service.memory_service
            
            if not memory or not memory._db:
                return JSONResponse(content={"status": "disabled"}, status_code=503)

            cursor = memory._db.cursor()
            
            # 获取活跃度最高的前 limit_nodes 个节点
            cursor.execute(
                "SELECT * FROM memory_nodes ORDER BY activation_strength DESC LIMIT ?", 
                (limit_nodes,)
            )
            node_rows = cursor.fetchall()
            nodes = []
            node_ids = []
            for row in node_rows:
                nodes.append({
                    "id": row["node_id"],
                    "type": row["node_type"],
                    "title": row["title"] or row["file_path"] or "Untitled",
                    "path": row["file_path"],
                    "activation": row["activation_strength"],
                    "importance": row["importance"],
                    "valence": row["emotional_valence"],
                    "arousal": row["emotional_arousal"],
                    "access_count": row["access_count"]
                })
                node_ids.append(row["node_id"])

            if not node_ids:
                return {"nodes": [], "links": []}

            # 获取这些节点之间的边
            placeholders = ",".join("?" for _ in node_ids)
            cursor.execute(
                f"SELECT * FROM memory_edges WHERE source_id IN ({placeholders}) AND target_id IN ({placeholders})",
                node_ids + node_ids
            )
            edge_rows = cursor.fetchall()
            links = []
            for row in edge_rows:
                links.append({
                    "id": row["edge_id"],
                    "source": row["source_id"],
                    "target": row["target_id"],
                    "type": row["edge_type"],
                    "weight": row["weight"],
                    "reason": row["reason"]
                })

            return {"nodes": nodes, "links": links}

        @self.app.post("/api/activate")
        async def activate_memory(req: ActivateRequest) -> Any:
            """执行激活扩散并返回路径。"""
            plugin: "LifeEnginePlugin" = self.plugin  # type: ignore
            service = plugin.service
            memory = service.memory_service
            
            if not memory:
                return JSONResponse(content={"status": "disabled"}, status_code=503)

            results = await memory.spread_activation(
                req.seed_ids, 
                max_depth=req.max_depth, 
                max_results=req.max_results
            )
            
            # 转换结果格式
            association_data = []
            for node_id, score, path, reason in results:
                node = await memory._get_node_by_id(node_id)
                association_data.append({
                    "id": node_id,
                    "title": node.title if node else "Unknown",
                    "score": score,
                    "path": path,
                    "reason": reason
                })
            
            return association_data

        @self.app.get("/api/search")
        async def search_memory(query: str, top_k: int = 5) -> Any:
            """搜索记忆节点，用于查找激活起点。"""
            plugin: "LifeEnginePlugin" = self.plugin  # type: ignore
            service = plugin.service
            memory = service.memory_service
            
            if not memory:
                return JSONResponse(content={"status": "disabled"}, status_code=503)

            results = await memory.search(query, top_k=top_k)
            return results
