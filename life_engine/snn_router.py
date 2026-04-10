"""SNN 可视化 Web 端点。"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from fastapi.responses import HTMLResponse, JSONResponse

from src.core.components.base.router import BaseRouter

if TYPE_CHECKING:
    from .plugin import LifeEnginePlugin


class SNNRouter(BaseRouter):
    """SNN 状态可视化路由。"""

    router_name = "snn"
    router_description = "SNN Dashboard Visualization"
    custom_route_path = "/snn"

    def register_endpoints(self) -> None:
        @self.app.get("/", response_class=HTMLResponse)
        async def get_dashboard() -> Any:
            """返回可视化面板 HTML。"""
            static_dir = os.path.join(os.path.dirname(__file__), "static")
            dashboard_path = os.path.join(static_dir, "snn_dashboard.html")
            if not os.path.exists(dashboard_path):
                return HTMLResponse(content="<h1>Dashboard HTML not found!</h1>", status_code=404)
            with open(dashboard_path, "r", encoding="utf-8") as f:
                html_content = f.read()
            return HTMLResponse(content=html_content)

        @self.app.get("/api/state")
        async def get_state() -> Any:
            """返回 SNN 状态数据供前端轮询。"""
            # type ignore 因为我们知道所属 plugin 是 LifeEnginePlugin
            plugin: "LifeEnginePlugin" = self.plugin  # type: ignore
            service = plugin.service
            net = service._snn_network
            
            if not net:
                return JSONResponse(content={"status": "disabled"}, status_code=503)

            return {
                "status": "active",
                "tick_count": net.tick_count,
                "drives": net.get_drive_dict(),
                "drives_discrete": net.get_drive_discrete(),
                "hidden_v": net.hidden.v.tolist(),
                "hidden_spikes": net.hidden.spikes.tolist(),
                "output_v": net.output.v.tolist(),
                "output_spikes": net.output.spikes.tolist(),
                "output_ema": net._output_ema.tolist(),
                "syn_in_hid_W": net.syn_in_hid.W.tolist(),
                "syn_hid_out_W": net.syn_hid_out.W.tolist(),
                "syn_in_hid_trace_pre": net.syn_in_hid.trace_pre.tolist(),
                "syn_hid_out_trace_post": net.syn_hid_out.trace_post.tolist(),
            }
