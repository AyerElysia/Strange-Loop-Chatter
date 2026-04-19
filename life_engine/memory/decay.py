"""记忆衰减与做梦系统接口。

包含 Ebbinghaus 遗忘曲线计算、衰减应用、
做梦游走、弱边修剪等函数。
"""

from __future__ import annotations

import math
import sqlite3
import time

from src.app.plugin_system.api.log_api import get_logger

logger = get_logger("life_engine.memory.decay")
import time
import uuid
from typing import Any, Dict, List, Optional

import numpy as np

from src.app.plugin_system.api import log_api

from .nodes import MemoryNode, NodeType, row_to_node
from .edges import EdgeType, row_to_edge, get_edges_from

logger = log_api.get_logger("life_engine.memory.decay")


# ============================================================
# 常量
# ============================================================

DECAY_LAMBDA = 0.05  # 遗忘衰减系数（约14天半衰期）
PRUNE_THRESHOLD = 0.1  # 边剪枝阈值
DREAM_LEARNING_RATE = 0.05  # REM 做梦学习率


# ============================================================
# 遗忘曲线
# ============================================================


def compute_memory_strength(node: MemoryNode, decay_lambda: float = DECAY_LAMBDA) -> float:
    """计算记忆强度，结合 Ebbinghaus 遗忘曲线和多种保护因素。

    Args:
        node: MemoryNode 实例
        decay_lambda: 遗忘衰减系数

    Returns:
        记忆强度 [0, 1]
    """
    if not node.last_accessed_at:
        return node.activation_strength

    now = time.time()
    days_since = (now - node.last_accessed_at) / 86400

    # 基础时间衰减 (Ebbinghaus-inspired)
    time_decay = math.exp(-decay_lambda * days_since)

    # 提取练习效应 (Testing Effect)
    retrieval_bonus = math.log(1 + node.access_count) * 0.1

    # 情感保护 (Emotional Enhancement)
    emotional_shield = node.emotional_arousal * 0.2

    # 重要性保护
    importance_shield = node.importance * 0.1

    # 最终强度
    strength = time_decay + retrieval_bonus + emotional_shield + importance_shield
    return min(max(strength, 0.0), 1.0)


async def apply_decay(db: sqlite3.Connection) -> int:
    """应用遗忘衰减（定期任务）。

    Args:
        db: SQLite 数据库连接

    Returns:
        更新的节点数量
    """
    logger.info("Starting memory decay process")
    start_time = time.time()

    cursor = db.cursor()
    cursor.execute("SELECT * FROM memory_nodes")
    rows = cursor.fetchall()

    updated = 0
    for row in rows:
        node = row_to_node(row)
        new_strength = compute_memory_strength(node)

        if abs(new_strength - node.activation_strength) > 0.01:
            cursor.execute(
                "UPDATE memory_nodes SET activation_strength = ? WHERE node_id = ?",
                (new_strength, node.node_id),
            )
            updated += 1

    # 边衰减
    cursor.execute(
        "SELECT * FROM memory_edges WHERE edge_type = ?",
        (EdgeType.ASSOCIATES.value,),
    )
    for row in cursor.fetchall():
        edge = row_to_edge(row)
        if edge.last_activated_at:
            days_since = (time.time() - edge.last_activated_at) / 86400
            decay_factor = math.exp(-DECAY_LAMBDA * days_since)
            new_weight = edge.base_strength + edge.reinforcement * decay_factor

            if new_weight < PRUNE_THRESHOLD:
                cursor.execute("DELETE FROM memory_edges WHERE edge_id = ?", (edge.edge_id,))
            elif abs(new_weight - edge.weight) > 0.01:
                cursor.execute(
                    "UPDATE memory_edges SET weight = ? WHERE edge_id = ?",
                    (new_weight, edge.edge_id),
                )

    db.commit()

    elapsed = time.time() - start_time
    logger.info(
        f"Memory decay completed: {updated} nodes updated in {elapsed:.2f}s"
    )
    return updated


# ============================================================
# 做梦系统接口
# ============================================================


