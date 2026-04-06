"""Life Engine 仿生记忆服务。

实现基于认知科学的记忆系统：
- 激活扩散 (Spreading Activation)：联想机制
- Hebbian 学习：共同激活强化连接
- 软遗忘：基于 Ebbinghaus 曲线的记忆衰减
"""

from __future__ import annotations

import hashlib
import math
import random
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.app.plugin_system.api import log_api
from src.app.plugin_system.api.llm_api import create_embedding_request, get_model_set_by_task
from src.kernel.vector_db import get_vector_db_service

logger = log_api.get_logger("life_engine.memory")


# ============================================================
# 数据类型定义
# ============================================================

class NodeType(Enum):
    """节点类型。"""
    FILE = "file"           # 文件节点：对应 workspace 中的实际文件
    CONCEPT = "concept"     # 概念节点：人物、地点、主题等抽象概念


class EdgeType(Enum):
    """边类型。"""
    # 文件 ↔ 文件（显式关联）
    RELATES = "relates"         # 相关（默认双向）
    CAUSES = "causes"           # 因果（A导致B）
    CONTINUES = "continues"     # 延续（A是B的后续）
    CONTRASTS = "contrasts"     # 对比（A和B观点不同）
    
    # 文件 → 概念（自动/半自动）
    MENTIONS = "mentions"       # 文件提及某概念
    
    # 任意节点间（动态增强）
    ASSOCIATES = "associates"   # 联想边（检索时共同激活产生）


@dataclass
class MemoryNode:
    """记忆节点。"""
    node_id: str
    node_type: NodeType
    file_path: Optional[str] = None     # 仅 FILE 类型有
    content_hash: Optional[str] = None
    title: str = ""
    
    # 激活相关
    activation_strength: float = 1.0
    access_count: int = 0
    last_accessed_at: Optional[float] = None
    
    # 情感标记
    emotional_valence: float = 0.0      # 情感效价 [-1, 1]
    emotional_arousal: float = 0.0      # 情感唤醒度 [0, 1]
    importance: float = 0.5             # 主观重要性 [0, 1]
    
    # 元数据
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    embedding_synced: bool = False


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
    reason: str = ""                    # 关联原因
    created_at: float = field(default_factory=time.time)
    bidirectional: bool = True


@dataclass
class SearchResult:
    """检索结果。"""
    file_path: str
    title: str
    snippet: str
    relevance: float
    source: str  # 'direct' | 'associated'
    association_path: List[str] = field(default_factory=list)
    association_reason: str = ""


# ============================================================
# 记忆服务
# ============================================================

