#!/usr/bin/env python3
"""仿生记忆系统简单测试脚本。"""

import asyncio
import sys
from pathlib import Path

# 添加项目根目录
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

async def test_memory_service():
    """测试记忆服务基础功能。"""
    print("=" * 60)
    print("仿生记忆系统测试")
    print("=" * 60)
    
    # 测试导入
    print("\n1. 测试模块导入...")
    try:
        from plugins.life_engine.memory import (
            LifeMemoryService,
            MemoryNode,
            MemoryEdge,
            EdgeType,
            SearchResult,
        )
        print("   ✅ 模块导入成功")
    except ImportError as e:
        print(f"   ❌ 导入失败: {e}")
        return False
    
    # 创建临时测试目录
    import tempfile
    import shutil
    
    test_dir = Path(tempfile.mkdtemp(prefix="memory_test_"))
    print(f"\n2. 创建测试目录: {test_dir}")
    
    try:
        # 初始化服务
        print("\n3. 初始化记忆服务...")
        service = LifeMemoryService(test_dir)
        await service.initialize()
        print("   ✅ 记忆服务初始化成功")
        
        # 测试创建节点
        print("\n4. 测试创建文件节点...")
        node1 = await service.get_or_create_file_node(
            "diaries/2026-03-31.md",
            "今天的日记"
        )
        print(f"   ✅ 创建节点: {node1.node_id[:20]}... title='{node1.title}'")
        
        node2 = await service.get_or_create_file_node(
            "notes/学习规划.md",
            "学习规划"
        )
        print(f"   ✅ 创建节点: {node2.node_id[:20]}... title='{node2.title}'")
        
        # 测试创建边
        print("\n5. 测试创建关联边...")
        edge = await service.create_or_update_edge(
            source_id=node1.node_id,
            target_id=node2.node_id,
            edge_type=EdgeType.CAUSES,
            reason="日记中的想法启发了学习规划",
            strength=0.8,
            bidirectional=False
        )
        print(f"   ✅ 创建边: {edge.edge_type.value}, weight={edge.weight:.2f}")
        
        # 测试获取边
        print("\n6. 测试获取相邻边...")
        edges = await service.get_edges_from(node1.node_id)
        print(f"   ✅ 找到 {len(edges)} 条边")
        for e in edges:
            print(f"      - {e.edge_type.value}: {e.source_id[:10]}... → {e.target_id[:10]}...")
        
        # 测试节点统计
        print("\n7. 测试统计功能...")
        stats = await service.get_stats()
        print(f"   ✅ 文件节点数: {stats['file_nodes']}")
        print(f"   ✅ 边数: {stats['total_edges']}")
        print(f"   ✅ 平均激活度: {stats['avg_activation']}")
        
        # 测试遗忘曲线计算
        print("\n8. 测试遗忘曲线...")
        strength = service.compute_memory_strength(node1)
        print(f"   ✅ 节点记忆强度: {strength:.3f}")
        
        # 测试衰减
        print("\n9. 测试记忆衰减...")
        decay_count = await service.apply_decay()
        print(f"   ✅ 衰减更新节点: {decay_count}")
        
        print("\n" + "=" * 60)
        print("✅ 所有测试通过！")
        print("=" * 60)
        return True
        
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False
        
    finally:
        # 清理测试目录
        print(f"\n清理测试目录: {test_dir}")
        shutil.rmtree(test_dir, ignore_errors=True)


if __name__ == "__main__":
    success = asyncio.run(test_memory_service())
    sys.exit(0 if success else 1)