async def dream_walk(
    db: sqlite3.Connection,
    num_seeds: int = 5,
    seed_ids: Optional[List[str]] = None,
    max_depth: int = 3,
    decay_factor: float = 0.6,
    learning_rate: float = DREAM_LEARNING_RATE,
    emit_visual_event: Any = None,
) -> Dict[str, Any]:
    """REM 做梦游走：从随机种子出发进行激活扩散，Hebbian 强化共激活节点。

    与搜索时的 spread_activation 的区别：
    - 种子是随机选取的（不是查询驱动的）
    - 衰减更慢（decay_factor=0.6 vs 0.7），扩散更远
    - 学习率更低（0.05 vs 0.1），梦中学习更温和
    - 不需要查询，不消耗 embedding API

    Args:
        db: SQLite 数据库连接
        num_seeds: 种子数量
        seed_ids: 指定的种子节点 ID（可选）
        max_depth: 最大扩散深度
        decay_factor: 扩散衰减系数
        learning_rate: 学习率
        emit_visual_event: 可视化事件发射函数

    Returns:
        {"nodes_activated": int, "new_edges_created": int, "seed_ids": list}
    """
    if not db:
        return {"nodes_activated": 0, "new_edges_created": 0, "seed_ids": []}

    cursor = db.cursor()

    # 按 activation_strength 加权随机选取种子节点
    cursor.execute(
        """
        SELECT node_id, activation_strength FROM memory_nodes
        WHERE activation_strength > 0.05 ORDER BY activation_strength DESC
        """
    )
    rows = cursor.fetchall()
    if not rows:
        return {"nodes_activated": 0, "new_edges_created": 0, "seed_ids": []}

    node_ids = [r["node_id"] for r in rows]
    strengths = np.array([r["activation_strength"] for r in rows], dtype=np.float64)
    total_strength = float(strengths.sum())
    if total_strength <= 0:
        strengths = np.ones(len(node_ids), dtype=np.float64) / max(len(node_ids), 1)
    else:
        strengths /= total_strength

    requested_seed_ids = [
        str(node_id or "").strip()
        for node_id in (seed_ids or [])
        if str(node_id or "").strip()
    ]
    actual_seed_ids = [node_id for node_id in requested_seed_ids if node_id in node_ids]
    remaining_pool = [node_id for node_id in node_ids if node_id not in actual_seed_ids]

    missing_count = max(0, min(num_seeds, len(node_ids)) - len(actual_seed_ids))
    if missing_count > 0 and remaining_pool:
        pool_indices = [node_ids.index(node_id) for node_id in remaining_pool]
        pool_strengths = strengths[pool_indices]
        pool_total = float(pool_strengths.sum())
        if pool_total <= 0:
            pool_strengths = np.ones(len(pool_indices), dtype=np.float64) / max(len(pool_indices), 1)
        else:
            pool_strengths = pool_strengths / pool_total
        sampled_indices = np.random.choice(
            len(pool_indices),
            size=min(missing_count, len(pool_indices)),
            replace=False,
            p=pool_strengths,
        )
        actual_seed_ids.extend(remaining_pool[idx] for idx in sampled_indices)

    if not actual_seed_ids:
        return {"nodes_activated": 0, "new_edges_created": 0, "seed_ids": []}

    # 梦游走式激活扩散
    activation: Dict[str, float] = {sid: 1.0 for sid in actual_seed_ids}
    visited = set(actual_seed_ids)
    frontier = list(actual_seed_ids)

    for depth in range(max_depth):
        next_frontier: List[str] = []
        decay = decay_factor ** (depth + 1)

        for node_id in frontier:
            current_act = activation[node_id]
            edges = await get_edges_from(db, node_id, min_weight=0.05)

            for edge in edges:
                neighbor = edge.target_id
                if neighbor in visited:
                    continue

                propagated = current_act * edge.weight * decay
                # 梦中阈值更低，允许更远的联想
                if propagated >= 0.1:
                    activation[neighbor] = activation.get(neighbor, 0) + propagated
                    next_frontier.append(neighbor)
                    visited.add(neighbor)

        frontier = next_frontier

        if emit_visual_event:
            emit_visual_event(
                "memory.dream.walk",
                {
                    "depth": depth,
                    "seed_ids": actual_seed_ids,
                    "activated_ids": list(activation.keys()),
                    "frontier_ids": next_frontier,
                },
                source="dream",
            )

        if not frontier:
            break

    all_activated = list(activation.keys())

    # Hebbian 强化共激活节点
    new_edges = 0
    now = time.time()

    top_activated = sorted(activation.items(), key=lambda x: -x[1])[:15]
    top_ids = [nid for nid, _ in top_activated]

    existing_ids: List[str] = []
    for nid in top_ids:
        cursor.execute("SELECT node_id FROM memory_nodes WHERE node_id = ?", (nid,))
        if cursor.fetchone():
            existing_ids.append(nid)

    for i, node_a in enumerate(existing_ids):
        for node_b in existing_ids[i + 1:]:
            cursor.execute(
                """
                SELECT edge_id, weight FROM memory_edges
                WHERE source_id = ? AND target_id = ? AND edge_type = ?
                """,
                (node_a, node_b, EdgeType.ASSOCIATES.value),
            )
            row = cursor.fetchone()

            if row:
                old_weight = row["weight"]
                delta = learning_rate * (1 - old_weight)
                new_weight = min(old_weight + delta, 1.0)
                cursor.execute(
                    """
                    UPDATE memory_edges SET weight = ?, reinforcement = reinforcement + ?,
                    activation_count = activation_count + 1, last_activated_at = ?
                    WHERE edge_id = ?
                    """,
                    (new_weight, delta, now, row["edge_id"]),
                )
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
                        0.15,
                        0.15,
                        0.0,
                        1,
                        now,
                        "REM 做梦联想",
                        now,
                        1,
                    ),
                )
                new_edges += 1

    db.commit()

    logger.info(
        f"REM dream_walk 完成: seeds={len(actual_seed_ids)} "
        f"activated={len(all_activated)} new_edges={new_edges}"
    )

    return {
        "nodes_activated": len(all_activated),
        "new_edges_created": new_edges,
        "seed_ids": actual_seed_ids,
    }


