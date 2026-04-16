"""记忆边数据结构与操作函数。

包含 EdgeType 枚举、MemoryEdge 数据类，
以及边的 CRUD 操作、Hebbian 强化函数。
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from src.app.plugin_system.api import log_api

logger = log_api.get_logger("life_engine.memory.edges")


# ============================================================
# 数据类型定义
# ============================================================


class EdgeType(Enum):
    """边类型。"""

    # 文件 ↔ 文件（显式关联）
    RELATES = "relates"  # 相关（默认双向）
    CAUSES = "causes"  # 因果（A导致B）
    CONTINUES = "continues"  # 延续（A是B的后续）
    CONTRASTS = "contrasts"  # 对比（A和B观点不同）

    # 文件 → 概念（自动/半自动）
    MENTIONS = "mentions"  # 文件提及某概念

    # 任意节点间（动态增强）
    ASSOCIATES = "associates"  # 联想边（检索时共同激活产生）


@dataclass
class MemoryEdge:
    """记忆边（关联）。"""

    edge_id: str
    source_id: str
    target_id: str
    edge_type: EdgeType

    # 连接强度
    weight: float = 0.5
    base_strength: float = 0.5
    reinforcement: float = 0.0

    # 激活统计
    activation_count: int = 0
    last_activated_at: Optional[float] = None

    # 元数据
    reason: str = ""  # 关联原因
    created_at: float = field(default_factory=time.time)
    bidirectional: bool = True


# ============================================================
# 辅助函数
# ============================================================


def row_to_edge(row: sqlite3.Row) -> MemoryEdge:
    """将数据库行转换为 MemoryEdge。"""
    return MemoryEdge(
        edge_id=row["edge_id"],
        source_id=row["source_id"],
        target_id=row["target_id"],
        edge_type=EdgeType(row["edge_type"]),
        weight=row["weight"],
        base_strength=row["base_strength"],
        reinforcement=row["reinforcement"],
        activation_count=row["activation_count"],
        last_activated_at=row["last_activated_at"],
        reason=row["reason"] or "",
        created_at=row["created_at"],
        bidirectional=bool(row["bidirectional"]),
    )


# ============================================================
# 边操作
# ============================================================


async def create_or_update_edge(
    db: sqlite3.Connection,
    source_id: str,
    target_id: str,
    edge_type: EdgeType,
    reason: str = "",
    strength: float = 0.5,
    bidirectional: bool = True,
    emit_visual_event: Any = None,
) -> MemoryEdge:
    """创建或更新边。

    Args:
        db: SQLite 数据库连接
        source_id: 源节点 ID
        target_id: 目标节点 ID
        edge_type: 边类型
        reason: 关联原因
        strength: 连接强度
        bidirectional: 是否双向
        emit_visual_event: 可视化事件发射函数

    Returns:
        MemoryEdge 实例
    """
    cursor = db.cursor()

    cursor.execute(
        """
        SELECT * FROM memory_edges
        WHERE source_id = ? AND target_id = ? AND edge_type = ?
        """,
        (source_id, target_id, edge_type.value),
    )
    row = cursor.fetchone()

    now = time.time()

    if row:
        edge = row_to_edge(row)
        cursor.execute(
            """
            UPDATE memory_edges
            SET weight = ?, reason = ?, last_activated_at = ?
            WHERE edge_id = ?
            """,
            (strength, reason or edge.reason, now, edge.edge_id),
        )
        db.commit()
        edge.weight = strength
        edge.reason = reason or edge.reason
        edge.last_activated_at = now
        if emit_visual_event:
            emit_visual_event(
                "memory.edges.updated",
                {
                    "edge": {
                        "id": edge.edge_id,
                        "source": edge.source_id,
                        "target": edge.target_id,
                        "type": edge.edge_type.value,
                        "weight": edge.weight,
                        "reason": edge.reason,
                        "last_activated_at": edge.last_activated_at,
                    }
                },
            )
        return edge

    edge_id = str(uuid.uuid4())[:8]
    edge = MemoryEdge(
        edge_id=edge_id,
        source_id=source_id,
        target_id=target_id,
        edge_type=edge_type,
        weight=strength,
        base_strength=strength,
        reason=reason,
        created_at=now,
        bidirectional=bidirectional,
    )

    cursor.execute(
        """
        INSERT INTO memory_edges
        (edge_id, source_id, target_id, edge_type, weight, base_strength,
         reinforcement, activation_count, last_activated_at, reason, created_at, bidirectional)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            edge.edge_id,
            edge.source_id,
            edge.target_id,
            edge.edge_type.value,
            edge.weight,
            edge.base_strength,
            edge.reinforcement,
            edge.activation_count,
            edge.last_activated_at,
            edge.reason,
            edge.created_at,
            1 if edge.bidirectional else 0,
        ),
    )

    # 如果是双向边，也创建反向边
    if bidirectional and edge_type not in (EdgeType.CAUSES, EdgeType.CONTINUES, EdgeType.MENTIONS):
        reverse_edge_id = str(uuid.uuid4())[:8]
        cursor.execute(
            """
            INSERT OR IGNORE INTO memory_edges
            (edge_id, source_id, target_id, edge_type, weight, base_strength,
             reinforcement, activation_count, last_activated_at, reason, created_at, bidirectional)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                reverse_edge_id,
                target_id,
                source_id,
                edge.edge_type.value,
                edge.weight,
                edge.base_strength,
                edge.reinforcement,
                edge.activation_count,
                edge.last_activated_at,
                edge.reason,
                edge.created_at,
                1,
            ),
        )

    db.commit()
    if emit_visual_event:
        emit_visual_event(
            "memory.edges.created",
            {
                "edge": {
                    "id": edge.edge_id,
                    "source": edge.source_id,
                    "target": edge.target_id,
                    "type": edge.edge_type.value,
                    "weight": edge.weight,
                    "reason": edge.reason,
                    "last_activated_at": edge.last_activated_at,
                }
            },
        )
    logger.debug(f"创建边: {source_id} --[{edge_type.value}]--> {target_id}")
    return edge


async def get_edges_from(
    db: sqlite3.Connection,
    node_id: str,
    min_weight: float = 0.0,
) -> List[MemoryEdge]:
    """获取从指定节点出发的边。

    Args:
        db: SQLite 数据库连接
        node_id: 节点 ID
        min_weight: 最小权重过滤

    Returns:
        边列表
    """
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT * FROM memory_edges
        WHERE source_id = ? AND weight >= ?
        ORDER BY weight DESC
        """,
        (node_id, min_weight),
    )
    return [row_to_edge(row) for row in cursor.fetchall()]


