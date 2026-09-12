# -*- coding: utf-8 -*-
"""MindForge v5.4.6 新功能回归测试

覆盖：cosine_similarity 修复、Embedding 多后端适配器、health_dashboard、
auto-archive、import_json/import_csv 智能去重、export_obsidian、
增量 rebuild-embeddings、REST API（含 SQLite 跨线程修复）。
"""
import json
import os
import sys
import threading
import time
import urllib.request

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.embedding import (
    EmbeddingEngine,
    create_backend,
    EmbeddingBackend,
    SentenceTransformerBackend,
    OpenAIBackend,
    OllamaBackend,
    HTTPBackend,
)
from core.mindforge import MindForge
from core.types import Importance, MemoryLayer


# ---------- 1. cosine_similarity 修复（v5.4.6 必改） ----------

class TestCosineSimilarity:
    def test_identical_vectors_return_1(self):
        v = [1.0, 2.0, 3.0, 4.0]
        assert abs(EmbeddingEngine.cosine_similarity(v, v) - 1.0) < 1e-6

    def test_orthogonal_vectors_return_0(self):
        assert abs(EmbeddingEngine.cosine_similarity([1, 0, 0], [0, 1, 0])) < 1e-6

    def test_non_normalized_scaled_vectors_return_1(self):
        # 相同方向但长度不同的向量相似度应为 1.0
        assert abs(EmbeddingEngine.cosine_similarity([1, 2, 3], [2, 4, 6]) - 1.0) < 1e-6

    def test_zero_vector_safe(self):
        assert EmbeddingEngine.cosine_similarity([0, 0, 0], [1, 0, 0]) == 0.0


# ---------- 2. Embedding 多后端适配器 ----------

class TestEmbeddingBackends:
    def test_backend_classes_exist(self):
        backends = [SentenceTransformerBackend, OpenAIBackend, OllamaBackend, HTTPBackend]
        for b in backends:
            assert isinstance(b, type) and issubclass(b, EmbeddingBackend)

    def test_create_backend_openai(self):
        backend = create_backend("openai")
        assert isinstance(backend, OpenAIBackend)

    def test_create_backend_ollama(self):
        backend = create_backend("ollama")
        assert isinstance(backend, OllamaBackend)

    def test_create_backend_http(self):
        backend = create_backend("http")
        assert isinstance(backend, HTTPBackend)

    def test_create_backend_unknown_raises(self):
        with pytest.raises(ValueError):
            create_backend("nonexistent-backend")


# ---------- 3. 主流程 + health_dashboard ----------

class TestHealthDashboard:
    def test_dashboard_structure(self, tmp_path):
        mf = MindForge(db_path=str(tmp_path / "mem.db"), encrypted=False)
        mf.add("我喜欢喝咖啡，尤其是美式", importance=Importance.HIGH, layer=MemoryLayer.LONG_TERM)
        mf.add("今天下午三点要和团队开会", importance=Importance.MEDIUM, layer=MemoryLayer.SHORT_TERM)

        dash = mf.health_dashboard()
        assert isinstance(dash, dict)
        assert "growth_curve" in dash
        assert "category_distribution" in dash
        assert "decay_warnings" in dash
        assert "top_access_low_importance" in dash
        assert "total_memories" in dash
        assert dash["total_memories"] == 2
        assert isinstance(dash["growth_curve"], list)


# ---------- 4. auto-archive 机制 ----------

class TestAutoArchive:
    def test_archive_and_restore(self, tmp_path):
        mf = MindForge(db_path=str(tmp_path / "mem.db"), encrypted=False)
        r = mf.add("临时事件：明天交房租", layer=MemoryLayer.SHORT_TERM)

        arch = mf.auto_archive(max_age_hours=0, layer="short_term")
        assert isinstance(arch, dict)
        assert arch.get("archived", 0) >= 1

        lst = mf.list_archived()
        assert isinstance(lst, list)
        assert len(lst) >= 1

        # 恢复后再清理
        aid = lst[0]["id"]
        restored = mf.restore_archived(aid)
        assert isinstance(restored, dict) or restored is not None

    def test_purge_archived_returns_int(self, tmp_path):
        mf = MindForge(db_path=str(tmp_path / "mem.db"), encrypted=False)
        mf.add("临时事件", layer=MemoryLayer.SHORT_TERM)
        mf.auto_archive(max_age_hours=0, layer="short_term")
        n = mf.purge_archived(older_than_days=0)
        assert isinstance(n, int)


