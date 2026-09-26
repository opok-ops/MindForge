# -*- coding: utf-8 -*-
"""v5.8.4 框架适配器回归测试

LangGraph BaseStore 适配（MindForgeStore）+ CrewAI Memory 适配（CrewAIMemory）。
零外部依赖：不 import langgraph/crewai，用真实 MindForge 实例验证转发语义。
"""
import os
import sys
import tempfile
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)


def _tmp():
    return tempfile.mkdtemp(prefix="mf_v584_", dir=_REPO)


def _mk_mf(name, encrypted=True):
    import core.mindforge as mf
    from core.types import MemoryConfig

    tmp = _tmp()
    eng = mf.MindForge(config=MemoryConfig(
        db_path=os.path.join(tmp, name + ".db"),
        key_file=os.path.join(tmp, name + ".key"), encrypted=encrypted))
    if encrypted:
        eng.init_with_password("test-pw-584")
    return eng


class TestLangGraphStoreAdapter(unittest.TestCase):
    """MindForgeStore：put/get/search/delete 语义"""

    def setUp(self):
        from adapters.langgraph_store import MindForgeStore
        self.mf = _mk_mf("lg")
        self.store = MindForgeStore(self.mf)

    def test_put_get_roundtrip(self):
        self.store.put(("users", "u1"), "prefs", {"theme": "dark"})
        item = self.store.get(("users", "u1"), "prefs")
        self.assertIsNotNone(item)
        self.assertEqual(item["value"], {"theme": "dark"})

    def test_put_is_idempotent_same_key(self):
        self.store.put(("users", "u1"), "prefs", {"v": 1})
        self.store.put(("users", "u1"), "prefs", {"v": 2})
        item = self.store.get(("users", "u1"), "prefs")
        self.assertIsNotNone(item)
        self.assertEqual(item["value"], {"v": 2})

    def test_search_with_query(self):
        self.store.put(("docs", "a"), "note1", {"body": "喜欢靠窗座位"})
        self.store.put(("docs", "a"), "note2", {"body": "讨厌早起"})
        hits = self.store.search(("docs", "a"), query="靠窗座位", limit=5)
        self.assertTrue(len(hits) >= 1)

    def test_delete(self):
        self.store.put(("users", "u2"), "k1", {"x": 1})
        self.assertIsNotNone(self.store.get(("users", "u2"), "k1"))
        self.store.delete(("users", "u2"), "k1")
        self.assertIsNone(self.store.get(("users", "u2"), "k1"))

    def test_namespace_isolation(self):
        self.store.put(("ns", "a"), "k", {"v": "AAA"})
        self.store.put(("ns", "b"), "k", {"v": "BBB"})
        item_a = self.store.get(("ns", "a"), "k")
        item_b = self.store.get(("ns", "b"), "k")
        self.assertIsNotNone(item_a)
        self.assertIsNotNone(item_b)
        self.assertEqual(item_a["value"], {"v": "AAA"})
        self.assertEqual(item_b["value"], {"v": "BBB"})

    def test_list_decrypts_in_encrypted_mode(self):
        """v5.8.6 审计修复：list() 在加密库下必须返回解密 value。"""
        self.store.put(("ns", "x"), "k1", {"v": "ONE"})
        self.store.put(("ns", "x"), "k2", {"v": "TWO"})
        items = self.store.list(("ns", "x"), limit=10)
        vals = sorted(i["value"].get("v") for i in items)
        self.assertEqual(vals, ["ONE", "TWO"])

    def test_get_no_prefix_cross_hit(self):
        """v5.8.8 加固：同 namespace 下 key 前缀相似时 get 不得串。"""
        self.store.put(("ns", "z"), "prefs", {"v": "MAIN"})
        self.store.put(("ns", "z"), "prefs_backup", {"v": "BACKUP"})
        self.assertEqual(self.store.get(("ns", "z"), "prefs")["value"], {"v": "MAIN"})
        self.assertEqual(self.store.get(("ns", "z"), "prefs_backup")["value"], {"v": "BACKUP"})


class TestCrewAIMemoryAdapter(unittest.TestCase):
    """CrewAIMemory：save/search/reset 语义"""

    def setUp(self):
        from adapters.crewai_memory import CrewAIMemory
        self.mf = _mk_mf("cr")
        self.mem = CrewAIMemory(self.mf, namespace="crew_travel")

    def test_save_search_roundtrip(self):
        self.mem.save("用户偏好靠窗座位", metadata={"trip": "tokyo"})
        hits = self.mem.search("靠窗座位", limit=5)
        self.assertTrue(len(hits) >= 1)
        self.assertIn("context", hits[0])
        self.assertIn("靠窗", hits[0]["context"])

    def test_reset_only_own_namespace(self):
        self.mem.save("crew 命名空间数据")
        self.mf.add("其他命名空间数据", category="other_app")
        self.mem.reset()
        self.assertEqual(len(self.mem.search("crew", limit=10)), 0)
        others = self.mf.list(category="other_app", limit=10)
        self.assertTrue(len(others) >= 1)

    def test_save_with_metadata_tags(self):
        self.mem.save("带元数据的记忆", metadata={"trip": "kyoto"})
        hits = self.mem.search("带元数据", limit=5)
        self.assertTrue(len(hits) >= 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
