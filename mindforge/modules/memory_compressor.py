"""
MindForge v6.1.5 新增：记忆压缩引擎

自动识别语义相近的记忆簇，生成合并摘要以节省检索位序，
同时保留原始记忆可随时解压缩（反向操作）。

核心思想：
  - 检索时 Top-K 窗口有限，同一主题的碎片化记忆会互相挤占位次。
  - 压缩将 N 条同主题记忆 → 1 条摘要 + N 条标记为 compressed_into 的原始条目。
  - 摘要在检索中占 1 个位次，命中后可展开为全部原始条目（类似文档聚合）。
  - 解压缩恢复独立状态，无数据丢失。

设计约束：
  - 不依赖外部 LLM——压缩算法为 TF-IDF 关键短语提取 + 结构模板拼接。
  - 与现有四层记忆模型兼容：压缩不改变 layer，不触发 decay。
  - STRICT 隐私级别的记忆不参与自动压缩（fail-closed）。
  - 操作原子：压缩/解压缩写入同一事务，不留中间态。
"""

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)


@dataclass
class CompressionCluster:
    """一个压缩簇：多条记忆 + 摘要文本"""
    cluster_id: str
    summary: str
    source_ids: List[str]
    category: str
    importance: str
    created_at: str
    metadata: Dict[str, Any] = field(default_factory=dict)