# ---------- 5. import_json / import_csv 智能去重 ----------

class TestSmartDedup:
    def _make_mf(self, tmp_path):
        return MindForge(db_path=str(tmp_path / "mem.db"), encrypted=False)

    def test_import_json_dedup_counts(self, tmp_path):
        mf = self._make_mf(tmp_path)
        jpath = tmp_path / "import.json"
        jpath.write_text(json.dumps({"memories": [
            {"id": "imp1", "content": "用户喜欢美式咖啡，每天一杯", "category": "pref"},
            {"id": "imp2", "content": "完全不同的新记忆：用户养了一只橘猫", "category": "life"},
        ]}, ensure_ascii=False), encoding="utf-8")

        st1 = mf.import_json(str(jpath), dedup_threshold=0.8)
        assert isinstance(st1, dict)
        assert "deduped" in st1

        # 再导一次相同内容 -> 应全部被去重
        st2 = mf.import_json(str(jpath), dedup_threshold=0.8)
        assert st2.get("deduped", 0) >= 1

    def test_import_csv(self, tmp_path):
        mf = self._make_mf(tmp_path)
        cpath = tmp_path / "import.csv"
        cpath.write_text(
            "content,category,importance\n"
            "用户不喜欢香菜,pref,low\n"
            "用户喜欢爬山,pref,high\n",
            encoding="utf-8",
        )
        st = mf.import_csv(str(cpath), dedup_threshold=0.8)
        assert isinstance(st, dict)
        assert st.get("imported", 0) >= 1


# ---------- 6. export_obsidian ----------

class TestExportObsidian:
    def test_export_generates_md_files(self, tmp_path):
        mf = MindForge(db_path=str(tmp_path / "mem.db"), encrypted=False)
        mf.add("用户喜欢美式咖啡", importance=Importance.HIGH, layer=MemoryLayer.LONG_TERM)

        vault = tmp_path / "vault"
        result = mf.export_obsidian(str(vault))
        assert isinstance(result, dict)
        assert "exported" in result

        md_files = list(vault.glob("*.md"))
        assert len(md_files) >= 1
        content = md_files[0].read_text(encoding="utf-8")
        assert "---" in content  # YAML frontmatter


# ---------- 7. 增量 rebuild-embeddings ----------

class TestIncrementalEmbeddings:
    def test_incremental_rebuild(self, tmp_path):
        mf = MindForge(db_path=str(tmp_path / "mem.db"), encrypted=False)
        mf.add("测试增量重建", layer=MemoryLayer.LONG_TERM)
        st = mf.rebuild_embeddings(incremental=True)
        assert isinstance(st, dict)


# ---------- 8. REST API（含 SQLite 跨线程修复） ----------

class TestRestAPI:
    def test_stats_and_health_endpoints(self, tmp_path, monkeypatch):
        from api.server import start_api_server

        # fail-closed 默认需认证；测试环境显式开启无认证
        monkeypatch.setenv("MINDFORGE_ALLOW_NOAUTH", "1")

        mf = MindForge(db_path=str(tmp_path / "apimem.db"), encrypted=False)
        mf.add("API 测试记忆", layer=MemoryLayer.LONG_TERM)

        port = 18799
        srv = threading.Thread(
            target=start_api_server,
            kwargs={"mindforge_instance": mf, "host": "127.0.0.1", "port": port},
            daemon=True,
        )
        srv.start()
        time.sleep(1.5)

        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/stats", timeout=5) as resp:
                stats = json.loads(resp.read().decode())
            assert isinstance(stats, dict)
            assert stats.get("total", 0) >= 1

            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=5) as resp:
                health = json.loads(resp.read().decode())
            assert isinstance(health, dict)
        except Exception:
            pytest.fail("REST API 请求失败（可能是 SQLite 跨线程问题）")
