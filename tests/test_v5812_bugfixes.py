# -*- coding: utf-8 -*-
"""v5.8.12 回归测试：搜索、召回、API 和 LangGraph 协议缺陷。"""
import os
import shutil
import tempfile
import unittest
from collections import namedtuple
from unittest.mock import patch

from MindForge import MindForge
from core.types import Importance, PrivacyLevel
from modules.recall import RecallConfig, RecallEngine

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class MindForgeCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_v5812_", dir=_REPO)
        self.mf = MindForge(db_path=os.path.join(self.tmp, "memory.db"),
                            encrypted=False)

    def tearDown(self):
        self.mf.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _handler(self, path, body=None, mindforge=None):
        from api.server import MindForgeAPIHandler

        handler = MindForgeAPIHandler.__new__(MindForgeAPIHandler)
        handler.mindforge = mindforge or self.mf
        handler.path = path
        handler.headers = {}
        handler.client_address = ("127.0.0.1", 0)
        handler._read_body = lambda: body
        handler._check_auth = lambda: True
        handler._check_rate_limit = lambda *args, **kwargs: True
        handler._extract_actor = lambda qs: ""
        responses = []
        handler._send_json = lambda payload, code=200: responses.append((code, payload))
        return handler, responses


class TestSearchRegressions(MindForgeCase):
    def test_negative_max_results_is_rejected(self):
        with self.assertRaises(ValueError):
            self.mf.search("anything", max_results=-1, use_embedding=False)

    def test_inaccessible_top_hit_does_not_hide_public_result(self):
        self.mf.add(
            "project project project confidential launch",
            privacy=PrivacyLevel.PRIVATE,
            source_agent="alice",
        )
        public = self.mf.add(
            "project public launch",
            privacy=PrivacyLevel.PUBLIC,
        )

        result = self.mf.search(
            "project", max_results=1, min_relevance=0.0, use_embedding=False
        )

        self.assertEqual([chunk.memory_id for chunk in result.chunks], [public.id])


class TestRecallRegressions(MindForgeCase):
    def test_excluded_category_and_minimum_importance_are_applied(self):
        self.mf.add(
            "recall needle sensitive",
            category="secret",
            importance=Importance.CRITICAL,
        )
        self.mf.add(
            "recall needle low priority",
            category="public",
            importance=Importance.LOW,
        )
        expected = self.mf.add(
            "recall needle high priority",
            category="public",
            importance=Importance.HIGH,
        )
        engine = RecallEngine(self.mf._storage, self.mf._index)

        result = engine.recall(
            "recall needle",
            config=RecallConfig(
                max_results=10,
                min_relevance=0.0,
                exclude_categories=["secret"],
                min_importance=Importance.HIGH,
                use_knowledge_graph=False,
                use_reranking=False,
            ),
        )

        self.assertEqual([chunk.memory_id for chunk in result.chunks], [expected.id])

    def test_session_recall_excludes_other_session_and_keeps_current_context(self):
        current = self.mf.add(
            "session A context about gardening",
            source_session="session-A",
        )
        self.mf.add(
            "searchable phrase only in session B",
            source_session="session-B",
        )
        engine = RecallEngine(self.mf._storage, self.mf._index)

        result = engine.get_session_relevant(
            "session-A", "searchable phrase", limit=10
        )

        self.assertTrue(result)
        self.assertEqual({entry.source_session for entry in result}, {"session-A"})
        self.assertIn(current.id, {entry.id for entry in result})


class TestRESTRegressions(MindForgeCase):
    def test_put_without_validity_fields_preserves_existing_window(self):
        entry = self.mf.add("bi-temporal fact", valid_from=1000.0, valid_to=5000.0)
        handler, responses = self._handler(
            f"/api/memories/{entry.id}", {"content": "updated fact"}
        )

        handler.do_PUT()

        self.assertEqual(responses[0][0], 200)
        updated = self.mf.get(entry.id)
        self.assertEqual(updated.valid_from, 1000.0)
        self.assertEqual(updated.valid_to, 5000.0)

    def test_conflict_reconcile_rejects_string_boolean(self):
        handler, responses = self._handler(
            "/api/conflicts/reconcile", {"auto": "false"}
        )
        with patch.object(self.mf, "reconcile_conflicts") as reconcile:
            handler.do_POST()

        self.assertEqual(responses[0][0], 400)
        reconcile.assert_not_called()

    def test_embedding_status_error_does_not_leak_exception(self):
        handler, responses = self._handler("/api/embedding/status")
        with patch.object(
            self.mf,
            "get_embedding_status",
            side_effect=RuntimeError("failed /secret/internal.db"),
        ):
            handler.do_GET()

        self.assertEqual(responses[0][0], 500)
        self.assertNotIn("/secret/internal.db", str(responses[0][1]))