async def list_dream_candidate_nodes(
    db: sqlite3.Connection,
    limit: int = 12,
) -> List[Dict[str, Any]]:
    """列出适合做梦选种的长期主题候选节点。

    Args:
        db: SQLite 数据库连接
        limit: 返回数量

    Returns:
        候选节点信息列表
    """
    if not db:
        return []

    cursor = db.cursor()
    cursor.execute(
        """
        SELECT node_id, file_path, title, activation_strength, access_count,
               emotional_valence, emotional_arousal, importance, updated_at
        FROM memory_nodes
        WHERE node_type = ?
        ORDER BY importance DESC,
                 emotional_arousal DESC,
                 access_count DESC,
                 activation_strength DESC,
                 updated_at DESC
        LIMIT ?
        """,
        (NodeType.FILE.value, max(1, int(limit))),
    )
    results: List[Dict[str, Any]] = []
    for row in cursor.fetchall():
        results.append({
            "node_id": row["node_id"],
            "file_path": row["file_path"],
            "title": row["title"] or "",
            "activation_strength": float(row["activation_strength"] or 0.0),
            "access_count": int(row["access_count"] or 0),
            "emotional_valence": float(row["emotional_valence"] or 0.0),
            "emotional_arousal": float(row["emotional_arousal"] or 0.0),
            "importance": float(row["importance"] or 0.0),
            "updated_at": float(row["updated_at"] or 0.0),
        })
    return results


async def list_random_file_nodes(
    db: sqlite3.Connection,
    limit: int = 15,
) -> List[Dict[str, Any]]:
    """随机采样文件节点，供做梦系统自由联想使用。

    与 list_dream_candidate_nodes 不同，此方法使用 ORDER BY RANDOM()
    从全图谱均匀采样，让任何记忆都有机会成为做梦素材。

    Args:
        db: SQLite 数据库连接
        limit: 返回数量

    Returns:
        随机节点信息列表
    """
    if not db:
        return []

    cursor = db.cursor()
    cursor.execute(
        """
        SELECT node_id, file_path, title, activation_strength, access_count,
               emotional_valence, emotional_arousal, importance, updated_at
        FROM memory_nodes
        WHERE node_type = ?
        ORDER BY RANDOM()
        LIMIT ?
        """,
        (NodeType.FILE.value, max(1, int(limit))),
    )
    results: List[Dict[str, Any]] = []
    for row in cursor.fetchall():
        results.append({
            "node_id": row["node_id"],
            "file_path": row["file_path"],
            "title": row["title"] or "",
            "activation_strength": float(row["activation_strength"] or 0.0),
            "access_count": int(row["access_count"] or 0),
            "emotional_valence": float(row["emotional_valence"] or 0.0),
            "emotional_arousal": float(row["emotional_arousal"] or 0.0),
            "importance": float(row["importance"] or 0.0),
            "updated_at": float(row["updated_at"] or 0.0),
        })
    return results


