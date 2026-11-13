# -*- coding: utf-8 -*-
"""v6.5.2 证据上下文窗口扩展（AdaptiveContextEngine 雏形）行为契约

坐标协议：入库 tags 携带 ctxgrp:<组id> + ctxseq:<序号>；
search(expand_context=N>0) 时按组拉邻居填充 context_expansion。
默认 expand_context=0 行为与 v6.5.1 完全一致（兼容性契约）。
背景：LoCoMo conv-26 实验窗口扩展使 multi-hop 正确率 +27pt。
"""
import os
import tempfile

import pytest

from mindforge.core.mindforge import MindForge
from mindforge.core.types import MemoryConfig


@pytest.fixture
def mf():
    tmp = tempfile.mkdtemp()
    inst = MindForge(config=MemoryConfig(
        db_path=f"{tmp}/mf.db", key_file=None, encrypted=False))
    # 无向量依赖的纯词法测试环境
    yield inst
    inst.close()
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)


def _seed(mf, group="g1", n=6, prefix="turn"):
    """入库带坐标的 6 轮对话"""
    for i in range(n):
        mf.add(f"{prefix} {i}: unique-words-{prefix}-{i}",
               category="session",
               tags=[f"ctxgrp:{group}", f"ctxseq:{i}"])


class TestContextExpansion:
    def test_default_off_compat(self, mf):
        """默认 expand_context=0：context_expansion 恒 None（兼容契约）"""
        _seed(mf)
        res = mf.search("unique-words", max_results=3)
        assert res.chunks
        for c in res.chunks:
            assert c.context_expansion is None

    def test_expansion_window(self, mf):
        """expand_context=2：窗口 = 命中轮 ±2（含自身），升序"""
        _seed(mf)
        res = mf.search("unique-words-turn-2", max_results=1,
                        expand_context=2)
        assert res.chunks
        hit = res.chunks[0]
        hit_seq = None
        for t in hit.tags:
            if t.startswith("ctxseq:"):
                hit_seq = int(t[7:])
        assert hit_seq is not None
        exp = hit.context_expansion
        assert exp is not None
        seqs = [s for s, _ in exp]
        assert seqs == sorted(seqs), "窗口应按 seq 升序"
        lo = max(0, hit_seq - 2)
        hi = min(5, hit_seq + 2)
        assert set(seqs) == set(range(lo, hi + 1)), (
            f"命中 seq={hit_seq} 期望窗口 [{lo}..{hi}] 实得 {sorted(seqs)}")

    def test_boundary_clamp(self, mf):
        """seq=0 命中：窗口不越界（无负 seq）"""
        _seed(mf)
        res = mf.search("unique-words-turn-0", max_results=1,
                        expand_context=3)
        assert res.chunks
        exp = res.chunks[0].context_expansion
        assert exp is not None
        seqs = [s for s, _ in exp]
        assert seqs == [0, 1, 2, 3]

    def test_no_coords_degrade(self, mf):
        """无坐标 tags 的记忆：字段 None，不炸（降级安全）"""
        mf.add("plain memory without coords", category="general")
        res = mf.search("plain memory", max_results=2,
                        expand_context=2)
        assert res.chunks
        for c in res.chunks:
            if "ctxgrp" not in (c.tags or []):
                assert c.context_expansion is None

    def test_groups_isolated(self, mf):
        """两个组：g1 窗口不混入 g2 的条目"""
        _seed(mf, group="g1", n=6, prefix="alpha")
        _seed(mf, group="g2", n=6, prefix="beta")
        res = mf.search("unique-words-alpha-3", max_results=1,
                        expand_context=5)
        assert res.chunks
        exp = res.chunks[0].context_expansion
        assert exp is not None
        texts = " ".join(t for _, t in exp)
        assert "beta" not in texts, "跨组隔离被破坏"

    def test_radius_one(self, mf):
        """expand_context=1：紧邻 ±1（以实际命中轮为中心）"""
        _seed(mf)
        res = mf.search("unique-words-turn-3", max_results=1,
                        expand_context=1)
        assert res.chunks
        hit = res.chunks[0]
        hit_seq = None
        for t in hit.tags:
            if t.startswith("ctxseq:"):
                hit_seq = int(t[7:])
        exp = hit.context_expansion
        assert exp is not None
        seqs = [s for s, _ in exp]
        lo = max(0, hit_seq - 1)
        hi = min(5, hit_seq + 1)
        assert set(seqs) == set(range(lo, hi + 1))

    def test_chunk_serializable(self, mf):
        """context_expansion 不破坏 MemoryChunk 基本序列化路径"""
        _seed(mf)
        res = mf.search("unique-words-turn-1", max_results=1,
                        expand_context=1)
        c = res.chunks[0]
        assert c.context_expansion is not None
        # dataclass 基本字段完整
        assert c.memory_id and c.content
        assert isinstance(c.relevance_score, float)