class LifeMemoryService:
    """仿生记忆服务。"""
    
    # 算法参数
    DECAY_LAMBDA = 0.05          # 遗忘衰减系数（约14天半衰期）
    LEARNING_RATE = 0.1          # Hebbian 学习率
    SPREAD_DECAY = 0.7           # 激活扩散衰减
    SPREAD_THRESHOLD = 0.3       # 激活扩散阈值
    PRUNE_THRESHOLD = 0.1        # 边剪枝阈值
    RRF_K = 60                   # RRF 融合参数
    
    def __init__(self, plugin: Any):
        self.plugin = plugin
        self._db: Optional[sqlite3.Connection] = None
        self._initialized = False
        self._chroma_collection = None
    
    def _get_config(self):
        """获取配置。"""
        from .config import LifeEngineConfig
        config = getattr(self.plugin, "config", None)
        if isinstance(config, LifeEngineConfig):
            return config
        return LifeEngineConfig()
    
    def _get_db_path(self) -> Path:
        """获取数据库路径。"""
        config = self._get_config()
        workspace = Path(config.settings.workspace_path)
        return workspace / ".memory" / "memory.db"
    
    def _get_vector_db_path(self) -> str:
        """获取向量数据库路径。"""
        config = self._get_config()
        workspace = Path(config.settings.workspace_path)
        return str(workspace / ".memory" / "chroma")
    
    async def initialize(self) -> None:
        """初始化记忆服务。"""
        if self._initialized:
            return
        
        db_path = self._get_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        
        self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        
        await self._create_tables()
        self._initialized = True
        logger.info(f"记忆服务初始化完成，数据库: {db_path}")
    
    async def _create_tables(self) -> None:
        """创建数据库表。"""
        cursor = self._db.cursor()
        
        # 记忆节点表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memory_nodes (
                node_id TEXT PRIMARY KEY,
                node_type TEXT NOT NULL,
                file_path TEXT,
                content_hash TEXT,
                title TEXT,
                activation_strength REAL DEFAULT 1.0,
                access_count INTEGER DEFAULT 0,
                last_accessed_at REAL,
                emotional_valence REAL DEFAULT 0.0,
                emotional_arousal REAL DEFAULT 0.0,
                importance REAL DEFAULT 0.5,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                embedding_synced INTEGER DEFAULT 0
            )
        """)
        
        # 记忆边表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memory_edges (
                edge_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                edge_type TEXT NOT NULL,
                weight REAL DEFAULT 0.5,
                base_strength REAL DEFAULT 0.5,
                reinforcement REAL DEFAULT 0.0,
                activation_count INTEGER DEFAULT 0,
                last_activated_at REAL,
                reason TEXT,
                created_at REAL NOT NULL,
                bidirectional INTEGER DEFAULT 1,
                FOREIGN KEY (source_id) REFERENCES memory_nodes(node_id) ON DELETE CASCADE,
                FOREIGN KEY (target_id) REFERENCES memory_nodes(node_id) ON DELETE CASCADE,
                UNIQUE(source_id, target_id, edge_type)
            )
        """)
        
        # 索引
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_nodes_type ON memory_nodes(node_type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_nodes_activation ON memory_nodes(activation_strength DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_nodes_file_path ON memory_nodes(file_path)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_edges_source ON memory_edges(source_id, weight DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_edges_target ON memory_edges(target_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_edges_type ON memory_edges(edge_type)")
        
        # 全文搜索虚拟表（存储文件内容摘要）
        cursor.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
                node_id,
                title,
                content,
                tokenize='unicode61'
            )
        """)
        
        self._db.commit()
        logger.debug("记忆数据库表创建完成")
    
    # --------------------------------------------------------
    # 节点操作
    # --------------------------------------------------------
    
    def _generate_file_node_id(self, file_path: str) -> str:
        """根据文件路径生成节点 ID。"""
        return f"file:{hashlib.md5(file_path.encode()).hexdigest()[:12]}"
    
    def _generate_concept_node_id(self, concept: str) -> str:
        """根据概念名称生成节点 ID。"""
        return f"concept:{hashlib.md5(concept.encode()).hexdigest()[:12]}"
    
    def _compute_content_hash(self, content: str) -> str:
        """计算内容 hash。"""
        return hashlib.sha256(content.encode()).hexdigest()[:16]
    
    async def get_or_create_file_node(
        self, 
        file_path: str, 
        title: str = "",
        content: str = ""
    ) -> MemoryNode:
        """获取或创建文件节点。"""
        node_id = self._generate_file_node_id(file_path)
        
        cursor = self._db.cursor()
        cursor.execute("SELECT * FROM memory_nodes WHERE node_id = ?", (node_id,))
        row = cursor.fetchone()
        
        now = time.time()
        content_hash = self._compute_content_hash(content) if content else None
        
        if row:
            # 已存在，检查是否需要更新
            node = self._row_to_node(row)
            
            if content_hash and node.content_hash != content_hash:
                # 内容变化，更新
                cursor.execute("""
                    UPDATE memory_nodes 
                    SET content_hash = ?, title = ?, updated_at = ?, embedding_synced = 0
                    WHERE node_id = ?
                """, (content_hash, title or node.title, now, node_id))
                self._db.commit()
                node.content_hash = content_hash
                node.title = title or node.title
                node.updated_at = now
                node.embedding_synced = False
                
                # 更新 FTS
                await self._update_fts(node_id, title, content[:2000])
            
            return node
        
        # 创建新节点
        node = MemoryNode(
            node_id=node_id,
            node_type=NodeType.FILE,
            file_path=file_path,
            content_hash=content_hash,
            title=title,
            created_at=now,
            updated_at=now
        )
        
        cursor.execute("""
            INSERT INTO memory_nodes 
            (node_id, node_type, file_path, content_hash, title, 
             activation_strength, access_count, last_accessed_at,
             emotional_valence, emotional_arousal, importance,
             created_at, updated_at, embedding_synced)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            node.node_id, node.node_type.value, node.file_path, node.content_hash, node.title,
            node.activation_strength, node.access_count, node.last_accessed_at,
            node.emotional_valence, node.emotional_arousal, node.importance,
            node.created_at, node.updated_at, 0
        ))
        self._db.commit()
        
        # 添加到 FTS
        if content:
            await self._update_fts(node_id, title, content[:2000])
        
        logger.debug(f"创建文件节点: {file_path}")
        return node
    
    async def get_node_by_file_path(self, file_path: str) -> Optional[MemoryNode]:
        """根据文件路径获取节点。"""
        node_id = self._generate_file_node_id(file_path)
        cursor = self._db.cursor()
        cursor.execute("SELECT * FROM memory_nodes WHERE node_id = ?", (node_id,))
        row = cursor.fetchone()
        return self._row_to_node(row) if row else None
    
    def _row_to_node(self, row: sqlite3.Row) -> MemoryNode:
        """将数据库行转换为 MemoryNode。"""
        return MemoryNode(
            node_id=row["node_id"],
            node_type=NodeType(row["node_type"]),
            file_path=row["file_path"],
            content_hash=row["content_hash"],
            title=row["title"] or "",
            activation_strength=row["activation_strength"],
            access_count=row["access_count"],
            last_accessed_at=row["last_accessed_at"],
            emotional_valence=row["emotional_valence"],
            emotional_arousal=row["emotional_arousal"],
            importance=row["importance"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            embedding_synced=bool(row["embedding_synced"])
        )
    
    async def _update_fts(self, node_id: str, title: str, content: str) -> None:
        """更新全文搜索索引。"""
        cursor = self._db.cursor()
        # 先删除旧的
        cursor.execute("DELETE FROM memory_fts WHERE node_id = ?", (node_id,))
        # 插入新的
        cursor.execute(
            "INSERT INTO memory_fts (node_id, title, content) VALUES (?, ?, ?)",
            (node_id, title, content)
        )
        self._db.commit()
    
    async def increment_access(self, node_id: str) -> None:
        """增加节点访问计数并更新激活强度。"""
        now = time.time()
        cursor = self._db.cursor()
        cursor.execute("""
            UPDATE memory_nodes 
            SET access_count = access_count + 1,
                last_accessed_at = ?,
                activation_strength = MIN(1.0, activation_strength + 0.1)
            WHERE node_id = ?
        """, (now, node_id))
        self._db.commit()
    
    # --------------------------------------------------------
    # 边操作
    # --------------------------------------------------------
    
    async def create_or_update_edge(
        self,
        source_id: str,
        target_id: str,
        edge_type: EdgeType,
        reason: str = "",
        strength: float = 0.5,
        bidirectional: bool = True
    ) -> MemoryEdge:
        """创建或更新边。"""
        cursor = self._db.cursor()
        
        # 检查是否已存在
        cursor.execute("""
            SELECT * FROM memory_edges 
            WHERE source_id = ? AND target_id = ? AND edge_type = ?
        """, (source_id, target_id, edge_type.value))
        row = cursor.fetchone()
        
        now = time.time()
        
        if row:
            # 更新现有边
            edge = self._row_to_edge(row)
            cursor.execute("""
                UPDATE memory_edges 
                SET weight = ?, reason = ?, last_activated_at = ?
                WHERE edge_id = ?
            """, (strength, reason or edge.reason, now, edge.edge_id))
            self._db.commit()
            edge.weight = strength
            edge.reason = reason or edge.reason
            edge.last_activated_at = now
            return edge
        
        # 创建新边
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
            bidirectional=bidirectional
        )
        
        cursor.execute("""
            INSERT INTO memory_edges 
            (edge_id, source_id, target_id, edge_type, weight, base_strength,
             reinforcement, activation_count, last_activated_at, reason, created_at, bidirectional)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            edge.edge_id, edge.source_id, edge.target_id, edge.edge_type.value,
            edge.weight, edge.base_strength, edge.reinforcement,
            edge.activation_count, edge.last_activated_at, edge.reason,
            edge.created_at, 1 if edge.bidirectional else 0
        ))
        
        # 如果是双向边，也创建反向边
        if bidirectional and edge_type not in (EdgeType.CAUSES, EdgeType.CONTINUES, EdgeType.MENTIONS):
            reverse_edge_id = str(uuid.uuid4())[:8]
            cursor.execute("""
                INSERT OR IGNORE INTO memory_edges 
                (edge_id, source_id, target_id, edge_type, weight, base_strength,
                 reinforcement, activation_count, last_activated_at, reason, created_at, bidirectional)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                reverse_edge_id, target_id, source_id, edge.edge_type.value,
                edge.weight, edge.base_strength, edge.reinforcement,
                edge.activation_count, edge.last_activated_at, edge.reason,
                edge.created_at, 1
            ))
        
        self._db.commit()
        logger.debug(f"创建边: {source_id} --[{edge_type.value}]--> {target_id}")
        return edge
    
    async def get_edges_from(self, node_id: str, min_weight: float = 0.0) -> List[MemoryEdge]:
        """获取从指定节点出发的边。"""
        cursor = self._db.cursor()
        cursor.execute("""
            SELECT * FROM memory_edges 
            WHERE source_id = ? AND weight >= ?
            ORDER BY weight DESC
        """, (node_id, min_weight))
        return [self._row_to_edge(row) for row in cursor.fetchall()]
    
    async def get_edges_to(self, node_id: str, min_weight: float = 0.0) -> List[MemoryEdge]:
        """获取指向指定节点的边。"""
        cursor = self._db.cursor()
        cursor.execute("""
            SELECT * FROM memory_edges 
            WHERE target_id = ? AND weight >= ?
            ORDER BY weight DESC
        """, (node_id, min_weight))
        return [self._row_to_edge(row) for row in cursor.fetchall()]
    
    def _row_to_edge(self, row: sqlite3.Row) -> MemoryEdge:
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
            bidirectional=bool(row["bidirectional"])
        )
    
    async def delete_edge(self, source_path: str, target_path: str, edge_type: Optional[EdgeType] = None) -> bool:
        """删除边。"""
        source_id = self._generate_file_node_id(source_path)
        target_id = self._generate_file_node_id(target_path)
        
        cursor = self._db.cursor()
        if edge_type:
            cursor.execute("""
                DELETE FROM memory_edges 
                WHERE source_id = ? AND target_id = ? AND edge_type = ?
            """, (source_id, target_id, edge_type.value))
            # 删除反向边
            cursor.execute("""
                DELETE FROM memory_edges 
                WHERE source_id = ? AND target_id = ? AND edge_type = ?
            """, (target_id, source_id, edge_type.value))
        else:
            cursor.execute("""
                DELETE FROM memory_edges 
                WHERE (source_id = ? AND target_id = ?) OR (source_id = ? AND target_id = ?)
            """, (source_id, target_id, target_id, source_id))
        
        deleted = cursor.rowcount > 0
        self._db.commit()
        return deleted
    
    # --------------------------------------------------------
    # 向量检索
    # --------------------------------------------------------
    
    async def _get_chroma_collection(self):
        """获取 ChromaDB collection。"""
        if self._chroma_collection is not None:
            return self._chroma_collection
        
        vector_db_path = self._get_vector_db_path()
        vector_service = get_vector_db_service(vector_db_path)
        self._chroma_collection = await vector_service.get_or_create_collection(
            "life_memory"
        )
        return self._chroma_collection
    
    async def _embed_text(self, text: str) -> List[float]:
        """生成文本的 embedding 向量。"""
        try:
            model_set = get_model_set_by_task("embedding")
            request = create_embedding_request(
                model_set=model_set,
                request_name="life_memory_embedding",
                inputs=[text]
            )
            response = await request.send()
            embeddings = getattr(response, "embeddings", None) or []
            if not embeddings:
                raise RuntimeError("Embedding 请求返回为空")
            return [float(v) for v in embeddings[0]]
        except Exception as e:
            logger.error(f"Embedding 生成失败: {e}")
            raise
    
    async def sync_embedding(self, file_path: str, content: str) -> None:
        """同步文件的 embedding 到向量数据库。"""
        node = await self.get_node_by_file_path(file_path)
        if not node:
            return
        
        try:
            embedding = await self._embed_text(content[:3000])  # 限制长度
            collection = await self._get_chroma_collection()
            
            collection.upsert(
                ids=[node.node_id],
                embeddings=[embedding],
                documents=[content[:500]],
                metadatas=[{
                    "file_path": file_path,
                    "title": node.title,
                    "created_at": node.created_at
                }]
            )
            
            # 标记已同步
            cursor = self._db.cursor()
            cursor.execute(
                "UPDATE memory_nodes SET embedding_synced = 1 WHERE node_id = ?",
                (node.node_id,)
            )
            self._db.commit()
            logger.debug(f"已同步 embedding: {file_path}")
        except Exception as e:
            logger.error(f"同步 embedding 失败 ({file_path}): {e}")
    
    async def vector_search(self, query: str, top_k: int = 10) -> List[Tuple[str, float]]:
        """向量相似度检索。返回 (node_id, similarity) 列表。"""
        try:
            query_embedding = await self._embed_text(query)
            collection = await self._get_chroma_collection()
            
            results = collection.query(
                query_embeddings=[query_embedding],
                n_results=top_k,
                include=["distances"]
            )
            
            if not results["ids"] or not results["ids"][0]:
                return []
            
            # ChromaDB 返回的是距离，转换为相似度
            pairs = []
            for node_id, distance in zip(results["ids"][0], results["distances"][0]):
                similarity = 1.0 / (1.0 + distance)  # 转换为相似度
                pairs.append((node_id, similarity))
            
            return pairs
        except Exception as e:
            logger.error(f"向量检索失败: {e}")
            return []
    
    # --------------------------------------------------------
    # 全文检索
    # --------------------------------------------------------
    
    async def fts_search(self, query: str, top_k: int = 10) -> List[Tuple[str, float]]:
        """全文搜索。返回 (node_id, score) 列表。"""
        cursor = self._db.cursor()
        
        # 使用 FTS5 的 BM25 排序
        cursor.execute("""
            SELECT node_id, bm25(memory_fts) as score
            FROM memory_fts
            WHERE memory_fts MATCH ?
            ORDER BY score
            LIMIT ?
        """, (query, top_k))
        
        results = []
        for row in cursor.fetchall():
            # BM25 返回负数，绝对值越大越相关
            score = abs(row["score"]) / 10.0  # 归一化
            results.append((row["node_id"], min(score, 1.0)))
        
        return results
    
    # --------------------------------------------------------
    # 混合检索 + 联想
    # --------------------------------------------------------
    
    async def search_memory(
        self,
        query: str,
        top_k: int = 5,
        enable_association: bool = True,
        file_types: Optional[List[str]] = None,
        time_range_days: int = 0
    ) -> List[SearchResult]:
        """
        混合检索 + 联想。
        
        1. 关键词检索 (FTS5)
        2. 语义检索 (ChromaDB)
        3. RRF 融合
        4. 激活扩散联想
        5. 强化共同激活的边
        """
        # Step 1 & 2: 并行执行关键词和语义检索
        fts_results = await self.fts_search(query, top_k * 2)
        vector_results = await self.vector_search(query, top_k * 2)
        
        # Step 3: RRF 融合
        seed_scores = self._rrf_fusion(fts_results, vector_results)
        
        # 过滤文件类型和时间范围
        if file_types or time_range_days > 0:
            seed_scores = await self._filter_results(seed_scores, file_types, time_range_days)
        
        # 取 top-k 作为种子
        seeds = seed_scores[:top_k]
        seed_ids = [node_id for node_id, _ in seeds]
        
        # Step 4: 激活扩散联想
        associated = []
        if enable_association and seeds:
            associated = await self.spread_activation(seed_ids, max_depth=2)
        
        # Step 5: 更新访问计数并强化边
        for node_id, _ in seeds:
            await self.increment_access(node_id)
        
        if len(seed_ids) > 1:
            await self._reinforce_coactivated(seed_ids)
        
        # 构建结果
        results = []
        
        # 直接命中
        for node_id, score in seeds:
            node = await self._get_node_by_id(node_id)
            if node and node.file_path:
                results.append(SearchResult(
                    file_path=node.file_path,
                    title=node.title,
                    snippet=await self._get_snippet(node_id),
                    relevance=score,
                    source="direct"
                ))
        
        # 联想结果
        for node_id, score, path, reason in associated:
            node = await self._get_node_by_id(node_id)
            if node and node.file_path:
                # 避免重复
                if any(r.file_path == node.file_path for r in results):
                    continue
                results.append(SearchResult(
                    file_path=node.file_path,
                    title=node.title,
                    snippet=await self._get_snippet(node_id),
                    relevance=score * 0.8,  # 联想结果稍微降权
                    source="associated",
                    association_path=path,
                    association_reason=reason
                ))
        
        return results[:top_k * 2]  # 返回更多结果供选择
    
    def _rrf_fusion(
        self, 
        fts_results: List[Tuple[str, float]], 
        vector_results: List[Tuple[str, float]]
    ) -> List[Tuple[str, float]]:
        """Reciprocal Rank Fusion 融合。"""
        scores: Dict[str, float] = {}
        
        # FTS 结果
        for rank, (node_id, _) in enumerate(fts_results):
            scores[node_id] = scores.get(node_id, 0) + 1.0 / (self.RRF_K + rank + 1)
        
        # Vector 结果
        for rank, (node_id, _) in enumerate(vector_results):
            scores[node_id] = scores.get(node_id, 0) + 1.0 / (self.RRF_K + rank + 1)
        
        # 排序
        return sorted(scores.items(), key=lambda x: -x[1])
    
    async def _filter_results(
        self, 
        results: List[Tuple[str, float]], 
        file_types: Optional[List[str]],
        time_range_days: int
    ) -> List[Tuple[str, float]]:
        """过滤结果。"""
        filtered = []
        now = time.time()
        cutoff = now - time_range_days * 86400 if time_range_days > 0 else 0
        
        for node_id, score in results:
            node = await self._get_node_by_id(node_id)
            if not node:
                continue
            
            # 时间过滤
            if cutoff > 0 and node.created_at < cutoff:
                continue
            
            # 类型过滤（根据文件路径判断）
            if file_types and node.file_path:
                path_lower = node.file_path.lower()
                match = False
                for ft in file_types:
                    if ft in path_lower or path_lower.startswith(ft):
                        match = True
                        break
                if not match:
                    continue
            
            filtered.append((node_id, score))
        
        return filtered
    
    async def _get_node_by_id(self, node_id: str) -> Optional[MemoryNode]:
        """根据 ID 获取节点。"""
        cursor = self._db.cursor()
        cursor.execute("SELECT * FROM memory_nodes WHERE node_id = ?", (node_id,))
        row = cursor.fetchone()
        return self._row_to_node(row) if row else None
    
    async def _get_snippet(self, node_id: str) -> str:
        """获取节点内容摘要。"""
        cursor = self._db.cursor()
        cursor.execute("SELECT content FROM memory_fts WHERE node_id = ?", (node_id,))
        row = cursor.fetchone()
        if row:
            content = row["content"]
            return content[:150] + "..." if len(content) > 150 else content
        return ""
    
    # --------------------------------------------------------
    # 激活扩散
    # --------------------------------------------------------
    
    async def spread_activation(
        self,
        seed_ids: List[str],
        max_depth: int = 2,
        max_results: int = 10
    ) -> List[Tuple[str, float, List[str], str]]:
        """
        激活扩散联想。
        
        返回: [(node_id, activation_score, path, reason), ...]
        """
        activation: Dict[str, float] = {seed: 1.0 for seed in seed_ids}
        paths: Dict[str, List[str]] = {seed: [seed] for seed in seed_ids}
        reasons: Dict[str, str] = {}
        visited = set(seed_ids)
        frontier = list(seed_ids)
        
        for depth in range(max_depth):
            next_frontier = []
            decay = self.SPREAD_DECAY ** (depth + 1)
            
            for node_id in frontier:
                current_activation = activation[node_id]
                edges = await self.get_edges_from(node_id, min_weight=self.SPREAD_THRESHOLD)
                
                for edge in edges:
                    neighbor = edge.target_id
                    if neighbor in visited:
                        continue
                    
                    # 计算传播的激活量
                    propagated = current_activation * edge.weight * decay
                    
                    if propagated >= self.SPREAD_THRESHOLD:
                        if neighbor not in activation:
                            activation[neighbor] = 0
                            paths[neighbor] = paths[node_id] + [neighbor]
                            reasons[neighbor] = f"{edge.edge_type.value}: {edge.reason}"
                        
                        activation[neighbor] += propagated
                        next_frontier.append(neighbor)
                        visited.add(neighbor)
            
            frontier = next_frontier
            if not frontier:
                break
        
        # 移除种子节点，返回联想到的节点
        for seed in seed_ids:
            activation.pop(seed, None)
        
        # 排序并返回
        sorted_items = sorted(activation.items(), key=lambda x: -x[1])[:max_results]
        
        return [
            (node_id, score, paths.get(node_id, []), reasons.get(node_id, ""))
            for node_id, score in sorted_items
        ]
    
    # --------------------------------------------------------
    # Hebbian 强化
    # --------------------------------------------------------
    
    async def _reinforce_coactivated(self, node_ids: List[str]) -> None:
        """
        强化共同激活的节点之间的边 (Hebbian Learning)。
        """
        cursor = self._db.cursor()
        now = time.time()
        
        for i, node_a in enumerate(node_ids):
            for node_b in node_ids[i+1:]:
                # 查找或创建 ASSOCIATES 边
                cursor.execute("""
                    SELECT * FROM memory_edges 
                    WHERE source_id = ? AND target_id = ? AND edge_type = ?
                """, (node_a, node_b, EdgeType.ASSOCIATES.value))
                row = cursor.fetchone()
                
                if row:
                    # 更新现有边
                    old_weight = row["weight"]
                    # Hebbian: Δw = α * (1 - w)
                    delta = self.LEARNING_RATE * (1 - old_weight)
                    new_weight = min(old_weight + delta, 1.0)
                    
                    cursor.execute("""
                        UPDATE memory_edges 
                        SET weight = ?, reinforcement = reinforcement + ?, 
                            activation_count = activation_count + 1, last_activated_at = ?
                        WHERE edge_id = ?
                    """, (new_weight, delta, now, row["edge_id"]))
                else:
                    # 创建新边
                    edge_id = str(uuid.uuid4())[:8]
                    cursor.execute("""
                        INSERT INTO memory_edges 
                        (edge_id, source_id, target_id, edge_type, weight, base_strength,
                         reinforcement, activation_count, last_activated_at, reason, created_at, bidirectional)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        edge_id, node_a, node_b, EdgeType.ASSOCIATES.value,
                        0.2, 0.2, 0.0, 1, now, "共同检索激活", now, 1
                    ))
        
        self._db.commit()
    
    # --------------------------------------------------------
    # 遗忘与衰减
    # --------------------------------------------------------
    
    def compute_memory_strength(self, node: MemoryNode) -> float:
        """
        计算记忆强度，结合 Ebbinghaus 遗忘曲线和多种保护因素。
        """
        if not node.last_accessed_at:
            return node.activation_strength
        
        now = time.time()
        days_since = (now - node.last_accessed_at) / 86400
        
        # 基础时间衰减 (Ebbinghaus-inspired)
        time_decay = math.exp(-self.DECAY_LAMBDA * days_since)
        
        # 提取练习效应 (Testing Effect)
        retrieval_bonus = math.log(1 + node.access_count) * 0.1
        
        # 情感保护 (Emotional Enhancement)
        emotional_shield = node.emotional_arousal * 0.2
        
        # 重要性保护
        importance_shield = node.importance * 0.1
        
        # 最终强度
        strength = time_decay + retrieval_bonus + emotional_shield + importance_shield
        return min(max(strength, 0.0), 1.0)
    
    async def apply_decay(self) -> int:
        """
        应用遗忘衰减（定期任务）。
        返回更新的节点数。
        """
        cursor = self._db.cursor()
        cursor.execute("SELECT * FROM memory_nodes")
        rows = cursor.fetchall()
        
        updated = 0
        for row in rows:
            node = self._row_to_node(row)
            new_strength = self.compute_memory_strength(node)
            
            if abs(new_strength - node.activation_strength) > 0.01:
                cursor.execute(
                    "UPDATE memory_nodes SET activation_strength = ? WHERE node_id = ?",
                    (new_strength, node.node_id)
                )
                updated += 1
        
        # 边衰减
        cursor.execute("SELECT * FROM memory_edges WHERE edge_type = ?", (EdgeType.ASSOCIATES.value,))
        for row in cursor.fetchall():
            edge = self._row_to_edge(row)
            if edge.last_activated_at:
                days_since = (time.time() - edge.last_activated_at) / 86400
                decay_factor = math.exp(-self.DECAY_LAMBDA * days_since)
                new_weight = edge.base_strength + edge.reinforcement * decay_factor
                
                if new_weight < self.PRUNE_THRESHOLD:
                    # 剪枝
                    cursor.execute("DELETE FROM memory_edges WHERE edge_id = ?", (edge.edge_id,))
                elif abs(new_weight - edge.weight) > 0.01:
                    cursor.execute(
                        "UPDATE memory_edges SET weight = ? WHERE edge_id = ?",
                        (new_weight, edge.edge_id)
                    )
        
        self._db.commit()
        logger.info(f"遗忘衰减完成，更新了 {updated} 个节点")
        return updated
    
    # --------------------------------------------------------
    # 获取关联图谱
    # --------------------------------------------------------
    
    async def get_file_relations(
        self, 
        file_path: str, 
        depth: int = 1,
        min_strength: float = 0.2
    ) -> Dict[str, Any]:
        """获取文件的关联图谱。"""
        node = await self.get_node_by_file_path(file_path)
        if not node:
            return {"error": f"未找到文件: {file_path}"}
        
        relations = {
            "center": {
                "file_path": file_path,
                "title": node.title,
                "activation_strength": node.activation_strength,
                "access_count": node.access_count
            },
            "outgoing": [],
            "incoming": []
        }
        
        # 出边
        out_edges = await self.get_edges_from(node.node_id, min_strength)
        for edge in out_edges:
            target = await self._get_node_by_id(edge.target_id)
            if target and target.file_path:
                relations["outgoing"].append({
                    "file_path": target.file_path,
                    "title": target.title,
                    "relation_type": edge.edge_type.value,
                    "strength": edge.weight,
                    "reason": edge.reason
                })
        
        # 入边
        in_edges = await self.get_edges_to(node.node_id, min_strength)
        for edge in in_edges:
            source = await self._get_node_by_id(edge.source_id)
            if source and source.file_path:
                relations["incoming"].append({
                    "file_path": source.file_path,
                    "title": source.title,
                    "relation_type": edge.edge_type.value,
                    "strength": edge.weight,
                    "reason": edge.reason
                })
        
        return relations
    
    # --------------------------------------------------------
    # 统计信息
    # --------------------------------------------------------
    
    async def get_stats(self) -> Dict[str, Any]:
        """获取记忆系统统计信息。"""
        cursor = self._db.cursor()
        
        cursor.execute("SELECT COUNT(*) as cnt FROM memory_nodes WHERE node_type = ?", (NodeType.FILE.value,))
        file_count = cursor.fetchone()["cnt"]
        
        cursor.execute("SELECT COUNT(*) as cnt FROM memory_nodes WHERE node_type = ?", (NodeType.CONCEPT.value,))
        concept_count = cursor.fetchone()["cnt"]
        
        cursor.execute("SELECT COUNT(*) as cnt FROM memory_edges")
        edge_count = cursor.fetchone()["cnt"]
        
        cursor.execute("SELECT AVG(activation_strength) as avg FROM memory_nodes")
        avg_activation = cursor.fetchone()["avg"] or 0
        
        return {
            "file_nodes": file_count,
            "concept_nodes": concept_count,
            "total_edges": edge_count,
            "avg_activation": round(avg_activation, 3)
        }
