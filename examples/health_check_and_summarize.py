"""
MindForge v5.0.5 - 健康检查与记忆摘要演示

本示例展示两个新功能：
1. health_check() - 全面体检数据库状态
2. summarize() - 生成记忆库摘要
"""

import os
import sys
import tempfile
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from mindforge.core import MindForge, MemoryConfig, Importance


def main():
    tmpdir = tempfile.mkdtemp()
    try:
        db_path = os.path.join(tmpdir, "demo.db")
        cm = MindForge(config=MemoryConfig(db_path=db_path, encrypted=False))

        print("=" * 60)
        print("MindForge v5.0.5 - 健康检查与记忆摘要演示")
        print("=" * 60)

        # === 添加一些测试数据 ===
        print("\n📝 添加测试数据...")
        cm.add(content="今天学习了 Python 的装饰器", category="learning",
               tags=["python", "decorator"], importance=Importance.HIGH)
        cm.add(content="用户喜欢用 Python 写脚本", category="preferences",
               tags=["python"], starred=True)
        cm.add(content="PostgreSQL 数据库优化技巧", category="tech",
               tags=["postgres", "db"], importance=Importance.MEDIUM)
        cm.add(content="JavaScript 异步编程最佳实践", category="tech",
               tags=["javascript", "async"])
        cm.add(content="读完《代码大全》第3章", category="reading",
               importance=Importance.LOW)
        cm.add(content="Python 性能分析工具 cProfile", category="tech",
               tags=["python", "performance"])

        stats = cm.stats()
        print(f"   共添加 {stats['total']} 条记忆")

        # === 1. 健康检查 ===
        print("\n" + "=" * 60)
        print("🩺 步骤 1：数据库健康检查")
        print("=" * 60)
        health = cm.health_check()

        status_icon = {"healthy": "✅", "warning": "⚠️", "critical": "🚨"}.get(health["status"], "❓")
        print(f"\n   状态：{status_icon} {health['status'].upper()}")
        print(f"   完整性检查：{health['integrity_check']}")
        print(f"   总记忆数：{health['total_memories']}")
        print(f"   数据库大小：{health['db_size_bytes']} 字节")
        print(f"   索引：{health['indexes']['found']}/{health['indexes']['expected']}")
        print(f"   孤立 FTS 记录：{health['fts_orphans']}")
        print(f"   孤立审计日志：{health['audit_orphans']}")
        print(f"   加密不一致：{health['encrypted_inconsistent']}")
        print("\n   建议：")
        for rec in health["recommendations"]:
            print(f"     • {rec}")

        # === 2. 记忆摘要 - 按分类 ===
        print("\n" + "=" * 60)
        print("📊 步骤 2：记忆摘要（按分类分组）")
        print("=" * 60)
        summary = cm.summarize(group_by="category")

        print(f"\n   总记忆数：{summary['total']}")
        print(f"   最近 7 天新增：{summary['recent_activity']['last_7d']}")
        print(f"   最近 30 天新增：{summary['recent_activity']['last_30d']}")

        print("\n   📂 分组详情：")
        for key, info in summary["grouped"].items():
            print(f"\n     ▸ {key} ({info['count']} 条)")
            print(f"       时间：{info['oldest']} ~ {info['latest']}")
            for s in info["samples"][:2]:
                print(f"       • {s[:60]}")

        if summary["top_tags"]:
            print("\n   🏷️  热门标签：")
            for tag, count in summary["top_tags"][:5]:
                print(f"     #{tag}: {count}")

        # === 3. 记忆摘要 - 按重要性 ===
        print("\n" + "=" * 60)
        print("📊 步骤 3：记忆摘要（按重要性分组）")
        print("=" * 60)
        summary2 = cm.summarize(group_by="importance")
        print("\n   分组：")
        for key, info in summary2["grouped"].items():
            print(f"     ▸ {key}: {info['count']} 条")

        # === 4. 模拟硬删除后再次健康检查 ===
        print("\n" + "=" * 60)
        print("🗑️  步骤 4：硬删除后健康检查（验证 FTS 同步修复）")
        print("=" * 60)
        first_id = cm.list(limit=1)[0].id
        print(f"   删除记忆 ID: {first_id[:16]}...")
        cm.delete(first_id, hard_delete=True, actor="demo", session_id="demo")

        health2 = cm.health_check()
        print(f"\n   删除后状态：{health2['status']}")
        print(f"   总记忆数：{health2['total_memories']}")
        print(f"   孤立 FTS 记录：{health2['fts_orphans']}（应为 0，证明修复生效）")

        print("\n✅ 演示完成！")
    finally:
        try:
            cm.close()
        except Exception:
            pass
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
