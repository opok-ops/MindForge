# -*- coding: utf-8 -*-
"""v6.7.3 终极修 bug 回归契约

本轮修复（全部由 pyflakes F821/F841 静态审查暴露、逐条人工复核）：
- K1 knowledge_graph：4 处 except 块引用未定义 logger（NameError 掩盖
  原始 sqlite 异常）→ 补模块级 logger
- K2 mindforge 门面：5 处方法签名字符串注解引用未导入的 RecallResult
  （search / search_multi / search_chain / search_two_hop，
  typing.get_type_hints / 静态检查直接失败）→ 顶部导入
- K3 privacy 合规报告：total_access_events 只统计从无写入点的 "access"
  action（恒 0）；denied_count 硬编码 0 且不输出 → 真实统计
  （access/search + acl_deny），新增 denied_access_events 字段
- K4 drama_binge 门面：did 归一后误传原值 → 与兄弟方法对齐
- K5 query.search：threshold_relaxed 赋值后从未消费 → 降级语义如实
  标注到 strategy_used（"+relaxed"）
- K6 vector_search：per-result decrypt_content 结果从未消费 → 删除
  （加密库下每条向量命中省一次 AES-GCM 解密），entry 保持密文态返回
- K7 outcome_learning：old_outcome 死变量清理，importance_before 契约
  保持
"""
import os
import sys
import sqlite3
from typing import get_type_hints
from unittest import mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mindforge.core.mindforge import MindForge  # noqa: E402
from mindforge.core.types import MemoryConfig  # noqa: E402
from mindforge.core.query import RecallResult  # noqa: E402
import mindforge.modules.knowledge_graph as kg  # noqa: E402


@pytest.fixture()
def mf_plain(tmp_path):
    """非加密库（FTS5 明文索引仅此分支生效）"""
    cfg = MemoryConfig(db_path=str(tmp_path / "m.db"), encrypted=False)
    m = MindForge(cfg)
    yield m
    m.close()


# ─────────────────────────── K1 knowledge_graph logger ───────────────────────────

class BrokenConn:
    """sqlite 连接替身：所有 SQL 调用抛 OperationalError（模拟表损坏）。"""

    def execute(self, *a, **kw):
        raise sqlite3.OperationalError("disk I/O error")

    def executescript(self, *a, **kw):
        raise sqlite3.OperationalError("disk I/O error")

    def commit(self):
        pass


class BrokenStorage:
    def _get_conn(self):
        return BrokenConn()


class TestKnowledgeGraphLogger:
    def test_module_logger_exists(self):
        """模块级 logger 必须存在且为标准 __name__ 惯例命名。"""
        import logging
        assert hasattr(kg, "logger")
        assert isinstance(kg.logger, logging.Logger)
        assert kg.logger.name == "mindforge.modules.knowledge_graph"

    def test_failure_path_no_nameerror(self):
        """修复前：_ensure_tables/_load_from_db 失败路径引用未定义 logger，
        except 块内抛 NameError 掩盖原始 sqlite3 异常。
        修复后：降级为 logger.warning，无 NameError。"""
        g = kg.KnowledgeGraph(storage=BrokenStorage(), auto_load=True)
        # 内存态结构仍可用（fail-safe 降级语义保持）
        assert g.entities == {}
        assert g.relations == {}

    def test_warning_logged_on_failure(self):
        """失败路径确实通过 logger.warning 记录（可观测）。"""
        with mock.patch.object(kg, "logger") as mlog:
            kg.KnowledgeGraph(storage=BrokenStorage(), auto_load=True)
            assert mlog.warning.called


# ─────────────────────── K2 RecallResult 注解可解析 ───────────────────────

class TestRecallResultAnnotation:
    @pytest.mark.parametrize("method_name", [
        "search", "search_multi", "search_chain", "search_two_hop",
    ])
    def test_get_type_hints_resolves(self, method_name):
        """修复前：字符串注解引用未导入名字，get_type_hints 直接 KeyError。"""
        hints = get_type_hints(getattr(MindForge, method_name))
        assert hints["return"] is RecallResult


# ─────────────────────── K3 合规报告真实统计 ───────────────────────

