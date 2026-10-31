# -*- coding: utf-8 -*-
"""v6.0.0 功能 1&2：一键导入记忆（ChatGPT/Claude）+ 记忆时间线可视化

功能 1 测试要点：
- ChatGPT mapping 树导出解析（user/assistant 消息、create_time）
- Claude accounts.conversations.chat_messages 导出解析
- 第一人称画像信号识别为 HIGH + user_preference
- 噪音消息（系统提示/寒暄/测试）跳过
- dry_run 不落库、审计记录写入

功能 2 测试要点：
- 按日聚合桶、层分布、分类分布、偏好主题
- 输出为聚合统计（privacy: aggregate-only，不含明文）
"""
import os
import shutil
import tempfile
import unittest

from mindforge import MindForge

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _make_mf():
    tmp = tempfile.mkdtemp(prefix="mf_v600_")
    mf = MindForge(db_path=os.path.join(tmp, "m.db"), encrypted=False)
    return mf, tmp


_CHATGPT_SAMPLE = {
    "mapping": {
        "n1": {"message": {"author": {"role": "user"},
                           "content": {"parts": ["我住在上海，做产品经理"]},
                           "create_time": 100.0}},
        "n2": {"message": {"author": {"role": "assistant"},
                           "content": {"parts": ["好的，了解了。"]},
                           "create_time": 101.0}},
        "n3": {"message": {"author": {"role": "user"},
                           "content": {"parts": ["我喜欢打羽毛球，最近在准备马拉松"]},
                           "create_time": 102.0}},
        "n4": {"message": {"author": {"role": "user"},
                           "content": {"parts": ["测试一下"]},
                           "create_time": 103.0}},
    }
}

_CLAUDE_SAMPLE = {
    "accounts": {
        "a1": {
            "conversations": {
                "c1": {
                    "chat_messages": [
                        {"sender": "human", "text": "我从事前端开发，住在杭州",
                         "timestamp": "2026-01-01T00:00:00Z"},
                        {"sender": "assistant", "text": "欢迎！", "timestamp": "2026-01-01T00:00:05Z"},
                        {"sender": "human", "text": "我喜欢旅行和摄影", "timestamp": "2026-01-01T00:00:10Z"},
                    ]
                }
            }
        }
    }
}


class TestImportChatHistory(unittest.TestCase):
    def setUp(self):
        self.mf, self.tmp = _make_mf()

    def tearDown(self):
        self.mf.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_chatgpt_import_recall(self):
        r = self.mf.import_chat_history(_CHATGPT_SAMPLE)
        self.assertTrue(r["success"])
        self.assertEqual(r["platform"], "chatgpt")
        self.assertEqual(r["imported"], 2, f"应导入 2 条用户信号: {r}")
        # 检索召回
        res = self.mf.search("马拉松", max_results=5)
        texts = [c.content for c in res.chunks]
        self.assertTrue(any("马拉松" in t for t in texts), "导入的记忆可被检索召回")

    def test_claude_import(self):
        r = self.mf.import_chat_history(_CLAUDE_SAMPLE)
        self.assertTrue(r["success"])
        self.assertEqual(r["platform"], "claude")
        self.assertEqual(r["imported"], 2, f"Claude 用户消息应导入 2 条: {r}")

    def test_dry_run_no_write(self):
        before = self.mf.stats()["total"]
        r = self.mf.import_chat_history(_CHATGPT_SAMPLE, dry_run=True)
        self.assertTrue(r["success"])
        self.assertTrue(r["dry_run"])
        after = self.mf.stats()["total"]
        self.assertEqual(before, after,
                         "dry_run 不得落库")

    def test_noise_skipped(self):
        # 仅系统提示 + 寒暄 + 测试消息
        data = {"mapping": {
            "n1": {"message": {"author": {"role": "user"},
                               "content": {"parts": ["你好"]}, "create_time": 1.0}},
            "n2": {"message": {"author": {"role": "system"},
                               "content": {"parts": ["You are ChatGPT"]}, "create_time": 2.0}},
        }}
        r = self.mf.import_chat_history(data)
        self.assertEqual(r["imported"], 0)
        self.assertGreaterEqual(r["skipped_noise"], 1)

    def test_audit_written(self):
        self.mf.import_chat_history(_CHATGPT_SAMPLE)
        logs = self.mf._storage.get_audit_log(limit=20)
        self.assertTrue(any(getattr(l, "action", None) == "import_chat" for l in logs),
                        "应记录 import_chat 审计")


class TestTimelineVisual(unittest.TestCase):
    def setUp(self):
        self.mf, self.tmp = _make_mf()
        self.mf.add("我喜欢咖啡", category="preference", tags=["user_preference"])
        self.mf.add("今天开会讨论 Q3 计划", category="work", tags=["meeting"])
        self.mf.add("周末去爬山", category="life", tags=["hobby"])

    def tearDown(self):
        self.mf.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_timeline_aggregate(self):
        r = self.mf.timeline_visual(days=30)
        self.assertTrue(r["success"])
        self.assertGreaterEqual(r["total_memories"], 3)
        self.assertEqual(r["privacy"], "aggregate-only")
        self.assertIn("timeline", r)
        self.assertIn("layers_distribution", r)
        self.assertIn("categories", r)
        self.assertIn("preferences", r)

    def test_preference_theme_detected(self):
        r = self.mf.timeline_visual(days=30)
        themes = r["preferences"]["themes"]
        self.assertGreaterEqual(len(themes), 1)

    def test_category_filter(self):
        r = self.mf.timeline_visual(category="work", days=30)
        self.assertEqual(r["total_memories"], 1)
        self.assertEqual(r["categories"].get("work"), 1)

    def test_no_plaintext_in_output(self):
        r = self.mf.timeline_visual(days=30)
        blob = str(r)
        for secret in ("我喜欢咖啡", "Q3 计划", "爬山"):
            self.assertNotIn(secret, blob, "可视化输出不得含记忆明文")


if __name__ == "__main__":
    unittest.main()
