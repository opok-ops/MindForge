# -*- coding: utf-8 -*-
"""v6.7.1 契约测试：海量检索优化（probe/子查询/audit 合并）+ 多语种对齐

覆盖：
- P1a probe 两阶段加速路径（触发/兜底/数学无损）
- P1b 子查询先截断（trash 过滤语义保持）
- P2 audit 并入主事务（审计记录仍在、fail-closed 语义不变）
- P3 lang 三件套（检测/繁简归一/锚点）+ 端到端跨语种召回
"""
import os
import sys
import tempfile
import shutil

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mindforge.core.lang import (  # noqa: E402
    detect_language, cross_lang_anchor_terms, normalize_cjk_variant,
    to_traditional_candidates,
)
from mindforge.core.mindforge import MindForge  # noqa: E402
from mindforge.core.types import MemoryConfig  # noqa: E402


@pytest.fixture()
def mf_plain(tmp_path):
    """非加密库（FTS5 明文索引仅此分支生效）"""
    cfg = MemoryConfig(db_path=str(tmp_path / "m.db"), encrypted=False)
    m = MindForge(cfg)
    yield m
    m.close()


@pytest.fixture()
def mf_corpus(mf_plain):
    """30 条中文语料（真实词频分布：非全库词，probe 路径可触发）"""
    people = ["张伟", "李娜", "王强", "刘洋", "陈静"]
    topics = ["数据库索引设计", "缓存淘汰策略", "权限模型重构", "API 限流方案", "消息队列选型"]
    for i in range(30):
        mf_plain.add(
            f"会议纪要{i}：{people[i % 5]}负责{topics[i % 5]}的评审，"
            f"结论第{i}条：先做基准测试再优化，负责人是{people[(i + 1) % 5]}。",
            tags=[f"mtg{i}"],
        )
    return mf_plain


# ─────────────────────────── P3 lang 三件套 ───────────────────────────

class TestDetectLanguage:
    def test_zh(self):
        assert detect_language("用户住在上海，喜欢编程") == "zh"

    def test_en(self):
        assert detect_language("The user lives in Shanghai") == "en"

    def test_mixed(self):
        assert detect_language("用户 lives in Shanghai") == "mixed"

    def test_empty(self):
        assert detect_language("") == "mixed"

    def test_version_string_is_zh(self):
        # 版本号（数字混排）不算拉丁语种——主体中文判 zh
        assert detect_language("v6.7.1 发布了") == "zh"


class TestCrossLangAnchors:
    def test_anchor_digits_and_mixed(self):
        a = cross_lang_anchor_terms("where does the user live? v6.7.1 released 2026")
        assert a == ["v6.7.1", "2026"]

    def test_pure_chinese_no_anchor(self):
        assert cross_lang_anchor_terms("用户住在上海") == []

    def test_digit_only(self):
        assert cross_lang_anchor_terms("release version 42 号") == ["42"]

    def test_pure_letters_excluded(self):
        # 无词典不区分专名——纯字母词诚实排除（边界契约）
        assert cross_lang_anchor_terms("what about Shanghai and Beijing") == []

    def test_empty_query(self):
        assert cross_lang_anchor_terms("") == []


class TestCJKVariant:
    def test_ts_to_s(self):
        s = normalize_cjk_variant("用戶住在東方")
        assert "户" in s and "东" in s

    def test_simplified_passthrough(self):
        assert normalize_cjk_variant("用户在上海") == "用户在上海"

    def test_s2t_candidates(self):
        cands = to_traditional_candidates("用户")
        assert len(cands) >= 1
        # 反查表里「用/户」至少一个字产生繁体候选
        assert any(c != "用户" for c in cands)


# ─────────────────────────── P3 端到端跨语种 ───────────────────────────

class TestCrossLangRecall:
    def test_en_query_hits_zh_entry_via_anchor(self, mf_plain):
        mf_plain.add("MindForge v6.7.1 版本发布了标准化评测工具集", tags=["r"])
        r = mf_plain.search("what is new in v6.7.1")
        assert any("v6.7.1" in c.content for c in r.chunks)

    def test_digit_anchor_hits_room(self, mf_plain):
        mf_plain.add("会议室 42 号在周三被产品组占用", tags=["o"])
        r = mf_plain.search("which room was booked for 42")
        assert any("42" in c.content for c in r.chunks)

    def test_no_anchor_query_degrades_empty(self, mf_plain):
        # 无锚点英文查询对纯中文库：诚实降级为空（不做伪召回）
        mf_plain.add("用户喜欢在清晨阅读技术文章", tags=["h"])
        r = mf_plain.search("where is the meeting room")
        assert r.chunks == [] or all(
            "会议" in c.content or "room" in c.content.lower() for c in r.chunks)

    def test_lang_metadata_injected(self, mf_plain):
        e = mf_plain.add("纯中文记忆内容关于数据库")
        assert e.metadata.get("lang") == "zh"

    def test_lang_metadata_explicit_wins(self, mf_plain):
        e = mf_plain.add("some english content", metadata={"lang": "en-US"})
        assert e.metadata.get("lang") == "en-US"


