# -*- coding: utf-8 -*-
"""v6.5.4 多查询融合检索（search_multi）行为契约

背景：LLM 拆解与实体扩展两条 eval 路径当日证伪（LoCoMo cat2 多跳
实验），本 API 作为中性融合基座保留——拆解策略由调用方提供。
契约：去重合并、查询序位保序、副作用只记一次、空输入降级安全。
"""
import tempfile

import pytest

from mindforge.core.mindforge import MindForge
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


def _seed(mf):
    mf.add("alpha project kickoff meeting notes", category="work")
    mf.add("beta launch timeline discussion", category="work")
    mf.add("alpha budget review with finance", category="work")
    mf.add("gamma unrelated memo", category="general")


class TestSearchMulti:
    def test_merge_dedup(self, mf):
        """两查询命中交集记忆：结果无重复 memory_id"""
        _seed(mf)
        res = mf.search_multi(["alpha project", "alpha budget"], per_query=3)
        ids = [c.memory_id for c in res.chunks]
        assert len(ids) == len(set(ids)), "memory_id 出现重复"

    def test_query_priority_order(self, mf):
        """第一查询的命中整体排在后续查询之前"""
        _seed(mf)
        res = mf.search_multi(["beta launch", "alpha"], per_query=2)
        assert res.chunks, "应有结果"
        first_query_ids = set()
        r1 = mf.search("beta launch", max_results=2)
        for ch in r1.chunks:
            first_query_ids.add(ch.memory_id)
        # 首查全部命中应在合并结果的前 len(first_query_ids) 位内
        got = set(c.memory_id for c in res.chunks[:len(first_query_ids)])
        assert first_query_ids <= got or first_query_ids == set(
            c.memory_id for c in res.chunks), "首查优先级被破坏"

    def test_empty_queries(self, mf):
        """空查询列表：空结果不炸"""
        _seed(mf)
        res = mf.search_multi([], per_query=3)
        assert res.chunks == []
        assert res.strategy_used == "multi:0queries"

    def test_none_queries(self, mf):
        """None 查询：等价空"""
        _seed(mf)
        res = mf.search_multi(None)
        assert res.chunks == []

    def test_single_query_equivalence(self, mf):
        """单查询（放宽序）：合并结果 ⊆ 单 search 结果集"""
        _seed(mf)
        res = mf.search_multi(["alpha"], per_query=3)
        r1 = mf.search("alpha", max_results=3)
        assert {c.memory_id for c in res.chunks} == {
            c.memory_id for c in r1.chunks}

    def test_max_results_cap(self, mf):
        """合并结果不超过 max_results"""
        _seed(mf)
        res = mf.search_multi(["alpha", "beta", "gamma"], per_query=3,
                              max_results=2)
        assert len(res.chunks) <= 2

    def test_strategy_and_timing(self, mf):
        """strategy_used 标记多查询数"""
        _seed(mf)
        res = mf.search_multi(["alpha", "beta"], per_query=2)
        assert res.strategy_used == "multi:2queries"
        assert res.query_time_ms >= 0

    def test_expand_context_passthrough(self, mf):
        """expand_context 透传到子查询"""
        for i in range(6):
            mf.add(f"unique-seq-{i} anchor-text",
                   category="session",
                   tags=[f"ctxgrp:sm", f"ctxseq:{i}"])
        res = mf.search_multi(["unique-seq-2", "unique-seq-4"],
                              per_query=1, expand_context=2)
        any_exp = any(c.context_expansion for c in res.chunks)
        assert any_exp, "context_expansion 未透传"
