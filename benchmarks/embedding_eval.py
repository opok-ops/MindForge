# -*- coding: utf-8 -*-
"""MindForge 向量检索评测基线（v5.7.8 新增）

可复现的检索质量评测：固定数据集 + 固定种子 + 四路对比。

四路口径：
- hybrid : mf.search() 默认多路融合（TF-IDF + FTS5 + Fuzzy + Vector）
- vector : storage.vector_search() 纯向量召回
- fts5   : index.fts_search() 纯 FTS5
- tfidf  : index.search() 纯 TF-IDF

指标：Recall@5 / MRR@10 / NDCG@10（binary relevance）

用法：
  python benchmarks/embedding_eval.py                      # FakeBackend（无模型可跑，确定性）
  python benchmarks/embedding_eval.py --backend sentence_transformers
  python benchmarks/embedding_eval.py --backend openai --seed 7 --json eval_report.json
  python benchmarks/embedding_eval.py --limit 20

可复现性：同 backend + 同 seed → 相同输出（FakeBackend 完全确定；
真实模型受硬件/版本影响，属正常偏差）。
"""
import argparse
import io
import json
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# ---------- 固定评测数据集（v5.7.8 锁定，勿改） ----------
EVAL_SET = [
    {"query": "python 连接 mysql 数据库", "content": "python 使用 mysql-connector 连接 mysql 数据库并执行查询", "keywords": ["python", "mysql"]},
    {"query": "选择 python web 框架", "content": "django 与 flask 是流行的 python web 框架，django 全栈、flask 轻量", "keywords": ["django", "flask"]},
    {"query": "docker 部署 kubernetes", "content": "docker 容器化应用后由 kubernetes 编排部署集群", "keywords": ["docker", "kubernetes"]},
    {"query": "sqlite 与 postgresql 差异", "content": "sqlite 单文件嵌入式适合本地，postgresql 服务端适合多写并发", "keywords": ["sqlite", "postgresql"]},
    {"query": "记忆衰减与遗忘曲线", "content": "艾宾浩斯遗忘曲线驱动记忆衰减，重要记忆可置顶冻结衰减", "keywords": ["衰减", "遗忘"]},
    {"query": "加密存储与密钥管理", "content": "AES-256-GCM 认证加密存储，PBKDF2 派生密钥，密钥文件权限 600", "keywords": ["加密", "密钥"]},
    {"query": "向量检索与语义相似度", "content": "嵌入向量余弦相似度实现语义检索，与 TF-IDF 加权融合提升召回", "keywords": ["向量", "相似度"]},
    {"query": "多租户隔离与审计", "content": "租户边界过滤 SQL 与全文索引，审计日志记录越权访问", "keywords": ["租户", "审计"]},
    {"query": "备份恢复与 WAL", "content": "SQLite WAL 模式支持在线备份，崩溃后自动恢复", "keywords": ["备份", "WAL"]},
    {"query": "agent memory governance", "content": "agent pin freezes decay, agent forget soft-deletes with reason", "keywords": ["pin", "forget"]},
    {"query": "knowledge graph entities", "content": "graph entities and relations extracted from memories for fact evolution", "keywords": ["graph", "entities"]},
    {"query": "data connectors ingestion", "content": "json csv markdown file url connectors ingest memories with audit", "keywords": ["connectors", "ingest"]},
    {"query": "bi-temporal fact tracking", "content": "valid_from valid_to windows expire stale facts instead of deleting", "keywords": ["valid", "expire"]},
    {"query": "GDPR data portability", "content": "gdpr export erasure right to be forgotten with backup", "keywords": ["gdpr", "export"]},
    {"query": "FTS5 full text search", "content": "sqlite fts5 tokenizer indexes text for fast keyword search", "keywords": ["fts5", "tokenizer"]},
    {"query": "privacy levels internal strict", "content": "privacy levels public internal private strict control access", "keywords": ["privacy", "strict"]},
    {"query": "conflict reconcile strategies", "content": "keep newer keep higher importance expire stale conflict side", "keywords": ["conflict", "stale"]},
    {"query": "MCP tools for agents", "content": "MCP server exposes memory tools to claude and other agents", "keywords": ["mcp", "tools"]},
    {"query": "REST API endpoints", "content": "REST API provides memories search valid-at connectors endpoints", "keywords": ["api", "endpoints"]},
    {"query": "CLI commands list", "content": "CLI provides add search stats export import supersede commands", "keywords": ["cli", "commands"]},
]


def _dcg(rels):
    return sum(r / __import__("math").log2(i + 2)
               for i, r in enumerate(rels) if r > 0)


def _ndcg(rels, k):
    d = _dcg(rels[:k])
    ideal = _dcg(sorted(rels, reverse=True)[:k])
    return d / ideal if ideal > 0 else 0.0