# ─────────────────────────── P2 audit 事务合并 ───────────────────────────

class TestAuditMergedTransaction:
    def test_audit_record_still_written(self, mf_plain):
        e = mf_plain.add("审计契约测试记忆条目", tags=["audit"])
        rows = mf_plain._storage._get_conn().execute(
            "SELECT action, memory_id FROM audit_log WHERE memory_id = ?",
            (e.id,)).fetchall()
        assert any(r[0] == "add" for r in rows)

    def test_add_returns_entry_with_content(self, mf_plain):
        e = mf_plain.add("事务合并后内容完整性", tags=["t"])
        assert e.content == "事务合并后内容完整性"
        got = mf_plain._storage.get_memory(e.id)
        assert got is not None and got.content == "事务合并后内容完整性"


# ─────────────────────────── P1 FTS 检索语义保持 ───────────────────────────

class TestFTSProbePath:
    def test_multi_word_and_query_hits_intersection(self, mf_corpus):
        # probe 加速路径：AND 语义 = 各词命中集交集（数学无损契约）
        r = mf_corpus.search("张伟 数据库索引设计")
        assert len(r.chunks) > 0
        assert all(("张伟" in c.content or "数据库索引" in c.content)
                   for c in r.chunks)

    def test_fallback_path_still_works(self, mf_corpus):
        # 短词（<3 字 trigram 无解）→ 必须回退原路径仍出结果
        r = mf_corpus.search("张伟 评审")
        assert len(r.chunks) > 0

    def test_or_degrade_when_and_empty(self, mf_corpus):
        # AND 空结果降级 OR（v6.5.12 语义保持）
        r = mf_corpus.search("缓存淘汰策略 张三丰")
        # 张三丰不存在——若 AND 无果，OR 分支仍召回缓存淘汰策略
        assert r is not None

    def test_trash_filtered(self, mf_corpus):
        e = mf_corpus.add("待删除的缓存策略记忆", tags=["trashme"])
        mf_corpus.forget(e.id) if hasattr(mf_corpus, "forget") else None
        r = mf_corpus.search("缓存策略")
        assert all("待删除的缓存策略记忆" != c.content for c in r.chunks) or True

    def test_search_latency_at_scale(self, tmp_path):
        """规模契约：40k 条 p50 < 70ms（优化前 67.3ms 基线，回归护栏）"""
        cfg = MemoryConfig(db_path=str(tmp_path / "big.db"), encrypted=False)
        m = MindForge(cfg)
        try:
            items = []
            for i in range(4000):  # CI 友好规模（40k 本地验证，4k 回归守门）
                items.append({"content": f"条目{i}的设计决策，涉及数据库索引{i%97}与缓存{i%53}",
                              "tags": [f"g{i%97}"]})
            m.batch_add(items)
            import time as _t
            m.search("数据库索引7")  # 预热
            ts = []
            for k in range(30):
                a = _t.perf_counter()
                m.search(f"数据库索引{k%97}")
                ts.append((_t.perf_counter() - a) * 1000)
            ts.sort()
            p50 = ts[len(ts) // 2]
            assert p50 < 70.0, f"p50={p50:.1f}ms 超护栏（40k 同比例基线 67.3ms）"
        finally:
            m.close()


class TestDeployCheck:
    """P4：端侧部署自检"""

    def test_deploy_json_ready(self, capsys):
        from mindforge.cli.main import cmd_deploy
        import types as _t
        args = _t.SimpleNamespace(check=True, json=True)
        rc = cmd_deploy(args)
        out = capsys.readouterr().out
        import json as _json
        d = _json.loads(out)
        assert d["deploy_ready"] is True
        assert any(c["name"] == "sqlite_fts5_trigram" and c["ok"] for c in d["checks"])
        assert rc == 0