class TestComplianceReport:
    def test_denied_events_field_and_counts(self, mf_plain):
        mf_plain.add("合规探针甲：账户密码采用scrypt存储", privacy="PRIVATE")
        report = mf_plain.privacy_engine.generate_compliance_report()
        assert "total_access_events" in report
        assert "denied_access_events" in report
        assert report["denied_access_events"] == 0
        # 非空库访问审计真实计数（search 落审计）
        before = report["total_access_events"]
        mf_plain.search("合规探针", use_embedding=False)
        after = mf_plain.privacy_engine.generate_compliance_report()
        assert after["total_access_events"] > before

    def test_acl_deny_counted_real_write(self, mf_plain):
        """acl_deny 审计（federated_acl.check_access 实际写入点）计入
        denied_access_events——修复前恒 0。"""
        from mindforge.modules.federated_acl import FederatedACLManager
        entry = mf_plain.add("合规探针乙：客户名单华东区", privacy="PRIVATE")
        acl = FederatedACLManager(mf_plain._storage)
        acl.add_rule(principal="peer-x", resource="all",
                     operations="read", effect="deny")
        verdict = acl.check_access("peer-x", entry.id, "read")
        assert verdict["allowed"] is False
        report = mf_plain.privacy_engine.generate_compliance_report()
        assert report["denied_access_events"] >= 1


# ─────────────────────── K4 drama_binge did 归一 ───────────────────────

class TestDramaBingeFacade:
    def test_none_and_invalid_input_match_storage(self, mf_plain):
        """None/非 str 入参必须走归一分支，与直连 storage 行为一致。"""
        assert mf_plain.drama_binge(None) == mf_plain._storage.drama_binge_stats(None)
        assert (mf_plain.drama_binge(123)
                == mf_plain._storage.drama_binge_stats(None))

    def test_long_id_truncated_like_siblings(self, mf_plain):
        """超长 drama_id 截断到 64 字符——与 list_lines_by_character /
        character_profile / drama_detail_stats 等兄弟方法同一归一。"""
        long_id = "d" * 80
        assert (mf_plain.drama_binge(long_id)
                == mf_plain._storage.drama_binge_stats(long_id[:64]))


# ─────────────────────── K5 阈值放宽可观测 ───────────────────────

class TestThresholdRelaxedAnnotation:
    def test_strategy_used_marks_relaxed(self, mf_plain):
        """v6.6.1 全空 quota 兜底触发时 strategy_used 带 +relaxed 标注。"""
        mf_plain.add("数据库学习计划：先读索引原理再练执行计划调优",
                     tags=["db"])
        mf_plain.add("数据库连接池配置心得：小库无需过度调参", tags=["db"])
        # 实测校准：「学习心得」组合词 TF-IDF 分数 ~0.18-0.2，
        # 落在 (0.35/2≈0.18 放宽线) 附近的弱相关区间
        res = mf_plain.search(
            "学习心得",
            min_relevance=0.35,
            use_embedding=False,
        )
        assert "relaxed" in res.strategy_used
        assert len(res.chunks) >= 1  # 宁给弱相关不给空

    def test_normal_hit_not_marked(self, mf_plain):
        mf_plain.add("缓存淘汰策略笔记：LRU 与 LFU 的取舍", tags=["cache"])
        res = mf_plain.search("缓存淘汰策略", use_embedding=False)
        assert "relaxed" not in res.strategy_used


# ─────────────────────── K6 vector_search 密文态返回 ───────────────────────

class TestVectorSearchNoDoubleDecrypt:
    def test_plain_db_result_shape(self, mf_plain):
        mf_plain.add("向量检索形状探针：知识图谱嵌入对齐", tags=["k6"])
        res = mf_plain._storage.vector_search("知识图谱", top_k=3)
        # 引擎不可用时返回 []（合法降级）；有结果时校验结构
        for item in res:
            assert set(item) == {"entry", "score", "strategy"}
            assert item["strategy"] == "vector"


# ─────────────────────── K7 outcome_learning 契约 ───────────────────────

class TestOutcomeLearningContract:
    def test_importance_before_field(self, mf_plain):
        """importance_before 是修复过程中差点被误删的对外契约字段。"""
        from mindforge.modules.outcome_learning import apply_outcome
        entry = mf_plain.add("结果学习探针：A方案上线后留存率提升12%")
        r = apply_outcome(mf_plain._storage, entry.id, "success", actor="t")
        assert r["success"] is True
        assert "importance_before" in r
        assert "importance_after" in r
        assert r["importance_after"] != r["importance_before"]
