"""life_engine memory_service 回归测试。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from plugins.life_engine.core.config import LifeEngineConfig
from plugins.life_engine.memory import EdgeType, LifeMemoryService


@dataclass
class _DummyPlugin:
    config: LifeEngineConfig


class _FakeVectorService:
    """最小向量服务桩。"""

    def __init__(self, collection: Any) -> None:
        self._collection = collection
        self.calls = 0

    async def get_or_create_collection(self, name: str) -> Any:
        assert name == "life_memory"
        self.calls += 1
        return self._collection


def _make_service(tmp_path: Path) -> LifeMemoryService:
    config = LifeEngineConfig()
    config.settings.workspace_path = str(tmp_path)
    return LifeMemoryService(_DummyPlugin(config=config))


def test_get_chroma_collection_awaits_async_vector_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """应 await 异步 get_or_create_collection，并缓存集合实例。"""

    service = _make_service(tmp_path)
    fake_collection = SimpleNamespace(query=lambda **_: {"ids": [[]], "distances": [[]]})
    fake_vector_service = _FakeVectorService(fake_collection)

    monkeypatch.setattr(
        "src.kernel.vector_db.get_vector_db_service",
        lambda _path: fake_vector_service,
    )

    first = asyncio.run(service._get_chroma_collection())
    second = asyncio.run(service._get_chroma_collection())

    assert first is fake_collection
    assert second is fake_collection
    assert fake_vector_service.calls == 1


def test_workspace_path_override_works_with_path_input(tmp_path: Path) -> None:
    """当传入 Path 作为构造参数时，应使用该路径作为记忆库根目录。"""
    service = LifeMemoryService(tmp_path)
    db_path = service._get_db_path()
    assert db_path == tmp_path / ".memory" / "memory.db"


def test_migrate_file_path_keeps_edges_and_fts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """文件重命名后，节点 ID/边/FTS 应随之迁移。"""
    async def _run() -> None:
        service = _make_service(tmp_path)
        await service.initialize()

        class _FakeCollection:
            def get(self, **_: Any) -> dict[str, Any]:
                return {"ids": [], "embeddings": [], "documents": [], "metadatas": []}

            def upsert(self, **_: Any) -> None:
                return None

            def delete(self, **_: Any) -> None:
                return None

        async def _fake_get_collection() -> Any:
            return _FakeCollection()

        monkeypatch.setattr(service, "_get_chroma_collection", _fake_get_collection)

        source_node = await service.get_or_create_file_node(
            "notes/a.md",
            title="A",
            content="alpha content",
        )
        target_node = await service.get_or_create_file_node(
            "notes/b.md",
            title="B",
            content="beta content",
        )
        await service.create_or_update_edge(
            source_id=source_node.node_id,
            target_id=target_node.node_id,
            edge_type=EdgeType.RELATES,
            reason="test edge",
            strength=0.8,
            bidirectional=True,
        )

        migrated = await service.migrate_file_path("notes/a.md", "archive/a.md")
        assert migrated is True

        old_node = await service.get_node_by_file_path("notes/a.md")
        new_node = await service.get_node_by_file_path("archive/a.md")
        assert old_node is None
        assert new_node is not None
        assert new_node.file_path == "archive/a.md"

        edges = await service.get_edges_from(new_node.node_id)
        assert any(edge.target_id == target_node.node_id for edge in edges)

        cursor = service._db.cursor()
        cursor.execute("SELECT content FROM memory_fts WHERE node_id = ?", (new_node.node_id,))
        fts_row = cursor.fetchone()
        assert fts_row is not None
        assert "alpha content" in (fts_row["content"] or "")

        cursor.execute("SELECT content FROM memory_fts WHERE node_id = ?", (source_node.node_id,))
        assert cursor.fetchone() is None

    asyncio.run(_run())
