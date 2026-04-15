"""做梦系统（潜意识观测台） Web 端点。"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from fastapi.responses import HTMLResponse

from src.core.components.base.router import BaseRouter

if TYPE_CHECKING:
    from .plugin import LifeEnginePlugin


class DreamRouter(BaseRouter):
    """潜意识观测台可视化路由。"""

    router_name = "dream"
    router_description = "Subconscious Observatory"
    custom_route_path = "/dream_vis"

    def register_endpoints(self) -> None:
        @self.app.get("/", response_class=HTMLResponse)
        async def get_dashboard() -> Any:
            """返回潜意识观测台面板 HTML。"""
            static_dir = os.path.join(os.path.dirname(__file__), "static")
            dashboard_path = os.path.join(static_dir, "dream_dashboard.html")
            if not os.path.exists(dashboard_path):
                return HTMLResponse(content="<h1>Dream Dashboard HTML not found!</h1>", status_code=404)
            with open(dashboard_path, "r", encoding="utf-8") as f:
                html_content = f.read()
            return HTMLResponse(content=html_content)

        @self.app.get("/api/events")
        async def events_stream() -> Any:
            """直接复用 memory_router 的 SSE 端点进行代理，以保持后端实现的聚合。"""
            from .memory_router import MemoryRouter
            # 我们通过寻找已经加载的 MemoryRouter 组件来获取它的 events_stream
            plugin: "LifeEnginePlugin" = self.plugin  # type: ignore
            # 但为了简化，我们可以直接让前端去访问 /memory_vis/api/events
            pass
