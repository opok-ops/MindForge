# -*- coding: utf-8 -*-
"""v6.6.1 LLM 判官接线契约测试

benchmarks/locomeval_eval.py 的 judge_llm 自 v5.8.5 起一直是降级脚手架
（"llm judge not wired in scaffold"）。本版接线为 OpenAI 兼容接口调用：
- 无 OPENAI_API_KEY → keyword-fallback
- 调用失败/输出不可解析 → keyword-fallback（带原因标注）
- 正常返回整数分数 → judge="llm"，hit = 分数 >= JUDGE_LLM_THRESHOLD

网络调用全部 mock，CI 零外部依赖。
"""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "benchmarks"))

import locomeval_eval as ev  # noqa: E402


class _Chunk:
    def __init__(self, text):
        self.content = text


QUERY = {"q": "用户对什么食物过敏？", "must_have": ["花生"]}
CHUNKS = [_Chunk("我对花生严重过敏，千万别放花生酱")]


class TestJudgeLLMWiring(unittest.TestCase):
    def setUp(self):
        self._key = os.environ.pop("OPENAI_API_KEY", None)

    def tearDown(self):
        if self._key is not None:
            os.environ["OPENAI_API_KEY"] = self._key
        else:
            os.environ.pop("OPENAI_API_KEY", None)

    def test_no_key_falls_back_to_keyword(self):
        r = ev.judge_llm(QUERY, CHUNKS)
        self.assertEqual(r["judge"], "keyword-fallback")
        self.assertTrue(r["hit"])

    def test_llm_low_score_not_hit(self):
        with mock.patch.object(ev, "_llm_chat", return_value="3"), \
                mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}):
            r = ev.judge_llm({"q": "无关问题", "must_have": ["不存在"]},
                             [_Chunk("今天天气不错")])
        self.assertEqual(r["judge"], "llm")
        self.assertEqual(r["llm_score"], 3)
        self.assertFalse(r["hit"])

    def test_llm_high_score_hit(self):
        with mock.patch.object(ev, "_llm_chat", return_value="9"), \
                mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}):
            r = ev.judge_llm(QUERY, CHUNKS)
        self.assertEqual(r["judge"], "llm")
        self.assertEqual(r["llm_score"], 9)
        self.assertTrue(r["hit"])

    def test_llm_error_falls_back(self):
        with mock.patch.object(ev, "_llm_chat", return_value=""), \
                mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}):
            r = ev.judge_llm(QUERY, CHUNKS)
        self.assertTrue(r["judge"].startswith("keyword-fallback (llm error)"))

    def test_llm_unparsable_falls_back(self):
        with mock.patch.object(ev, "_llm_chat", return_value="无法评分"), \
                mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}):
            r = ev.judge_llm(QUERY, CHUNKS)
        self.assertTrue(r["judge"].startswith(
            "keyword-fallback (llm unparsable)"))

    def test_llm_chat_request_shape(self):
        """_llm_chat 请求体与鉴权头符合 OpenAI 兼容规范"""
        captured = {}

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                captured["body"] = None
                import json as _json
                return _json.dumps({
                    "choices": [{"message": {"content": " 7 "}}]
                }).encode()

        def _fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["auth"] = req.headers.get("Authorization")
            captured["data"] = req.data
            return _Resp()

        with mock.patch.dict(os.environ, {
                "OPENAI_API_KEY": "sk-test",
                "OPENAI_BASE_URL": "https://example.com/v1",
                "OPENAI_MODEL": "test-model"}), \
                mock.patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            out = ev._llm_chat("ping", max_tokens=8)
        self.assertEqual(out, "7")
        self.assertEqual(captured["url"], "https://example.com/v1/chat/completions")
        self.assertEqual(captured["auth"], "Bearer sk-test")
        import json as _json
        body = _json.loads(captured["data"].decode())
        self.assertEqual(body["model"], "test-model")
        self.assertEqual(body["messages"][0]["content"], "ping")
        self.assertEqual(body["temperature"], 0.0)


if __name__ == "__main__":
    unittest.main()
