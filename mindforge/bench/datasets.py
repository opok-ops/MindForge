# -*- coding: utf-8 -*-
"""评测数据集：内置冒烟集 + LoCoMo 加载器。

统一条目 schema（评测中立的中间表示）::

    {
      "qid": str,            # 全局唯一
      "question": str,
      "gold_answer": str,    # 金标准答案（判官用）
      "must_have": [str],    # deterministic 判官的命中关键词
      "category": str,       # singlehop | multihop | temporal |
                             # adversarial | open_domain
      "source_turns": [str], # 入库文本（backend.add 用）
      "meta": dict,          # 数据集溯源（conv_id 等）
    }

LoCoMo 官方类别数字映射（已与 v6.5.12 全量报告 58.4% 的类别
分布逐项核对：113/282=40.1 temporal、39/321=12.2 multihop、
558/841=66.4 singlehop、425/446=95.3 adversarial）::

    1→temporal(282)  2→multihop(321)  3→open_domain(96)
    4→singlehop(841) 5→adversarial(446)   共 1986 问 / 10 会话

LoCoMo 数据文件不随包分发（体积与版权），用户自备：
github.com/snap-research/locomo → data/locomo10.json
"""
import json
import os
from typing import Any, Dict, List, Optional

LOCOMO_CATEGORY_MAP = {
    1: "temporal",
    2: "multihop",
    3: "open_domain",
    4: "singlehop",
    5: "adversarial",
}

LOCOMO_DATASET_ID = "locomo-v0.1-2026-09-26"
SMOKE_DATASET_ID = "mindforge-smoke-v1.0"

# ---------- 内置冒烟集（12 条对话轮 + 12 问，中文，零下载） ----------
# 与 benchmarks/locomeval_eval.py（v5.8.5 起）同源，保证口径连续。
SMOKE_SESSIONS: List[Dict[str, str]] = [
    {"session": "s1", "speaker": "user", "text": "我对花生严重过敏，千万别放花生酱"},
    {"session": "s1", "speaker": "user", "text": "我们家计划下个月去京都旅行一周"},
    {"session": "s1", "speaker": "user", "text": "我最喜欢的餐厅是银座的寿司久兵卫"},
    {"session": "s1", "speaker": "user", "text": "我上次搬过家，现在住在东京都港区"},
    {"session": "s1", "speaker": "user", "text": "之前我住在大阪市中央区两年"},
    {"session": "s1", "speaker": "user", "text": "京都旅行订了周三下午的新干线车票"},
    {"session": "s1", "speaker": "user", "text": "我女儿叫樱，今年七岁"},
    {"session": "s1", "speaker": "user", "text": "寿司久兵卫需要提前两周预订"},
    {"session": "s1", "speaker": "user", "text": "我最近在学弹吉他，每天练一小时"},
    {"session": "s1", "speaker": "user", "text": "樱对猫毛过敏，家里不能养猫"},
    {"session": "s1", "speaker": "user", "text": "京都住的酒店叫京都格兰比亚，在京都站旁边"},
    {"session": "s1", "speaker": "user", "text": "我的邮箱是 tanaka@example.jp"},
]

SMOKE_QUESTIONS: List[Dict[str, Any]] = [
    {"q": "用户对什么食物过敏？", "type": "singlehop", "must_have": ["花生"]},
    {"q": "用户女儿叫什么名字，多大？", "type": "singlehop", "must_have": ["樱"]},
    {"q": "用户现在住在哪？", "type": "temporal", "must_have": ["东京", "港区"]},
    {"q": "用户之前住在大阪多久？", "type": "temporal", "must_have": ["大阪", "两年"]},
    {"q": "用户计划去京都什么时候出发？", "type": "multihop", "must_have": ["京都", "周三"]},
    {"q": "用户要去的餐厅有预订要求吗？", "type": "multihop", "must_have": ["寿司久兵卫", "两周"]},
    {"q": "京都住的酒店在哪一站旁边？", "type": "multihop", "must_have": ["京都"]},
    {"q": "用户家里为什么不能养猫？", "type": "multihop", "must_have": ["樱", "猫毛"]},
    {"q": "用户的邮箱地址是什么？", "type": "singlehop", "must_have": ["tanaka@example.jp"]},
    {"q": "用户每天练什么乐器多久？", "type": "singlehop", "must_have": ["吉他"]},
    {"q": "用户去京都住的酒店叫什么？", "type": "multihop", "must_have": ["京都", "格兰比亚"]},
    {"q": "用户去京都旅行大概多久？", "type": "singlehop", "must_have": ["一周"]},
]


def _smoke_dataset() -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for i, q in enumerate(SMOKE_QUESTIONS):
        items.append({
            "qid": f"smoke:{i}",
            "question": q["q"],
            "gold_answer": "",  # 冒烟集为检索式评测，金标准以 must_have 表达
            "must_have": list(q["must_have"]),
            "category": q["type"],
            "source_turns": [f"{s['speaker']}: {s['text']}" for s in SMOKE_SESSIONS],
            "meta": {"dataset": SMOKE_DATASET_ID},
        })
    return items


def load_locomo(data_path: str, convs: Optional[int] = None) -> List[Dict[str, Any]]:
    """加载 LoCoMo 官方数据文件（locomo10.json）为统一条目。

    口径与 benchmarks/locomo_deepseek_eval.py（v6.5.12 全量 58.4%）
    完全一致：session_N 逐轮入库、adversarial 类 gold 取
    adversarial_answer、category 数字映射见模块 docstring。
    """
    with open(data_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    items: List[Dict[str, Any]] = []
    n_conv = min(convs, len(data)) if convs else len(data)
    for ci in range(n_conv):
        conv = data[ci]
        cid = str(conv.get("sample_id", f"conv{ci}"))
        seg = conv.get("conversation", {})
        turns: List[str] = []
        for day in sorted(k for k in seg
                          if k.startswith("session_") and not k.endswith("_date_time")):
            for t in seg[day]:
                turns.append(f"{t.get('speaker', '?')}: {t.get('text', '')}")
        for qi, qa in enumerate(conv.get("qa", [])):
            cat_raw = qa.get("category")
            cat = LOCOMO_CATEGORY_MAP.get(cat_raw, f"unknown_{cat_raw}")
            gold = qa.get("answer") or qa.get("adversarial_answer") or ""
            items.append({
                "qid": f"{cid}:{qi}",
                "question": qa["question"],
                "gold_answer": str(gold),
                "must_have": [],  # LoCoMo 走 LLM 判官，不做关键词假设
                "category": cat,
                "source_turns": turns,
                "meta": {
                    "dataset": LOCOMO_DATASET_ID,
                    "conv_id": cid,
                    "evidence": qa.get("evidence", []),
                },
            })
    return items


def load_dataset(name: str, data_path: Optional[str] = None,
                 convs: Optional[int] = None) -> List[Dict[str, Any]]:
    """按名称加载数据集。smoke 内置；locomo 需 data_path。"""
    if name == "smoke":
        return _smoke_dataset()
    if name == "locomo":
        if not data_path or not os.path.isfile(data_path):
            raise FileNotFoundError(
                "locomo 数据集需 --data 指向 locomo10.json"
                "（github.com/snap-research/locomo 自备下载）")
        return load_locomo(data_path, convs=convs)
    raise ValueError(f"未知数据集: {name}（可选 smoke | locomo）")
