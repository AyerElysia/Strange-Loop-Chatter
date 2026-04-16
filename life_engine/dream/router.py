"""做梦系统（潜意识观测台） Web 端点。"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from fastapi.responses import HTMLResponse

from src.core.components.base.router import BaseRouter

if TYPE_CHECKING:
    from ..core.plugin import LifeEnginePlugin

# static/ 位于 plugins/life_engine/static/，即本文件的上两级目录
_PLUGIN_ROOT = os.path.dirname(os.path.dirname(__file__))


class DreamRouter(BaseRouter):
    """潜意识观测台可视化路由。"""

    router_name = "dream"
    router_description = "Subconscious Observatory"
    custom_route_path = "/dream_vis"

    def register_endpoints(self) -> None:
        @self.app.get("/", response_class=HTMLResponse)
        async def get_dashboard() -> Any:
            """返回潜意识观测台面板 HTML。"""
            static_dir = os.path.join(_PLUGIN_ROOT, "static")
            dashboard_path = os.path.join(static_dir, "dream_dashboard.html")
            if not os.path.exists(dashboard_path):
                return HTMLResponse(content="<h1>Dream Dashboard HTML not found!</h1>", status_code=404)
            with open(dashboard_path, "r", encoding="utf-8") as f:
                html_content = f.read()
            return HTMLResponse(content=html_content)

        @self.app.get("/api/events")
        async def events_stream() -> Any:
            """代理到 memory_vis 的 SSE 端点，前端可直接访问 /memory_vis/api/events。"""
            from fastapi.responses import JSONResponse
            return JSONResponse(
                content={"redirect": "/memory_vis/api/events",
                         "hint": "请直接访问 /memory_vis/api/events 获取 SSE 事件流"},
                status_code=307,
                headers={"Location": "/memory_vis/api/events"},
            )