class MemoryCompressor:
    """记忆压缩引擎

    使用方式：
        compressor = MemoryCompressor(storage, index)
        # 自动检测并压缩
        result = compressor.auto_compress(threshold=0.75, max_cluster=8)
        # 手动指定一组记忆压缩
        result = compressor.compress_group(ids=["id1", "id2", "id3"])
        # 解压缩
        result = compressor.decompress(cluster_id="xxx")
    """

    def __init__(self, storage, index):
        """
        Args:
            storage: StorageEngine 实例
            index: IndexEngine 实例
        """
        self.storage = storage
        self.index = index

    def _hydrate_index_from_storage(self) -> int:
        """v6.3.6：从 storage 现场加载明文记忆到 TF-IDF 索引。

        背景：门面/CLI 的搜索主路径走 FTS5 + 模糊，IndexEngine 的
        _doc_texts 只有显式 index_memory 后才有内容；真实使用中几乎
        恒为空，导致 find_similar_clusters 无候选可聚。

        过滤规则与压缩语义一致：
        - trash 不参与
        - 已压缩源（metadata.compressed_into）不重复参与
        - STRICT 隐私不参与（find 阶段仍会二次过滤，此处提前剪除）

        Returns:
            实际索引的记忆条数（失败返回 0，保持旧行为：0 簇）
        """
        try:
            mems = self.storage.list_memories(limit=10000)
        except Exception:
            logger.warning("compressor 索引水合失败：list_memories 异常", exc_info=True)
            return 0
        count = 0
        for m in mems:
            content = getattr(m, "content", None)
            mid = getattr(m, "id", None) or getattr(m, "memory_id", None)
            if not content or not mid:
                continue
            if getattr(m, "category", "") == "trash":
                continue
            meta = getattr(m, "metadata", None)
            if isinstance(meta, dict) and meta.get("compressed_into"):
                continue
            privacy = getattr(m, "privacy", None)
            pval = getattr(privacy, "value", privacy)
            if str(pval).upper() == "STRICT":
                continue
            self.index.index_memory(mid, content)
            count += 1
        if count:
            # index_memory 在 >=5 条时自动 fit；小库手动补
            if not getattr(self.index, "_fitted", False):
                self.index._fit_vectorizer()
            logger.info("compressor 索引水合完成：%d 条候选", count)
        return count

    def find_similar_clusters(
        self,
        threshold: float = 0.75,
        min_cluster_size: int = 3,
        max_cluster_size: int = 8,
        categories: Optional[List[str]] = None,
    ) -> List[List[str]]:
        """基于 TF-IDF 余弦相似度聚类，找出相似记忆组。

        使用贪心合并：按 doc_id 遍历，若与已有簇内任一条相似度
        超阈值则合入，否则自成一簇。仅保留 >= min_cluster_size 的簇。

        Args:
            threshold: 相似度阈值 (0-1)
            min_cluster_size: 最小簇大小
            max_cluster_size: 最大簇大小（超过则按相似度分批）
            categories: 可选，仅在指定类别内聚类

        Returns:
            List of memory_id lists (each inner list is a cluster)
        """
        docs = self.index._doc_texts
        if not docs:
            return []

        # Filter by category if specified
        candidate_ids = list(docs.keys())
        if categories:
            candidate_ids = [
                mid for mid in candidate_ids
                if self._get_category(mid) in categories
            ]

        if len(candidate_ids) < min_cluster_size:
            return []

        # Build similarity via vectorizer
        self._ensure_fitted()
        if not self.index._fitted:
            return []

        vectors = {}
        for mid in candidate_ids:
            text = docs.get(mid)
            if text:
                vectors[mid] = self.index.vectorizer.transform(text)

        # Greedy clustering
        clusters: List[List[str]] = []
        assigned: set = set()

        for mid in candidate_ids:
            if mid in assigned:
                continue
            vec = vectors.get(mid)
            if not vec:
                continue
            cluster = [mid]
            assigned.add(mid)

            for other in candidate_ids:
                if other in assigned or len(cluster) >= max_cluster_size:
                    continue
                other_vec = vectors.get(other)
                if not other_vec:
                    continue
                sim = self.index.vectorizer.cosine_similarity(vec, other_vec)
                if sim >= threshold:
                    cluster.append(other)
                    assigned.add(other)

            if len(cluster) >= min_cluster_size:
                clusters.append(cluster)

        return clusters

    def auto_compress(
        self,
        threshold: float = 0.75,
        min_cluster_size: int = 3,
        max_cluster_size: int = 8,
        dry_run: bool = False,
        categories: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """自动压缩：检测 + 生成摘要 + 写入。

        v6.3.5：加密库入口即拒绝（此前守卫深埋在 _write_compression，
        聚空簇时静默返回 0，用户无从知晓原因）。

        Args:
            threshold: 相似度阈值
            min_cluster_size: 最小簇大小
            max_cluster_size: 最大簇大小
            dry_run: 为 True 时只返回将压缩的簇，不写入
            categories: 限定类别

        Returns:
            {
                "compressed_count": int,
                "clusters": [CompressionCluster dict],
                "skipped_strict": int,
            }
        """
        # v6.3.5：加密库入口即拒绝（摘要需进 FTS，当前实现仅支持明文库）
        if getattr(self.storage, "encrypted", False) is True:
            from mindforge.core.encryption import SecurityError
            raise SecurityError(
                "加密库暂不支持记忆压缩（摘要需进入明文 FTS 索引）。"
                "如需压缩请先解密数据库。"
            )

        # v6.3.6 修复：真实库上 _doc_texts 可能为空（搜索主路径走 FTS5，
        # 不触发 TF-IDF 索引水合），find_similar_clusters 会静默返回 []，
        # 压缩恒为 0 簇。此处从 storage 现场加载明文记忆并索引。
        if not getattr(self.index, "_doc_texts", None):
            self._hydrate_index_from_storage()

        clusters = self.find_similar_clusters(
            threshold=threshold,
            min_cluster_size=min_cluster_size,
            max_cluster_size=max_cluster_size,
            categories=categories,
        )

        if not clusters:
            return {"compressed_count": 0, "clusters": [], "skipped_strict": 0}

        results = []
        skipped_strict = 0

        for cluster_ids in clusters:
            # STRICT privacy guard: remove STRICT memories from cluster
            safe_ids = []
            for mid in cluster_ids:
                entry = self.storage.get_memory(mid)
                if entry and getattr(entry, "privacy", "PUBLIC") == "STRICT":
                    skipped_strict += 1
                else:
                    safe_ids.append(mid)
            if len(safe_ids) < min_cluster_size:
                continue

            summary = self.generate_summary(safe_ids)
            cluster_obj = CompressionCluster(
                cluster_id=str(uuid.uuid4()),
                summary=summary,
                source_ids=safe_ids,
                category=self._majority_category(safe_ids),
                importance=self._max_importance(safe_ids),
                created_at=self._now_iso(),
            )

            if not dry_run:
                self._write_compression(cluster_obj)

            results.append({
                "cluster_id": cluster_obj.cluster_id,
                "summary": cluster_obj.summary[:120],
                "source_count": len(cluster_obj.source_ids),
            })

        return {
            "compressed_count": len(results),
            "clusters": results,
            "skipped_strict": skipped_strict,
        }

    def compress_group(
        self,
        memory_ids: List[str],
        dry_run: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """手动指定一组记忆进行压缩。

        Args:
            memory_ids: 要压缩的记忆 id 列表（>= 2）
            dry_run: 只返回摘要不写入

        Returns:
            压缩结果 dict 或 None（ids < 2 时）
        """
        if len(memory_ids) < 2:
            return None

        # v6.3.5：加密库入口即拒绝（与 auto_compress 一致）
        if getattr(self.storage, "encrypted", False) is True:
            from mindforge.core.encryption import SecurityError
            raise SecurityError(
                "加密库暂不支持记忆压缩（摘要需进入明文 FTS 索引）。"
                "如需压缩请先解密数据库。"
            )

        # STRICT guard
        safe_ids = []
        for mid in memory_ids:
            entry = self.storage.get_memory(mid)
            if entry and getattr(entry, "privacy", "PUBLIC") != "STRICT":
                safe_ids.append(mid)

        if len(safe_ids) < 2:
            return None

        summary = self.generate_summary(safe_ids)
        cluster_obj = CompressionCluster(
            cluster_id=str(uuid.uuid4()),
            summary=summary,
            source_ids=safe_ids,
            category=self._majority_category(safe_ids),
            importance=self._max_importance(safe_ids),
            created_at=self._now_iso(),
        )

        if not dry_run:
            self._write_compression(cluster_obj)

        return {
            "cluster_id": cluster_obj.cluster_id,
            "summary": cluster_obj.summary,
            "source_count": len(cluster_obj.source_ids),
        }

    def generate_summary(self, memory_ids: List[str]) -> str:
        """基于 TF-IDF 关键短语提取 + 结构模板生成摘要。

        不依赖外部 LLM，纯统计方法：
        1. 提取各条目中 TF-IDF 权重最高的 3 个短语
        2. 去重后按全局频率排序
        3. 以模板 "[摘要] N 条记忆合并 | 核心: ..." 生成文本
        """
        self._ensure_fitted()

        # Collect content texts
        texts = []
        for mid in memory_ids:
            text = self.index._doc_texts.get(mid)
            if not text and hasattr(self.storage, "get_memory"):
                entry = self.storage.get_memory(mid)
                if entry and hasattr(entry, "content"):
                    text = entry.content
            if text:
                texts.append(text)

        if not texts:
            return f"[摘要] {len(memory_ids)} 条记忆（内容不可读）"

        # Extract top terms across all texts
        term_scores: Dict[str, float] = {}
        if self.index._fitted:
            for text in texts:
                vec = self.index.vectorizer.transform(text)
                for idx, score in vec.items():
                    term = self._idx_to_term(idx)
                    if term and len(term) >= 2:
                        term_scores[term] = max(term_scores.get(term, 0), score)

        top_terms = sorted(term_scores.items(), key=lambda x: x[1], reverse=True)[:8]
        keywords = [t[0] for t in top_terms]

        # Build summary with first sentence of each text
        lead_sentences = []
        for text in texts[:3]:
            sentence = text.split("。")[0].split(".")[0][:80]
            if sentence.strip():
                lead_sentences.append(sentence.strip())

        parts = [f"[摘要] {len(memory_ids)} 条记忆合并"]
        if keywords:
            parts.append(f"核心词: {', '.join(keywords[:6])}")
        if lead_sentences:
            parts.append(f"要点: {' | '.join(lead_sentences[:2])}")

        return " | ".join(parts)

    def decompress(self, cluster_id: str) -> Dict[str, Any]:
        """解压缩：恢复原始记忆的独立检索状态。

        v6.3.5 重写：与 _write_compression 对称走存储层通道。原实现裸
        DELETE memories——摘要行在 memory_fts / memory_embeddings /
        审计的派生数据全部残留（v6.1.2 级联删除修复前同类问题）。
        现改为：源恢复走 update_memory（读-合并-写回去除标记），
        摘要删除走 delete_memory（级联清理 FTS / 嵌入 / 子表）。

        Args:
            cluster_id: 压缩簇 ID

        Returns:
            {"decompressed": bool, "restored_count": int, "error": str}
        """
        try:
            conn = self.storage._get_conn()
            self._ensure_table(conn)
            # Find compression record
            row = conn.execute(
                "SELECT source_ids, summary_id FROM compressions WHERE cluster_id = ?",
                (cluster_id,)
            ).fetchone()

            if not row:
                return {"decompressed": False, "restored_count": 0,
                        "error": f"压缩簇 {cluster_id} 不存在"}

            source_ids = json.loads(row[0])
            summary_id = row[1]

            # Restore source entries: remove compressed_into marker
            # (update_memory 的 metadata 为整体替换语义——读旧、去键、写回)
            restored = 0
            for mid in source_ids:
                entry = self.storage.get_memory(mid)
                if entry is None:
                    continue  # 源已被独立删除，跳过
                meta = dict(entry.metadata or {})
                meta.pop("compressed_into", None)
                if self.storage.update_memory(mid, metadata=meta, actor="memory-compressor"):
                    restored += 1

            # Delete summary memory (cascade: FTS / embeddings / subtables)
            self.storage.delete_memory(summary_id, actor="memory-compressor")

            # Remove mapping row
            conn.execute("DELETE FROM compressions WHERE cluster_id = ?", (cluster_id,))
            conn.commit()

            return {"decompressed": True, "restored_count": restored, "error": ""}

        except Exception as e:
            logger.error("解压缩失败: %s", e)
            return {"decompressed": False, "restored_count": 0, "error": str(e)}

    def list_compressions(self) -> List[Dict[str, Any]]:
        """列出所有压缩簇"""
        try:
            conn = self.storage._get_conn()
            self._ensure_table(conn)
            rows = conn.execute(
                "SELECT cluster_id, summary_id, source_ids, category, importance, created_at "
                "FROM compressions ORDER BY created_at DESC"
            ).fetchall()
            return [
                {
                    "cluster_id": r[0],
                    "summary_id": r[1],
                    "source_ids": json.loads(r[2]),
                    "category": r[3],
                    "importance": r[4],
                    "created_at": r[5],
                }
                for r in rows
            ]
        except Exception:
            return []

    # ===================================================================
    # Private helpers
    # ===================================================================

    @staticmethod
    def _ensure_table(conn):
        conn.execute("""
            CREATE TABLE IF NOT EXISTS compressions (
                cluster_id TEXT PRIMARY KEY,
                summary_id TEXT NOT NULL,
                source_ids TEXT NOT NULL,
                category TEXT DEFAULT 'general',
                importance TEXT DEFAULT 'MEDIUM',
                created_at TEXT NOT NULL,
                metadata TEXT DEFAULT '{}'
            )
        """)

    def _write_compression(self, cluster: CompressionCluster):
        """原子写入压缩结果：创建摘要记忆 + 标记源条目 + 记录映射。

        v6.3.5 重写：写路径全部改走存储层标准通道。原实现（v6.1.5 草稿）
        直插 memories 表存在五项缺陷——created_at 以 TEXT 写入 REAL 列、
        FTS 索引不同步（摘要对 FTS5 路不可见）、加密库明文直插（安全缺陷）、
        不生成嵌入向量（向量路不可见）、不落审计。现改为：
          - 摘要走 add_memory（id 分配 / FTS / 嵌入 / 审计 自动正确）
          - 源标记走 update_memory（读-合并-写回，metadata 为整体替换语义）
          - compressions 映射表保留模块私有 SQL（不触碰 memories 表）
        """
        if getattr(self.storage, "encrypted", False) is True:
            from mindforge.core.encryption import SecurityError
            raise SecurityError(
                "加密库暂不支持记忆压缩：摘要需进入 FTS 检索索引，"
                "而当前压缩实现仅支持明文库。如需压缩请先解密数据库，"
                "或等待后续版本的加密库压缩支持。"
            )

        from mindforge.core.types import Importance, MemoryLayer

        # 1. 摘要记忆：标准写入通道（FTS + 嵌入 + 审计 + 完整 uuid）
        try:
            importance_enum = Importance(cluster.importance)
        except ValueError:
            importance_enum = Importance.MEDIUM

        summary_entry = self.storage.add_memory(
            content=cluster.summary,
            category=cluster.category,
            importance=importance_enum,
            layer=MemoryLayer.LONG_TERM,
            tags=["compression-summary"],
            metadata={
                "is_compression_summary": True,
                "cluster_id": cluster.cluster_id,
            },
            source_session="memory-compressor",
        )
        summary_id = summary_entry.id

        # 2. 源条目标记 compressed_into（合并语义：读旧 → 合并 → 写回）
        for mid in cluster.source_ids:
            entry = self.storage.get_memory(mid)
            if entry is None:
                continue  # 源已不存在（并发删除）——摘要仍成立
            meta = dict(entry.metadata or {})
            meta["compressed_into"] = cluster.cluster_id
            self.storage.update_memory(mid, metadata=meta, actor="memory-compressor")

        # 3. 映射记录（模块私有表，不触碰 memories）
        conn = self.storage._get_conn()
        self._ensure_table(conn)
        conn.execute("""
            INSERT INTO compressions (cluster_id, summary_id, source_ids, category, importance, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            cluster.cluster_id,
            summary_id,
            json.dumps(cluster.source_ids),
            cluster.category,
            cluster.importance,
            cluster.created_at,
        ))
        conn.commit()

    def _ensure_fitted(self):
        if not self.index._fitted and self.index._doc_texts:
            self.index._fit_vectorizer()

    @staticmethod
    def _idx_to_term(idx: int, vectorizer=None):
        """TFIDFVectorizer 反向查找：从 vocab 索引取词"""
        if vectorizer is None:
            return None
        for term, i in vectorizer.vocab.items():
            if i == idx:
                return term
        return None

    def _idx_to_term(self, idx: int) -> Optional[str]:
        """Instance-level reverse lookup using self.index.vectorizer"""
        for term, i in self.index.vectorizer.vocab.items():
            if i == idx:
                return term
        return None

    def _get_category(self, mid: str) -> Optional[str]:
        entry = self.storage.get_memory(mid)
        return getattr(entry, "category", None) if entry else None

    def _majority_category(self, ids: List[str]) -> str:
        cats: Dict[str, int] = {}
        for mid in ids:
            c = self._get_category(mid)
            if c:
                cats[c] = cats.get(c, 0) + 1
        return max(cats, key=cats.get) if cats else "general"

    def _max_importance(self, ids: List[str]) -> str:
        rank = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
        best = "MEDIUM"
        for mid in ids:
            entry = self.storage.get_memory(mid)
            if entry:
                imp = getattr(entry, "importance", "MEDIUM")
                # Handle both enum and string
                imp_val = getattr(imp, "value", str(imp)).upper() if imp else "MEDIUM"
                if rank.get(imp_val, 1) > rank.get(best, 1):
                    best = imp_val
        return best

    @staticmethod
    def _now_iso() -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()
