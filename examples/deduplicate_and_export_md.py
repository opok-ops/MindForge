"""
MindForge v5.0.4 - 记忆去重与 Markdown 导出演示

本示例展示两个新功能：
1. deduplicate() - 检测并合并相似/重复记忆
2. export_as_markdown() - 将记忆导出为可读性强的 Markdown 文档
"""

import os
import sys
import tempfile
from pathlib import Path

# 添加项目根目录到 path
sys.path.insert(0, str(Path(__file__).parent.parent))

from mindforge.core import MindForge, MemoryConfig, Importance


def main():
    # 使用临时目录做演示
    tmpdir = tempfile.mkdtemp()
    try:
        db_path = os.path.join(tmpdir, "demo.db")
        cm = MindForge(config=MemoryConfig(db_path=db_path, encrypted=False))

        print("=" * 60)
        print("MindForge v5.0.4 - 去重与 Markdown 导出演示")
        print("=" * 60)

        # === 添加一些记忆，故意制造重复 ===
        print("\n📝 添加记忆（含重复）...")
        cm.add(content="Python 是最流行的编程语言之一", category="tech")
        cm.add(content="Python 是最流行的编程语言之一。", category="tech")  # 几乎重复
        cm.add(content="python 是最流行的编程语言之一", category="tech")    # 大小写不同
        cm.add(content="JavaScript 主要用于前端开发", category="tech")
        cm.add(content="JavaScript 主要用于前端开发", category="tech")     # 完全重复
        cm.add(content="今天天气不错", category="daily")
        cm.add(content="今天天气真好", category="daily")                  # 相似
        cm.add(content="用户喜欢用 Python 写脚本", category="preferences",
               importance=Importance.HIGH, starred=True)

        stats = cm.stats()
        print(f"   共添加 {stats['total']} 条记忆")

        # === 1. 试运行去重 ===
        print("\n" + "=" * 60)
        print("🔍 步骤 1：试运行去重（dry_run=True）")
        print("=" * 60)
        result = cm.deduplicate(dry_run=True, similarity_threshold=0.85)
        print(f"   发现重复组：{result['duplicates_found']}")
        print(f"   将删除：    {result['would_remove']} 条")
        print("   详情：")
        for i, d in enumerate(result["details"], 1):
            print(f"     [{i}] 分类={d['category']} 相似度={d['similarity']}")
            print(f"         保留: {d['keeper_preview'][:50]}")
            print(f"         删除: {d['loser_preview'][:50]}")

        # === 2. 实际执行去重 ===
        print("\n" + "=" * 60)
        print("🗑️  步骤 2：实际执行去重（dry_run=False）")
        print("=" * 60)
        result = cm.deduplicate(dry_run=False, similarity_threshold=0.85)
        print(f"   实际删除：{result['removed']} 条")

        stats = cm.stats()
        print(f"   去重后剩余：{stats['total']} 条记忆")

        # === 3. 导出为 Markdown ===
        print("\n" + "=" * 60)
        print("📄 步骤 3：导出为 Markdown")
        print("=" * 60)
        md_path = os.path.join(tmpdir, "memory_export.md")
        out = cm.export_as_markdown(output_path=md_path)
        content = out.read_text(encoding="utf-8")
        size = out.stat().st_size
        print(f"   导出文件：{out}")
        print(f"   文件大小：{size} 字节")
        print("   内容预览（前 500 字）：")
        print("-" * 60)
        print(content[:500])
        print("-" * 60)

        # === 4. 仅导出收藏的记忆 ===
        print("\n⭐ 步骤 4：仅导出收藏的记忆")
        starred_md = os.path.join(tmpdir, "starred.md")
        out2 = cm.export_as_markdown(output_path=starred_md, starred_only=True)
        content2 = out2.read_text(encoding="utf-8")
        print(f"   导出文件：{out2}")
        print(f"   文件大小：{out2.stat().st_size} 字节")
        print("   内容预览：")
        print("-" * 60)
        print(content2[:400])
        print("-" * 60)

        print("\n✅ 演示完成！")
    finally:
        # 关闭数据库连接，避免 Windows 文件锁
        try:
            cm.close()
        except Exception:
            pass
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
