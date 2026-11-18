# -*- coding: utf-8 -*-
"""答案生成器（pipeline 第二段：检索片段 → 候选答案）。

- ExtractiveAnswerer — 零成本抽取式：从片段中选出与问题词重叠
  最高的句子作为答案。deterministic 全链路无 API，是默认链路。
- OpenAICompatAnswerer — OpenAI 兼容 chat/completions 生成答案
  （DeepSeek/OpenAI/本地 vLLM 均可），与 58.4% 全量报告的
  GEN_PROMPT 同口径。零第三方依赖（urllib）。

生成器只见「片段 + 问题」，不见金标准答案——评测防泄漏是硬边界。
"""
import json
import re
import time
import urllib.request
from typing import Any, Dict, List, Protocol

GEN_PROMPT = """基于以下记忆片段回答问题。只给出简洁的最终答案，不要解释。

记忆片段：
{ctx}

问题：{q}
答案："""


class BaseAnswerer(Protocol):
    name: str

    def answer(self, question: str, chunks: List[Dict[str, Any]]) -> str:
        """基于片段生成候选答案（不得接触金标准）。"""

    def close(self) -> None:
        pass


def _sentences(text: str) -> List[str]:
    parts = re.split(r"(?<=[。！？!?.])\s*|\n+", text)
    return [p.strip() for p in parts if p.strip()]


def _overlap_score(sentence: str, query: str) -> int:
    # 中文按字符 bigram、拉丁按词，简单鲁棒的重叠度量
    pat = r"[\u4e00-\u9fff]{2}|[a-zA-Z0-9@._\-]+"
    q_tokens = set(re.findall(pat, query.lower()))
    s_tokens = set(re.findall(pat, sentence.lower()))
    return len(q_tokens & s_tokens)


class ExtractiveAnswerer:
    """抽取式答案：片段内与问题重叠最高的句子。"""

    name = "extractive"

    def answer(self, question: str, chunks: List[Dict[str, Any]]) -> str:
        best, best_score = "", -1
        for c in chunks:
            for s in _sentences(c.get("content", "")):
                sc = _overlap_score(s, question)
                if sc > best_score:
                    best, best_score = s, sc
        return best

    def close(self) -> None:
        pass


class OpenAICompatAnswerer:
    """OpenAI 兼容接口生成答案。key 缺失/失败时交上层降级。"""

    name = "openai_compat"

    def __init__(self, api_key: str, base_url: str = "https://api.deepseek.com",
                 model: str = "deepseek-chat", max_tokens: int = 400,
                 retries: int = 3) -> None:
        self._key = api_key
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._model = model
        self._max_tokens = max_tokens
        self._retries = retries

    def _chat(self, prompt: str) -> str:
        body = json.dumps({"model": self._model, "messages": [
            {"role": "user", "content": prompt}],
            "max_tokens": self._max_tokens,
            "temperature": 0.0}).encode()
        for attempt in range(self._retries):
            try:
                req = urllib.request.Request(self._url, data=body, headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self._key}"})
                with urllib.request.urlopen(req, timeout=90) as r:
                    out = json.loads(r.read())
                return out["choices"][0]["message"]["content"].strip()
            except Exception:
                if attempt == self._retries - 1:
                    return ""
                time.sleep(2 * (attempt + 1))
        return ""

    def answer(self, question: str, chunks: List[Dict[str, Any]]) -> str:
        ctx = "\n".join(f"- {c.get('content', '')}" for c in chunks)
        return self._chat(GEN_PROMPT.format(ctx=ctx, q=question))

    def close(self) -> None:
        pass


def build_answerer(kind: str, **kwargs: Any) -> Any:
    if kind == "extractive":
        return ExtractiveAnswerer()
    if kind == "llm":
        if not kwargs.get("api_key"):
            raise ValueError("answerer=llm 需要 api_key")
        return OpenAICompatAnswerer(
            api_key=kwargs["api_key"],
            base_url=kwargs.get("base_url") or "https://api.deepseek.com",
            model=kwargs.get("model") or "deepseek-chat")
    raise ValueError(f"未知 answerer: {kind}")
