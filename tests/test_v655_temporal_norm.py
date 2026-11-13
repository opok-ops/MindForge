# -*- coding: utf-8 -*-
"""v6.5.5 时间归一化窗口扩展（preview）行为契约

ctxdate 协议：入库 tags 可选携带 ctxdate:YYYY-MM-DD；窗口扩展时
邻居文本相对时间词（today/yesterday/两周前…）就地换算绝对日期。
缺 ctxdate 降级安全；无时间词文本不受影响。
"""
import tempfile

import pytest

from mindforge.core.mindforge import MindForge
from mindforge.core.query import _normalize_temporal
from mindforge.core.types import MemoryConfig


@pytest.fixture
def mf():
    tmp = tempfile.mkdtemp()
    inst = MindForge(config=MemoryConfig(
        db_path=f"{tmp}/mf.db", key_file=None, encrypted=False))
    yield inst
    inst.close()
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)


class TestNormalizeUnit:
    def test_yesterday(self):
        out = _normalize_temporal("I went there yesterday", "2023-05-07")
        assert "[2023-05-06]" in out

    def test_today(self):
        out = _normalize_temporal("meeting is today", "2023-05-07")
        assert "[2023-05-07]" in out

    def test_n_days_ago(self):
        out = _normalize_temporal("we met 3 days ago", "2023-05-07")
        assert "[2023-05-04]" in out

    def test_in_n_weeks(self):
        out = _normalize_temporal("leaving in 2 weeks", "2023-05-07")
        assert "[2023-05-21]" in out

    def test_chinese(self):
        out = _normalize_temporal("两周前我们讨论过", "2023-05-07")
        assert "[2023-04-23]" in out

    def test_no_anchor(self):
        assert _normalize_temporal("went yesterday", None) == "went yesterday"

    def test_bad_anchor(self):
        assert _normalize_temporal("went yesterday", "not-a-date") == "went yesterday"

    def test_no_temporal_words(self):
        out = _normalize_temporal("she likes hiking", "2023-05-07")
        assert out == "she likes hiking"


class TestExpandWithDate:
    def test_ctxdate_normalized(self, mf):
        """带 ctxdate 的邻居：相对词被换算"""
        for i in range(5):
            mf.add(f"day-{i} we visited the museum yesterday",
                   category="session",
                   tags=["ctxgrp:tn", f"ctxseq:{i}", "ctxdate:2023-05-10"])
        res = mf.search("day-2 visited museum", max_results=1,
                        expand_context=2)
        assert res.chunks
        exp = res.chunks[0].context_expansion
        assert exp is not None
        joined = " ".join(t for _, t in exp)
        assert "[2023-05-09]" in joined, "yesterday 未按 ctxdate 归一化"

    def test_no_ctxdate_untouched(self, mf):
        """无 ctxdate：邻居原文保留（降级安全）"""
        for i in range(5):
            mf.add(f"nd-{i} we watched a movie yesterday",
                   category="session",
                   tags=["ctxgrp:nd", f"ctxseq:{i}"])
        res = mf.search("nd-2 watched movie", max_results=1,
                        expand_context=2)
        exp = res.chunks[0].context_expansion
        assert exp is not None
        joined = " ".join(t for _, t in exp)
        assert "yesterday" in joined and "[2023" not in joined

    def test_mixed_dates(self, mf):
        """不同邻居不同日期锚：各自归一化"""
        mf.add("ma-0 breakfast today", category="session",
               tags=["ctxgrp:mx", "ctxseq:0", "ctxdate:2023-01-01"])
        mf.add("ma-1 lunch tomorrow", category="session",
               tags=["ctxgrp:mx", "ctxseq:1", "ctxdate:2023-01-02"])
        res = mf.search("ma-0 breakfast", max_results=1, expand_context=1)
        exp = res.chunks[0].context_expansion
        joined = " ".join(t for _, t in exp)
        assert "[2023-01-01]" in joined and "[2023-01-03]" in joined
