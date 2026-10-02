"""
MindForge v5.0.2 - 星标与时间范围筛选示例
演示如何使用收藏功能和时间范围搜索
"""
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mindforge.core.mindforge import MindForge
from mindforge.core.types import Importance


def main():
    print("=" * 60)
    print("MindForge v5.0.2 - 星标与时间范围筛选示例")
    print("=" * 60)

    db_path = "./data/starred_example.db"
    key_path = "./data/starred_example.key"

    for p in [db_path, key_path]:
        if os.path.exists(p):
            os.remove(p)

    cm = MindForge(db_path=db_path, key_file=key_path, encrypted=False)

    print("\n【1】添加一批记忆（部分收藏）")
    print("-" * 40)

    memories = [
        ("学习 Python 的基础知识", "study", ["python", "programming"], False),
        ("Python 高级特性：装饰器和生成器", "study", ["python", "advanced"], True),
        ("今天吃了火锅，很好吃", "life", ["food", "daily"], False),
        ("阅读《设计模式》第3章", "study", ["book", "patterns"], True),
        ("项目会议记录 - 产品需求评审", "work", ["meeting", "product"], False),
        ("重要：数据库备份方案", "work", ["db", "backup"], True),
        ("周末去爬山了", "life", ["outdoor", "weekend"], False),
    ]

    ids = []
    for content, category, tags, starred in memories:
        entry = cm.add(
            content=content,
            category=category,
            tags=tags,
            importance=Importance.HIGH if starred else Importance.MEDIUM,
            starred=starred,
        )
        ids.append(entry.id)
        star_mark = "⭐" if starred else "  "
        print(f"  {star_mark} [{category}] {content[:40]}...")

    print(f"\n共添加 {len(memories)} 条记忆")

    print("\n【2】列出所有收藏的记忆")
    print("-" * 40)

    starred_entries = cm.list(starred=True)
    print(f"收藏的记忆共 {len(starred_entries)} 条：")
    for entry in starred_entries:
        print(f"  ⭐ [{entry.category}] {entry.content[:50]}")

    print("\n【3】对某条记忆进行收藏/取消收藏")
    print("-" * 40)

    target_id = ids[0]
    print("  对第1条记忆进行收藏...")
    cm.star(target_id)
    entry = cm.get(target_id)
    print(f"  状态：{'⭐ 已收藏' if entry.starred else '未收藏'}")

    print("\n  取消收藏...")
    cm.unstar(target_id)
    entry = cm.get(target_id)
    print(f"  状态：{'⭐ 已收藏' if entry.starred else '未收藏'}")

    print("\n【4】按时间范围筛选")
    print("-" * 40)

    now = time.time()
    one_hour_ago = now - 3600
    one_hour_later = now + 3600

    recent = cm.list(created_after=one_hour_ago, created_before=one_hour_later)
    print(f"最近1小时内的记忆：{len(recent)} 条")
    for entry in recent:
        print(f"  - [{entry.category}] {entry.content[:40]}")

    future = cm.list(created_after=one_hour_later)
    print(f"\n1小时之后的记忆：{len(future)} 条（应该为0）")

    print("\n【5】组合筛选：收藏 + 分类")
    print("-" * 40)

    study_starred = cm.list(category="study", starred=True)
    print(f"学习分类中已收藏的记忆：{len(study_starred)} 条")
    for entry in study_starred:
        print(f"  ⭐ {entry.content[:50]}")

    print("\n" + "=" * 60)
    print("示例完成！星标和时间范围筛选功能正常工作 ✅")
    print("=" * 60)

    cm.close()

    for p in [db_path, key_path]:
        if os.path.exists(p):
            os.remove(p)


if __name__ == "__main__":
    main()