class TestMCPRegressions(MindForgeCase):
    def test_conflict_reconcile_rejects_string_boolean(self):
        from mcp.server import h_conflict_reconcile

        with patch.object(self.mf, "reconcile_conflicts") as reconcile:
            result = h_conflict_reconcile(self.mf, {"auto": "false"})

        self.assertFalse(result["ok"])
        reconcile.assert_not_called()


class TestLangGraphNamespaceRegressions(MindForgeCase):
    def setUp(self):
        super().setUp()
        from adapters.langgraph_store import MindForgeStore
        self.store = MindForgeStore(self.mf)

    def test_prefix_search_returns_descendant_namespaces(self):
        self.store.put(("users", "u1"), "prefs", {"theme": "dark"})
        self.store.put(("users", "u2"), "prefs", {"theme": "light"})
        self.store.put(("teams", "t1"), "prefs", {"theme": "blue"})

        results = self.store.search(("users",), limit=10)

        self.assertEqual({item["namespace"] for item in results}, {
            ("users", "u1"), ("users", "u2")
        })

    def test_list_namespaces_and_batch_operation(self):
        self.store.put(("users", "u1"), "prefs", {"theme": "dark"})
        self.store.put(("users", "u2", "profile"), "name", {"name": "A"})
        self.mf.add("ordinary memory is not a Store item", category="users/u3")

        namespaces = self.store.list_namespaces(prefix=("users",))
        self.assertEqual(set(namespaces), {
            ("users", "u1"), ("users", "u2", "profile")
        })
        self.assertEqual(
            set(self.store.list_namespaces(prefix=("users",), max_depth=1)),
            {("users",)},
        )
        self.assertEqual(
            set(self.store.list_namespaces(prefix=("users",), max_depth=2)),
            {("users", "u1"), ("users", "u2")},
        )

        try:
            from langgraph.store.base import ListNamespacesOp
            op = ListNamespacesOp(match_conditions=None, max_depth=None,
                                  limit=10, offset=0)
        except ImportError:
            ListNamespacesOp = namedtuple(
                "ListNamespacesOp", "match_conditions max_depth limit offset"
            )
            op = ListNamespacesOp(None, None, 10, 0)
        self.assertEqual(set(self.store.batch([op])[0]), set(namespaces))

    def test_items_expose_langgraph_attributes_and_legacy_mapping(self):
        self.assertIsNone(self.store.put(("docs", "one"), "k", {"v": 1}))
        item = self.store.get(("docs", "one"), "k")
        self.assertEqual(item.value, {"v": 1})
        self.assertEqual(item.namespace, ("docs", "one"))
        self.assertEqual(item["value"], {"v": 1})
        results = self.store.search(("docs", "one"), limit=5)
        self.assertEqual(results[0].value, {"v": 1})
        self.assertEqual(results[0]["key"], "k")
        self.assertEqual(
            self.store.search(("docs", "one"), filter={"v": {"$gt": "x"}}),
            [],
        )

        # Slash-joined category strings are ambiguous; tuple namespaces remain distinct.
        slash_ns = ("users/u1",)
        nested_ns = ("users", "u1")
        self.store.put(slash_ns, "same", {"path": "slash"})
        self.store.put(nested_ns, "same", {"path": "nested"})
        self.assertEqual(self.store.get(slash_ns, "same").value, {"path": "slash"})
        self.assertEqual(self.store.get(nested_ns, "same").value, {"path": "nested"})
        self.store.delete(slash_ns, "same")
        self.assertIsNone(self.store.get(slash_ns, "same"))
        self.assertEqual(self.store.get(nested_ns, "same").value, {"path": "nested"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
