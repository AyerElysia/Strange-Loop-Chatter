"""life 与 chatter 联合消息时间线可视化。"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from fastapi.responses import HTMLResponse, JSONResponse

from src.core.components.base.router import BaseRouter

if TYPE_CHECKING:
    from ..core.plugin import LifeEnginePlugin

_PLUGIN_ROOT = os.path.dirname(os.path.dirname(__file__))


class MessageTimelineRouter(BaseRouter):
    """联合消息时间线面板。"""

    router_name = "message_timeline"
    router_description = "Life / Chatter Message Timeline"
    custom_route_path = "/message_timeline"

    def register_endpoints(self) -> None:
        @self.app.get("/", response_class=HTMLResponse)
        async def get_dashboard() -> Any:
            static_dir = os.path.join(_PLUGIN_ROOT, "static")
            dashboard_path = os.path.join(static_dir, "life_message_dashboard.html")
            if not os.path.exists(dashboard_path):
                return HTMLResponse(content="<h1>Message Timeline Dashboard HTML not found!</h1>", status_code=404)
            with open(dashboard_path, "r", encoding="utf-8") as f:
                html_content = f.read()
            return HTMLResponse(content=html_content)

        @self.app.get("/api/snapshot")
        async def get_snapshot(
            event_limit: int = 24,
            stream_limit: int = 12,
            message_limit: int = 8,
        ) -> Any:
            plugin: "LifeEnginePlugin" = self.plugin  # type: ignore
            service = plugin.service
            snapshot = await service.get_message_observability_snapshot(
                event_limit=event_limit,
                stream_limit=stream_limit,
                message_limit=message_limit,
            )
            return snapshot

        @self.app.get("/api/stream/{stream_id}")
        async def get_stream_snapshot(stream_id: str) -> Any:
            plugin: "LifeEnginePlugin" = self.plugin  # type: ignore
            service = plugin.service
            snapshot = await service.get_message_observability_snapshot(stream_limit=50, message_limit=20)
            for stream in snapshot.get("streams", []):
                if str(stream.get("stream_id", "")) == stream_id:
                    return stream
            return JSONResponse(content={"error": "stream not found"}, status_code=404)

        @self.app.get("/api/history_search")
        async def history_search(
            query: str = "",
            stream_id: str = "",
            cross_stream: bool = True,
            limit: int = 20,
            source_mode: str = "auto",
            include_tool_calls: bool = True,
        ) -> Any:
            from ..tools.chat_history_tools import LifeEngineFetchChatHistoryTool

            plugin: "LifeEnginePlugin" = self.plugin  # type: ignore
            tool = LifeEngineFetchChatHistoryTool(plugin=plugin)
            ok, data = await tool.execute(
                query=query,
                stream_ids=[stream_id] if stream_id.strip() else [],
                cross_stream=cross_stream,
                limit=limit,
                source_mode=source_mode if source_mode in {"auto", "local_db", "napcat"} else "auto",
                include_tool_calls=include_tool_calls,
            )
            if ok:
                return data
            return JSONResponse(content={"error": str(data)}, status_code=400)
