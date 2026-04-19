"""ThoughtStream 管理器。"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .models import ThoughtStream

logger = logging.getLogger("life_engine.streams")


class ThoughtStreamManager:
    """思考流管理器：CRUD、评分、持久化。"""

    def __init__(
        self,
        workspace_path: str,
        max_active: int = 5,
        dormancy_hours: int = 24,
    ) -> None:
        """初始化思考流管理器。

        Args:
            workspace_path: 工作空间路径
            max_active: 最大活跃思考流数量
            dormancy_hours: 超过此小时数未推进则自动休眠
        """
        self._workspace = Path(workspace_path)
        self._thoughts_dir = self._workspace / "thoughts"
        self._thoughts_dir.mkdir(parents=True, exist_ok=True)
        self._index_file = self._thoughts_dir / "streams.json"
        self._max_active = max_active
        self._dormancy_hours = dormancy_hours
        self._streams: dict[str, ThoughtStream] = {}
        self._load()

    def _load(self) -> None:
        """从磁盘加载思考流索引。"""
        if not self._index_file.exists():
            return
        try:
            data = json.loads(self._index_file.read_text(encoding="utf-8"))
            for item in data.get("streams", []):
                ts = ThoughtStream(
                    id=item["id"],
                    title=item["title"],
                    created_at=item["created_at"],
                    last_advanced_at=item["last_advanced_at"],
                    advance_count=item.get("advance_count", 0),
                    curiosity_score=item.get("curiosity_score", 0.7),
                    last_thought=item.get("last_thought", ""),
                    related_memories=item.get("related_memories", []),
                    status=item.get("status", "active"),
                )
                self._streams[ts.id] = ts
        except Exception as e:
            logger.warning(f"加载思考流索引失败: {e}")

    def _save(self) -> None:
        """持久化到磁盘。"""
        data = {
            "schema_version": 1,
            "streams": [
                {
                    "id": ts.id,
                    "title": ts.title,
                    "created_at": ts.created_at,
                    "last_advanced_at": ts.last_advanced_at,
                    "advance_count": ts.advance_count,
                    "curiosity_score": ts.curiosity_score,
                    "last_thought": ts.last_thought,
                    "related_memories": ts.related_memories,
                    "status": ts.status,
                }
                for ts in self._streams.values()
            ],
        }
        try:
            self._index_file.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error(f"保存思考流索引失败: {e}")

    def create(
        self,
        title: str,
        reason: str = "",
        related_memories: list[str] | None = None,
    ) -> ThoughtStream:
        """创建新思考流。

        Args:
            title: 思考流标题
            reason: 创建原因（可选）
            related_memories: 关联记忆节点ID列表（可选）

        Returns:
            新创建的 ThoughtStream 实例
        """
        now = datetime.now(timezone.utc).isoformat()
        ts = ThoughtStream(
            id=f"ts_{uuid4().hex[:8]}",
            title=title,
            created_at=now,
            last_advanced_at=now,
            related_memories=related_memories or [],
        )

        # 检查活跃上限
        active = [s for s in self._streams.values() if s.is_active()]
        if len(active) >= self._max_active:
            # 将好奇心最低的转为休眠
            active.sort(key=lambda s: s.curiosity_score)
            weakest = active[0]
            weakest.status = "dormant"
            logger.info(f"思考流达上限，{weakest.title} 转入休眠")

        self._streams[ts.id] = ts
        self._save()
        logger.info(f"创建思考流: {ts.title} ({ts.id})")
        return ts

    def list_active(self) -> list[ThoughtStream]:
        """列出所有活跃思考流，按好奇心排序。"""
        active = [s for s in self._streams.values() if s.is_active()]
        active.sort(key=lambda s: s.curiosity_score, reverse=True)
        return active

    def list_all(self) -> list[ThoughtStream]:
        """列出所有思考流。"""
        return list(self._streams.values())

    def get(self, stream_id: str) -> ThoughtStream | None:
        """获取指定思考流。

        Args:
            stream_id: 思考流ID

        Returns:
            ThoughtStream 实例，不存在则返回 None
        """
        return self._streams.get(stream_id)

    def advance(
        self,
        stream_id: str,
        thought: str,
        curiosity_delta: float = 0.0,
    ) -> tuple[bool, str]:
        """推进一条思考流。

        Args:
            stream_id: 思考流ID
            thought: 本次思考内容
            curiosity_delta: 好奇心变化量

        Returns:
            (是否成功, 消息)
        """
        ts = self._streams.get(stream_id)
        if not ts:
            return False, f"思考流 {stream_id} 不存在"
        if ts.status != "active":
            return False, f"思考流 {ts.title} 当前状态为 {ts.status}，无法推进"

        ts.advance_count += 1
        ts.last_thought = thought[:500]
        ts.last_advanced_at = datetime.now(timezone.utc).isoformat()
        ts.curiosity_score = max(0.0, min(1.0, ts.curiosity_score + curiosity_delta))

        self._save()
        logger.info(f"推进思考流: {ts.title} (第{ts.advance_count}次)")
        return True, f"已推进思考流「{ts.title}」(第{ts.advance_count}次，好奇心: {ts.curiosity_score:.2f})"

    def retire(
        self,
        stream_id: str,
        new_status: str = "completed",
        conclusion: str = "",
    ) -> tuple[bool, str]:
        """结束或休眠一条思考流。

        Args:
            stream_id: 思考流ID
            new_status: 新状态（"completed" 或 "dormant"）
            conclusion: 结束时的总结想法

        Returns:
            (是否成功, 消息)
        """
        ts = self._streams.get(stream_id)
        if not ts:
            return False, f"思考流 {stream_id} 不存在"

        old_status = ts.status
        ts.status = new_status
        if conclusion:
            ts.last_thought = conclusion[:500]

        self._save()
        logger.info(f"思考流 {ts.title}: {old_status} -> {new_status}")
        return True, f"思考流「{ts.title}」已标记为 {new_status}"

    def reactivate(self, stream_id: str) -> tuple[bool, str]:
        """重新激活一条休眠的思考流。

        Args:
            stream_id: 思考流ID

        Returns:
            (是否成功, 消息)
        """
        ts = self._streams.get(stream_id)
        if not ts:
            return False, f"思考流 {stream_id} 不存在"
        if ts.status == "active":
            return False, f"思考流「{ts.title}」已经是活跃状态"
        if ts.status == "completed":
            return False, f"已完成的思考流不能重新激活"

        ts.status = "active"
        ts.curiosity_score = max(ts.curiosity_score, 0.5)
        ts.last_advanced_at = datetime.now(timezone.utc).isoformat()
        self._save()
        return True, f"思考流「{ts.title}」已重新激活"

    def check_dormancy(self) -> list[str]:
        """检查并自动休眠超时的思考流。

        Returns:
            被休眠的思考流ID列表
        """
        dormant_ids: list[str] = []
        for ts in self._streams.values():
            if ts.should_go_dormant(self._dormancy_hours):
                ts.status = "dormant"
                dormant_ids.append(ts.id)
        if dormant_ids:
            self._save()
            logger.info(f"自动休眠思考流: {dormant_ids}")
        return dormant_ids

    def format_for_prompt(self, max_items: int = 3) -> str:
        """格式化为心跳 prompt 片段。

        Args:
            max_items: 最多展示的思考流数量

        Returns:
            格式化后的 prompt 文本
        """
        active = self.list_active()[:max_items]
        if not active:
            return ""

        lines = ["### 当前思考流", ""]
        for ts in active:
            # 计算上次推进距现在多久
            try:
                last = datetime.fromisoformat(ts.last_advanced_at)
                now = datetime.now(timezone.utc)
                minutes_ago = int((now - last).total_seconds() / 60)
                if minutes_ago < 60:
                    time_str = f"{minutes_ago}分钟前"
                else:
                    hours_ago = minutes_ago // 60
                    time_str = f"{hours_ago}小时前"
            except (ValueError, TypeError):
                time_str = "未知"

            lines.append(
                f"**{ts.title}** (好奇心: {ts.curiosity_score:.0%}, 上次推进: {time_str})"
            )
            if ts.last_thought:
                lines.append(f"  最近想法: {ts.last_thought[:200]}")
            lines.append("")

        if len(self.list_active()) > max_items:
            lines.append(f"... 还有 {len(self.list_active()) - max_items} 条活跃思考流")
            lines.append("")

        return "\n".join(lines)