async def get_edges_to(
    db: sqlite3.Connection,
    node_id: str,
    min_weight: float = 0.0,
) -> List[MemoryEdge]:
    """获取指向指定节点的边。

    Args:
        db: SQLite 数据库连接
        node_id: 节点 ID
        min_weight: 最小权重过滤

    Returns:
        边列表
    """
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT * FROM memory_edges
        WHERE target_id = ? AND weight >= ?
        ORDER BY weight DESC
        """,
        (node_id, min_weight),
    )
    return [row_to_edge(row) for row in cursor.fetchall()]


async def delete_edge(
    db: sqlite3.Connection,
    source_path: str,
    target_path: str,
    edge_type: Optional[EdgeType] = None,
    generate_file_node_id_func: Any = None,
) -> bool:
    """删除边。

    Args:
        db: SQLite 数据库连接
        source_path: 源文件路径
        target_path: 目标文件路径
        edge_type: 边类型（可选）
        generate_file_node_id_func: 节点 ID 生成函数

    Returns:
        是否删除成功
    """
    from .nodes import generate_file_node_id

    gen_func = generate_file_node_id_func or generate_file_node_id
    source_id = gen_func(source_path)
    target_id = gen_func(target_path)

    cursor = db.cursor()
    if edge_type:
        cursor.execute(
            """
            DELETE FROM memory_edges
            WHERE source_id = ? AND target_id = ? AND edge_type = ?
            """,
            (source_id, target_id, edge_type.value),
        )
        cursor.execute(
            """
            DELETE FROM memory_edges
            WHERE source_id = ? AND target_id = ? AND edge_type = ?
            """,
            (target_id, source_id, edge_type.value),
        )
    else:
        cursor.execute(
            """
            DELETE FROM memory_edges
            WHERE (source_id = ? AND target_id = ?) OR (source_id = ? AND target_id = ?)
            """,
            (source_id, target_id, target_id, source_id),
        )

    deleted = cursor.rowcount > 0
    db.commit()
    return deleted


# ============================================================
# Hebbian 强化
# ============================================================


async def reinforce_coactivated(
    db: sqlite3.Connection,
    node_ids: List[str],
    learning_rate: float = 0.1,
    filter_existing_func: Any = None,
    emit_visual_event: Any = None,
) -> None:
    """强化共同激活的节点之间的边 (Hebbian Learning)。

    Args:
        db: SQLite 数据库连接
        node_ids: 节点 ID 列表
        learning_rate: 学习率
        filter_existing_func: 过滤存在节点的函数
        emit_visual_event: 可视化事件发射函数
    """
    # 仅使用节点表存在的 ID，避免外键约束异常
    deduped_ids: List[str] = []
    seen = set()
    for node_id in node_ids:
        if node_id not in seen:
            deduped_ids.append(node_id)
            seen.add(node_id)

    if filter_existing_func:
        _, stale_ids = await filter_existing_func([(node_id, 1.0) for node_id in deduped_ids])
        stale_set = set(stale_ids)
        existing_ids = [node_id for node_id in deduped_ids if node_id not in stale_set]
        if stale_ids:
            logger.warning(
                f"Hebbian 强化跳过 {len(stale_ids)} 个不存在节点ID，防止外键错误: {stale_ids[:5]}"
            )
    else:
        existing_ids = deduped_ids

    if len(existing_ids) < 2:
        return

    cursor = db.cursor()
    now = time.time()
    reinforced_edges: List[Dict[str, Any]] = []

    for i, node_a in enumerate(existing_ids):
        for node_b in existing_ids[i + 1:]:
            # 查找或创建 ASSOCIATES 边
            cursor.execute(
                """
                SELECT * FROM memory_edges
                WHERE source_id = ? AND target_id = ? AND edge_type = ?
                """,
                (node_a, node_b, EdgeType.ASSOCIATES.value),
            )
            row = cursor.fetchone()

            if row:
                old_weight = row["weight"]
                # Hebbian: Δw = α * (1 - w)
                delta = learning_rate * (1 - old_weight)
                new_weight = min(old_weight + delta, 1.0)

                cursor.execute(
                    """
                    UPDATE memory_edges
                    SET weight = ?, reinforcement = reinforcement + ?,
                        activation_count = activation_count + 1, last_activated_at = ?
                    WHERE edge_id = ?
                    """,
                    (new_weight, delta, now, row["edge_id"]),
                )
                reinforced_edges.append({
                    "id": row["edge_id"],
                    "source": node_a,
                    "target": node_b,
                    "type": EdgeType.ASSOCIATES.value,
                    "weight": new_weight,
                    "delta": delta,
                })
            else:
                edge_id = str(uuid.uuid4())[:8]
                cursor.execute(
                    """
                    INSERT INTO memory_edges
                    (edge_id, source_id, target_id, edge_type, weight, base_strength,
                     reinforcement, activation_count, last_activated_at, reason, created_at, bidirectional)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        edge_id,
                        node_a,
                        node_b,
                        EdgeType.ASSOCIATES.value,
                        0.2,
                        0.2,
                        0.0,
                        1,
                        now,
                        "共同检索激活",
                        now,
                        1,
                    ),
                )
                reinforced_edges.append({
                    "id": edge_id,
                    "source": node_a,
                    "target": node_b,
                    "type": EdgeType.ASSOCIATES.value,
                    "weight": 0.2,
                    "delta": learning_rate,
                })

    db.commit()
    if reinforced_edges and emit_visual_event:
        emit_visual_event(
            "memory.edges.reinforced",
            {"edges": reinforced_edges},
        )