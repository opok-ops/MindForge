"""
MindForge v5.0 基础使用示例
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from mindforge.core import MindForge, PrivacyLevel, Importance, MemoryLayer


def basic_usage():
    """基础使用示例"""
    print("=" * 60)
    print("MindForge v5.0 - 基础使用示例")
    print("=" * 60)

    memory = MindForge(
        db_path="./data/example.db",
        encrypted=False,
    )

    print("\n1. 添加记忆...")
    entries = [
        memory.add(
            content="今天学习了 MindForge v5.0 的四层记忆架构",
            category="learning",
            tags=["MindForge", "记忆系统", "AI"],
            importance=Importance.HIGH,
            layer=MemoryLayer.SHORT_TERM,
        ),
        memory.add(
            content="Python 装饰器可以用来增强函数功能",
            category="tech",
            tags=["python", "装饰器", "编程"],
            importance=Importance.MEDIUM,
            layer=MemoryLayer.LONG_TERM,
        ),
        memory.add(
            content="用户喜欢用简洁的代码风格，偏好函数式编程",
            category="preferences",
            tags=["用户偏好", "编程风格"],
            privacy=PrivacyLevel.PRIVATE,
            importance=Importance.HIGH,
            layer=MemoryLayer.LONG_TERM,
        ),
        memory.add(
            content="PostgreSQL 查询优化技巧：创建合适的索引",
            category="tech",
            tags=["数据库", "postgresql", "优化"],
            importance=Importance.HIGH,
            layer=MemoryLayer.LONG_TERM,
        ),
    ]

    for entry in entries:
        print(f"   ✓ 添加: {entry.preview[:40]}...")

    print("\n2. 统计信息...")
    stats = memory.stats()
    print(f"   总记忆数: {stats['total']}")
    print(f"   分类: {stats.get('top_categories', {})}")

    print("\n3. 搜索记忆 'Python'...")
    results = memory.search("Python", max_results=5)
    print(f"   找到 {results.total_found} 条相关记忆")
    print(f"   耗时: {results.query_time_ms}ms")
    for i, chunk in enumerate(results.chunks, 1):
        print(f"   {i}. [{chunk.category}] {chunk.content[:50]}... (相关度: {chunk.relevance_score:.3f})")

    print("\n4. 记忆层级分布...")
    by_layer = stats.get('by_layer', {})
    for layer, count in by_layer.items():
        print(f"   {layer}: {count} 条")

    print("\n✅ 基础示例完成！")
    return memory


def knowledge_graph_example():
    """知识图谱示例"""
    print("\n" + "=" * 60)
    print("知识图谱示例")
    print("=" * 60)

    from mindforge.modules import KnowledgeGraph

    kg = KnowledgeGraph()

    print("\n1. 从文本提取实体...")
    text = "我正在用 Python 和 PostgreSQL 开发一个 AI Agent 项目"
    entities = kg.extract_entities(text)
    print(f"   文本: {text}")
    print(f"   提取到 {len(entities)} 个实体:")
    for name, etype in entities:
        print(f"     - {name} ({etype})")

    print("\n2. 添加实体和关系...")
    kg.add_relation("Python", "编程开发", "is_a", weight=0.9)
    kg.add_relation("PostgreSQL", "数据库", "is_a", weight=0.9)
    kg.add_relation("Python", "PostgreSQL", "uses", weight=0.7)
    kg.add_relation("AI Agent", "Python", "developed_with", weight=0.8)

    print("\n3. 查询与 'Python' 相关的实体...")
    related = kg.get_related_entities("Python", depth=2)
    for name, rel_type, weight in related[:5]:
        print(f"   - {name}  [{rel_type}]  (权重: {weight:.2f})")

    stats = kg.get_entity_stats()
    print("\n4. 图谱统计...")
    print(f"   实体总数: {stats['total_entities']}")
    print(f"   关系总数: {stats['total_relations']}")

    print("\n✅ 知识图谱示例完成！")


def personality_example(memory):
    """人格化示例"""
    print("\n" + "=" * 60)
    print("人格化引擎示例")
    print("=" * 60)

    from mindforge.modules import PersonalityEngine

    pe = PersonalityEngine(memory.storage)

    print("\n1. 学习用户交互...")
    interactions = [
        ("你好，请帮我写个Python脚本", "好的，这是一个简洁的Python脚本示例..."),
        ("这个代码解释一下", "这段代码使用了装饰器模式来增强功能..."),
        ("哈哈，你太厉害了", "谢谢夸奖！很高兴能帮到你 😊"),
    ]

    for user_msg, response in interactions:
        pe.learn_from_interaction("user_demo", user_msg, response)
        print(f"   ✓ 学习交互: {user_msg[:20]}...")

    print("\n2. 用户画像...")
    profile = pe.get_profile("user_demo")
    print(f"   交互次数: {profile.total_interactions}")

    print("\n3. 推荐交流风格...")
    style = pe.get_recommended_style("user_demo")
    for key, value in style.items():
        print(f"   {key}: {value}")

    print("\n4. 兴趣主题...")
    interests = pe.get_top_interests("user_demo", 3)
    for topic, score in interests:
        bar = "█" * int(score * 20)
        print(f"   {topic:<15} {bar} {score:.2f}")

    print("\n✅ 人格化示例完成！")


if __name__ == "__main__":
    memory = basic_usage()
    knowledge_graph_example()
    personality_example(memory)

    print("\n" + "=" * 60)
    print("🎉 所有示例运行完成！")
    print("=" * 60)
