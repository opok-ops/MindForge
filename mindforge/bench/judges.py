# -*- coding: utf-8 -*-
"""评分器（pipeline 第三段：候选答案 vs 金标准）。

- DeterministicJudge — 零成本：must_have 关键词在答案中全命中
  记满分（10），否则 1 分。correct = score >= 9。答案为空记 1 分。
- LLMJudge — OpenAI 兼容判官，1-10 分，**>=9 记正确**——与
  MindForge v6.5.12 LoCoMo 全量报告（58.4%）同一口径。

双指标说明：评测同时产出「检索层」指标（Recall@K / MRR@K，
基于 must_have 在片段中的命中）与「答案层」指标（acc / 均分）。
冒烟集两条都有；LoCoMo 只有金标准答案，检索层指标在报告中
标注为不可用（null），不做假设。
"""
import json
import re
import time
import urllib.request
from typing import Any, Dict, List, Protocol

JUDGE_PROMPT = """你是评测判官。根据金标准答案给系统生成的答案打分。

问题：{q}
金标准答案：{gold}
系统生成答案：{pred}

评分标准（1-10 整数）：
- 10：完全正确（含时间/数量等细节全对）
- 7-9：核心正确但有细节瑕疵或表述含糊
- 4-6：部分正确
- 1-3：错误或未回答

只输出一个整数分数。"""

LLM_ERROR = "__LLM_ERROR__"


class BaseJudge(Protocol):
    name: str

    def score(self, item: Dict[str, Any], pred: str,
              chunks: List[Dict[str, Any]]) -> Dict[str, Any]:
        """返回 {score, correct, retrieved, first_hit_rank, ...}。"""

    def close(self) -> None:
        pass


class DeterministicJudge:
    """零成本判官。两种模式自动选择：

    - must_have 非空（smoke 检索式）：关键词全命中记 10 分；
    - must_have 为空（LoCoMo QA 式）：金标准答案子串匹配——
      gold（去空格小写）出现在 pred 中记 10 分，短答案反向命中
      （pred ⊆ gold 且 len(gold)<=12）也记 10 分。
    """

    name = "deterministic"

    @staticmethod
    def _gold_hit(gold: str, pred_l: str) -> bool:
        g = re.sub(r"\s+", "", (gold or "")).lower()
        if not g:
            return False
        if g in pred_l.replace(" ", ""):
            return True
        # 短金标答案反向命中（生成答案略长于金标时）
        return len(g) <= 12 and g in pred_l.replace(" ", "")

    def score(self, item: Dict[str, Any], pred: str,
              chunks: List[Dict[str, Any]]) -> Dict[str, Any]:
        must = item.get("must_have") or []
        pred_l = (pred or "").lower()
        if must:
            # 答案层：must_have 全命中 → 10 分
            hit = all(kw.lower() in pred_l for kw in must)
        else:
            # QA 式：金标答案子串匹配
            hit = self._gold_hit(item.get("gold_answer", ""), pred_l)
        ans_score = 10 if hit else 1
        # 检索层（仅 must_have 口径有意义；QA 式记 None 表示不可用）
        chunks_l = [c.get("content", "") or "" for c in chunks]
        if must:
            concat_l = "\n".join(chunks_l).lower()
            retrieved = all(kw.lower() in concat_l for kw in must)
            first_rank = 0
            if retrieved:
                for i, t in enumerate(chunks_l, start=1):
                    if all(kw.lower() in t.lower() for kw in must):
                        first_rank = i
                        break
                if first_rank == 0:
                    first_rank = len(chunks_l)  # 跨片段聚合命中，记 K
        else:
            retrieved = None
            first_rank = 0
        return {"score": ans_score, "correct": hit,
                "retrieved": retrieved, "first_hit_rank": first_rank}

    def close(self) -> None:
        pass


class LLMJudge:
    """LLM 判官：1-10 分，>=9 记正确（v6.5.12 同源口径）。"""

    name = "llm"

    def __init__(self, api_key: str, base_url: str = "https://api.deepseek.com",
                 model: str = "deepseek-chat", max_tokens: int = 16,
                 retries: int = 5) -> None:
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
                    return LLM_ERROR
                time.sleep(3 * (attempt + 1))
        return LLM_ERROR

    def score(self, item: Dict[str, Any], pred: str,
              chunks: List[Dict[str, Any]]) -> Dict[str, Any]:
        gold = item.get("gold_answer") or ""
        raw = self._chat(JUDGE_PROMPT.format(
            q=item["question"], gold=gold,
            pred=(pred or "").strip() or "（未回答）"))
        if raw == LLM_ERROR or not raw:
            sc = 0  # 判官失败显式记 0，不静默漂白
        else:
            digits = "".join(c for c in raw if c.isdigit())
            sc = int(digits[:4] or 0)
            sc = max(0, min(10, sc))
        return {"score": sc, "correct": sc >= 9,
                "retrieved": None, "first_hit_rank": 0,
                "judge_raw": raw}

    def close(self) -> None:
        pass


def build_judge(kind: str, **kwargs: Any) -> Any:
    if kind == "deterministic":
        return DeterministicJudge()
    if kind == "llm":
        if not kwargs.get("api_key"):
            raise ValueError("judge=llm 需要 --judge-api-key")
        return LLMJudge(
            api_key=kwargs["api_key"],
            base_url=kwargs.get("judge_base_url") or "https://api.deepseek.com",
            model=kwargs.get("judge_model") or "deepseek-chat")
    raise ValueError(f"未知 judge: {kind}")