def _metrics(hits, relevant_ids, k=10):
    """hits: [{"id": ...}]；relevant_ids: set"""
    ranked = [h["id"] for h in hits]
    rels = [1 if rid in relevant_ids else 0 for rid in ranked]
    rec5 = sum(rels[:5]) / max(1, len(relevant_ids))
    mrr = 0.0
    for i, r in enumerate(rels[:10]):
        if r:
            mrr = 1.0 / (i + 1)
            break
    return {"recall@5": round(rec5, 4), "mrr@10": round(mrr, 4),
            "ndcg@10": round(_ndcg(rels, 10), 4)}


def run_eval(backend="fake", seed=42, limit=None, verbose=False):
    from core.mindforge import MindForge
    from core.types import MemoryConfig

    dataset = EVAL_SET[:limit] if limit else EVAL_SET
    d = tempfile.mkdtemp(prefix="mf_eval_")
    try:
        os.environ["MINDFORGE_EMBEDDING_BACKEND"] = backend
        if backend == "fake":
            os.environ["MINDFORGE_EMBEDDING_MODEL"] = str(seed)
        mf = MindForge(config=MemoryConfig(
            db_path=os.path.join(d, "m.db"),
            key_file=os.path.join(d, "k.key"),
            encrypted=False))
        # 写入正例（每 query 一条）
        for item in dataset:
            mf.add(item["content"], category="eval")
        # 重建嵌入（fake/真实后端均可用）
        rb = mf.rebuild_embeddings(incremental=False)
        if verbose:
            print("rebuild:", rb)

        from core.query import _vector_degradation_warned
        _vector_degradation_warned = True  # 静默降级警告

        st = mf._storage
        idx = mf._index
        if idx.needs_hydration:
            idx.hydrate(st.get_indexable_documents())

        results = {}
        for item in dataset:
            q = item["query"]
            rel = set()
            for i2, other in enumerate(dataset):
                kws = other["keywords"]
                if any(k in q for k in kws) or any(k in item["keywords"] for k in kws):
                    pass
            # 正例 = 本 query 对应的内容
            rel = set()
            for i2, other in enumerate(dataset):
                if i2 == dataset.index(item):
                    continue
                if any(k in q for k in other["keywords"]):
                    rel.add(other["content"])
            rel.add(item["content"])

            def ids(hits):
                return [{"id": h.get("entry", h).id if hasattr(h.get("entry", h), "id") else h.get("id", "")} for h in hits]

            # hybrid
            r = mf.search(q, max_results=10)
            ids_hybrid = [{"id": c.memory_id} for c in r.chunks[:10]]
            # vector
            vres = st.vector_search(q, top_k=10)
            ids_vec = [{"id": v["entry"].id} for v in vres]
            # fts5
            fres = idx.fts_search(st._get_conn(), q, top_k=10)
            ids_fts = [{"id": doc_id} for doc_id, _ in fres]
            # tfidf
            tres = idx.search(q, top_k=10)
            ids_tf = [{"id": doc_id} for doc_id, _ in tres]

            # 建立 content -> id 映射（get_indexable_documents 返回 {id: content}）
            content_ids = {content: mid for mid, content in st.get_indexable_documents().items()}
            rel_ids = {content_ids[c] for c in rel if c in content_ids}

            def m(hs):
                return _metrics(hs, rel_ids)

            for name, hs in (("hybrid", ids_hybrid), ("vector", ids_vec),
                             ("fts5", ids_fts), ("tfidf", ids_tf)):
                results.setdefault(name, []).append(m(hs))

        # 汇总
        summary = {}
        for name, lst in results.items():
            n = len(lst)
            summary[name] = {k2: round(sum(d[k2] for d in lst) / n, 4)
                             for k2 in ("recall@5", "mrr@10", "ndcg@10")}
        return {"backend": backend, "seed": seed,
                "dataset_size": len(dataset), "metrics": summary,
                "weights": {"text": 0.6, "vector": 0.4}}
    finally:
        mf.close()
        import shutil
        shutil.rmtree(d, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description="MindForge 向量检索评测基线 (v5.7.8)")
    ap.add_argument("--backend", default="fake",
                    choices=["fake", "sentence_transformers", "openai", "ollama", "http"])
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--json", default="", help="输出 JSON 报告路径")
    args = ap.parse_args()

    t0 = time.time()
    report = run_eval(backend=args.backend, seed=args.seed, limit=args.limit)
    report["elapsed_s"] = round(time.time() - t0, 2)

    print(f"\n=== MindForge 检索质量评测 (v5.7.8) ===")
    print(f"backend={report['backend']}  seed={report['seed']}  dataset={report['dataset_size']}  elapsed={report['elapsed_s']}s")
    print(f"融合权重: text={report['weights']['text']} / vector={report['weights']['vector']}")
    print(f"\n{'路径':<12}{'Recall@5':>10}{'MRR@10':>10}{'NDCG@10':>10}")
    print("-" * 42)
    for name, m in report["metrics"].items():
        print(f"{name:<12}{m['recall@5']:>10}{m['mrr@10']:>10}{m['ndcg@10']:>10}")
    if args.json:
        with io.open(args.json, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\n报告已写入: {args.json}")


if __name__ == "__main__":
    main()