async def prune_weak_edges(
    db: sqlite3.Connection,
    threshold: float = PRUNE_THRESHOLD,
) -> int:
    """修剪弱 ASSOCIATES 边（仅自动生成的联想边，保护手动关联）。

    Args:
        db: SQLite 数据库连接
        threshold: 剪枝阈值

    Returns:
        被修剪的边数量
    """
    if not db:
        return 0

    cursor = db.cursor()
    cursor.execute(
        """
        SELECT edge_id, weight FROM memory_edges
        WHERE edge_type = ? AND weight < ?
        """,
        (EdgeType.ASSOCIATES.value, threshold),
    )
    rows = cursor.fetchall()

    if not rows:
        return 0

    edge_ids = [r["edge_id"] for r in rows]
    placeholders = ",".join("?" for _ in edge_ids)
    cursor.execute(
        f"DELETE FROM memory_edges WHERE edge_id IN ({placeholders})",
        edge_ids,
    )
    db.commit()

    logger.info(f"REM 弱边修剪完成: pruned={len(edge_ids)} threshold={threshold}")
    return len(edge_ids)


# ============================================================
# 关联图谱
# ============================================================


async def get_file_relations(
    db: sqlite3.Connection,
    file_path: str,
    depth: int = 1,
    min_strength: float = 0.2,
    get_node_by_file_path_func: Any = None,
    get_edges_from_func: Any = None,
    get_edges_to_func: Any = None,
    get_node_by_id_func: Any = None,
) -> Dict[str, Any]:
    """获取文件的关联图谱。

    Args:
        db: SQLite 数据库连接
        file_path: 文件路径
        depth: 关联深度
        min_strength: 最小关联强度
        get_node_by_file_path_func: 获取节点的函数
        get_edges_from_func: 获取出边的函数
        get_edges_to_func: 获取入边的函数
        get_node_by_id_func: 根据 ID 获取节点的函数

    Returns:
        关联图谱信息
    """
    from .nodes import get_node_by_file_path
    from .edges import get_edges_from, get_edges_to
    from .search import get_node_by_id

    get_node_func = get_node_by_file_path_func or get_node_by_file_path
    get_from_func = get_edges_from_func or get_edges_from
    get_to_func = get_edges_to_func or get_edges_to
    get_id_func = get_node_by_id_func or get_node_by_id

    node = await get_node_func(db, file_path)
    if not node:
        return {"error": f"未找到文件: {file_path}"}

    relations = {
        "center": {
            "file_path": file_path,
            "title": node.title,
            "activation_strength": node.activation_strength,
            "access_count": node.access_count,
        },
        "outgoing": [],
        "incoming": [],
    }

    # 出边
    out_edges = await get_from_func(db, node.node_id, min_strength)
    for edge in out_edges:
        target = await get_id_func(db, edge.target_id)
        if target and target.file_path:
            relations["outgoing"].append({
                "file_path": target.file_path,
                "title": target.title,
                "relation_type": edge.edge_type.value,
                "strength": edge.weight,
                "reason": edge.reason,
            })

    # 入边
    in_edges = await get_to_func(db, node.node_id, min_strength)
    for edge in in_edges:
        source = await get_id_func(db, edge.source_id)
        if source and source.file_path:
            relations["incoming"].append({
                "file_path": source.file_path,
                "title": source.title,
                "relation_type": edge.edge_type.value,
                "strength": edge.weight,
                "reason": edge.reason,
            })

    return relations


# ============================================================
# 统计信息
# ============================================================


async def get_stats(db: sqlite3.Connection) -> Dict[str, Any]:
    """获取记忆系统统计信息。

    Args:
        db: SQLite 数据库连接

    Returns:
        统计信息字典
    """
    cursor = db.cursor()

    cursor.execute(
        "SELECT COUNT(*) as cnt FROM memory_nodes WHERE node_type = ?",
        (NodeType.FILE.value,),
    )
    file_count = cursor.fetchone()["cnt"]

    cursor.execute(
        "SELECT COUNT(*) as cnt FROM memory_nodes WHERE node_type = ?",
        (NodeType.CONCEPT.value,),
    )
    concept_count = cursor.fetchone()["cnt"]

    cursor.execute("SELECT COUNT(*) as cnt FROM memory_edges")
    edge_count = cursor.fetchone()["cnt"]

    cursor.execute("SELECT AVG(activation_strength) as avg FROM memory_nodes")
    avg_activation = cursor.fetchone()["avg"] or 0

    return {
        "file_nodes": file_count,
        "concept_nodes": concept_count,
        "total_edges": edge_count,
        "avg_activation": round(avg_activation, 3),
    }