"""
MindForge v5.0.3 - 批量删除与标签搜索示例
演示批量删除、按标签搜索和增强统计功能
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mindforge.core.mindforge import MindForge
from mindforge.core.types import Importance


def main():
    print("=" * 60)
    print("MindForge v5.0.3 - 批量删除与标签搜索示例")
    print("=" * 60)

    db_path = "./data/batch_ops_example.db"
    key_path = "./data/batch_ops_example.key"

    for p in [db_path, key_path]:
        if os.path.exists(p):
            os.remove(p)

    cm = MindForge(db_path=db_path, key_file=key_path, encrypted=False)

    print("\n【1】添加一批带标签的记忆")
    print("-" * 40)

    memories = [
        ("Python 装饰器详解", "study", ["python", "decorator", "advanced"]),
        ("Python 生成器使用技巧", "study", ["python", "generator", "advanced"]),
        ("JavaScript 异步编程", "study", ["javascript", "async", "frontend"]),
        ("React Hooks 最佳实践", "study", ["react", "frontend", "hooks"]),
        ("周末爬山记录", "life", ["outdoor", "weekend", "exercise"]),
        ("美食探店 - 火锅店", "life", ["food", "daily"]),
        ("产品需求评审会议", "work", ["meeting", "product"]),
        ("数据库备份方案", "work", ["db", "backup", "important"]),
        ("Vue3 组合式 API", "study", ["vue", "frontend"]),
        ("健身计划 - 第1周", "life", ["fitness", "exercise", "plan"]),
    ]

    for content, category, tags in memories:
        cm.add(
            content=content,
            category=category,
            tags=tags,
            importance=Importance.HIGH if "important" in tags else Importance.MEDIUM,
        )

    print(f"共添加 {len(memories)} 条记忆")

    print("\n【2】统计信息（增强版）")
    print("-" * 40)

    stats = cm.stats()
    print(f"总记忆数：{stats['total']}")
    print(f"⭐ 收藏数：{stats.get('starred_count', 0)}")
    print("热门标签：")
    for tag, count in stats.get("top_tags", {}).items():
        print(f"  #{tag}: {count}")

    print("\n【3】按标签搜索记忆")
    print("-" * 40)

    for tag in ["python", "frontend", "exercise"]:
        results = cm.search_by_tag(tag=tag)
        print(f"\n搜索 #{tag}：找到 {len(results)} 条")
        for entry in results[:3]:
            print(f"  - [{entry.category}] {entry.content[:40]}")

    print("\n【4】按标签 + 分类组合搜索")
    print("-" * 40)

    results = cm.search_by_tag(tag="frontend", category="study")
    print(f"搜索 #frontend + study：找到 {len(results)} 条")
    for entry in results:
        print(f"  - {entry.content[:50]}")

    print("\n【5】收藏部分记忆，再按收藏筛选")
    print("-" * 40)

    all_entries = cm.list(limit=5)
    for entry in all_entries[:3]:
        cm.star(entry.id)
        print(f"  ⭐ 收藏：{entry.content[:30]}...")

    stats = cm.stats()
    print(f"\n当前收藏数：{stats.get('starred_count', 0)}")

    starred_entries = cm.list(starred=True)
    print(f"收藏列表：{len(starred_entries)} 条")

    print("\n【6】批量删除（按分类）")
    print("-" * 40)

    life_count = len(cm.list(category="life"))
    print(f"删除前 life 分类有 {life_count} 条")

    deleted = cm.batch_delete(category="life")
    print(f"已删除 {deleted} 条 life 分类的记忆")

    stats = cm.stats()
    print(f"剩余总记忆数：{stats['total']}")

    print("\n【7】验证删除结果")
    print("-" * 40)

    remaining = cm.list(limit=20)
    print(f"剩余 {len(remaining)} 条记忆：")
    for entry in remaining:
        star_mark = "⭐" if entry.starred else "  "
        print(f"  {star_mark} [{entry.category}] {entry.content[:40]}...")

    print("\n" + "=" * 60)
    print("示例完成！批量删除和标签搜索功能正常工作 ✅")
    print("=" * 60)

    cm.close()

    for p in [db_path, key_path]:
        if os.path.exists(p):
            os.remove(p)


if __name__ == "__main__":
    main()
