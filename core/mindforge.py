"""
MindForge v5.5.8 主入口类
统一的 API 接口，集成所有核心功能
"""

from typing import Optional, List, Dict, Any
from pathlib import Path
from datetime import datetime, timezone
import json
import csv
import uuid
import logging

logger = logging.getLogger(__name__)

from .types import (
    PrivacyLevel,
    Importance,
    MemoryType,
    MemoryLayer,
    MemoryConfig,
    DramaGenre,
    DramaStatus,
)
from .storage import StorageEngine, MemoryEntry
from .encryption import (
    EncryptionEngine,
    SecurityError,
    init_engine as _init_engine,
    rekey_engine as _rekey_engine,
    get_key_params,
    PBKDF2_ITERATIONS_CURRENT,
)
from .indexer import IndexEngine
from .query import QueryEngine
from .embedding import EmbeddingEngine

# v5.6.2 收敛：版本号唯一真值在 core/version.py，
# 此前此处带一份硬编码兜底，发版漏改会导致与顶层包版本漂移
from .version import __version__


# ===== 路径安全校验（v5.2.9 新增：核心层统一防护，防止路径遍历 / 符号链接攻击）=====

# v5.3.5 安全加固：检测 Windows 短文件名（8.3）绕过尝试
def _is_suspicious_windows_path_mf(comp: str) -> bool:
    """检测 Windows 短文件名绕过模式"""
    if not comp or len(comp) == 0:
        return False
    # v5.4.5 修复 #11：豁免 Unix 根路径 '/'，否则 Linux/Mac 上所有导出功能不可用
    if comp == '/':
        return False
    import re as _re
    # v5.3.7 修复：豁免 Windows 盘符根（如 C:\、D:），之前误报导致所有导出功能失效
    if len(comp) <= 3 and _re.match(r'^[A-Za-z]:\\?$', comp):
        return False
    if _re.match(r'^[^~]{1,6}~\d(\..{1,3})?$', comp, _re.IGNORECASE):
        return True
    # v5.5.1 fix: colon only dangerous on Windows
    import sys as _sys
    _dangerous = ('..', '/', '\\', '\x00', ':') if _sys.platform == 'win32' else ('..', '/', '\\', '\x00')
    if any(s in comp for s in _dangerous):
        return True
    return False


def _safe_path(path_str, must_exist=False, allow_symlinks=False,
               max_size=None, allowed_exts=None, max_len=4096):
    """校验文件路径安全性，防止路径遍历攻击

    Args:
        path_str: 用户输入的路径
        must_exist: 是否要求文件必须存在
        allow_symlinks: 是否允许符号链接
        max_size: 文件大小上限（仅 must_exist=True 时）
        allowed_exts: 允许的文件扩展名集合
        max_len: 路径最大长度

    Returns:
        Path: 校验通过后的绝对路径

    Raises:
        ValueError / OSError: 路径不安全
    """
    if not path_str or not isinstance(path_str, str):
        raise ValueError("路径不能为空")
    if len(path_str) > max_len:
        raise ValueError(f"路径过长（上限 {max_len} 字符）")
    # v5.3.5 安全：过滤 Unicode 双向和控制字符
    import unicodedata
    for ch in path_str:
        cat = unicodedata.category(ch)
        if cat in ('Cf', 'Cc') and ch not in '\n\r\t':
            raise ValueError("路径中包含非法控制字符")
    # v5.3.5 安全：逐组件检测 Windows 短文件名绕过
    target = Path(path_str)
    if not target.is_absolute():
        target = Path.cwd() / target
    for comp in target.parts:
        if comp and _is_suspicious_windows_path_mf(comp):
            raise ValueError(f"路径组件不安全: {comp}")

    try:
        resolved = target.resolve()
    except (OSError, RuntimeError) as e:
        raise ValueError(f"路径解析失败: {e}")

    # 符号链接检查 — 使用 lstat() 逐组件检查原始路径，非解析后路径
    if not allow_symlinks:
        import os as _os
        check_path = target
        while check_path != check_path.parent:
            try:
                if _os.path.islink(str(check_path)):
                    raise ValueError(f"不允许操作符号链接: {check_path}")
            except OSError:
                pass
            check_path = check_path.parent

    # 扩展名检查
    if allowed_exts is not None:
        ext = resolved.suffix.lower()
        if ext not in allowed_exts:
            raise ValueError(
                f"不支持的文件类型: {ext}（允许: {', '.join(sorted(allowed_exts))}）"
            )

    # 存在性检查
    if must_exist and not resolved.exists():
        raise FileNotFoundError(f"文件不存在: {resolved}")

    # 大小检查
    if max_size is not None and resolved.exists() and resolved.is_file():
        size = resolved.stat().st_size
        if size > max_size:
            raise ValueError(f"文件过大: {size} 字节（上限 {max_size}）")

    return resolved


class MindForge:
    """MindForge 主类 - AI Agent 终身记忆系统 v5.3.7"""

    def __init__(self, config: Optional[MemoryConfig] = None, **kwargs):
        if config is None:
            config = MemoryConfig(**kwargs)
        self.config = config
        self._encryption: Optional[EncryptionEngine] = None
        self._storage: Optional[StorageEngine] = None
        self._index: Optional[IndexEngine] = None
        self._query: Optional[QueryEngine] = None
        self._multi_agent = None
        self._intent_router = None  # v5.3.9 lazy
        self._federated = None          # v5.4.2 lazy
        self._federated_acl = None      # v5.4.2 lazy
        self._share_conflict = None     # v5.4.2 lazy
        self._evolution = None          # v5.4.8 lazy (记忆巩固)
        self._event_bus = None          # v5.5.5 lazy (事件总线)
        self._privacy_engine = None     # v5.6.2 lazy (隐私引擎)

        if self.config.encrypted:
            self._init_encryption()

        # v5.4.8 P0-001 修复：encrypted=True 但加密引擎未就绪时，
        # 不创建未加密数据库，延迟到 init_with_password() 完成
        self._pending_encryption = (
            self.config.encrypted and self._encryption is None
        )

        if not self._pending_encryption:
            self._init_storage()
            self._init_index()
            self._init_query()
        else:
            self._storage = None
            self._index = None
            self._query = None
            logger.error(
                "encrypted=True but no encryption key available. "
                "Storage initialization deferred. "
                "Call init_with_password(password) to complete setup. "
                "DO NOT use add/search/update until encryption is initialized."
            )

    def _init_encryption(self):
        """初始化加密引擎（v5.2.2 修复：补充缺失的初始化逻辑）

        之前 key_file 存在/不存在两个分支均为 pass，导致加密引擎从未被实际初始化。
        修复后：根据 key_file 是否存在决定新建或加载加密引擎。
        v5.4.7 修复 M-9：当 encrypted=True 但无密码时记录警告日志。
        """

        key_file = Path(self.config.key_file)
        key_file.parent.mkdir(parents=True, exist_ok=True)

        if not key_file.exists():
            # 密钥文件不存在时，生成新密钥需要密码
            # 此场景下应通过 init_with_password() 完成初始化
            logger.warning(
                "encrypted=True but no key file found. "
                "Call init_with_password() to enable encryption."
            )
            return

        # 密钥文件存在时，需要密码加载
        # 实际解密需在 init_with_password() 中完成
        logger.warning(
            "encrypted=True but key file exists and no password provided. "
            "Call init_with_password() to enable encryption."
        )
        return

    def _init_storage(self):
        self._storage = StorageEngine(
            db_path=self.config.db_path,
            encryption=self._encryption,
            encrypted=self.config.encrypted and self._encryption is not None,
        )

    def _init_index(self):
        self._index = IndexEngine(db_path=self.config.db_path)

    def _init_query(self):
        if self._storage and self._index:
            self._query = QueryEngine(self._storage, self._index)

    def _rebuild_index_from_storage(self):
        """从存储层重建内存索引（v5.5.5 修复：替代不存在的方法调用）

        遍历所有非软删除记忆，重新构建 IndexEngine。
        """
        if not self._storage or not self._index:
            return
        try:
            offset = 0
            page_size = 500
            while True:
                entries = self._storage.list_memories(
                    limit=page_size, offset=offset,
                    sort_by="created_at", sort_order="asc"
                )
                if not entries:
                    break
                for entry in entries:
                    if entry.category == 'trash':
                        continue
                    try:
                        imp_val = entry.importance.value if hasattr(entry.importance, 'value') else str(entry.importance)
                        self._index.index_memory(
                            entry.id, entry.content,
                            metadata={
                                "category": entry.category,
                                "tags": entry.tags or [],
                                "importance": imp_val,
                                "layer": entry.layer.value if hasattr(entry.layer, 'value') else str(entry.layer),
                            }
                        )
                    except Exception as e:
                        logger.warning("Failed to index memory %s: %s", entry.id, e)
                offset += page_size
                if len(entries) < page_size:
                    break
        except Exception as e:
            logger.error("Index rebuild failed at offset %d: %s", offset, e)

    def init_with_password(self, password: str):
        """使用密码初始化加密引擎

        v5.4.8 P0-001 修复：支持延迟初始化场景。
        如果 __init__ 因缺少密钥而延迟了存储引擎创建，
        此处完成完整的初始化链。
        """
        if not self.config.encrypted:
            return

        # 检查是否存在未加密的旧数据库
        db_path = Path(self.config.db_path)
        if self._pending_encryption and db_path.exists():
            logger.warning(
                "Existing database found at %s before encryption was initialized. "
                "This database may contain unencrypted data. "
                "Consider backing up and recreating with encryption.",
                db_path
            )

        engine = _init_engine(password, self.config.key_file)
        self._encryption = engine
        self._pending_encryption = False

        # 完成延迟的初始化链
        self._init_storage()
        if self._index is None:
            self._init_index()
        if self._query is None:
            self._init_query()
        else:
            # 更新已有 query engine 的 storage 引用
            self._query.storage = self._storage

    def rekey(self, old_password: str, new_password: str,
              new_iterations: Optional[int] = None) -> dict:
        """更换加密密钥（密码变更或 KDF 参数升级）

        安全流程：
        1. 用旧密码和旧 KDF 参数验证并解密
        2. 生成新密钥（新密码 + 新 KDF 参数 + 新盐）
        3. 原子更新密钥文件（带备份）
        4. 批量重加密所有记忆数据（事务保障，失败回滚）

        Args:
            old_password: 旧密码
            new_password: 新密码（可与旧密码相同，仅升级 KDF 参数）
            new_iterations: 新的 PBKDF2 迭代次数，默认使用当前推荐值

        Returns:
            dict with keys:
                - rekeyed_count: 重加密的记忆条数
                - old_iterations: 旧迭代次数
                - new_iterations: 新迭代次数
                - backup_path: 密钥文件备份路径

        Raises:
            SecurityError: 旧密码错误或重加密失败
        """
        if not self.config.encrypted:
            raise SecurityError("加密未启用，无法执行 rekey")

        if self._pending_encryption:
            raise SecurityError("加密引擎未初始化，请先调用 init_with_password()")

        # 1. 获取旧参数信息（用于返回报告）
        old_params = get_key_params(self.config.key_file)
        old_iterations = old_params.iterations if old_params else 0

        # 2. 更换密钥文件
        old_engine, new_engine = _rekey_engine(
            old_password, new_password,
            key_file=self.config.key_file,
            new_iterations=new_iterations,
        )

        new_params = get_key_params(self.config.key_file)
        new_iterations_val = new_params.iterations if new_params else 0

        # 3. 更新当前实例的加密引擎
        self._encryption = new_engine
        if self._storage:
            self._storage.encryption = new_engine

        # 4. 批量重加密所有记忆
        count = 0
        if self._storage:
            try:
                count = self._storage.rekey_memories(old_engine, new_engine)
            except Exception:
                # 重加密失败：恢复旧密钥文件，确保旧密文 + 旧密钥一致
                import shutil
                backup_path = Path(self.config.key_file).with_suffix(
                    Path(self.config.key_file).suffix + ".bak"
                )
                if backup_path.exists():
                    shutil.copy2(backup_path, self.config.key_file)
                # 恢复引擎引用
                self._encryption = old_engine
                if self._storage:
                    self._storage.encryption = old_engine
                # 全局引擎也恢复
                from core.encryption import _set_global_engine
                _set_global_engine(old_engine)
                raise

        return {
            "rekeyed_count": count,
            "old_iterations": old_iterations,
            "new_iterations": new_iterations_val,
            "backup_path": str(Path(self.config.key_file).with_suffix(".key.bak")),
        }

    def get_encryption_info(self) -> Optional[dict]:
        """获取当前加密配置信息

        Returns:
            加密状态和 KDF 参数 dict，未加密时返回 None
        """
        if not self.config.encrypted:
            return None

        params = get_key_params(self.config.key_file)
        return {
            "enabled": True,
            "algorithm": params.algorithm if params else None,
            "iterations": params.iterations if params else None,
            "is_current": (params.iterations == PBKDF2_ITERATIONS_CURRENT) if params else False,
        }

    def add(self,
            content: str,
            category: str = "general",
            tags: Optional[List[str]] = None,
            privacy: PrivacyLevel = None,
            importance: Importance = None,
            memory_type: MemoryType = None,
            layer: MemoryLayer = None,
            source_session: str = "",
            source_agent: str = "",
            starred: bool = False,
            pinned: bool = False,
            expires_at: float = 0.0,
            metadata: Optional[Dict[str, Any]] = None) -> MemoryEntry:
        """添加一条记忆到存储并建立索引。

        v5.5.2 新增 expires_at 参数（TTL 过期时间戳，0=永不过期）。
        v5.5.6 新增 pinned 参数（置顶标记）。

        Args:
            content: 记忆内容文本。
            category: 分类标签，默认 "general"。
            tags: 自定义标签列表。
            privacy: 隐私级别，默认使用配置中的 default_privacy。
            importance: 重要程度，默认使用配置中的 default_importance。
            memory_type: 记忆类型，默认 MemoryType.TEXT。
            layer: 记忆层级，默认使用配置中的 default_layer。
            source_session: 来源会话 ID（可选）。
            source_agent: 来源 Agent 标识（可选）。
            starred: 是否星标收藏。
            pinned: 是否置顶（v5.5.6 新增）。
            expires_at: 过期时间戳（Unix epoch 秒），0 表示永不过期。
            metadata: 附加元数据字典。

        Returns:
            MemoryEntry: 新创建的记忆条目。包含自动生成的 id、created_at、
            updated_at 等字段，调用方可直接通过 entry.id 获取新记忆的唯一标识。

        Example:
            entry = mf.add("今天开会讨论了 Q3 计划", category="work",
                           tags=["meeting", "planning"])
            print(entry.id)          # 自动生成的 UUID
            print(entry.created_at)  # 创建时间戳
        """
        # v5.5.8: 修复 falsy 枚举值被默认值覆盖的 bug
        # 之前用 `or` 导致 PrivacyLevel.NONE 等有效 falsy 值被 default 替换
        privacy = privacy if privacy is not None else self.config.default_privacy
        importance = importance if importance is not None else self.config.default_importance
        layer = layer if layer is not None else self.config.default_layer
        memory_type = memory_type if memory_type is not None else MemoryType.TEXT

        # v5.6.2 安全修复：content 类型与长度校验
        if not isinstance(content, str):
            raise ValueError("content 必须是 str 类型")
        if not content.strip():
            raise ValueError("content 不能为空")
        _MAX_CONTENT_LENGTH = 1_000_000  # 1MB 字符上限
        if len(content) > _MAX_CONTENT_LENGTH:
            raise ValueError(f"content 超过最大长度限制（{_MAX_CONTENT_LENGTH} 字符）")

        entry = self._storage.add_memory(
            content=content,
            category=category,
            tags=tags,
            privacy=privacy,
            importance=importance,
            memory_type=memory_type,
            layer=layer,
            source_session=source_session,
            source_agent=source_agent,
            starred=starred,
            pinned=pinned,
            expires_at=expires_at,
            metadata=metadata,
        )

        self._index.index_memory(
            entry.id,
            content,
            metadata={"category": category, "tags": tags or [], "importance": importance.value if hasattr(importance, 'value') else str(importance)},
        )

        return entry

    def get(self, memory_id: str, actor: str = "", session_id: str = "") -> Optional[MemoryEntry]:
        """获取记忆"""
        entry = self._storage.get_memory(memory_id, actor, session_id)
        if entry is None:
            return None
        # v5.6.2 安全修复：隐私访问控制
        ok, _reason = self.privacy_engine.check_access(entry, actor, session_id)
        if not ok:
            return None
        return entry

    def search(self,
               query: str,
               max_results: int = 10,
               min_relevance: float = 0.3,
               categories: Optional[List[str]] = None,
               layers: Optional[List[MemoryLayer]] = None,
               agent_id: str = "",
               session_id: str = "",
               use_embedding: bool = True):
        """搜索记忆

        v5.4.5 新增 use_embedding 参数：
        - True（默认）：启用向量召回 + TF-IDF + Fuzzy 多路融合搜索
        - False：降级为 TF-IDF + Fuzzy 两路搜索（资源受限时使用）
        """
        # v5.4.8 P1 修复：query 类型检查，防止非字符串输入崩溃
        if query is None:
            query = ""
        elif not isinstance(query, str):
            query = str(query)
        result = self._query.search(
            query=query,
            max_results=max_results,
            min_relevance=min_relevance,
            categories=categories,
            layers=layers,
            agent_id=agent_id,
            session_id=session_id,
            use_embedding=use_embedding,
        )
        # 批量获取记忆条目做隐私过滤，避免 N+1 查询
        if result.chunks:
            mem_ids = [c.memory_id for c in result.chunks]
            entries = self._storage.get_memories_by_ids(mem_ids)
            entry_map = {e.id: e for e in entries if e is not None}
            filtered_chunks = []
            for chunk in result.chunks:
                entry = entry_map.get(chunk.memory_id)
                if entry is None:
                    continue
                ok, _ = self.privacy_engine.check_access(entry, agent_id, session_id)
                if ok:
                    filtered_chunks.append(chunk)
            result.chunks = filtered_chunks
            result.total_found = len(filtered_chunks)
        return result

    def rebuild_embeddings(self, batch_size: int = 100,
                           incremental: bool = True) -> dict:
        """重建/增量构建嵌入向量（v5.4.5 新增，v5.4.6 增量模式）

        v5.4.6 改进：默认 incremental=True，只处理缺失嵌入的记忆。
        add_memory 时已自动生成嵌入，此方法仅补全缺失项。
        全量重建（模型升级后）设 incremental=False。

        Args:
            batch_size: 批量编码大小
            incremental: True=只处理缺失项（默认），False=全量重建

        Returns:
            {success, total, embedded, skipped, errors, mode}
        """
        return self._storage.rebuild_embeddings(batch_size=batch_size,
                                                  incremental=incremental)

    def get_embedding_status(self) -> dict:
        """获取嵌入向量状态（v5.4.5 新增，v5.4.7 修复）

        v5.4.7 修复：即使 embedding engine 不可用，也查询 DB 返回实际向量数量，
        让用户知道历史向量是否存在。

        Returns:
            {available, model_name, dimension, embedding_count}
        """
        # 无论 engine 是否可用，都查询 DB 中实际向量数量
        conn = self._storage._get_conn()
        row = conn.execute(
            "SELECT COUNT(*) FROM memory_embeddings"
        ).fetchone()
        count = row[0] if row else 0

        engine = self._storage.embedding_engine
        if engine is None or not engine.is_available:
            return {
                "available": False,
                "model_name": "",
                "dimension": 0,
                "embedding_count": count,
            }
        return {
            "available": True,
            "model_name": engine.model_name,
            "dimension": engine.dimension,
            "embedding_count": count,
        }

    def list(self,
             category: Optional[str] = None,
             layer: Optional[MemoryLayer] = None,
             starred: Optional[bool] = None,
             pinned: Optional[bool] = None,
             created_after: Optional[float] = None,
             created_before: Optional[float] = None,
             limit: int = 50,
             offset: int = 0,
             sort_by: str = "created_at",
             sort_order: str = "desc") -> List[MemoryEntry]:
        """列出记忆

        v5.5.6 新增 pinned 参数（置顶筛选）。
        """
        return self._storage.list_memories(
            category=category,
            layer=layer,
            starred=starred,
            pinned=pinned,
            created_after=created_after,
            created_before=created_before,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
            sort_order=sort_order,
        )

    def star(self, memory_id: str, actor: str = "", session_id: str = "") -> bool:
        """收藏记忆（加星标）"""
        return self._storage.update_memory(
            entry_id=memory_id,
            starred=True,
            actor=actor,
            session_id=session_id,
        )

    def unstar(self, memory_id: str, actor: str = "", session_id: str = "") -> bool:
        """取消收藏（取消星标）"""
        return self._storage.update_memory(
            entry_id=memory_id,
            starred=False,
            actor=actor,
            session_id=session_id,
        )

    def update(self,
               memory_id: str,
               content: Optional[str] = None,
               category: Optional[str] = None,
               tags: Optional[List[str]] = None,
               privacy: Optional[PrivacyLevel] = None,
               importance: Optional[Importance] = None,
               layer: Optional[MemoryLayer] = None,
               starred: Optional[bool] = None,
               pinned: Optional[bool] = None,
               metadata: Optional[Dict[str, Any]] = None,
               actor: str = "",
               session_id: str = "") -> bool:
        """更新记忆

        v5.5.6 新增 pinned 参数。
        """
        # v5.6.2 安全修复：隐私访问控制
        entry = self._storage.get_memory(memory_id, actor, session_id)
        if entry is None:
            return False
        ok, _reason = self.privacy_engine.check_access(entry, actor, session_id)
        if not ok:
            return False

        success = self._storage.update_memory(
            entry_id=memory_id,
            content=content,
            category=category,
            tags=tags,
            privacy=privacy,
            importance=importance,
            layer=layer,
            starred=starred,
            pinned=pinned,
            metadata=metadata,
            actor=actor,
            session_id=session_id,
        )

        if success:
            # v5.5.1 fix: 从存储层获取最新记忆以重建完整索引元数据
            try:
                updated_entry = self._storage.get_memory(memory_id, actor, session_id)
                if updated_entry:
                    idx_content = content or updated_entry.content
                    idx_meta = {
                        "category": updated_entry.category,
                        "tags": updated_entry.tags or [],
                        "importance": updated_entry.importance.value if hasattr(updated_entry.importance, 'value') else str(updated_entry.importance),
                    }
                    self._index.index_memory(memory_id, idx_content, metadata=idx_meta)
            except Exception as e:
                logger.warning("Failed to re-index memory %s after update: %s", memory_id, e)

        return success

    def delete(self, memory_id: str, actor: str = "",
               session_id: str = "", hard_delete: bool = False) -> bool:
        """删除记忆"""
        # v5.6.2 安全修复：隐私访问控制
        entry = self._storage.get_memory(memory_id, actor, session_id)
        if entry is None:
            return False
        ok, _reason = self.privacy_engine.check_access(entry, actor, session_id)
        if not ok:
            return False

        success = self._storage.delete_memory(
            memory_id, actor, session_id, hard_delete
        )
        # v5.5.1 fix: soft-delete also removes from index to prevent stale search results
        if success:
            self._index.remove_memory(memory_id)
        return success

    def restore(self, memory_id: str, actor: str = "",
                session_id: str = "") -> bool:
        """从回收站恢复记忆（v5.1.1 新增）
        
        v5.5.1 fix: 恢复后重建索引，使记忆重新可被搜索到
        """
        success = self._storage.restore_memory(memory_id, actor, session_id)
        if success:
            try:
                entry = self._storage.get_memory(memory_id, actor, session_id)
                if entry:
                    self._index.index_memory(
                        memory_id,
                        entry.content,
                        metadata={
                            "category": entry.category,
                            "tags": entry.tags or [],
                            "importance": entry.importance.value if hasattr(entry.importance, 'value') else str(entry.importance),
                        },
                    )
            except Exception as e:
                logger.warning("Failed to re-index restored memory %s: %s", memory_id, e)
        return success

    def batch_delete(self,
                     category: Optional[str] = None,
                     layer: Optional[MemoryLayer] = None,
                     starred: Optional[bool] = None,
                     created_after: Optional[float] = None,
                     created_before: Optional[float] = None,
                     hard_delete: bool = False,
                     actor: str = "",
                     session_id: str = "") -> int:
        """批量删除记忆，返回删除数量
        
        v5.5.1 fix: 删除前获取 ID 列表，删除后清理索引（软删除也需要）
        """
        # v5.5.1: 先查询匹配的记忆 ID，用于后续索引清理
        try:
            matched_entries = self._storage.list_memories(
                category=category,
                layer=layer,
                starred=starred,
                created_after=created_after,
                created_before=created_before,
                limit=100000,
            )
            matched_ids = [e.id for e in matched_entries]
        except Exception:
            matched_ids = []
        
        count = self._storage.batch_delete(
            category=category,
            layer=layer,
            starred=starred,
            created_after=created_after,
            created_before=created_before,
            hard_delete=hard_delete,
            actor=actor,
            session_id=session_id,
        )
        
        # v5.5.1: 清理索引（软删除也要清理，防止搜索返回已删除记忆）
        if count > 0 and matched_ids:
            for mid in matched_ids:
                try:
                    self._index.remove_memory(mid)
                except Exception:
                    pass
        
        return count

    def search_by_tag(self, tag: str,
                      category: Optional[str] = None,
                      layer: Optional[MemoryLayer] = None,
                      limit: int = 50,
                      offset: int = 0):
        """按标签搜索记忆"""
        return self._storage.search_by_tag(
            tag=tag,
            category=category,
            layer=layer,
            limit=limit,
            offset=offset,
        )

    def deduplicate(self,
                    category: Optional[str] = None,
                    similarity_threshold: float = 0.95,
                    dry_run: bool = True,
                    actor: str = "system",
                    session_id: str = "") -> dict:
        """记忆去重 - 检测并合并高度相似的记忆（v5.0.4 新增）

        Args:
            category: 限定分类，None 表示全部
            similarity_threshold: 相似度阈值（0-1），默认 0.95
            dry_run: True=只报告不删除，False=实际删除
            actor: 操作者（审计日志用）
            session_id: 会话 ID

        Returns:
            dict: {duplicates_found, would_remove, removed, details}
        """
        return self._storage.deduplicate(
            category=category,
            similarity_threshold=similarity_threshold,
            dry_run=dry_run,
            actor=actor,
            session_id=session_id,
        )

    def export_as_markdown(self,
                           output_path: str,
                           category: Optional[str] = None,
                           layer: Optional[MemoryLayer] = None,
                           starred_only: bool = False):
        """导出记忆为 Markdown 格式（v5.0.4 新增）

        Args:
            output_path: 输出 .md 文件路径
            category: 限定分类
            layer: 限定层级
            starred_only: 仅导出收藏的记忆

        Returns:
            Path: 导出文件路径
        """
        # v5.6.2 安全修复：路径校验
        safe_out = _safe_path(output_path, allowed_exts={".md"})
        return self._storage.export_as_markdown(
            output_path=str(safe_out),
            category=category,
            layer=layer,
            starred_only=starred_only,
        )

    def export_obsidian(self, output_dir: str,
                        category: Optional[str] = None,
                        layer: Optional[MemoryLayer] = None,
                        starred_only: bool = False) -> Dict[str, Any]:
        """导出记忆为 Obsidian Vault 格式（v5.4.6 新增）

        每条记忆生成一个 .md 文件，包含：
        - YAML frontmatter（元数据）
        - 正文内容
        - #标签
        - [[双向链接]] 到关联记忆

        Args:
            output_dir: 输出目录（Obsidian vault 根目录）
            category: 限定分类
            layer: 限定层级
            starred_only: 仅导出收藏的记忆

        Returns:
            {exported, output_dir, errors}
        """
        import re as _re

        entries = self._storage.list_memories(
            category=category,
            layer=layer,
            starred=starred_only if starred_only else None,
            limit=100000,
        )

        vault_path = _safe_path(output_dir)
        vault_path.mkdir(parents=True, exist_ok=True)

        # 获取记忆关联（用于双向链接）
        link_map = {}
        try:
            conn = self._storage._get_conn()
            link_rows = conn.execute(
                "SELECT source_id, target_id FROM memory_links"
            ).fetchall()
            for row in link_rows:
                src = row[0] if isinstance(row[0], str) else row["source_id"]
                tgt = row[1] if isinstance(row[1], str) else row["target_id"]
                if src not in link_map:
                    link_map[src] = []
                link_map[src].append(tgt)
        except Exception:
            pass

        exported = 0
        errors = 0

        for entry in entries:
            try:
                # 生成文件名：使用 ID 前 8 位 + 内容摘要
                slug = _re.sub(r'[^\w\s-]', '', entry.content[:40]).strip()
                slug = _re.sub(r'[\s_]+', '-', slug).lower()[:40]
                if not slug:
                    slug = "untitled"
                filename = f"{entry.id[:8]}_{slug}.md"

                # YAML frontmatter
                frontmatter_lines = [
                    "---",
                    f"id: {entry.id}",
                    f"category: {entry.category}",
                    f"layer: {entry.layer.value if hasattr(entry.layer, 'value') else entry.layer}",
                    f"importance: {entry.importance.value if hasattr(entry.importance, 'value') else entry.importance}",
                    f"privacy: {entry.privacy.value if hasattr(entry.privacy, 'value') else entry.privacy}",
                    f"created: {datetime.fromtimestamp(entry.created_at, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ') if entry.created_at else ''}",
                    f"updated: {datetime.fromtimestamp(entry.updated_at, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ') if entry.updated_at else ''}",
                    f"starred: {entry.starred}",
                    f"access_count: {entry.access_count}",
                ]
                if entry.tags:
                    frontmatter_lines.append(f"tags: [{', '.join(entry.tags)}]")
                frontmatter_lines.append("---")

                # 正文
                body_lines = [
                    "",
                    f"# {entry.content[:80]}",
                    "",
                    entry.content,
                    "",
                ]

                # 标签
                if entry.tags:
                    body_lines.append("## Tags")
                    body_lines.append(" ".join(f"#{t}" for t in entry.tags))
                    body_lines.append("")

                # 双向链接
                links = link_map.get(entry.id, [])
                if links:
                    body_lines.append("## Links")
                    for link_id in links:
                        body_lines.append(f"- [[{link_id[:8]}_{link_id}]]")
                    body_lines.append("")

                # 写入文件
                file_path = vault_path / filename
                file_path.write_text("\n".join(frontmatter_lines + body_lines), encoding="utf-8")
                exported += 1
            except Exception:
                errors += 1

        return {
            "exported": exported,
            "output_dir": str(vault_path),
            "errors": errors,
        }

    def health_check(self) -> dict:
        """数据库健康检查（v5.0.5 新增）

        检查项目：完整性、索引、FTS 同步、孤立审计日志、加密一致性。

        Returns:
            dict: 含 status (healthy/warning/critical) 和 recommendations 列表
        """
        return self._storage.health_check()

    def health_dashboard(self) -> dict:
        """记忆健康仪表盘（v5.4.6 新增）

        生成可视化报告数据：记忆增长曲线、分类分布、衰减预警、
        Top10 高访问低重要度记忆。

        v5.6.2 性能修复：改用分页查询 + SQL 聚合统计，避免全量加载到内存。

        Returns:
            dict: 含 growth_curve, category_distribution, decay_warnings,
                  top_access_low_importance, layer_distribution, importance_distribution
        """
        import time as _time

        conn = self._storage._get_conn()
        now = _time.time()

        # 1. 统计总数（不加载条目）
        total = conn.execute("SELECT COUNT(*) FROM memories WHERE category != 'trash'").fetchone()[0]

        # 2. 分类分布（SQL 聚合，不加载条目）
        cat_rows = conn.execute(
            "SELECT category, COUNT(*) as cnt FROM memories WHERE category != 'trash' "
            "GROUP BY category ORDER BY cnt DESC LIMIT 20"
        ).fetchall()
        category_dist = [{"category": r[0], "count": r[1]} for r in cat_rows]

        # 3. 层级分布（SQL 聚合）
        layer_rows = conn.execute(
            "SELECT layer, COUNT(*) as cnt FROM memories GROUP BY layer ORDER BY cnt DESC"
        ).fetchall()
        layer_dist = [{"layer": r[0], "count": r[1]} for r in layer_rows]

        # 4. 重要度分布（SQL 聚合）
        imp_rows = conn.execute(
            "SELECT importance, COUNT(*) as cnt FROM memories GROUP BY importance ORDER BY cnt DESC"
        ).fetchall()
        importance_dist = [{"importance": r[0], "count": r[1]} for r in imp_rows]

        # 5. 记忆增长曲线（SQL 按天聚合最近 30 天）
        thirty_days_ago = now - 30 * 86400
        growth_rows = conn.execute(
            "SELECT created_at FROM memories WHERE created_at >= ? AND category != 'trash'",
            (thirty_days_ago,)
        ).fetchall()
        growth = {}
        for row in growth_rows:
            day = _time.strftime("%Y-%m-%d", _time.gmtime(row[0]))
            growth[day] = growth.get(day, 0) + 1
        sorted_days = sorted(growth.keys())
        cumulative = 0
        growth_curve = []
        for day in sorted_days:
            cumulative += growth[day]
            growth_curve.append({"date": day, "daily": growth[day], "cumulative": cumulative})

        # 6. 衰减预警（SQL 筛选，只加载符合条件的条目）
        decay_rows = conn.execute(
            "SELECT id, content, category, forgetting_score, access_count, strength "
            "FROM memories WHERE category != 'trash' AND forgetting_score >= 0.5 "
            "ORDER BY forgetting_score DESC LIMIT 20"
        ).fetchall()
        decay_warnings = []
        for r in decay_rows:
            decay_warnings.append({
                "id": r[0],
                "content": (r[1] or "")[:100],
                "category": r[2],
                "forgetting_score": round(r[3] or 0, 3),
                "access_count": r[4],
                "strength": round(r[5] or 0, 3),
            })

        # 7. Top10 高访问低重要度记忆（SQL 筛选）
        access_rows = conn.execute(
            "SELECT id, content, category, access_count, importance "
            "FROM memories WHERE category != 'trash' AND access_count >= 3 "
            "AND importance IN ('LOW', 'MEDIUM') "
            "ORDER BY access_count DESC LIMIT 10"
        ).fetchall()
        access_low_imp = []
        for r in access_rows:
            access_low_imp.append({
                "id": r[0],
                "content": (r[1] or "")[:100],
                "category": r[2],
                "access_count": r[3],
                "importance": r[4],
            })

        return {
            "generated_at": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(now)),
            "total_memories": total,
            "growth_curve": growth_curve,
            "category_distribution": category_dist,
            "layer_distribution": layer_dist,
            "importance_distribution": importance_dist,
            "decay_warnings": decay_warnings,
            "top_access_low_importance": access_low_imp,
            "summary": {
                "categories": len(category_dist),
                "decay_warning_count": len(decay_warnings),
                "high_access_low_importance_count": len(access_low_imp),
                "avg_growth_per_day": round(cumulative / max(len(sorted_days), 1), 1),
            },
        }

    def rebuild_fts(self) -> dict:
        """重建 FTS 全文索引（v5.0.6 新增）

        清空并重建 memory_fts 表，消除孤立记录。
        配合 health_check 发现的 fts_orphans 问题使用。

        Returns:
            dict: {rebuilt: bool, indexed: int, duration_ms: float}
        """
        return self._storage.rebuild_fts()

    def purge_trash(self, actor: str = "system", session_id: str = "") -> int:
        """清空回收站，永久删除所有软删除的记忆（v5.0.6 新增）

        软删除的记忆 category 会被改为 'trash'，本方法将其彻底删除。

        Args:
            actor: 操作者（审计日志用）
            session_id: 会话 ID

        Returns:
            永久删除的记忆数量
        """
        return self._storage.purge_trash(actor=actor, session_id=session_id)

    def summarize(self,
                  category: Optional[str] = None,
                  group_by: str = "category") -> dict:
        """生成记忆摘要（v5.0.5 新增）

        Args:
            category: 限定分类，None=全部
            group_by: 分组维度 'category'|'layer'|'importance'|'privacy'

        Returns:
            dict: 含 total, grouped, recent_activity, top_tags
        """
        return self._storage.summarize(category=category, group_by=group_by)

    def stats(self) -> dict:
        """获取统计信息"""
        return self._storage.get_stats()

    def count_memories(self, category: Optional[str] = None,
                       layer: Optional[MemoryLayer] = None) -> int:
        """统计记忆数量（v5.4.8 P2-004 修复：补充缺失的 facade 方法）

        Args:
            category: 按分类筛选（可选）
            layer: 按层级筛选（可选）

        Returns:
            int: 记忆数量
        """
        return self._storage.count_memories(category, layer)

    def audit_log(self, memory_id: Optional[str] = None,
                  actor: Optional[str] = None,
                  limit: int = 100):
        """获取审计日志"""
        return self._storage.get_audit_log(memory_id, actor, limit)

    def backup(self, output_path: Optional[str] = None) -> dict:
        """创建记忆库完整备份（v5.6.0 增强）

        打包 SQLite 数据库、密钥文件和配置到单个 ZIP 归档，
        用于灾备恢复或跨机器迁移。与 export-md 不同，
        备份是二进制级别的完整快照（含加密数据）。

        Args:
            output_path: 输出 ZIP 路径，默认在 data/ 目录下生成带时间戳的文件名

        Returns:
            dict with keys:
                - path: 备份文件绝对路径
                - size_bytes: 文件大小（字节）
                - size_mb: 文件大小（MB）
                - memory_count: 备份时的记忆总数
                - encrypted: 是否加密
                - created_at: 备份时间戳
                - version: MindForge 版本
        """
        import zipfile
        import time
        from datetime import datetime

        db_path = Path(self.config.db_path)
        if not db_path.exists():
            raise ValueError(f"数据库文件不存在: {db_path}")

        # 生成默认输出路径
        if output_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_dir = db_path.parent
            output_path = str(output_dir / f"mindforge_backup_{timestamp}.zip")

        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)

        # 获取记忆总数
        stats = self._storage.get_stats() if self._storage else {"total": 0}
        memory_count = stats.get("total", 0)

        # 构建清单
        manifest = {
            "backup_version": "1.0",
            "mindforge_version": __version__,
            "created_at": time.time(),
            "created_at_iso": datetime.now().isoformat(),
            "encrypted": self.config.encrypted,
            "memory_count": memory_count,
            "files": {
                "database": "memory.db",
                "key_file": ".key" if self.config.encrypted else None,
                "config": "config.json",
                "manifest": "manifest.json",
            },
        }

        # 构建配置快照
        config_snapshot = {
            "db_path": self.config.db_path,
            "key_file": self.config.key_file,
            "encrypted": self.config.encrypted,
            "default_privacy": self.config.default_privacy.value,
            "default_importance": self.config.default_importance.value,
            "default_layer": self.config.default_layer.value,
        }

        # 确保数据库连接已刷新（WAL 模式下确保数据落盘）
        if self._storage:
            self._storage.checkpoint()

        # 写入 ZIP
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
            # 1. 数据库文件
            zf.write(db_path, "memory.db")

            # 2. 密钥文件（仅加密模式）
            if self.config.encrypted:
                key_path = Path(self.config.key_file)
                if key_path.exists():
                    # P1 安全修复：校验 key_file 路径在预期位置，防 from_config 加载
                    # 恶意 JSON 时将系统密钥文件打包进备份（路径遍历）
                    _safe_path(str(key_path), must_exist=True)
                    zf.write(key_path, ".key")

            # 3. 配置快照
            zf.writestr("config.json", json.dumps(config_snapshot, indent=2, ensure_ascii=False))

            # 4. 清单文件
            zf.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))

        file_size = output.stat().st_size

        return {
            "path": str(output.resolve()),
            "size_bytes": file_size,
            "size_mb": round(file_size / (1024 * 1024), 2),
            "memory_count": memory_count,
            "encrypted": self.config.encrypted,
            "created_at": manifest["created_at"],
            "version": __version__,
        }

    def export_json(self, output_path: str,
                    category: Optional[str] = None,
                    layer: Optional[MemoryLayer] = None,
                    include_private: bool = False) -> int:
        """导出记忆为 JSON 文件（v5.2.9 安全加固：路径校验 + 权限收紧）"""
        entries = self._storage.list_memories(
            category=category,
            layer=layer,
            limit=100000,
            offset=0,
        )

        if not include_private:
            entries = [
                e for e in entries
                if e.privacy in (PrivacyLevel.PUBLIC, PrivacyLevel.INTERNAL)
            ]

        data = {
            "version": __version__,
            "export_time": "",
            "total": len(entries),
            "memories": [e.to_dict() for e in entries],
        }

        from datetime import datetime
        data["export_time"] = datetime.now().isoformat()

        # v5.2.9 安全加固：防止路径遍历
        path = _safe_path(output_path, allowed_exts={".json"})
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        # v5.2.9 安全加固：限制文件权限
        try:
            import stat
            import os
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # v5.5.1: 0600, owner-only
        except (OSError, ImportError):
            pass

        return len(entries)

    def import_json(self, input_path: str,
                    skip_duplicates: bool = True,
                    target_layer: Optional[MemoryLayer] = None,
                    dedup_threshold: float = 0.0) -> Dict[str, int]:
        """从 JSON 文件导入记忆

        v5.2.9 安全加固：路径校验 + 内容长度限制 + 枚举校验
        v5.4.6 新增：智能导入去重（dedup_threshold > 0 时启用语义相似度去重）

        Args:
            input_path: JSON 文件路径
            skip_duplicates: 是否跳过 ID 重复的记忆
            target_layer: 导入到指定层级
            dedup_threshold: 语义去重阈值 (0-1)，0=禁用，>0 时启用。
                             相似度 > 阈值则跳过（或合并标签）。
                             需要嵌入引擎可用，否则降级为文本相似度。
        """
        # v5.2.9 安全加固：防止路径遍历 + 文件大小限制
        path = _safe_path(input_path, must_exist=True, allowed_exts={".json"}, max_size=500 * 1024 * 1024)

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # v5.2.9 安全加固：限制 memories 数组最大长度，防止 OOM
        memories_raw = data.get("memories", [])
        if len(memories_raw) > 100000:
            raise ValueError(f"记忆条数过多（{len(memories_raw)} 条，上限 100000），请分批导入")

        memories = memories_raw
        stats = {"imported": 0, "skipped": 0, "failed": 0, "deduped": 0}

        # v5.5.1 性能优化：预加载已有记忆并预计算嵌入向量缓存
        _existing_contents = None
        _existing_vec_cache = {}  # v5.5.1: id -> precomputed embedding vector
        _dedup_engine = None
        if dedup_threshold > 0:
            from difflib import SequenceMatcher
            try:
                existing_entries = self._storage.list_memories(limit=100000)
                _existing_contents = [(e.id, e.content) for e in existing_entries]
            except Exception:
                _existing_contents = []
            # 尝试使用嵌入引擎（如果可用）
            try:
                eng = self._storage.embedding_engine
                if eng and eng.is_available:
                    _dedup_engine = eng
                    # v5.5.1: 预计算所有已有记忆的嵌入向量，避免 O(n*m) 重复 encode
                    for ex_id, ex_content in _existing_contents:
                        try:
                            v = _dedup_engine.encode(ex_content)
                            if v is not None:
                                _existing_vec_cache[ex_id] = v
                        except Exception:
                            pass
                    logger.info("智能去重已启用 (threshold=%.2f, existing=%d, embedding=on, vec_cache=%d)",
                                dedup_threshold, len(_existing_contents), len(_existing_vec_cache))
            except Exception:
                pass
            if not _dedup_engine:
                logger.info("智能去重已启用 (threshold=%.2f, existing=%d, embedding=off)",
                            dedup_threshold, len(_existing_contents))

        # v5.2.9 安全加固：内容长度白名单常量
        _MAX_CONTENT = 1000000  # 单条记忆内容 1MB
        _MAX_CAT = 256
        _MAX_TAG = 128
        _MAX_TAGS = 64

        for mem_data in memories:
            try:
                # v5.2.9 安全加固：字段长度限制
                content_raw = mem_data.get("content", "")
                if not isinstance(content_raw, str):
                    stats["failed"] += 1
                    continue
                content = content_raw[:_MAX_CONTENT]

                cat_raw = mem_data.get("category", "general")
                if not isinstance(cat_raw, str):
                    cat_raw = "general"
                category = cat_raw[:_MAX_CAT]

                tags_raw = mem_data.get("tags", [])
                if not isinstance(tags_raw, list):
                    tags_raw = []
                tags = [
                    str(t)[:_MAX_TAG] for t in tags_raw[:_MAX_TAGS]
                    if isinstance(t, (str, int, float)) and t
                ]

                existing_id = mem_data.get("id", "")
                if not isinstance(existing_id, str):
                    existing_id = ""
                existing = self._storage.get_memory(existing_id[:64]) if existing_id else None
                if existing and skip_duplicates:
                    stats["skipped"] += 1
                    continue

                # v5.5.1 智能去重：语义相似度检查（使用预计算向量缓存）
                if dedup_threshold > 0 and _existing_contents:
                    is_dup = False
                    if _dedup_engine and _existing_vec_cache:
                        # 使用嵌入向量计算语义相似度
                        new_vec = _dedup_engine.encode(content)
                        if new_vec:
                            for ex_id, ex_vec in _existing_vec_cache.items():
                                sim = EmbeddingEngine.cosine_similarity(new_vec, ex_vec)
                                if sim >= dedup_threshold:
                                    is_dup = True
                                    logger.debug("去重命中(semantic): sim=%.3f vs %s", sim, ex_id)
                                    break
                    else:
                        # 降级：使用文本相似度（difflib）
                        for ex_id, ex_content in _existing_contents:
                            sim = SequenceMatcher(None, content, ex_content).ratio()
                            if sim >= dedup_threshold:
                                is_dup = True
                                logger.debug("去重命中(text): sim=%.3f vs %s", sim, ex_id)
                                break
                    if is_dup:
                        stats["deduped"] += 1
                        continue

                layer = target_layer
                if not layer and mem_data.get("layer"):
                    try:
                        layer = MemoryLayer(str(mem_data["layer"]))
                    except ValueError:
                        layer = self.config.default_layer

                privacy = self.config.default_privacy
                if mem_data.get("privacy"):
                    try:
                        privacy = PrivacyLevel(str(mem_data["privacy"]))
                    except ValueError:
                        pass

                importance = self.config.default_importance
                if mem_data.get("importance"):
                    try:
                        importance = Importance(str(mem_data["importance"]))
                    except ValueError:
                        pass

                memory_type = MemoryType.TEXT
                if mem_data.get("memory_type"):
                    try:
                        memory_type = MemoryType(str(mem_data["memory_type"]))
                    except ValueError:
                        pass

                new_id = str(uuid.uuid4()) if (skip_duplicates and existing) else (
                    existing_id[:64] if existing_id else str(uuid.uuid4())
                )

                # v5.2.9 安全加固：截断 source_session / source_agent
                session_raw = mem_data.get("source_session", "")
                agent_raw = mem_data.get("source_agent", "")
                session_id = str(session_raw)[:128] if isinstance(session_raw, (str, int)) else ""
                agent_id = str(agent_raw)[:128] if isinstance(agent_raw, (str, int)) else ""

                entry = self._storage.add_memory(
                    content=content,
                    category=category,
                    tags=tags,
                    privacy=privacy,
                    importance=importance,
                    memory_type=memory_type,
                    layer=layer or self.config.default_layer,
                    source_session=session_id,
                    source_agent=agent_id,
                    pinned=bool(mem_data.get("pinned", False)),
                    starred=bool(mem_data.get("starred", False)),
                    expires_at=float(mem_data.get("expires_at", 0.0) or 0.0),
                    metadata={},
                )

                self._index.index_memory(
                    entry.id,
                    content,
                    metadata={"category": category},
                )

                stats["imported"] += 1
                # v5.4.6 智能去重：将新导入的内容加入比较池
                if _existing_contents is not None:
                    _existing_contents.append((entry.id, content))
            except (ValueError, TypeError, KeyError, AttributeError):
                stats["failed"] += 1

        return stats

    def import_csv(self, input_path: str,
                   skip_duplicates: bool = True,
                   target_layer: Optional[MemoryLayer] = None,
                   dedup_threshold: float = 0.0) -> Dict[str, int]:
        """从 CSV 文件导入记忆（v5.4.6 新增）

        支持 smart import dedup（同 import_json）。

        Args:
            input_path: CSV 文件路径
            skip_duplicates: 是否跳过重复
            target_layer: 导入到指定层级
            dedup_threshold: 语义去重阈值 (0-1)，0=禁用

        Returns:
            {imported, skipped, failed, deduped}
        """
        path = _safe_path(input_path, must_exist=True, allowed_exts={".csv"}, max_size=500 * 1024 * 1024)

        import csv as _csv
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            reader = _csv.DictReader(f)
            # 转为统一的 memories 格式
            memories = []
            for row in reader:
                tags_str = row.get("tags", "")
                tags = [t.strip() for t in tags_str.split(",") if t.strip()] if tags_str else []
                memories.append({
                    "content": row.get("content", ""),
                    "category": row.get("category", "general"),
                    "tags": tags,
                    "privacy": row.get("privacy", "INTERNAL"),
                    "importance": row.get("importance", "MEDIUM"),
                    "layer": row.get("layer", ""),
                    "source_session": row.get("source_session", ""),
                    "source_agent": row.get("source_agent", ""),
                })

        # 复用 import_json 的去重逻辑
        stats = {"imported": 0, "skipped": 0, "failed": 0, "deduped": 0}

        _existing_contents = None
        _existing_vec_cache = {}  # v5.5.1: id -> precomputed embedding vector
        _dedup_engine = None
        if dedup_threshold > 0:
            from difflib import SequenceMatcher
            try:
                existing_entries = self._storage.list_memories(limit=100000)
                _existing_contents = [(e.id, e.content) for e in existing_entries]
            except Exception:
                _existing_contents = []
            try:
                eng = self._storage.embedding_engine
                if eng and eng.is_available:
                    _dedup_engine = eng
                    # v5.5.1: 预计算所有已有记忆的嵌入向量，避免 O(n*m) 重复 encode
                    for ex_id, ex_content in _existing_contents:
                        try:
                            v = _dedup_engine.encode(ex_content)
                            if v is not None:
                                _existing_vec_cache[ex_id] = v
                        except Exception:
                            pass
            except Exception:
                pass

        _MAX_CONTENT = 1000000

        for mem_data in memories:
            try:
                content = str(mem_data.get("content", ""))[:_MAX_CONTENT]
                if not content:
                    stats["failed"] += 1
                    continue

                category = str(mem_data.get("category", "general"))[:256]
                tags = [str(t)[:128] for t in mem_data.get("tags", [])[:64] if t]

                # 智能去重（v5.5.1: 使用预计算向量缓存）
                if dedup_threshold > 0 and _existing_contents:
                    is_dup = False
                    if _dedup_engine and _existing_vec_cache:
                        new_vec = _dedup_engine.encode(content)
                        if new_vec:
                            for ex_id, ex_vec in _existing_vec_cache.items():
                                sim = EmbeddingEngine.cosine_similarity(new_vec, ex_vec)
                                if sim >= dedup_threshold:
                                    is_dup = True
                                    break
                    else:
                        for ex_id, ex_content in _existing_contents:
                            sim = SequenceMatcher(None, content, ex_content).ratio()
                            if sim >= dedup_threshold:
                                is_dup = True
                                break
                    if is_dup:
                        stats["deduped"] += 1
                        continue

                layer = target_layer
                if not layer and mem_data.get("layer"):
                    try:
                        layer = MemoryLayer(str(mem_data["layer"]))
                    except ValueError:
                        layer = self.config.default_layer

                privacy = self.config.default_privacy
                if mem_data.get("privacy"):
                    try:
                        privacy = PrivacyLevel(str(mem_data["privacy"]))
                    except ValueError:
                        pass

                importance = self.config.default_importance
                if mem_data.get("importance"):
                    try:
                        importance = Importance(str(mem_data["importance"]))
                    except ValueError:
                        pass

                entry = self._storage.add_memory(
                    content=content,
                    category=category,
                    tags=tags,
                    privacy=privacy,
                    importance=importance,
                    layer=layer or self.config.default_layer,
                )

                self._index.index_memory(
                    entry.id,
                    content,
                    metadata={"category": category},
                )

                stats["imported"] += 1
                if _existing_contents is not None:
                    _existing_contents.append((entry.id, content))
            except (ValueError, TypeError, KeyError, AttributeError):
                stats["failed"] += 1

        return stats

    def export_csv(self, output_path: str,
                   category: Optional[str] = None,
                   include_private: bool = False) -> int:
        """导出记忆为 CSV 文件（v5.2.9 安全加固：路径校验 + CSV 公式注入防护）"""
        entries = self._storage.list_memories(
            category=category,
            limit=100000,
            offset=0,
        )

        if not include_private:
            entries = [
                e for e in entries
                if e.privacy in (PrivacyLevel.PUBLIC, PrivacyLevel.INTERNAL)
            ]

        # v5.2.9 安全加固：防止路径遍历
        path = _safe_path(output_path, allowed_exts={".csv"})
        path.parent.mkdir(parents=True, exist_ok=True)

        # v5.2.9 安全加固：CSV 公式注入防护 - 以 = + - @ 开头的单元格加 Tab 前缀
        def _csv_safe(v):
            if v is None:
                return ""
            s = str(v)
            if s and s[0] in ("=", "+", "-", "@"):
                return "\t" + s
            return s

        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "id", "content", "category", "tags", "privacy",
                "importance", "memory_type", "layer", "access_count",
                "created_at", "updated_at",
            ])

            for e in entries:
                writer.writerow([
                    _csv_safe(e.id),
                    _csv_safe(e.content),
                    _csv_safe(e.category),
                    _csv_safe(",".join(e.tags)),
                    _csv_safe(e.privacy.value),
                    _csv_safe(e.importance.value),
                    _csv_safe(e.memory_type.value),
                    _csv_safe(e.layer.value),
                    _csv_safe(e.access_count),
                    _csv_safe(e.created_at),
                    _csv_safe(e.updated_at),
                ])

        return len(entries)

    @property
    def storage(self) -> StorageEngine:
        return self._storage

    @property
    def index(self) -> IndexEngine:
        return self._index

    @property
    def query(self) -> QueryEngine:
        return self._query

    @property
    def privacy_engine(self):
        """v5.6.2 安全修复：懒加载隐私引擎，集成访问控制到核心 API"""
        if self._privacy_engine is None:
            from modules.privacy import PrivacyEngine
            self._privacy_engine = PrivacyEngine(self._storage)
        return self._privacy_engine

    def cleanup(self, max_age_hours: int = 24, layer: str = "sensory") -> int:
        """清理过期记忆（v5.1.3 新增）

        Args:
            max_age_hours: 最大保留时长（小时），超过此时间的记忆将被软删除
            layer: 记忆层级（sensory/short_term/long_term/permanent）

        Returns:
            被清理的记忆数量
        """
        return self._storage.cleanup_expired(max_age_hours, layer)

    def auto_archive(self, max_age_hours: int = 24,
                     layer: str = "sensory") -> Dict[str, Any]:
        """自动归档过期记忆（v5.4.6 新增）

        将到期记忆移到 archived_memories 表，而非直接删除。
        可通过 restore_archived() 恢复。

        Args:
            max_age_hours: 最大保留时长（小时）
            layer: 记忆层级（sensory/short_term）

        Returns:
            {archived, layer, max_age_hours}
        """
        return self._storage.auto_archive(max_age_hours, layer)

    def list_archived(self, layer: Optional[str] = None,
                      category: Optional[str] = None,
                      limit: int = 50,
                      offset: int = 0) -> List[Dict[str, Any]]:
        """列出归档记忆（v5.4.6 新增）"""
        return self._storage.list_archived(layer, category, limit, offset)

    def restore_archived(self, archive_id: str) -> Dict[str, Any]:
        """从归档恢复记忆（v5.4.6 新增）"""
        return self._storage.restore_archived(archive_id)

    def purge_archived(self, older_than_days: int = 90) -> int:
        """永久删除过期归档记忆（v5.4.6 新增）"""
        return self._storage.purge_archived(older_than_days)

    def batch_add(self, entries: List[Dict[str, Any]]) -> int:
        """批量添加记忆（v5.1.3 新增）

        Args:
            entries: 记忆条目列表，每个条目包含 content、category、tags 等字段

        Returns:
            成功添加的记忆数量
        """
        return self._storage.batch_add(entries)

    def find_similar(self, content: str, limit: int = 5, threshold: float = 0.3):
        """查找相似记忆（v5.1.3 新增）

        Args:
            content: 参考内容
            limit: 返回数量限制
            threshold: 相似度阈值（0-1）

        Returns:
            相似记忆列表
        """
        return self._storage.find_similar(content, limit, threshold)

    def detailed_stats(self) -> Dict[str, Any]:
        """获取详细统计信息（v5.1.4 新增）"""
        return self._storage.get_detailed_stats()

    def random(self, count: int = 1, category: Optional[str] = None,
               layer: Optional[MemoryLayer] = None,
               min_strength: Optional[float] = None) -> List[Any]:
        """随机获取记忆（v5.1.7 新增）"""
        return self._storage.get_random_memories(count, category, layer, min_strength)

    def rename_tag(self, old_tag: str, new_tag: str) -> int:
        """重命名标签（v5.1.7 新增）"""
        return self._storage.rename_tag(old_tag, new_tag)

    def rename_category(self, old_cat: str, new_cat: str) -> int:
        """重命名分类（v5.1.7 新增）"""
        return self._storage.rename_category(old_cat, new_cat)

    def config_summary(self) -> Dict[str, Any]:
        """获取配置摘要（v5.1.7 新增）"""
        return self._storage.get_config_summary()

    def close(self):
        if self._storage:
            self._storage.close()

    @classmethod
    def from_config(cls, config_path: str) -> "MindForge":
        """从配置文件创建 MindForge 实例（v5.2.9 安全加固：路径校验）"""
        # v5.2.9 安全加固：防止 config 路径遍历
        path = _safe_path(config_path, must_exist=True, allowed_exts={".json"}, max_size=10 * 1024 * 1024)

        with open(path, "r", encoding="utf-8") as f:
            config_data = json.load(f)

        config_kwargs = {}
        for key in [
            "db_path", "key_file", "encrypted",
            "default_privacy", "default_importance", "default_layer",
        ]:
            if key in config_data:
                if key == "default_privacy":
                    try:
                        config_kwargs[key] = PrivacyLevel(config_data[key])
                    except ValueError:
                        logger.warning("Invalid default_privacy in config %r: %r, using default", config_path, config_data[key])
                elif key == "default_importance":
                    try:
                        config_kwargs[key] = Importance(config_data[key])
                    except ValueError:
                        logger.warning("Invalid default_importance in config %r: %r, using default", config_path, config_data[key])
                elif key == "default_layer":
                    try:
                        config_kwargs[key] = MemoryLayer(config_data[key])
                    except ValueError:
                        logger.warning("Invalid default_layer in config %r: %r, using default", config_path, config_data[key])
                else:
                    config_kwargs[key] = config_data[key]

        config = MemoryConfig(**config_kwargs)
        instance = cls(config=config)
        # v5.6.3 安全增强：数据默认明文落盘（encrypted=False）时给出明确告警。
        # 敏感记忆的 content 将以明文存储在 SQLite 中，建议对含隐私数据的部署
        # 启用加密（config.json 设 "encrypted": true 并通过 init_with_password 初始化）。
        if not config.encrypted:
            logger.warning(
                "MindForge started with encryption DISABLED (config 'encrypted': false). "
                "Memory content will be stored as plaintext in the SQLite database. "
                "Enable encryption (set 'encrypted': true and call init_with_password) "
                "if the store holds sensitive data."
            )
        return instance

    @classmethod
    def load_config(cls, config_path: str) -> dict:
        """加载配置文件为字典（v5.2.9 安全加固：路径校验）"""
        path = _safe_path(config_path, must_exist=True, allowed_exts={".json"}, max_size=10 * 1024 * 1024)

        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def save_config(self, config_path: str):
        """保存当前配置到文件（v5.2.9 安全加固：路径校验 + 600 权限）"""
        config_data = {
            "db_path": self.config.db_path,
            "key_file": self.config.key_file,
            "encrypted": self.config.encrypted,
            "default_privacy": self.config.default_privacy.value,
            "default_importance": self.config.default_importance.value,
            "default_layer": self.config.default_layer.value,
        }

        # v5.2.9 安全加固：防止 config 路径写入敏感位置
        path = _safe_path(config_path, allowed_exts={".json"})
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w", encoding="utf-8") as f:
            json.dump(config_data, f, ensure_ascii=False, indent=2)

        # v5.2.9 安全加固：限制文件权限（类 Unix 系统）
        try:
            import stat
            import os
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        except (OSError, ImportError):
            pass

    def export_excel(self,
                     output_path: str,
                     category: Optional[str] = None,
                     layer: Optional[MemoryLayer] = None,
                     starred_only: bool = False):
        """导出记忆为 Excel 格式（v5.1.9 新增）"""
        return self._storage.export_as_excel(
            output_path=output_path,
            category=category,
            layer=layer,
            starred_only=starred_only,
        )

    def import_excel(self,
                     input_path: str,
                     target_category: Optional[str] = None,
                     target_layer: Optional[MemoryLayer] = None) -> Dict[str, int]:
        """从 Excel 文件导入记忆（v5.1.9 新增）"""
        # v5.6.2 安全修复：路径校验
        safe_in = _safe_path(input_path, must_exist=True,
                             allowed_exts={".xlsx", ".xls"}, max_size=50 * 1024 * 1024)
        return self._storage.import_from_excel(
            input_path=str(safe_in),
            target_category=target_category,
            target_layer=target_layer,
        )

    def copy(self, memory_id: str, new_category: str,
             actor: str = "", session_id: str = "") -> bool:
        """复制记忆到新分类（v5.1.9 新增）"""
        return self._storage.copy_memory(
            entry_id=memory_id,
            new_category=new_category,
            actor=actor,
            session_id=session_id,
        )

    def move(self, memory_id: str, new_category: str,
             actor: str = "", session_id: str = "") -> bool:
        """移动记忆到新分类（v5.1.9 新增）"""
        return self._storage.move_memory(
            entry_id=memory_id,
            new_category=new_category,
            actor=actor,
            session_id=session_id,
        )

    # ===== 搜索增强（v5.2.0 新增）=====

    def fuzzy_search(self,
                     query: str,
                     category: Optional[str] = None,
                     layer: Optional[MemoryLayer] = None,
                     limit: int = 20,
                     threshold: float = 0.3):
        """模糊搜索记忆（v5.2.0 新增）

        结合全文搜索和相似度计算，支持拼写纠错和近似匹配。
        """
        return self._storage.fuzzy_search(
            query=query,
            category=category,
            layer=layer,
            limit=limit,
            threshold=threshold,
        )

    def search_history(self, limit: int = 20):
        """获取搜索历史（v5.2.0 新增）"""
        return self._storage.get_search_history(limit)

    def highlight(self, text: str, query: str,
                  before_tag: str = "<mark>",
                  after_tag: str = "</mark>") -> str:
        """高亮搜索关键词（v5.2.0 新增，v5.5.2 增强多关键词支持）"""
        return self._storage.highlight_text(text, query, before_tag, after_tag)

    # ===== TTL / 过期管理（v5.5.2 新增）=====

    def set_ttl(self, memory_id: str, ttl_seconds: float,
                actor: str = "", session_id: str = "") -> bool:
        """为记忆设置 TTL（存活时间）

        v5.5.2 新增。

        Args:
            memory_id: 记忆 ID
            ttl_seconds: 存活秒数；<= 0 表示取消过期（永不过期）
            actor: 操作者
            session_id: 会话 ID

        Returns:
            是否成功
        """
        return self._storage.set_ttl(memory_id, ttl_seconds, actor, session_id)

    def list_expired(self, limit: int = 1000) -> List[MemoryEntry]:
        """列出所有已过期但尚未清理的记忆

        v5.5.2 新增。
        """
        return self._storage.list_expired(limit)

    def purge_expired(self, actor: str = "", session_id: str = "") -> int:
        """清理所有已过期记忆（移入回收站）

        v5.5.2 新增。
        v5.5.3 fix: 清理索引，避免过期记忆仍可搜索

        Returns:
            清理的记忆条数
        """
        # 先获取过期记忆 ID 列表
        expired_entries = self._storage.list_expired(limit=10000)
        expired_ids = [e.id for e in expired_entries]
        
        # 执行清理
        count = self._storage.purge_expired(actor, session_id)
        
        # 清理索引
        for mid in expired_ids:
            try:
                self._index.remove_memory(mid)
            except Exception:
                pass
        
        return count

    # ===== 按分类/标签批量删除（v5.5.2 新增）=====

    def batch_delete_by_category(self, category: str,
                                  actor: str = "", session_id: str = "",
                                  permanent: bool = False) -> int:
        """按分类批量删除记忆（移入回收站或永久删除）

        v5.5.2 新增。
        v5.5.3 fix: 删除后清理索引，避免搜索结果返回已删除记忆

        Args:
            category: 要删除的分类名称
            actor: 操作者
            session_id: 会话 ID
            permanent: True=永久删除，False=移入回收站

        Returns:
            删除的记忆条数
        """
        # 先获取匹配的记忆 ID
        matched_entries = self._storage.list_memories(category=category, limit=100000)
        matched_ids = [e.id for e in matched_entries]
        
        # 执行删除
        count = self._storage.batch_delete_by_category(category, actor, session_id, permanent)
        
        # 清理索引
        for mid in matched_ids:
            try:
                self._index.remove_memory(mid)
            except Exception:
                pass
        
        return count

    def batch_delete_by_tag(self, tag: str,
                             actor: str = "", session_id: str = "",
                             permanent: bool = False) -> int:
        """按标签批量删除记忆（移入回收站或永久删除）

        v5.5.2 新增。
        v5.5.3 fix: 删除后清理索引，避免搜索结果返回已删除记忆
        v5.5.4 fix: 用 storage 层 SQL 过滤替代全表加载，修复大数据量性能问题

        Args:
            tag: 要删除的标签
            actor: 操作者
            session_id: 会话 ID
            permanent: True=永久删除，False=移入回收站

        Returns:
            删除的记忆条数
        """
        # 先获取匹配的记忆 ID（SQL 级过滤，避免全表加载）
        matched_ids = self._storage.get_ids_by_tag(tag)
        
        # 执行删除
        count = self._storage.batch_delete_by_tag(tag, actor, session_id, permanent)
        
        # 清理索引
        for mid in matched_ids:
            try:
                self._index.remove_memory(mid)
            except Exception:
                pass
        
        return count

    # ===== 记忆合并（v5.5.4 新增）=====

    def merge_memories(self, source_id: str, target_id: str,
                       actor: str = "", session_id: str = ""):
        """合并两条记忆

        v5.5.4 新增。

        合并规则：
        - 内容：按行去重合并（target 在前，source 在后）
        - 标签：取并集
        - 重要性：取较高值
        - 层级：取较深层级
        - 访问次数：相加
        - 元数据：合并（target 优先）
        - source 记忆移入回收站

        Args:
            source_id: 源记忆 ID（将被移入回收站）
            target_id: 目标记忆 ID（保留并吸收内容）
            actor: 操作者
            session_id: 会话 ID

        Returns:
            合并后的目标记忆条目，失败返回 None
        """
        result = self._storage.merge_memories(source_id, target_id, actor, session_id)
        if result:
            # 更新内存索引
            try:
                self._index.remove_memory(source_id)
            except Exception:
                pass
            try:
                self._index.remove_memory(target_id)
                self._index.index_memory(target_id, result.content, {
                    "category": result.category,
                    "tags": result.tags or [],
                })
            except Exception:
                pass
            # 发布事件（惰性初始化 EventBus）
            try:
                if self._event_bus is None:
                    from modules.event_bus import EventBus
                    self._event_bus = EventBus()
                self._event_bus.publish("memory_updated", {
                    "memory_id": target_id,
                    "actor": actor,
                    "session_id": session_id,
                    "merge_from": source_id,
                })
                self._event_bus.publish("memory_deleted", {
                    "memory_id": source_id,
                    "actor": actor,
                    "session_id": session_id,
                    "soft_delete": True,
                    "merge_into": target_id,
                })
            except Exception as e:
                logger.warning("EventBus publish failed: %s", e)
        return result

    # ===== 最常访问 / 最近访问（v5.5.4 新增）=====

    def most_accessed(self, limit: int = 10,
                      category: Optional[str] = None,
                      layer: Optional[MemoryLayer] = None) -> list:
        """按访问次数降序返回最常访问的记忆

        v5.5.4 新增。

        Args:
            limit: 返回数量上限
            category: 按分类筛选（可选）
            layer: 按层级筛选（可选）

        Returns:
            记忆条目列表
        """
        return self._storage.most_accessed(limit, category, layer)

    def recently_accessed(self, limit: int = 10,
                          category: Optional[str] = None,
                          layer: Optional[MemoryLayer] = None) -> list:
        """按最近访问时间降序返回最近访问的记忆

        v5.5.4 新增。

        Args:
            limit: 返回数量上限
            category: 按分类筛选（可选）
            layer: 按层级筛选（可选）

        Returns:
            记忆条目列表
        """
        return self._storage.recently_accessed(limit, category, layer)

    # ===== 按筛选批量更新（v5.5.4 新增）=====

    def bulk_update_by_filter(self,
                              category: Optional[str] = None,
                              tag: Optional[str] = None,
                              updates: Optional[Dict[str, Any]] = None,
                              actor: str = "",
                              session_id: str = "") -> int:
        """按分类或标签筛选后批量更新记忆

        v5.5.4 新增。

        支持的更新字段：
        - new_category: 新分类
        - new_layer: 新层级
        - new_importance: 新重要性 (0-10)
        - new_privacy: 新隐私级别
        - add_tags: 要添加的标签列表
        - remove_tags: 要移除的标签列表
        - starred: 是否星标 (bool)
        - pinned: 是否置顶 (bool)

        Args:
            category: 按分类筛选
            tag: 按标签筛选
            updates: 要更新的字段字典
            actor: 操作者
            session_id: 会话 ID

        Returns:
            更新的记忆条数
        """
        count = self._storage.bulk_update_by_filter(category, tag, updates, actor, session_id)
        if count > 0:
            # 标记索引需要重新水合（批量更新可能影响大量条目）
            self._index._dirty = True
        return count

    # ===== 标签统计（v5.5.4 新增）=====

    def tag_stats(self, limit: int = 20) -> Dict[str, Any]:
        """标签统计信息

        v5.5.4 新增。

        Args:
            limit: Top 标签数量

        Returns:
            统计字典
        """
        return self._storage.tag_stats(limit)

    # ===== 索引一致性检查（v5.5.4 新增）=====

    def check_index_consistency(self) -> Dict[str, Any]:
        """检查 FTS5 索引与主表的一致性

        v5.5.4 新增。

        Returns:
            检查结果字典
        """
        return self._storage.check_index_consistency()

    # ===== 记忆分层与时间衰减（v5.5.5 新增）=====

    def memory_decay_weights(self, memory_id: str) -> Dict[str, float]:
        """计算单条记忆的综合权重（近期度、重要度、访问频率、强度）

        v5.5.5 新增。模仿人脑记忆模式，用于检索排序和自动分层。

        Args:
            memory_id: 记忆 ID

        Returns:
            包含各权重分量和 final_score 的字典
        """
        return self._storage.memory_decay_weights(memory_id)

    def auto_layer_consolidate(self, dry_run: bool = False) -> Dict[str, Any]:
        """自动记忆分层整合

        v5.5.5 新增。基于综合权重自动调整记忆层级：
        - 升级：访问频繁、重要度高的记忆向更高层级迁移
        - 降级：长期未访问、权重低的记忆向更低层级迁移
        - 低优先级标记：权重极低的短时记忆标记为低优先级

        Args:
            dry_run: True=只统计不实际修改

        Returns:
            {promoted, demoted, low_priority, dry_run, details}
        """
        result = self._storage.auto_layer_consolidate(dry_run)
        if not dry_run and result.get("promoted", 0) + result.get("demoted", 0) > 0:
            # 重建索引以反映层级变化
            try:
                self._index = IndexEngine()
                self._rebuild_index_from_storage()
            except Exception:
                pass
        return result

    def layered_retrieval(self, query: str, limit: int = 20, min_score: float = 0.1) -> List[Any]:
        """分层检索记忆

        v5.5.5 新增。按层级优先级检索：
        PERMANENT（30%）→ LONG_TERM（50%）→ SHORT_TERM（20%）
        过滤低权重记忆，按综合权重排序返回。

        Args:
            query: 检索关键词
            limit: 返回数量上限
            min_score: 最低权重阈值

        Returns:
            记忆条目列表
        """
        return self._storage.layered_retrieval(query, limit, min_score)

    def get_memory_layers_stats(self) -> Dict[str, Any]:
        """获取各层记忆统计

        v5.5.5 新增。返回每层的数量、平均重要度、平均访问次数等。
        """
        return self._storage.get_memory_layers_stats()

    # ===== 硬件自适应与缓存（v5.5.5 新增）=====

    def get_hardware_profile(self) -> Dict[str, Any]:
        """获取硬件性能画像

        v5.5.5 新增。自动检测 CPU、内存、磁盘性能，给出性能级别和推荐配置。
        """
        return self._storage.get_hardware_profile()

    def get_cache_stats(self) -> Dict[str, Any]:
        """获取记忆缓存统计

        v5.5.5 新增。返回缓存大小、命中率等统计信息。
        """
        return self._storage.get_cache_stats()

    def adapt_retrieval_params(self, base_limit: int = 20) -> Dict[str, Any]:
        """根据硬件性能自适应调整检索参数

        v5.5.5 新增。低配设备自动减少检索数量和深度，
        高性能设备放开检索深度以获得更全面的结果。

        Args:
            base_limit: 基础检索数量

        Returns:
            {limit, depth, use_vector, use_fts}
        """
        return self._storage.adapt_retrieval_params(base_limit)

    # ===== 前置过滤与冲突检测（v5.5.5 新增）=====

    def prefilter_memories(self, memory_ids: List[str], query: str = "") -> Dict[str, Any]:
        """前置过滤：送入大模型前筛掉重复、低关联、过期的记忆

        v5.5.5 新增。四步过滤：
        1. 去重（内容相似度 >= 0.85 只保留最新）
        2. 低关联过滤（与 query 相关性 < 0.2 移除）
        3. 过期过滤（已过期记忆移除）
        4. 低质量过滤（内容过短或纯空白移除）

        Args:
            memory_ids: 候选记忆 ID 列表
            query: 可选查询词，用于相关性计算

        Returns:
            {kept, removed, reasons, stats}
        """
        return self._storage.prefilter_memories(memory_ids, query)

    def detect_conflicts(self, memory_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """检测记忆中的事实冲突

        v5.5.5 新增。检测四种冲突类型：
        - value_update: 数值/价格等矛盾
        - timeline_inconsistency: 时间线不一致
        - factual_contradiction: 事实陈述矛盾（否定词检测）
        - 标签冲突

        Args:
            memory_ids: 可选，只检测指定记忆；不传则检测所有

        Returns:
            冲突列表，按置信度降序
        """
        return self._storage.detect_conflicts(memory_ids)

    def resolve_conflict(self, conflict: Dict[str, Any], strategy: str = "auto") -> Optional[str]:
        """解决记忆冲突

        v5.5.5 新增。

        Args:
            conflict: detect_conflicts 返回的冲突对象
            strategy: auto / keep_a / keep_b / merge

        Returns:
            保留的记忆 ID
        """
        result = self._storage.resolve_conflict(conflict, strategy)
        if result:
            # 清理索引
            try:
                if strategy == "keep_a" and conflict.get("memory_b_id"):
                    self._index.remove_memory(conflict["memory_b_id"])
                elif strategy == "keep_b" and conflict.get("memory_a_id"):
                    self._index.remove_memory(conflict["memory_a_id"])
            except Exception:
                pass
        return result

    def get_conflict_stats(self) -> Dict[str, Any]:
        """获取冲突统计

        v5.5.5 新增。返回总冲突数、各类型数量、置信度分布等。
        """
        return self._storage.get_conflict_stats()

    # ===== 标签批量管理（v5.2.0 新增）=====

    def batch_add_tags(self, entry_ids: List[str], tags: List[str],
                       actor: str = "", session_id: str = "") -> int:
        """批量添加标签（v5.2.0 新增）"""
        return self._storage.batch_add_tags(entry_ids, tags, actor, session_id)

    def batch_remove_tags(self, entry_ids: List[str], tags: List[str],
                          actor: str = "", session_id: str = "") -> int:
        """批量移除标签（v5.2.0 新增）"""
        return self._storage.batch_remove_tags(entry_ids, tags, actor, session_id)

    def merge_tags(self, source_tags: List[str], target_tag: str,
                   actor: str = "", session_id: str = "") -> int:
        """合并多个标签为一个标签（v5.2.0 新增）"""
        return self._storage.merge_tags(source_tags, target_tag, actor, session_id)

    def add_tags_by_category(self, category: str, tags: List[str],
                             actor: str = "", session_id: str = "") -> int:
        """按分类批量添加标签（v5.2.0 新增）"""
        return self._storage.add_tags_by_category(category, tags, actor, session_id)

    # ===== 数据备份与恢复（v5.2.0 新增）=====

    def create_backup(self, backup_dir: str = "./data/backups") -> Dict[str, Any]:
        """创建数据库备份（v5.2.0 新增）"""
        return self._storage.create_backup(backup_dir)

    def list_backups(self, backup_dir: str = "./data/backups"):
        """列出所有备份（v5.2.0 新增）"""
        return self._storage.list_backups(backup_dir)

    @staticmethod
    def restore_backup(backup_path: str, db_path: str,
                       key_file: Optional[str] = None,
                       force: bool = False) -> dict:
        """从备份文件恢复（v5.6.0 增强）

        从 ZIP 备份归档恢复数据库和密钥文件。
        支持加密和非加密模式的备份恢复。

        Args:
            backup_path: 备份 ZIP 文件路径
            db_path: 恢复后的数据库路径
            key_file: 恢复后的密钥文件路径（加密模式必需）
            force: 是否覆盖已存在的文件

        Returns:
            dict with keys:
                - memory_count: 备份中的记忆数量
                - encrypted: 是否加密
                - backup_version: 备份文件版本
                - mindforge_version: 备份时的 MindForge 版本
                - restored_db: 数据库文件路径
                - restored_key: 密钥文件路径（加密模式）
                - restored_at: 恢复时间戳

        Raises:
            ValueError: 备份文件损坏或缺失必要文件
            FileExistsError: 目标文件已存在且 force=False
        """
        import zipfile
        import time

        # v5.6.2 安全修复：路径校验，防路径遍历
        backup = _safe_path(backup_path, must_exist=True,
                            allowed_exts={".zip"}, max_size=2 * 1024 * 1024 * 1024)
        target_db = _safe_path(db_path, allowed_exts={".db"})
        target_key = _safe_path(key_file) if key_file else None

        # 检查目标文件是否已存在
        if target_db.exists() and not force:
            raise FileExistsError(
                f"数据库文件已存在: {target_db}（使用 force=True 覆盖）"
            )
        if target_key and target_key.exists() and not force:
            raise FileExistsError(
                f"密钥文件已存在: {target_key}（使用 force=True 覆盖）"
            )

        with zipfile.ZipFile(backup, "r") as zf:
            # 验证清单
            if "manifest.json" not in zf.namelist():
                raise ValueError("备份文件无效：缺少 manifest.json")

            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))

            # 验证数据库文件
            if "memory.db" not in zf.namelist():
                raise ValueError("备份文件无效：缺少 memory.db")

            # 加密模式检查密钥文件
            encrypted = manifest.get("encrypted", False)
            if encrypted:
                if ".key" not in zf.namelist():
                    raise ValueError("备份文件无效：加密模式但缺少 .key 文件")
                if not target_key:
                    raise ValueError("加密备份需要指定 key_file 参数")

            # 创建目标目录
            target_db.parent.mkdir(parents=True, exist_ok=True)
            if target_key:
                target_key.parent.mkdir(parents=True, exist_ok=True)

            # 提取数据库
            with zf.open("memory.db") as src, open(target_db, "wb") as dst:
                dst.write(src.read())

            # 提取密钥文件
            restored_key = None
            if encrypted and target_key:
                with zf.open(".key") as src, open(target_key, "wb") as dst:
                    dst.write(src.read())
                # 限制密钥文件权限
                import sys
                if sys.platform != "win32":
                    import stat
                    os.chmod(target_key, stat.S_IRUSR | stat.S_IWUSR)
                restored_key = str(target_key.resolve())

        return {
            "memory_count": manifest.get("memory_count", 0),
            "encrypted": encrypted,
            "backup_version": manifest.get("backup_version", "unknown"),
            "mindforge_version": manifest.get("mindforge_version", "unknown"),
            "restored_db": str(target_db.resolve()),
            "restored_key": restored_key,
            "restored_at": time.time(),
        }

    def delete_old_backups(self, backup_dir: str = "./data/backups",
                           keep_count: int = 10) -> int:
        """删除旧备份，保留最新的 N 个（v5.2.0 新增）"""
        return self._storage.delete_old_backups(backup_dir, keep_count)

    # ===== AI 短剧记忆模块（v5.2.1 新增）=====

    # --- 短剧系列 ---

    def add_drama(self,
                  title: str,
                  genre: str = "other",
                  total_episodes: int = 0,
                  status: str = "planned",
                  platform: str = "",
                  rating: float = 0.0,
                  description: str = "",
                  tags: Optional[List[str]] = None,
                  cover_url: str = "",
                  metadata: Optional[Dict[str, Any]] = None):
        """添加短剧（v5.2.1 新增）"""
        return self._storage.add_drama(
            title=title,
            genre=DramaGenre.from_string(genre),
            total_episodes=total_episodes,
            status=DramaStatus.from_string(status),
            platform=platform,
            rating=rating,
            description=description,
            tags=tags,
            cover_url=cover_url,
            metadata=metadata,
        )

    def get_drama(self, drama_id: str):
        """获取短剧详情（v5.2.1 新增）"""
        return self._storage.get_drama(drama_id)

    def list_dramas(self,
                    genre: Optional[str] = None,
                    status: Optional[str] = None,
                    platform: Optional[str] = None,
                    min_rating: float = 0.0,
                    limit: int = 50,
                    offset: int = 0,
                    sort_by: str = "updated_at",
                    sort_order: str = "desc"):
        """列出短剧（v5.2.1 新增）"""
        return self._storage.list_dramas(
            genre=DramaGenre.from_string(genre) if genre else None,
            status=DramaStatus.from_string(status) if status else None,
            platform=platform,
            min_rating=min_rating,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
            sort_order=sort_order,
        )

    def update_drama(self,
                     drama_id: str,
                     title: Optional[str] = None,
                     genre: Optional[str] = None,
                     total_episodes: Optional[int] = None,
                     current_episode: Optional[int] = None,
                     status: Optional[str] = None,
                     platform: Optional[str] = None,
                     rating: Optional[float] = None,
                     description: Optional[str] = None,
                     tags: Optional[List[str]] = None,
                     cover_url: Optional[str] = None,
                     metadata: Optional[Dict[str, Any]] = None,
                     mark_watched: bool = False) -> bool:
        """更新短剧信息（v5.2.1 新增）"""
        return self._storage.update_drama(
            drama_id=drama_id,
            title=title,
            genre=DramaGenre.from_string(genre) if genre else None,
            total_episodes=total_episodes,
            current_episode=current_episode,
            status=DramaStatus.from_string(status) if status else None,
            platform=platform,
            rating=rating,
            description=description,
            tags=tags,
            cover_url=cover_url,
            metadata=metadata,
            mark_watched=mark_watched,
        )

    def delete_drama(self, drama_id: str) -> bool:
        """删除短剧（v5.2.1 新增）"""
        return self._storage.delete_drama(drama_id)

    def drama_stats(self) -> Dict[str, Any]:
        """短剧统计（v5.2.1 新增）"""
        return self._storage.drama_stats()

    # --- 短剧场次 ---

    def add_scene(self,
                  drama_id: str,
                  episode: int,
                  scene_number: int,
                  title: str,
                  content: str = "",
                  location: str = "",
                  time_of_day: str = "",
                  tags: Optional[List[str]] = None,
                  metadata: Optional[Dict[str, Any]] = None):
        """添加短剧场次（v5.2.1 新增）"""
        return self._storage.add_scene(
            drama_id=drama_id,
            episode=episode,
            scene_number=scene_number,
            title=title,
            content=content,
            location=location,
            time_of_day=time_of_day,
            tags=tags,
            metadata=metadata,
        )

    def get_scene(self, scene_id: str):
        """获取场次详情（v5.2.1 新增）"""
        return self._storage.get_scene(scene_id)

    def list_scenes(self,
                    drama_id: Optional[str] = None,
                    episode: Optional[int] = None,
                    limit: int = 100,
                    offset: int = 0):
        """列出短剧场次（v5.2.1 新增）"""
        return self._storage.list_scenes(
            drama_id=drama_id,
            episode=episode,
            limit=limit,
            offset=offset,
        )

    def update_scene(self,
                     scene_id: str,
                     title: Optional[str] = None,
                     content: Optional[str] = None,
                     location: Optional[str] = None,
                     time_of_day: Optional[str] = None,
                     tags: Optional[List[str]] = None,
                     metadata: Optional[Dict[str, Any]] = None) -> bool:
        """更新场次（v5.2.1 新增）"""
        return self._storage.update_scene(
            scene_id=scene_id,
            title=title,
            content=content,
            location=location,
            time_of_day=time_of_day,
            tags=tags,
            metadata=metadata,
        )

    def delete_scene(self, scene_id: str) -> bool:
        """删除场次（v5.2.1 新增）"""
        return self._storage.delete_scene(scene_id)

    # --- 短剧角色 ---

    def add_character(self,
                      drama_id: str,
                      name: str,
                      role: str = "supporting",
                      actor: str = "",
                      description: str = "",
                      personality: str = "",
                      avatar_url: str = "",
                      tags: Optional[List[str]] = None,
                      metadata: Optional[Dict[str, Any]] = None):
        """添加短剧角色（v5.2.1 新增）"""
        return self._storage.add_character(
            drama_id=drama_id,
            name=name,
            role=role,
            actor=actor,
            description=description,
            personality=personality,
            avatar_url=avatar_url,
            tags=tags,
            metadata=metadata,
        )

    def get_character(self, char_id: str):
        """获取角色详情（v5.2.1 新增）"""
        return self._storage.get_character(char_id)

    def list_characters(self,
                        drama_id: Optional[str] = None,
                        role: Optional[str] = None,
                        limit: int = 100,
                        offset: int = 0):
        """列出短剧角色（v5.2.1 新增）"""
        return self._storage.list_characters(
            drama_id=drama_id,
            role=role,
            limit=limit,
            offset=offset,
        )

    def update_character(self,
                         char_id: str,
                         name: Optional[str] = None,
                         role: Optional[str] = None,
                         actor: Optional[str] = None,
                         description: Optional[str] = None,
                         personality: Optional[str] = None,
                         avatar_url: Optional[str] = None,
                         tags: Optional[List[str]] = None,
                         metadata: Optional[Dict[str, Any]] = None) -> bool:
        """更新角色信息（v5.2.1 新增）"""
        return self._storage.update_character(
            char_id=char_id,
            name=name,
            role=role,
            actor=actor,
            description=description,
            personality=personality,
            avatar_url=avatar_url,
            tags=tags,
            metadata=metadata,
        )

    def delete_character(self, char_id: str) -> bool:
        """删除角色（v5.2.1 新增）"""
        return self._storage.delete_character(char_id)

    # --- 短剧台词 ---

    def add_line(self,
                 drama_id: str,
                 line_text: str,
                 scene_id: str = "",
                 character_id: str = "",
                 character_name: str = "",
                 context: str = "",
                 episode: int = 0,
                 timestamp: str = "",
                 is_classic: bool = False,
                 tags: Optional[List[str]] = None,
                 metadata: Optional[Dict[str, Any]] = None):
        """添加短剧台词（v5.2.1 新增）"""
        return self._storage.add_line(
            drama_id=drama_id,
            line_text=line_text,
            scene_id=scene_id,
            character_id=character_id,
            character_name=character_name,
            context=context,
            episode=episode,
            timestamp=timestamp,
            is_classic=is_classic,
            tags=tags,
            metadata=metadata,
        )

    def get_line(self, line_id: str):
        """获取台词详情（v5.2.1 新增）"""
        return self._storage.get_line(line_id)

    def list_lines(self,
                   drama_id: Optional[str] = None,
                   scene_id: Optional[str] = None,
                   character_id: Optional[str] = None,
                   is_classic: Optional[bool] = None,
                   episode: Optional[int] = None,
                   limit: int = 100,
                   offset: int = 0):
        """列出台词（v5.2.1 新增）"""
        return self._storage.list_lines(
            drama_id=drama_id,
            scene_id=scene_id,
            character_id=character_id,
            is_classic=is_classic,
            episode=episode,
            limit=limit,
            offset=offset,
        )

    def update_line(self,
                    line_id: str,
                    line_text: Optional[str] = None,
                    character_name: Optional[str] = None,
                    context: Optional[str] = None,
                    is_classic: Optional[bool] = None,
                    tags: Optional[List[str]] = None,
                    metadata: Optional[Dict[str, Any]] = None) -> bool:
        """更新台词（v5.2.1 新增）"""
        return self._storage.update_line(
            line_id=line_id,
            line_text=line_text,
            character_name=character_name,
            context=context,
            is_classic=is_classic,
            tags=tags,
            metadata=metadata,
        )

    def delete_line(self, line_id: str) -> bool:
        """删除台词（v5.2.1 新增）"""
        return self._storage.delete_line(line_id)

    def search_lines(self,
                     query: str,
                     drama_id: Optional[str] = None,
                     is_classic_only: bool = False,
                     limit: int = 20):
        """搜索台词（v5.2.1 新增）"""
        return self._storage.search_lines(query, drama_id, is_classic_only, limit)

    def classic_lines(self,
                      drama_id: Optional[str] = None,
                      limit: int = 20):
        """获取经典台词（v5.2.1 新增）"""
        return self._storage.classic_lines(drama_id, limit)

    # ===== AI 短剧增强（v5.2.9 新增）=====

    def drama_stars(self,
                    genre: Optional[str] = None,
                    min_rating: float = 0.0,
                    limit: int = 50) -> List[Any]:
        """高分短剧排行榜（v5.2.9 新增，别名 drama-stars）

        Args:
            genre: 类型过滤（枚举白名单内）
            min_rating: 最低评分（0-10）
            limit: 返回数量

        Returns:
            高分短剧列表
        """
        # v5.2.9 安全加固：genre 在 storage 层再做枚举白名单
        min_rating = max(0.0, min(10.0, float(min_rating)))
        limit = max(1, min(1000, int(limit)))
        return self._storage.top_rated_dramas(
            genre=genre, min_rating=min_rating, limit=limit
        )

    def scene_lines(self,
                    scene_id: str,
                    limit: int = 500,
                    offset: int = 0) -> List[Any]:
        """按场次列出台词（v5.2.9 新增）"""
        if not scene_id or not isinstance(scene_id, str):
            return []
        return self._storage.list_lines_by_scene(
            scene_id=scene_id[:64], limit=limit, offset=offset
        )

    def character_lines(self,
                        character_id: str,
                        drama_id: Optional[str] = None,
                        limit: int = 500,
                        offset: int = 0) -> List[Any]:
        """按角色列出台词（v5.2.9 新增）"""
        if not character_id or not isinstance(character_id, str):
            return []
        cid = character_id[:64]
        did = drama_id[:64] if (isinstance(drama_id, str) and drama_id) else None
        return self._storage.list_lines_by_character(
            character_id=cid, drama_id=did, limit=limit, offset=offset
        )

    def import_dramas(self,
                      input_path: str,
                      skip_existing: bool = True) -> Dict[str, int]:
        """从 JSON 批量导入短剧（v5.2.9 新增）

        结构与 export_dramas 相同：{dramas: [{scenes, characters, lines}...]}
        """
        # v5.2.9 安全加固：路径再次校验（storage 层也会再校验一次，双重保险）
        p = _safe_path(input_path, must_exist=True,
                       allowed_exts={".json"}, max_size=500 * 1024 * 1024)
        return self._storage.import_dramas_from_json(
            str(p), skip_existing=bool(skip_existing)
        )

    # ===== v5.2.2 新增功能 =====

    def recommend_dramas(self,
                         genre: Optional[str] = None,
                         min_rating: float = 0.0,
                         exclude_ids: Optional[List[str]] = None,
                         status: Optional[str] = None,
                         limit: int = 5) -> List[Any]:
        """AI 短剧智能推荐（v5.2.2 新增）

        基于用户观看历史和评分，推荐高评分、状态良好的短剧。
        排除已看/弃剧的短剧，优先推荐未观看的高分作品。

        Args:
            genre: 类型筛选（如 "romance"）
            min_rating: 最低评分
            exclude_ids: 排除的短剧 ID 列表
            status: 状态筛选（None 表示全部，'planned' 表示仅推荐未观看的）
            limit: 返回数量

        Returns:
            推荐短剧列表
        """
        exclude = set(exclude_ids or [])

        # v5.2.2 修复：status 参数改为可选，None 时不过滤状态
        if status:
            status_enum = DramaStatus.from_string(status)
        else:
            status_enum = None

        # 获取候选短剧
        candidates = self._storage.list_dramas(
            genre=DramaGenre.from_string(genre) if genre else None,
            min_rating=min_rating,
            status=status_enum,
            limit=500,
        )

        # 过滤排除的
        candidates = [d for d in candidates if d.id not in exclude]

        # v5.2.2 优化：未指定 status 时，自动剔除已弃剧
        if not status:
            candidates = [d for d in candidates if d.status != DramaStatus.DROPPED]

        # 按评分 + 进度综合排序
        def score(drama) -> float:
            base_score = drama.rating * 10
            if drama.cover_url:
                base_score += 1
            if drama.tags and len(drama.tags) > 0:
                base_score += 0.5 * min(len(drama.tags), 5)
            # 已完成的短剧略微加分（因为用户已认可）
            if drama.status == DramaStatus.COMPLETED:
                base_score += 2
            # 计划中的高评分短剧加分
            if drama.status == DramaStatus.PLANNED and drama.rating >= 8.5:
                base_score += 3
            return base_score

        candidates.sort(key=score, reverse=True)
        return candidates[:limit]

    def drama_watching_progress(self) -> Dict[str, Any]:
        """观看进度统计（v5.2.2 新增）

        返回整体观看进度，包括：
        - 总集数（已规划）
        - 已观看集数
        - 完成度百分比
        - 按类型分布
        """
        dramas = self._storage.list_dramas(limit=1000)
        total_planned = sum(d.total_episodes for d in dramas)
        total_watched = sum(d.current_episode for d in dramas)
        progress_by_genre = {}

        for d in dramas:
            genre = d.genre.value
            if genre not in progress_by_genre:
                progress_by_genre[genre] = {
                    "total_planned": 0,
                    "total_watched": 0,
                    "count": 0,
                }
            progress_by_genre[genre]["total_planned"] += d.total_episodes
            progress_by_genre[genre]["total_watched"] += d.current_episode
            progress_by_genre[genre]["count"] += 1

        completion_rate = (total_watched / total_planned * 100) if total_planned > 0 else 0.0

        return {
            "total_dramas": len(dramas),
            "total_planned_episodes": total_planned,
            "total_watched_episodes": total_watched,
            "completion_rate": round(completion_rate, 2),
            "by_genre": progress_by_genre,
        }

    def export_dramas(self, output_path: str, drama_ids: Optional[List[str]] = None) -> int:
        """导出短剧数据（v5.2.2 新增，v5.2.9 安全加固：路径校验 + JSON 注入防护）

        导出短剧及其关联的场次、角色、台词为 JSON 文件。

        Args:
            output_path: 输出文件路径
            drama_ids: 指定导出的短剧 ID 列表（None 表示全部）

        Returns:
            导出的短剧数量
        """
        import json

        if drama_ids:
            # v5.2.9 安全加固：ID 长度限制白名单
            safe_ids = [did[:64] for did in drama_ids if isinstance(did, str) and 1 <= len(did) <= 64]
            dramas = [self._storage.get_drama(did) for did in safe_ids]
            dramas = [d for d in dramas if d is not None]
        else:
            dramas = self._storage.list_dramas(limit=10000)

        export_data = {
            "version": __version__,
            "export_time": "",
            "total": len(dramas),
            "dramas": [],
        }
        from datetime import datetime
        export_data["export_time"] = datetime.now().isoformat()

        for drama in dramas:
            drama_data = drama.to_dict() if hasattr(drama, "to_dict") else vars(drama)
            drama_data["scenes"] = [vars(s) if not hasattr(s, "to_dict") else s.to_dict()
                                     for s in self._storage.list_scenes(drama_id=drama.id, limit=1000)]
            drama_data["characters"] = [vars(c) if not hasattr(c, "to_dict") else c.to_dict()
                                         for c in self._storage.list_characters(drama_id=drama.id, limit=1000)]
            drama_data["lines"] = [vars(l) if not hasattr(l, "to_dict") else l.to_dict()
                                    for l in self._storage.list_lines(drama_id=drama.id, limit=10000)]
            export_data["dramas"].append(drama_data)

        # v5.2.9 安全加固：路径校验
        path = _safe_path(output_path, allowed_exts={".json"})
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(export_data, f, ensure_ascii=False, indent=2)

        # v5.2.9 安全加固：权限收紧
        try:
            import stat
            import os
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # v5.5.1: 0600, owner-only
        except (OSError, ImportError):
            pass

        return len(dramas)

    # ===== Agent 记忆优化（v5.2.2 新增）=====

    def agent_stats(self, agent_id: Optional[str] = None) -> Dict[str, Any]:
        """Agent 记忆统计（v5.2.2 新增）

        统计按 Agent 来源分组的记忆数据。

        Args:
            agent_id: 指定 Agent ID（None 表示统计全部 Agent）

        Returns:
            Agent 统计数据
        """
        return self._storage.agent_stats(agent_id)

    def list_by_agent(self,
                      agent_id: str,
                      limit: int = 100,
                      offset: int = 0) -> List[Any]:
        """列出特定 Agent 的记忆（v5.2.2 新增）

        Args:
            agent_id: Agent ID
            limit: 返回数量限制
            offset: 偏移量

        Returns:
            该 Agent 创建的记忆列表
        """
        return self._storage.list_by_agent(agent_id, limit, offset)

    def evolve_memories(self, dry_run: bool = False) -> Dict[str, Any]:
        """记忆演化 - 基于艾宾浩斯遗忘曲线自动升级记忆层级（v5.2.2 新增）

        Args:
            dry_run: 仅统计不执行

        Returns:
            演化统计结果
        """
        return self._storage.evolve_memories(dry_run=dry_run)

    def transfer_agent_memories(self,
                                from_agent: str,
                                to_agent: str,
                                category: Optional[str] = None) -> Dict[str, Any]:
        """Agent 记忆迁移 - 将一个 Agent 的记忆转移给另一个（v5.2.2 新增）

        Args:
            from_agent: 源 Agent ID
            to_agent: 目标 Agent ID
            category: 可选，仅迁移指定分类

        Returns:
            迁移统计
        """
        return self._storage.transfer_agent_memories(
            from_agent=from_agent,
            to_agent=to_agent,
            category=category,
        )

    def clean_agent_memories(self,
                             agent_id: str,
                             older_than_days: int = 90,
                             max_importance: Optional[str] = None,
                             dry_run: bool = False) -> Dict[str, Any]:
        """清理 Agent 的旧记忆（v5.2.2 新增）

        清理指定 Agent 创建的、超过指定天数、重要度低于等于指定级别的记忆，移入回收站。

        Args:
            agent_id: Agent ID
            older_than_days: 清理超过多少天的记忆
            max_importance: 最高清理的重要级别（LOW/MEDIUM/HIGH/CRITICAL），None 表示清理所有
            dry_run: 仅统计不执行

        Returns:
            清理统计
        """
        older_than_days = max(0, min(3650, int(older_than_days)))
        if max_importance:
            try:
                Importance.from_string(max_importance)
            except (ValueError, KeyError):
                max_importance = None
        return self._storage.clean_agent_memories(
            agent_id=agent_id,
            older_than_days=older_than_days,
            max_importance=max_importance,
            dry_run=dry_run,
        )

    def quality_score(self, memory_id: str) -> Optional[Dict[str, Any]]:
        """记忆质量评分（v5.2.2 新增）"""
        return self._storage.quality_score(memory_id)

    # ===== Agent 记忆增强（v5.2.9 新增）=====

    def rank_agents(self,
                    by: str = "count",
                    limit: int = 20) -> List[Dict[str, Any]]:
        """Agent 排行榜（v5.2.9 新增）

        Args:
            by: 排序维度（count / last_active / avg_importance / starred）
            limit: 返回数量

        Returns:
            Agent 排名列表
        """
        # v5.2.9 安全加固：by 白名单枚举
        _ALLOWED = {"count", "last_active", "avg_importance", "starred"}
        if by not in _ALLOWED:
            by = "count"
        limit = max(1, min(1000, int(limit)))
        return self._storage.rank_agents(by=by, limit=limit)

    def forget_agent(self,
                     agent_id: str,
                     min_quality_score: int = 30,
                     older_than_days: int = 30,
                     dry_run: bool = False) -> Dict[str, Any]:
        """遗忘 Agent 的低质量旧记忆（v5.2.9 新增，代理 forget_agent_memories）"""
        # v5.2.9 安全加固：参数边界
        if not agent_id or not isinstance(agent_id, str) or len(agent_id) > 128:
            return {"evaluated": 0, "selected": 0, "cleaned": 0, "error": "无效 agent_id"}
        min_quality_score = max(0, min(100, int(min_quality_score)))
        older_than_days = max(0, int(older_than_days))
        return self._storage.forget_agent_memories(
            agent_id=agent_id[:128],
            min_quality_score=min_quality_score,
            older_than_days=older_than_days,
            dry_run=dry_run,
            actor="cli",
            session_id="forget",
        )

    # ===== Agent 记忆增强（v5.3.0 新增）=====

    def agent_profile(self, agent_id: str) -> Dict[str, Any]:
        """Agent 记忆画像（v5.3.0 新增）

        聚合分析指定 Agent 的记忆全景。
        """
        if not agent_id or not isinstance(agent_id, str) or len(agent_id) > 128:
            return {"error": "无效 agent_id"}
        return self._storage.agent_profile(agent_id[:128])

    def merge_agents(self,
                     from_agent: str,
                     to_agent: str,
                     dedup: str = "exact",
                     dry_run: bool = False) -> Dict[str, Any]:
        """合并两个 Agent 的记忆（v5.3.0 新增）

        Args:
            from_agent: 源 Agent ID
            to_agent: 目标 Agent ID
            dedup: 去重模式（exact / none）
            dry_run: 仅预览
        """
        # v5.3.0 安全加固
        _ALLOWED_DEDUP = {"exact", "none"}
        if dedup not in _ALLOWED_DEDUP:
            dedup = "exact"
        if not from_agent or not isinstance(from_agent, str) or len(from_agent) > 128:
            return {"evaluated": 0, "migrated": 0, "error": "无效 from_agent"}
        if not to_agent or not isinstance(to_agent, str) or len(to_agent) > 128:
            return {"evaluated": 0, "migrated": 0, "error": "无效 to_agent"}
        return self._storage.merge_agent_memories(
            from_agent=from_agent[:128],
            to_agent=to_agent[:128],
            dedup=dedup,
            dry_run=dry_run,
            actor="cli",
            session_id="merge",
        )

    def export_agent(self,
                     agent_id: str,
                     output_path: str,
                     include_audit: bool = False) -> Dict[str, Any]:
        """导出 Agent 全部记忆为独立 JSON 包（v5.3.0 新增）"""
        if not agent_id or not isinstance(agent_id, str) or len(agent_id) > 128:
            return {"error": "无效 agent_id"}
        # 路径校验（mindforge 层 + storage 层双重）
        _safe_path(output_path, allowed_exts={".json"})
        return self._storage.export_agent_memories(
            agent_id=agent_id[:128],
            output_path=output_path,
            include_audit=bool(include_audit),
        )

    # ===== AI 短剧增强（v5.3.0 新增）=====

    def drama_info(self, drama_id: str) -> Optional[Dict[str, Any]]:
        """短剧深度统计（v5.3.0 新增）

        台词数/角色数/场次数/总字数/经典占比/每集分布/角色 Top-5。
        """
        if not drama_id or not isinstance(drama_id, str):
            return None
        return self._storage.drama_detail_stats(drama_id[:64])

    def random_lines(self,
                     drama_id: Optional[str] = None,
                     character_id: Optional[str] = None,
                     is_classic: Optional[bool] = None,
                     count: int = 1) -> List[Any]:
        """随机抽取台词（v5.3.0 新增）"""
        count = max(1, min(100, int(count)))
        did = drama_id[:64] if (isinstance(drama_id, str) and drama_id) else None
        cid = character_id[:64] if (isinstance(character_id, str) and character_id) else None
        return self._storage.random_lines(
            drama_id=did, character_id=cid,
            is_classic=is_classic, count=count,
        )

    def character_profile(self,
                          character_id: str,
                          drama_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """角色画像分析（v5.3.0 新增）"""
        if not character_id or not isinstance(character_id, str):
            return None
        cid = character_id[:64]
        did = drama_id[:64] if (isinstance(drama_id, str) and drama_id) else None
        return self._storage.character_profile(character_id=cid, drama_id=did)

    # ===== v5.3.1 新增 =====

    def agent_search(self,
                     agent_id: str,
                     keyword: str,
                     limit: int = 50,
                     offset: int = 0) -> List[Any]:
        """在指定 Agent 的记忆中搜索关键词（v5.3.1 新增）

        Args:
            agent_id: Agent ID
            keyword: 搜索关键词
            limit: 返回数量上限
            offset: 偏移量

        Returns:
            匹配的记忆列表
        """
        if not agent_id or not isinstance(agent_id, str):
            return []
        if not keyword or not isinstance(keyword, str):
            return []
        # v5.3.1 安全加固：Unicode 控制字符过滤
        import unicodedata
        keyword = "".join(c for c in keyword if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        keyword = keyword[:200]
        agent_id = agent_id[:128]

        limit = max(1, min(500, int(limit)))
        offset = max(0, int(offset))
        return self._storage.search_agent_memories(agent_id, keyword, limit, offset)

    def agent_compare(self,
                      agent_a: str,
                      agent_b: str) -> Dict[str, Any]:
        """对比两个 Agent 的记忆差异（v5.3.1 新增）

        Args:
            agent_a: Agent A ID
            agent_b: Agent B ID

        Returns:
            对比结果：各自记忆数、共同分类、独有分类、共同标签
        """
        if not agent_a or not isinstance(agent_a, str):
            return {"error": "Agent A ID 不能为空"}
        if not agent_b or not isinstance(agent_b, str):
            return {"error": "Agent B ID 不能为空"}
        return self._storage.compare_agents(agent_a[:128], agent_b[:128])

    def drama_search(self,
                     keyword: str,
                     genre: Optional[str] = None,
                     min_rating: float = 0.0,
                     limit: int = 50,
                     offset: int = 0) -> List[Any]:
        """按关键词搜索短剧（v5.3.1 新增）

        Args:
            keyword: 搜索关键词（匹配标题/描述/标签）
            genre: 类型过滤
            min_rating: 最低评分
            limit: 返回数量上限
            offset: 偏移量

        Returns:
            匹配的短剧列表
        """
        if not keyword or not isinstance(keyword, str):
            return []
        # v5.3.1 安全加固：Unicode 控制字符过滤
        import unicodedata
        keyword = "".join(c for c in keyword if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        keyword = keyword[:200]

        limit = max(1, min(500, int(limit)))
        offset = max(0, int(offset))
        min_rating = max(0.0, min(10.0, float(min_rating)))

        # v5.5.0 修复：genre 枚举白名单与 DramaGenre 枚举对齐
        valid_genres = {"ROMANCE", "SUSPENSE", "COMEDY", "ACTION", "HORROR",
                        "SCIFI", "FANTASY", "DRAMA", "OTHER"}
        if genre and genre.upper() not in valid_genres:
            genre = None

        return self._storage.search_dramas(keyword, genre, min_rating, limit, offset)

    def character_ranking(self,
                          drama_id: Optional[str] = None,
                          sort_by: str = "lines",
                          limit: int = 20) -> List[Dict[str, Any]]:
        """角色台词排行榜（v5.3.1 新增）

        Args:
            drama_id: 限定短剧（可选）
            sort_by: 排序维度 lines/classic/scenes
            limit: 返回数量上限

        Returns:
            角色排行列表
        """
        limit = max(1, min(100, int(limit)))
        # v5.3.1 安全加固：sort_by 枚举白名单
        valid_sorts = {"lines", "classic", "scenes"}
        if sort_by not in valid_sorts:
            sort_by = "lines"
        did = drama_id[:64] if (isinstance(drama_id, str) and drama_id) else None
        return self._storage.character_ranking(drama_id=did, sort_by=sort_by, limit=limit)

    # ===== v5.3.2 新增 =====

    def agent_diff(self,
                   agent_id: str,
                   days_a: int = 7,
                   days_b: int = 1) -> Dict[str, Any]:
        """对比同一 Agent 在不同时间段的记忆差异（v5.3.2 新增）

        Args:
            agent_id: Agent ID
            days_a: 时间段 A 回溯天数
            days_b: 时间段 B 回溯天数

        Returns:
            差异报告字典
        """
        if not agent_id or not isinstance(agent_id, str):
            return {"error": "Agent ID 不能为空"}
        # v5.3.2 安全：Unicode 控制字符过滤 + 长度上限
        import unicodedata
        agent_id = "".join(c for c in agent_id[:128] if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        days_a = max(1, min(3650, int(days_a)))
        days_b = max(1, min(3650, int(days_b)))
        return self._storage.agent_diff_memories(agent_id, days_a, days_b)

    def agent_purge(self,
                    agent_id: str,
                    actor: str = "system",
                    session_id: str = "",
                    dry_run: bool = True) -> Dict[str, Any]:
        """清空指定 Agent 的全部记忆（v5.3.2 新增，高危操作）

        Args:
            agent_id: 目标 Agent ID
            actor: 操作者（审计日志）
            session_id: 会话 ID
            dry_run: True=仅预览，False=实际执行

        Returns:
            清理结果字典
        """
        if not agent_id or not isinstance(agent_id, str):
            return {"error": "Agent ID 不能为空"}
        import unicodedata
        agent_id = "".join(c for c in agent_id[:128] if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        actor = (actor or "system")[:64]
        session_id = (session_id or "")[:64]
        return self._storage.agent_purge(agent_id, actor, session_id, dry_run)

    def drama_progress(self,
                       drama_id: str,
                       current_episode: int,
                       status: Optional[str] = None,
                       user_rating: Optional[float] = None,
                       actor: str = "system") -> Dict[str, Any]:
        """更新短剧观看进度（v5.3.2 新增）

        Args:
            drama_id: 短剧 ID
            current_episode: 当前看到第几集（≥1）
            status: WATCHING/COMPLETED/DROPPED/PLANNING
            user_rating: 用户评分 0-10
            actor: 操作者

        Returns:
            更新结果字典
        """
        if not drama_id or not isinstance(drama_id, str):
            return {"error": "短剧 ID 不能为空"}
        import unicodedata
        drama_id = "".join(c for c in drama_id[:64] if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        current_episode = max(1, min(10000, int(current_episode)))
        if user_rating is not None:
            user_rating = max(0.0, min(10.0, float(user_rating)))
        # v5.5.0 修复：status 枚举白名单与 DramaStatus 枚举对齐
        valid_status = {"WATCHING", "COMPLETED", "DROPPED", "PLANNED"}
        if status:
            status = status.upper()
            if status not in valid_status:
                status = None
        actor = (actor or "system")[:64]
        return self._storage.drama_update_progress(
            drama_id, current_episode, status, user_rating, actor)

    def drama_recommend_v2(self,
                           genre: Optional[str] = None,
                           min_rating: float = 0.0,
                           mode: str = "unwatched",
                           limit: int = 20) -> List[Dict[str, Any]]:
        """短剧智能推荐 v2（v5.3.2 新增）

        Args:
            genre: 类型过滤
            min_rating: 最低评分（0-10）
            mode: unwatched/watching/dropped/all
            limit: 返回数量上限（1-200）

        Returns:
            推荐短剧列表
        """
        limit = max(1, min(200, int(limit)))
        min_rating = max(0.0, min(10.0, float(min_rating)))
        # v5.5.0 修复：genre 枚举白名单与 DramaGenre 枚举对齐
        valid_genres = {"ROMANCE", "SUSPENSE", "COMEDY", "ACTION", "HORROR",
                        "SCIFI", "FANTASY", "DRAMA", "OTHER"}
        if genre:
            genre = genre.upper()
            if genre not in valid_genres:
                genre = None
        valid_modes = {"unwatched", "watching", "dropped", "all"}
        if mode not in valid_modes:
            mode = "unwatched"
        return self._storage.drama_recommend_v2(genre, min_rating, mode, limit)

    # ===== v5.3.3 新增 =====

    def agent_timeline(self,
                       agent_id: str,
                       days: int = 30) -> Dict[str, Any]:
        """Agent 记忆时间线分析（v5.3.3 新增）

        按天/小时统计记忆创建趋势，识别活跃时段和趋势方向。

        Args:
            agent_id: Agent ID
            days: 回溯天数（1-365）

        Returns:
            时间线分析：按天计数、按小时分布、活跃峰、趋势(rising/declining/stable)
        """
        if not agent_id or not isinstance(agent_id, str):
            return {"error": "Agent ID 不能为空"}
        # v5.3.3 安全加固：Unicode 控制字符过滤 + 长度上限
        import unicodedata
        agent_id = "".join(c for c in agent_id[:128]
                           if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        days = max(1, min(365, int(days)))
        return self._storage.agent_timeline(agent_id, days)

    def agent_heatmap(self,
                      agent_id: str,
                      days: int = 30) -> Dict[str, Any]:
        """Agent 记忆热力图矩阵（v5.3.3 新增）

        生成 分类 × 重要度 的记忆密度矩阵。

        Args:
            agent_id: Agent ID
            days: 回溯天数（1-365）

        Returns:
            热力图矩阵：分类行 × 重要度列的计数矩阵 + 行列总计 + 密度最高单元格
        """
        if not agent_id or not isinstance(agent_id, str):
            return {"error": "Agent ID 不能为空"}
        # v5.3.3 安全加固：Unicode 控制字符过滤 + 长度上限
        import unicodedata
        agent_id = "".join(c for c in agent_id[:128]
                           if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        days = max(1, min(365, int(days)))
        return self._storage.agent_heatmap(agent_id, days)

    def drama_binge(self,
                    drama_id: Optional[str] = None) -> Dict[str, Any]:
        """追剧统计（v5.3.3 新增）

        统计观看进度记录，包括完成率、评分分布、最近观看 Top-5。

        Args:
            drama_id: 指定短剧（可选，None=全部）

        Returns:
            追剧统计结果
        """
        did = drama_id[:64] if (isinstance(drama_id, str) and drama_id) else None
        return self._storage.drama_binge_stats(drama_id)

    def char_network(self,
                     drama_id: str) -> Dict[str, Any]:
        """角色关系网络分析（v5.3.3 新增）

        分析短剧中角色间的共同出场频率，构建角色关系网络。

        Args:
            drama_id: 短剧 ID

        Returns:
            角色关系网络：节点列表 + 边列表（含共同出场次数权重）
        """
        if not drama_id or not isinstance(drama_id, str):
            return {"error": "短剧 ID 不能为空"}
        # v5.3.3 安全加固：Unicode 控制字符过滤 + 长度上限
        import unicodedata
        drama_id = "".join(c for c in drama_id[:64]
                           if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        return self._storage.character_network(drama_id)

    # ===== v5.3.4 新增 =====

    def agent_sentiment(self,
                        agent_id: str,
                        days: int = 30) -> Dict[str, Any]:
        """Agent 记忆情感分析（v5.3.4 新增）

        Args:
            agent_id: Agent ID
            days: 回溯天数（1-365）

        Returns:
            情感分析结果
        """
        if not agent_id or not isinstance(agent_id, str):
            return {"error": "Agent ID 不能为空"}
        # v5.3.4 安全：Unicode 控制字符过滤 + 长度上限
        import unicodedata
        agent_id = "".join(c for c in agent_id[:128]
                           if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        days = max(1, min(365, int(days)))
        return self._storage.agent_sentiment(agent_id, days)

    @property
    def evolution(self):
        """记忆演化引擎（v5.4.8 新增 facade）

        提供记忆巩固（短期→长期）、遗忘曲线计算等功能。
        """
        if self._evolution is None:
            try:
                from ..modules.evolution import MemoryEvolution
            except (ImportError, ValueError):
                from modules.evolution import MemoryEvolution
            self._evolution = MemoryEvolution(self._storage)
        return self._evolution

    def consolidate(self, agent_id: str = "", session_id: str = "") -> Dict[str, Any]:
        """记忆巩固：将短期记忆转化为长期记忆（v5.4.8 新增 facade）

        基于艾宾浩斯遗忘曲线，评估短期记忆的强度、访问频率和重要性，
        将符合条件的记忆提升到长期记忆层。

        Args:
            agent_id: 操作者 Agent ID（可选，用于审计日志）
            session_id: 会话 ID（可选）

        Returns:
            巩固结果：{promoted, demoted, consolidated, details}
        """
        return self.evolution.consolidate(agent_id, session_id)

    def memory_decay(self,
                     agent_id: str,
                     days: int = 30) -> Dict[str, Any]:
        """记忆衰减评分（v5.3.4 新增）

        Args:
            agent_id: Agent ID
            days: 回溯天数（1-365）

        Returns:
            衰减分析结果
        """
        if not agent_id or not isinstance(agent_id, str):
            return {"error": "Agent ID 不能为空"}
        import unicodedata
        agent_id = "".join(c for c in agent_id[:128]
                           if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        days = max(1, min(365, int(days)))
        return self._storage.memory_decay(agent_id, days)

    def drama_compare(self,
                      drama_ids: List[str]) -> Dict[str, Any]:
        """短剧对比分析（v5.3.4 新增）

        Args:
            drama_ids: 短剧 ID 列表（最多 5 部）

        Returns:
            对比分析结果
        """
        if not drama_ids or not isinstance(drama_ids, list):
            return {"error": "短剧 ID 列表不能为空"}
        # v5.3.4 安全：每个 ID 消毒 + 数量限制
        import unicodedata
        clean_ids = []
        for did in drama_ids:
            if isinstance(did, str) and did:
                clean = "".join(c for c in did[:64]
                                if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
                if clean:
                    clean_ids.append(clean)
        if not clean_ids:
            return {"error": "无有效短剧 ID"}
        return self._storage.drama_compare(clean_ids[:5])

    def character_arc(self,
                      drama_id: str,
                      character_id: str) -> Dict[str, Any]:
        """角色成长弧线分析（v5.3.4 新增）

        Args:
            drama_id: 短剧 ID
            character_id: 角色 ID

        Returns:
            角色成长弧线数据
        """
        if not drama_id or not isinstance(drama_id, str):
            return {"error": "短剧 ID 不能为空"}
        if not character_id or not isinstance(character_id, str):
            return {"error": "角色 ID 不能为空"}
        import unicodedata
        drama_id = "".join(c for c in drama_id[:64]
                           if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        character_id = "".join(c for c in character_id[:64]
                               if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        return self._storage.character_arc(drama_id, character_id)

    # ===== v5.3.5 新增 =====

    def memory_cluster(self,
                       agent_id: str,
                       days: int = 30,
                       max_clusters: int = 10) -> Dict[str, Any]:
        """记忆主题聚类（v5.3.5 新增）

        基于关键词和标签相似度，将 Agent 记忆聚合成主题组。

        Args:
            agent_id: Agent ID
            days: 回溯天数（1-365）
            max_clusters: 最大聚类数（1-50）

        Returns:
            主题聚类结果
        """
        if not agent_id or not isinstance(agent_id, str):
            return {"error": "Agent ID 不能为空"}
        import unicodedata
        agent_id = "".join(c for c in agent_id[:128]
                           if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        days = max(1, min(365, int(days)))
        max_clusters = max(1, min(50, int(max_clusters)))
        return self._storage.memory_cluster(agent_id, days, max_clusters)

    def agent_insight(self,
                      agent_id: str,
                      days: int = 30) -> Dict[str, Any]:
        """Agent 行为洞察（v5.3.5 新增）

        综合分析 Agent 记忆的活跃度趋势、标签偏好、记忆层分布。

        Args:
            agent_id: Agent ID
            days: 回溯天数（1-365）

        Returns:
            行为洞察报告
        """
        if not agent_id or not isinstance(agent_id, str):
            return {"error": "Agent ID 不能为空"}
        import unicodedata
        agent_id = "".join(c for c in agent_id[:128]
                           if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        days = max(1, min(365, int(days)))
        return self._storage.agent_insight(agent_id, days)

    def drama_summary(self,
                      drama_id: str,
                      max_length: int = 500) -> Dict[str, Any]:
        """短剧剧情摘要（v5.3.5 新增）

        基于场景描述和经典台词，生成短剧核心剧情摘要。

        Args:
            drama_id: 短剧 ID
            max_length: 摘要最大字符数（100-2000）

        Returns:
            剧情摘要、核心角色、关键场景索引
        """
        if not drama_id or not isinstance(drama_id, str):
            return {"error": "短剧 ID 不能为空"}
        import unicodedata
        drama_id = "".join(c for c in drama_id[:64]
                           if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        max_length = max(100, min(2000, int(max_length)))
        return self._storage.drama_summary(drama_id, max_length)

    def scene_tension(self,
                      drama_id: str,
                      top_k: int = 10) -> Dict[str, Any]:
        """场景张力分析（v5.3.5 新增）

        识别高张力场景（冲突/高潮），分析张力曲线。

        Args:
            drama_id: 短剧 ID
            top_k: 返回 Top-K 高张力场景（1-50）

        Returns:
            张力排行、各场景张力曲线、高潮场景索引
        """
        if not drama_id or not isinstance(drama_id, str):
            return {"error": "短剧 ID 不能为空"}
        import unicodedata
        drama_id = "".join(c for c in drama_id[:64]
                           if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        top_k = max(1, min(50, int(top_k)))
        return self._storage.scene_tension(drama_id, top_k)

    # ===== v5.3.6 新增 =====

    def memory_link(self,
                    agent_id: str,
                    memory_id: str,
                    top_k: int = 10,
                    days: int = 90) -> Dict[str, Any]:
        """记忆关联推理（v5.3.6 新增）

        基于关键词重叠、标签共享、时间邻近度，自动发现指定记忆
        与同 Agent 其他记忆之间的隐式关联。

        Args:
            agent_id: Agent ID
            memory_id: 目标记忆 ID
            top_k: 返回 Top-K 关联记忆（1-50）
            days: 回溯窗口天数（1-365）

        Returns:
            关联记忆列表（含关联类型与关联强度）、关联图谱摘要
        """
        if not agent_id or not isinstance(agent_id, str):
            return {"error": "Agent ID 不能为空"}
        if not memory_id or not isinstance(memory_id, str):
            return {"error": "记忆 ID 不能为空"}
        import unicodedata
        agent_id = "".join(c for c in agent_id[:128]
                           if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        memory_id = "".join(c for c in memory_id[:64]
                            if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        top_k = max(1, min(50, int(top_k)))
        days = max(1, min(365, int(days)))
        return self._storage.memory_link(agent_id, memory_id, top_k, days)

    def memory_recall(self,
                      agent_id: str,
                      query: str,
                      top_k: int = 10,
                      days: int = 180) -> Dict[str, Any]:
        """智能记忆召回（v5.3.6 新增）

        基于查询关键词的语义召回，按重要度、访问频次、
        时间衰减综合评分返回最相关记忆。

        Args:
            agent_id: Agent ID
            query: 查询文本
            top_k: 返回 Top-K 召回记忆（1-50）
            days: 回溯窗口天数（1-365）

        Returns:
            召回记忆列表（含召回分、匹配关键词）、召回统计
        """
        if not agent_id or not isinstance(agent_id, str):
            return {"error": "Agent ID 不能为空"}
        if not query or not isinstance(query, str):
            return {"error": "查询文本不能为空"}
        import unicodedata
        agent_id = "".join(c for c in agent_id[:128]
                           if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        # 查询文本：长度截断 + 剔除控制字符（保留换行/回车/制表）
        query = "".join(c for c in query[:500]
                        if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        top_k = max(1, min(50, int(top_k)))
        days = max(1, min(365, int(days)))
        return self._storage.memory_recall(agent_id, query, top_k, days)

    def drama_pacing(self,
                     drama_id: str,
                     window: int = 3) -> Dict[str, Any]:
        """剧集节奏分析（v5.3.6 新增）

        按场景分析节奏分布（快/中/慢），识别拖沓段和密集段，
        给出节奏健康度评分。

        Args:
            drama_id: 短剧 ID
            window: 滑动窗口大小（场景数，1-10）

        Returns:
            节奏分布、拖沓/密集段、节奏健康度
        """
        if not drama_id or not isinstance(drama_id, str):
            return {"error": "短剧 ID 不能为空"}
        import unicodedata
        drama_id = "".join(c for c in drama_id[:64]
                           if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        window = max(1, min(10, int(window)))
        return self._storage.drama_pacing(drama_id, window)

    def char_interaction(self,
                         drama_id: str,
                         top_k: int = 15) -> Dict[str, Any]:
        """角色互动分析（v5.3.6 新增）

        分析角色两两之间的台词互动频率、冲突度，
        构建角色互动矩阵，识别核心关系。

        Args:
            drama_id: 短剧 ID
            top_k: 返回 Top-K 互动关系（1-50）

        Returns:
            互动矩阵、Top 互动关系、核心角色识别
        """
        if not drama_id or not isinstance(drama_id, str):
            return {"error": "短剧 ID 不能为空"}
        import unicodedata
        drama_id = "".join(c for c in drama_id[:64]
                           if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        top_k = max(1, min(50, int(top_k)))
        return self._storage.char_interaction(drama_id, top_k)

    # ===== v5.3.7 新增 =====

    def memory_importance(self,
                          agent_id: str,
                          days: int = 30) -> Dict[str, Any]:
        """记忆重要度分析（v5.3.7 新增）

        分析 Agent 记忆的重要度分布趋势、重要度漂移、
        低估/高估记忆识别，并给出动态重评估建议。
        参考 Mem0 的动态记忆评分机制，基于使用模式重评估。

        Args:
            agent_id: Agent ID
            days: 回溯窗口天数（1-365）

        Returns:
            重要度分布、漂移分析、低估/高估记忆、重评估建议
        """
        if not agent_id or not isinstance(agent_id, str):
            return {"error": "Agent ID 不能为空"}
        import unicodedata
        agent_id = "".join(c for c in agent_id[:128]
                           if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        days = max(1, min(365, int(days)))
        return self._storage.memory_importance(agent_id, days)

    def memory_context(self,
                       agent_id: str,
                       query: str,
                       max_tokens: int = 4000) -> Dict[str, Any]:
        """上下文记忆注入（v5.3.7 新增）

        给定查询，选择并格式化最相关的记忆以适配 LLM 提示词的
        token 预算。参考 Letta 的上下文窗口管理，将召回记忆
        格式化为可直接注入的上下文字符串。

        Args:
            agent_id: Agent ID
            query: 查询文本
            max_tokens: token 预算上限（500-32000）

        Returns:
            格式化上下文字符串、包含记忆数、token 估计、排除数
        """
        if not agent_id or not isinstance(agent_id, str):
            return {"error": "Agent ID 不能为空"}
        if not query or not isinstance(query, str):
            return {"error": "查询文本不能为空"}
        import unicodedata
        agent_id = "".join(c for c in agent_id[:128]
                           if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        query = "".join(c for c in query[:500]
                        if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        max_tokens = max(500, min(32000, int(max_tokens)))
        return self._storage.memory_context(agent_id, query, max_tokens)

    def agent_emotion(self,
                     agent_id: str,
                     days: int = 30) -> Dict[str, Any]:
        """Agent 情感追踪（v5.3.7 新增）

        基于记忆情感的时间追踪，构建情感时间线、情感转换、
        主导情感与情感波动性评分。参考 Zep 的情感记忆功能。

        Args:
            agent_id: Agent ID
            days: 回溯天数（1-365）

        Returns:
            情感分布、时间线、转换序列、主导情感、波动性评分
        """
        if not agent_id or not isinstance(agent_id, str):
            return {"error": "Agent ID 不能为空"}
        import unicodedata
        agent_id = "".join(c for c in agent_id[:128]
                           if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        days = max(1, min(365, int(days)))
        return self._storage.agent_emotion(agent_id, days)

    def drama_genre_trend(self,
                          days: int = 90) -> Dict[str, Any]:
        """短剧类型趋势分析（v5.3.7 新增）

        分析所有短剧的类型分布与流行度趋势，识别上升/下降/稳定类型，
        按类型平均评分。参考竞品「爆款风向标」功能。

        Args:
            days: 回溯窗口天数（1-365）

        Returns:
            类型分布、趋势方向、各类型平均评分、热门类型
        """
        days = max(1, min(365, int(days)))
        return self._storage.drama_genre_trend(days)

    def drama_binge_score(self,
                          drama_id: str) -> Dict[str, Any]:
        """追剧粘性评分（v5.3.7 新增）

        计算短剧的追剧粘性评分（0-100），基于多因子加权：
        节奏健康度 25% + 平均张力 25% + 互动密度 20% +
        经典台词比 15% + 完成率 15%。

        Args:
            drama_id: 短剧 ID

        Returns:
            总分、因子分解、评级（低/中/高/极高）、推荐建议
        """
        if not drama_id or not isinstance(drama_id, str):
            return {"error": "短剧 ID 不能为空"}
        import unicodedata
        drama_id = "".join(c for c in drama_id[:64]
                           if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        return self._storage.drama_binge_score(drama_id)

    def char_relationship(self,
                           drama_id: str,
                           char1_id: str,
                           char2_id: str) -> Dict[str, Any]:
        """角色关系深度分析（v5.3.7 新增）

        分析两个特定角色之间的关系：场景共现、对话交流模式、
        冲突水平、情感发展。关系类型：ally/rival/romance/
        family/mentor/stranger。

        Args:
            drama_id: 短剧 ID
            char1_id: 角色 1 ID
            char2_id: 角色 2 ID

        Returns:
            关系类型、互动数、冲突水平、情感弧线、关键场景、关系强度
        """
        if not drama_id or not isinstance(drama_id, str):
            return {"error": "短剧 ID 不能为空"}
        if not char1_id or not isinstance(char1_id, str):
            return {"error": "角色 1 ID 不能为空"}
        if not char2_id or not isinstance(char2_id, str):
            return {"error": "角色 2 ID 不能为空"}
        import unicodedata
        drama_id = "".join(c for c in drama_id[:64]
                           if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        char1_id = "".join(c for c in char1_id[:64]
                            if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        char2_id = "".join(c for c in char2_id[:64]
                            if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        return self._storage.char_relationship(drama_id, char1_id, char2_id)

    # ===== v5.4.1 新增能力包装 =====

    def memory_reflection(self,
                          agent_id: str,
                          days: int = 30) -> Dict[str, Any]:
        """记忆反思（v5.4.1 新增）

        对时间窗口内的 Agent 记忆做元认知反思：主题分布、情感基调、
        关键经验、焦点漂移与结构化反思报告。

        Args:
            agent_id: Agent ID
            days: 回溯窗口天数（1-365）
        """
        if not agent_id or not isinstance(agent_id, str):
            return {"error": "Agent ID 不能为空"}
        days = max(1, min(365, int(days)))
        return self._storage.memory_reflection(agent_id, days)

    def memory_lineage(self, memory_id: str) -> Dict[str, Any]:
        """记忆血缘/溯源追踪（v5.4.1 新增）

        追踪单条记忆的完整来源脉络：基础快照、版本历史、关联链接、
        审计事件与生命周期时间线。

        Args:
            memory_id: 记忆 ID
        """
        if not memory_id or not isinstance(memory_id, str):
            return {"error": "记忆 ID 不能为空"}
        return self._storage.memory_lineage(memory_id)

    def memory_reinforce(self,
                         agent_id: str,
                         days: int = 90,
                         limit: int = 10) -> Dict[str, Any]:
        """记忆强化候选（v5.4.1 新增）

        前瞻性识别「高价值但正在衰减」的记忆，输出强化候选排序、
        原因与推荐动作。

        Args:
            agent_id: Agent ID
            days: 回溯窗口天数（1-365）
            limit: 返回候选数量上限（1-50）
        """
        if not agent_id or not isinstance(agent_id, str):
            return {"error": "Agent ID 不能为空"}
        days = max(1, min(365, int(days)))
        limit = max(1, min(50, int(limit)))
        return self._storage.memory_reinforce(agent_id, days, limit)

    def drama_plot_thread(self, drama_id: str) -> Dict[str, Any]:
        """剧情线索/伏笔追踪（v5.4.1 新增）

        识别伏笔埋设（setup）与回收（payoff），输出线索列表、
        未回收线索与回收率。

        Args:
            drama_id: 短剧 ID
        """
        if not drama_id or not isinstance(drama_id, str):
            return {"error": "短剧 ID 不能为空"}
        import unicodedata
        drama_id = "".join(c for c in drama_id[:64]
                           if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        return self._storage.drama_plot_thread(drama_id)

    def drama_episode_curve(self, drama_id: str) -> Dict[str, Any]:
        """分集张力曲线（v5.4.1 新增）

        按集聚合张力指标，生成全剧张力曲线、高潮集、波动率
        与曲线形态分类。

        Args:
            drama_id: 短剧 ID
        """
        if not drama_id or not isinstance(drama_id, str):
            return {"error": "短剧 ID 不能为空"}
        import unicodedata
        drama_id = "".join(c for c in drama_id[:64]
                           if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        return self._storage.drama_episode_curve(drama_id)

    def drama_screen_time(self, drama_id: str) -> Dict[str, Any]:
        """角色戏份平衡分析（v5.4.1 新增）

        统计角色台词量/字数/出场占比，计算群像平衡度（Top 占比 +
        基尼系数），识别独角戏/双核/群像结构。

        Args:
            drama_id: 短剧 ID
        """
        if not drama_id or not isinstance(drama_id, str):
            return {"error": "短剧 ID 不能为空"}
        import unicodedata
        drama_id = "".join(c for c in drama_id[:64]
                           if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        return self._storage.drama_screen_time(drama_id)

    def analyze_similarity(self,
                           memory_id: str,
                           limit: int = 10,
                           min_similarity: float = 0.3) -> List[Dict[str, Any]]:
        """相似度分析（v5.2.2 新增）

        分析指定记忆与其他记忆的相似度。

        Args:
            memory_id: 目标记忆 ID
            limit: 返回数量
            min_similarity: 最低相似度阈值

        Returns:
            相似记忆列表
        """
        limit = max(1, min(100, int(limit)))
        min_similarity = max(0.0, min(1.0, float(min_similarity)))
        return self._storage.analyze_similarity(memory_id, limit, min_similarity)

    def batch_quality_score(self,
                            category: Optional[str] = None,
                            limit: int = 100) -> Dict[str, Any]:
        """批量质量评分（v5.2.2 新增）

        对指定范围内的记忆进行批量质量评分。

        Args:
            category: 分类过滤
            limit: 数量限制

        Returns:
            批量评分结果
        """
        limit = max(1, min(1000, int(limit)))
        return self._storage.batch_quality_score(category, limit)

    # ===== v5.2.4 新增 API =====

    def add_note(self, memory_id: str, content: str, author: str = "",
                 tags: Optional[List[str]] = None) -> Dict[str, Any]:
        """添加记忆笔记/批注（v5.2.4 新增）

        Args:
            memory_id: 记忆 ID
            content: 笔记内容
            author: 作者
            tags: 笔记标签

        Returns:
            操作结果
        """
        if not content or not content.strip():
            return {"success": False, "error": "笔记内容不能为空"}
        if len(content) > 10000:
            return {"success": False, "error": "笔记内容不能超过 10000 字符"}
        return self._storage.add_note(memory_id, content.strip(), author, tags)

    def list_notes(self, memory_id: str, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        """列出记忆的笔记（v5.2.4 新增）

        Args:
            memory_id: 记忆 ID
            limit: 数量限制
            offset: 偏移量

        Returns:
            笔记列表
        """
        limit = max(1, min(200, int(limit)))
        return self._storage.list_notes(memory_id, limit, offset)

    def delete_note(self, note_id: str) -> Dict[str, Any]:
        """删除笔记（v5.2.4 新增）

        Args:
            note_id: 笔记 ID

        Returns:
            操作结果
        """
        return self._storage.delete_note(note_id)

    def add_template(self, name: str, content_template: str, category: str = "general",
                     tags: Optional[List[str]] = None, importance: str = "MEDIUM",
                     layer: str = "short_term", description: str = "") -> Dict[str, Any]:
        """添加记忆模板（v5.2.4 新增）

        Args:
            name: 模板名称
            content_template: 模板内容（支持 {变量名} 占位符）
            category: 默认分类
            tags: 默认标签
            importance: 默认重要性
            layer: 默认层级
            description: 模板描述

        Returns:
            操作结果
        """
        if not name or not name.strip():
            return {"success": False, "error": "模板名称不能为空"}
        if not content_template or not content_template.strip():
            return {"success": False, "error": "模板内容不能为空"}
        if len(name) > 100:
            return {"success": False, "error": "模板名称不能超过 100 字符"}
        return self._storage.add_template(name.strip(), content_template.strip(),
                                          category, tags, importance, layer, description)

    def list_templates(self, category: Optional[str] = None,
                       limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        """列出记忆模板（v5.2.4 新增）

        Args:
            category: 按分类筛选
            limit: 数量限制
            offset: 偏移量

        Returns:
            模板列表
        """
        limit = max(1, min(200, int(limit)))
        return self._storage.list_templates(category, limit, offset)

    def use_template(self, template_id: str, variables: Optional[Dict[str, str]] = None,
                     actor: str = "", session_id: str = "") -> Dict[str, Any]:
        """使用模板创建记忆（v5.2.4 新增）

        Args:
            template_id: 模板 ID
            variables: 模板变量替换字典
            actor: 操作者
            session_id: 会话 ID

        Returns:
            操作结果（包含新创建的记忆 ID）
        """
        return self._storage.use_template(template_id, variables, actor, session_id)

    def delete_template(self, template_id: str) -> Dict[str, Any]:
        """删除模板（v5.2.4 新增）

        Args:
            template_id: 模板 ID

        Returns:
            操作结果
        """
        return self._storage.delete_template(template_id)

    def batch_update(self, memory_ids: List[str],
                     category: Optional[str] = None,
                     tags: Optional[List[str]] = None,
                     importance: Optional[str] = None,
                     layer: Optional[str] = None,
                     starred: Optional[bool] = None,
                     actor: str = "", session_id: str = "") -> Dict[str, Any]:
        """批量更新记忆（v5.2.4 新增）

        Args:
            memory_ids: 记忆 ID 列表
            category: 新分类
            tags: 新标签
            importance: 新重要性
            layer: 新层级
            starred: 新收藏状态
            actor: 操作者
            session_id: 会话 ID

        Returns:
            批量更新结果
        """
        if not memory_ids:
            return {"success": False, "error": "未指定记忆 ID", "updated": 0}
        if len(memory_ids) > 500:
            return {"success": False, "error": "单次批量更新不能超过 500 条", "updated": 0}
        # 验证 importance 和 layer 合法性
        if importance:
            try:
                Importance.from_string(importance)
            except (ValueError, KeyError):
                return {"success": False, "error": f"无效的重要性级别: {importance}", "updated": 0}
        if layer:
            try:
                MemoryLayer.from_string(layer)
            except (ValueError, KeyError):
                return {"success": False, "error": f"无效的记忆层级: {layer}", "updated": 0}
        return self._storage.batch_update(memory_ids, category, tags, importance,
                                          layer, starred, actor, session_id)

    def create_review_schedule(self, memory_id: str, interval_days: float = 1.0,
                               actor: str = "") -> Dict[str, Any]:
        """创建复习计划（v5.2.4 新增）

        Args:
            memory_id: 记忆 ID
            interval_days: 复习间隔天数
            actor: 操作者

        Returns:
            操作结果
        """
        interval_days = max(0.1, min(365, float(interval_days)))
        return self._storage.create_review_schedule(memory_id, interval_days, actor)

    def list_due_reviews(self, limit: int = 20) -> List[Dict[str, Any]]:
        """列出到期复习（v5.2.4 新增）

        Args:
            limit: 数量限制

        Returns:
            到期复习列表
        """
        limit = max(1, min(100, int(limit)))
        return self._storage.list_due_reviews(limit)

    def complete_review(self, schedule_id: str) -> Dict[str, Any]:
        """完成复习（v5.2.4 新增）

        完成一次复习后自动安排下次复习（间隔重复算法）。

        Args:
            schedule_id: 复习计划 ID

        Returns:
            操作结果
        """
        return self._storage.complete_review(schedule_id)

    def get_review_stats(self) -> Dict[str, Any]:
        """复习计划统计（v5.2.4 新增）

        Returns:
            复习统计数据
        """
        return self._storage.get_review_stats()

    # ===== 记忆关联（v5.2.5 新增）=====

    def link_memories(self, source_id: str, target_id: str,
                      link_type: str = "related", note: str = "") -> Dict[str, Any]:
        """创建记忆关联（双向）

        Args:
            source_id: 源记忆 ID
            target_id: 目标记忆 ID
            link_type: 关联类型（related/depends_on/extends/contradicts/custom）
            note: 关联备注

        Returns:
            操作结果
        """
        return self._storage.link_memories(source_id, target_id, link_type, note)

    def list_links(self, memory_id: str) -> List[Dict[str, Any]]:
        """列出记忆的所有关联（双向）"""
        return self._storage.list_links(memory_id)

    def unlink_memories(self, link_id: str) -> bool:
        """删除记忆关联"""
        return self._storage.unlink_memories(link_id)

    # ===== 置顶功能（v5.2.5 新增，v5.5.6 增强 actor/session_id）=====

    def pin(self, memory_id: str, actor: str = "", session_id: str = "") -> bool:
        """置顶记忆

        v5.5.6 增强：支持 actor/session_id 审计。
        """
        return self._storage.update_memory(
            entry_id=memory_id, pinned=True, actor=actor, session_id=session_id
        )

    def unpin(self, memory_id: str, actor: str = "", session_id: str = "") -> bool:
        """取消置顶

        v5.5.6 增强：支持 actor/session_id 审计。
        """
        return self._storage.update_memory(
            entry_id=memory_id, pinned=False, actor=actor, session_id=session_id
        )

    def list_pinned(self, limit: int = 50) -> list:
        """列出所有置顶记忆"""
        return self._storage.list_pinned(limit)

    # ===== v5.5.6 新增功能 =====

    def batch_get(self, memory_ids: List[str],
                  actor: str = "", session_id: str = "") -> List[Any]:
        """批量获取记忆（v5.5.6 新增）

        单次查询获取多条记忆，避免 N+1 查询。

        Args:
            memory_ids: 记忆 ID 列表
            actor: 操作者
            session_id: 会话 ID

        Returns:
            按输入顺序排列的记忆列表
        """
        return self._storage.batch_get_memories(memory_ids, actor, session_id)

    def timeline(self, category: Optional[str] = None,
                 layer: Optional[MemoryLayer] = None,
                 limit: int = 500) -> Dict[str, Any]:
        """记忆时间线视图（v5.5.6 新增）

        按创建时间分组为：今天、昨天、本周、本月、更早。

        Args:
            category: 限定分类
            layer: 限定层级
            limit: 最大返回条数

        Returns:
            {"today": [...], "yesterday": [...], "this_week": [...], "this_month": [...], "earlier": [...]}
        """
        return self._storage.timeline_view(category, layer, limit)

    def search_suggestions(self, prefix: str, limit: int = 10,
                           category: Optional[str] = None) -> Dict[str, List[str]]:
        """搜索建议（v5.5.6 新增）

        基于已有标签和分类返回自动补全建议。

        Args:
            prefix: 输入前缀
            limit: 每类返回数量上限
            category: 限定分类范围

        Returns:
            {"tags": [...], "categories": [...]}
        """
        return self._storage.search_suggestions(prefix, limit, category)

    def check_duplicates(self, content: str,
                         similarity_threshold: float = 0.85,
                         category: Optional[str] = None,
                         limit: int = 5) -> List[Dict[str, Any]]:
        """添加前检测近重复记忆（v5.5.6 新增）

        Args:
            content: 待检测内容
            similarity_threshold: 相似度阈值（0-1）
            category: 限定分类范围
            limit: 最大返回数量

        Returns:
            [{"entry": MemoryEntry, "similarity": float, "match_type": str}, ...]
        """
        return self._storage.check_duplicates(content, similarity_threshold, category, limit)

    def add_tags_to_memories(self, memory_ids: List[str], tags: List[str],
                             actor: str = "", session_id: str = "") -> int:
        """按 ID 列表批量添加标签（v5.5.6 新增）

        Args:
            memory_ids: 记忆 ID 列表
            tags: 要添加的标签列表
            actor: 操作者
            session_id: 会话 ID

        Returns:
            实际更新的记忆条数
        """
        return self._storage.add_tags_to_ids(memory_ids, tags, actor, session_id)

    def remove_tags_from_memories(self, memory_ids: List[str], tags: List[str],
                                  actor: str = "", session_id: str = "") -> int:
        """按 ID 列表批量移除标签（v5.5.6 新增）

        Args:
            memory_ids: 记忆 ID 列表
            tags: 要移除的标签列表
            actor: 操作者
            session_id: 会话 ID

        Returns:
            实际更新的记忆条数
        """
        return self._storage.remove_tags_from_ids(memory_ids, tags, actor, session_id)

    # ===== 记忆版本历史（v5.2.7 新增）=====

    def save_version(self, memory_id: str, content: str, category: str = "",
                     tags=None, importance="", actor: str = "") -> Dict[str, Any]:
        """保存记忆历史版本（v5.2.7 新增）"""
        return self._storage.save_version(memory_id, content, category, tags, importance, actor)

    def list_versions(self, memory_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """列出记忆的所有历史版本（v5.2.7 新增）"""
        return self._storage.list_versions(memory_id, limit)

    def get_version(self, version_id: str) -> Optional[Dict[str, Any]]:
        """获取指定版本详情（v5.2.7 新增）"""
        return self._storage.get_version(version_id)

    def rollback_to_version(self, version_id: str, actor: str = "") -> Dict[str, Any]:
        """回滚记忆到指定历史版本（v5.2.7 新增）"""
        return self._storage.rollback_to_version(version_id, actor)

    def memory_diff(self, version_id_a: str, version_id_b: str) -> Dict[str, Any]:
        """对比两个记忆版本的差异（v5.5.8 新增）

        比较两个历史版本的内容、分类、标签和重要度，
        返回结构化差异报告。

        Args:
            version_id_a: 版本 A 的 ID
            version_id_b: 版本 B 的 ID

        Returns:
            差异字典：version_a, version_b, changes (content/category/tags/importance)
        """
        ver_a = self._storage.get_version(version_id_a)
        ver_b = self._storage.get_version(version_id_b)
        if not ver_a:
            return {"error": f"版本 {version_id_a} 不存在"}
        if not ver_b:
            return {"error": f"版本 {version_id_b} 不存在"}

        changes: Dict[str, Any] = {}

        # 内容差异
        content_a = ver_a.get("content", "")
        content_b = ver_b.get("content", "")
        if content_a != content_b:
            import difflib
            diff_lines = list(difflib.unified_diff(
                content_a.splitlines(keepends=True),
                content_b.splitlines(keepends=True),
                fromfile=f"v{ver_a.get('version_number', '?')}",
                tofile=f"v{ver_b.get('version_number', '?')}",
                lineterm=""
            ))
            changes["content"] = {
                "changed": True,
                "diff": "".join(diff_lines) if diff_lines else "",
                "length_a": len(content_a),
                "length_b": len(content_b),
            }
        else:
            changes["content"] = {"changed": False}

        # 分类差异
        cat_a = ver_a.get("category", "")
        cat_b = ver_b.get("category", "")
        if cat_a != cat_b:
            changes["category"] = {"changed": True, "from": cat_a, "to": cat_b}
        else:
            changes["category"] = {"changed": False}

        # 标签差异
        tags_a = set(ver_a.get("tags", []) if isinstance(ver_a.get("tags"), list) else [])
        tags_b = set(ver_b.get("tags", []) if isinstance(ver_b.get("tags"), list) else [])
        added_tags = sorted(tags_b - tags_a)
        removed_tags = sorted(tags_a - tags_b)
        if added_tags or removed_tags:
            changes["tags"] = {"changed": True, "added": added_tags, "removed": removed_tags}
        else:
            changes["tags"] = {"changed": False}

        # 重要度差异
        imp_a = ver_a.get("importance", "")
        imp_b = ver_b.get("importance", "")
        if imp_a != imp_b:
            changes["importance"] = {"changed": True, "from": imp_a, "to": imp_b}
        else:
            changes["importance"] = {"changed": False}

        changed_fields = [k for k, v in changes.items() if v.get("changed")]

        return {
            "version_a": {
                "version_id": ver_a["version_id"],
                "version_number": ver_a["version_number"],
                "changed_at": ver_a["changed_at"],
            },
            "version_b": {
                "version_id": ver_b["version_id"],
                "version_number": ver_b["version_number"],
                "changed_at": ver_b["changed_at"],
            },
            "changed_fields": changed_fields,
            "changes": changes,
        }

    # ===== 多 Agent 记忆空间（v5.2.8 实验性 — v6.0.0 全量推送预览）=====

    @property
    def multi_agent(self):
        """多 Agent 记忆空间管理器（v5.2.8 新增，实验性）

        EXPERIMENTAL: v6.0.0 全量推送预览，API 在正式发布前可能变化。
        提供共享记忆空间、角色权限隔离（owner/editor/reader）、
        隐私护栏（PRIVATE/STRICT 禁止共享）与冲突解决（last-write-wins）。
        """
        if self._multi_agent is None:
            try:
                from ..modules.multi_agent import MultiAgentMemoryManager
            except (ImportError, ValueError):
                from modules.multi_agent import MultiAgentMemoryManager
            self._multi_agent = MultiAgentMemoryManager(self._storage)
        return self._multi_agent

    # ===== 联邦记忆细粒度 ACL / 共享冲突解决（v5.4.2）=====

    @property
    def federated_acl(self):
        """联邦记忆细粒度 ACL 管理器（v5.4.2 新增）

        按「主体（peer）× 资源（记忆/分类/标签/全部）× 操作」配置
        allow/deny 规则，支持优先级、信任阈值与过期时间；默认拒绝。
        """
        if self._federated_acl is None:
            try:
                from ..modules.federated_acl import FederatedACLManager
            except (ImportError, ValueError):
                from modules.federated_acl import FederatedACLManager
            self._federated_acl = FederatedACLManager(self._storage)
        return self._federated_acl

    @property
    def share_conflict(self):
        """共享记忆冲突解析器（v5.4.2 新增）

        联邦/多 Agent 并发更新的冲突检测与解决：
        lww（版本+时间戳决胜）/ keep_both（分支保留）/ 人工挂起。
        """
        if self._share_conflict is None:
            try:
                from ..modules.share_conflict import SharedConflictResolver
            except (ImportError, ValueError):
                from modules.share_conflict import SharedConflictResolver
            self._share_conflict = SharedConflictResolver(self._storage)
        return self._share_conflict

    @property
    def federated(self):
        """联邦记忆管理器（v5.4.2 接入主类）

        自动注入细粒度 ACL 与共享冲突解析器：
        share_memory 按信任度 + ACL 过滤节点，accept_incoming
        自动检测共享记忆冲突。
        """
        if self._federated is None:
            try:
                from ..modules.federated import FederatedMemory
            except (ImportError, ValueError):
                from modules.federated import FederatedMemory
            self._federated = FederatedMemory(
                storage=self._storage,
                acl=self.federated_acl,
                conflict_resolver=self.share_conflict,
            )
        return self._federated

    # ===== v5.3.9 五大能力增强 API =====

    # --- 1. 意图分类路由 ---
    def classify_intent(self, text: str, force: Optional[str] = None) -> Dict[str, Any]:
        """意图分类路由（v5.3.9 新增）

        三层路由：规则正则 → 关键词加权 → （可选）LLM 兜底。
        """
        try:
            from ..modules.intent_router import IntentRouter
        except (ImportError, ValueError):
            from modules.intent_router import IntentRouter
        if self._intent_router is None:
            self._intent_router = IntentRouter()
        result = self._intent_router.classify((text or "")[:4096], force_override=force)
        out = result.to_dict()
        out["routing_target"] = self._intent_router.routing_target(result.intent)
        return out

    # --- 2. 矛盾检测 + 自动衰减 ---
    def scan_conflicts(self,
                       category: Optional[str] = None,
                       limit: int = 500,
                       apply_decay: bool = False) -> Dict[str, Any]:
        """扫描记忆中的矛盾（反义词/属性值/时间线）并可自动衰减（v5.3.9 新增）"""
        try:
            from ..modules.conflict_detector import ConflictDetector
        except (ImportError, ValueError):
            from modules.conflict_detector import ConflictDetector
        entries = self._storage.list_memories(
            category=category, limit=max(1, min(5000, int(limit))), offset=0,
        )
        mem_dicts = []
        by_id: Dict[str, Dict[str, Any]] = {}
        for e in entries:
            created_ts = 0.0
            try:
                created_ts = getattr(e, "created_at").timestamp()
            except Exception:
                pass
            d = {"id": str(e.id), "content": str(e.content),
                 "created_at": created_ts, "importance": getattr(e, "importance", None),
                 "tags": list(getattr(e, "tags", None) or [])}
            mem_dicts.append(d)
            by_id[d["id"]] = d
        detector = ConflictDetector()
        conflicts = detector.scan_memories(mem_dicts)
        actions = detector.plan_decay(conflicts, by_id)
        if apply_decay:
            applied = 0
            for act in actions:
                try:
                    self._storage.adjust_importance(act.memory_id, act.delta_importance)
                    if act.added_tags:
                        self._storage.append_tags(act.memory_id, act.added_tags)
                    applied += 1
                except Exception:
                    pass
            return {
                "conflicts_found": len(conflicts),
                "conflicts": [c.to_dict() for c in conflicts[:50]],
                "decay_planned": [a.to_dict() for a in actions[:100]],
                "decay_applied": applied,
            }
        return {
            "conflicts_found": len(conflicts),
            "conflicts": [c.to_dict() for c in conflicts[:100]],
            "decay_plan": [a.to_dict() for a in actions[:200]],
        }

    # --- 3. 记忆 → 技能转化 ---
    def extract_skills(self, category: Optional[str] = None,
                       limit: int = 2000,
                       min_cluster_size: int = 2) -> Dict[str, Any]:
        """从记忆中抽取可复用的技能模板（v5.3.9 新增）"""
        try:
            from ..modules.skill_extractor import SkillExtractor
        except (ImportError, ValueError):
            from modules.skill_extractor import SkillExtractor
        entries = self._storage.list_memories(
            category=category, limit=max(1, min(10000, int(limit))), offset=0,
        )
        mem_dicts = []
        for e in entries:
            mem_dicts.append({
                "id": str(e.id), "content": str(e.content),
                "tags": list(getattr(e, "tags", None) or []),
                "category": getattr(e, "category", ""),
            })
        extractor = SkillExtractor(min_cluster_size=max(1, int(min_cluster_size)))
        skills = extractor.extract(mem_dicts)
        return {
            "memories_processed": len(mem_dicts),
            "skills_found": len(skills),
            "skills": [s.to_dict() for s in skills[:50]],
        }

    # --- 4. 混合检索增强：查询扩展 + Cross-Encoder 重排 ---
    def search_enhanced(self, query: str,
                        max_results: int = 20,
                        min_relevance: float = 0.0,
                        rerank_top_k: int = 20,
                        expand: bool = True,
                        rerank: bool = True) -> Dict[str, Any]:
        """混合检索增强版：查询扩展 + 三路召回 + Cross-Encoder 重排（v5.3.9 新增）"""
        try:
            from ..modules.hybrid_search import QueryExpander, CrossEncoderReranker
        except (ImportError, ValueError):
            from modules.hybrid_search import QueryExpander, CrossEncoderReranker

        q = (query or "")[:2048]
        expansion = None
        search_query = q
        if expand:
            expander = QueryExpander()
            expansion = expander.expand(q)
            # 用 rewrite 列表里多个查询的并集作为召回（取第一个 rewrite 为主查询）
            search_query = " ".join(expansion.rewrites[:3]) if expansion.rewrites else q

        base = self.search(
            query=search_query,
            max_results=max(1, min(200, int(max_results) * 3)),
            min_relevance=float(min_relevance),
        )
        if not rerank:
            return {
                "query_original": q,
                "query_expansion": expansion.to_dict() if expansion else None,
                "chunks": [{"memory_id": c.memory_id, "content": c.content,
                            "relevance_score": float(c.relevance_score),
                            "category": c.category,
                            "layer": c.layer.value if hasattr(c.layer, 'value') else str(c.layer),
                            "tags": list(c.tags) if c.tags else []}
                           for c in base.chunks[:max_results]],
                "rerank": False,
            }
        # 转成重排输入
        cands = []
        for c in base.chunks:
            meta = {"memory_id": c.memory_id}
            try:
                entry = self._storage.get_memory(c.memory_id)
                if entry is not None:
                    # v5.5.6 fix(P2-2): 透传元数据；枚举统一取 .value（layer 输出与 search 一致的小写）
                    imp_v = getattr(entry, "importance", None)
                    lay_v = getattr(entry, "layer", None)
                    meta["importance"] = getattr(imp_v, "value", imp_v)
                    meta["category"] = getattr(entry, "category", None)
                    meta["layer"] = getattr(lay_v, "value", lay_v)
                    meta["tags"] = getattr(entry, "tags", None)
            except Exception:
                pass
            cands.append({
                "memory_id": c.memory_id,
                "content": c.content,
                "original_score": float(c.relevance_score),
                **meta,
            })
        reranker = CrossEncoderReranker()
        reranked = reranker.rerank(q, cands, top_k=max(1, int(rerank_top_k)))
        reranked = reranked[:max(1, int(max_results))]
        return {
            "query_original": q,
            "query_expansion": expansion.to_dict() if expansion else None,
            "initial_count": len(base.chunks),
            "reranked_count": len(reranked),
            "reranked": [r.to_dict() for r in reranked],
        }

    # --- 5. 会话焦点增强 ---
    def session_focus(self, messages: List[Dict[str, Any]],
                      window_size: int = 40,
                      augment_query: Optional[str] = None) -> Dict[str, Any]:
        """会话焦点聚类 + 漂移检测 + 查询增强（v5.3.9 新增）

        messages: [{id, role, content, timestamp}]
        """
        try:
            from ..modules.session_focus import SessionFocus
        except (ImportError, ValueError):
            from modules.session_focus import SessionFocus
        engine = SessionFocus(max_messages_per_window=max(5, int(window_size)))
        summary = engine.summarize(messages or [], window_size=max(5, int(window_size)))
        out = summary.to_dict()
        if augment_query:
            out["enhanced_query"] = summary.enhance_query(str(augment_query))
        return out

    # ===== v5.4.3 新增 API 包装 =====

    def agent_influence_map(self,
                            agent_id: str,
                            days: int = 30) -> Dict[str, Any]:
        """Agent 记忆影响力图谱（v5.4.3 新增）

        分析指定 Agent 的记忆如何被其他 Agent 引用/关联，以及该 Agent
        引用了哪些其他 Agent 的记忆，构建双向影响力网络。

        Args:
            agent_id: Agent ID
            days: 回溯天数（1-365）

        Returns:
            影响力图谱：节点列表、边列表、入度/出度排行、核心影响力 Agent
        """
        if not agent_id or not isinstance(agent_id, str):
            return {"error": "Agent ID 不能为空"}
        import unicodedata
        agent_id = "".join(c for c in agent_id[:128]
                           if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        days = max(1, min(365, int(days)))
        return self._storage.agent_influence_map(agent_id, days)

    def memory_overlap(self,
                       agent_id_a: str,
                       agent_id_b: str,
                       days: int = 30) -> Dict[str, Any]:
        """记忆重叠分析（v5.4.3 新增）

        分析两个 Agent 的记忆在标签、分类、关键词层面的重叠度，
        识别共同知识领域和各自独有的知识领域。

        Args:
            agent_id_a: Agent A ID
            agent_id_b: Agent B ID
            days: 回溯天数（1-365）

        Returns:
            重叠分析：标签重叠率、分类重叠率、Jaccard 相似度
        """
        if not agent_id_a or not isinstance(agent_id_a, str):
            return {"error": "Agent A ID 不能为空"}
        if not agent_id_b or not isinstance(agent_id_b, str):
            return {"error": "Agent B ID 不能为空"}
        import unicodedata
        agent_id_a = "".join(c for c in agent_id_a[:128]
                             if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        agent_id_b = "".join(c for c in agent_id_b[:128]
                             if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        days = max(1, min(365, int(days)))
        return self._storage.memory_overlap(agent_id_a, agent_id_b, days)

    def conflict_graph(self,
                       agent_id: str,
                       days: int = 30) -> Dict[str, Any]:
        """记忆冲突检测图（v5.4.3 新增）

        检测同一 Agent 记忆中潜在的知识冲突：相同标签/分类下
        重要性差异显著的记忆对，或内容关键词高度重叠但重要性
        矛盾的记忆对。

        Args:
            agent_id: Agent ID
            days: 回溯天数（1-365）

        Returns:
            冲突图：冲突节点对、冲突类型、严重度分布、冲突密度
        """
        if not agent_id or not isinstance(agent_id, str):
            return {"error": "Agent ID 不能为空"}
        import unicodedata
        agent_id = "".join(c for c in agent_id[:128]
                           if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        days = max(1, min(365, int(days)))
        return self._storage.conflict_graph(agent_id, days)

    def drama_quote_map(self, drama_id: str) -> Dict[str, Any]:
        """经典台词地图（v5.4.3 新增）

        将短剧中的经典台词映射到集/场景/角色维度，分析经典台词的
        分布密度、角色贡献度和集数集中度。

        Args:
            drama_id: 短剧 ID

        Returns:
            台词地图：按集分布、按角色分布、按场景分布、台词时间线
        """
        if not drama_id or not isinstance(drama_id, str):
            return {"error": "短剧 ID 不能为空"}
        import unicodedata
        drama_id = "".join(c for c in drama_id[:64]
                           if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        return self._storage.drama_quote_map(drama_id)

    def character_growth(self,
                         drama_id: str,
                         character_id: str) -> Dict[str, Any]:
        """角色成长深度分析（v5.4.3 新增）

        在 character_arc 基础上深化分析：情感成长轨迹、对话复杂度演变、
        角色活跃度阶段划分。

        Args:
            drama_id: 短剧 ID
            character_id: 角色 ID

        Returns:
            成长分析：情感弧线、复杂度曲线、活跃度阶段、成长评分
        """
        if not drama_id or not isinstance(drama_id, str):
            return {"error": "短剧 ID 不能为空"}
        if not character_id or not isinstance(character_id, str):
            return {"error": "角色 ID 不能为空"}
        import unicodedata
        drama_id = "".join(c for c in drama_id[:64]
                           if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        character_id = "".join(c for c in character_id[:64]
                               if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        return self._storage.character_growth(drama_id, character_id)

    def scene_rhythm(self, drama_id: str) -> Dict[str, Any]:
        """场景节奏分析（v5.4.3 新增）

        分析短剧各场景的台词密度、对话节奏和场景长度分布，
        识别快节奏/慢节奏场景，评估整体节奏健康度。

        Args:
            drama_id: 短剧 ID

        Returns:
            节奏分析：各场景节奏数据、节奏曲线、节奏分类、整体评估
        """
        if not drama_id or not isinstance(drama_id, str):
            return {"error": "短剧 ID 不能为空"}
        import unicodedata
        drama_id = "".join(c for c in drama_id[:64]
                           if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        return self._storage.scene_rhythm(drama_id)

    # ===== v5.4.8 新增 =====

    def agent_memory_reinforce(self, agent_id: str,
                               min_access_count: int = 3,
                               boost_importance: bool = True,
                               dry_run: bool = False) -> Dict[str, Any]:
        """Agent 记忆强化（v5.4.8）— 基于访问频率自动提升高频记忆重要性"""
        if not agent_id or not isinstance(agent_id, str):
            return {"error": "Agent ID 不能为空"}
        import unicodedata
        agent_id = "".join(c for c in agent_id[:128]
                           if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        min_access_count = max(1, int(min_access_count))
        return self._storage.agent_memory_reinforce(
            agent_id, min_access_count, boost_importance, dry_run)

    def agent_shared_memories(self, from_agent: str, to_agent: str,
                              categories: Optional[List[str]] = None,
                              max_count: int = 50,
                              dry_run: bool = False) -> Dict[str, Any]:
        """跨 Agent 记忆共享（v5.4.8）— 将源 Agent 记忆复制给目标 Agent"""
        if not isinstance(from_agent, str) or not isinstance(to_agent, str):
            return {"error": "Agent ID 必须为字符串"}
        if not from_agent or not to_agent:
            return {"error": "Agent ID 不能为空"}
        import unicodedata
        from_agent = "".join(c for c in from_agent[:128]
                             if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        to_agent = "".join(c for c in to_agent[:128]
                           if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        max_count = max(1, min(1000, int(max_count)))
        return self._storage.agent_shared_memories(
            from_agent, to_agent, categories, max_count, dry_run)

    def agent_knowledge_domains(self, agent_id: str,
                                top_n: int = 10) -> Dict[str, Any]:
        """Agent 知识领域分析（v5.4.8）— 分析 Agent 知识分布"""
        if not agent_id or not isinstance(agent_id, str):
            return {"error": "Agent ID 不能为空"}
        import unicodedata
        agent_id = "".join(c for c in agent_id[:128]
                           if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        top_n = max(1, min(100, int(top_n)))
        return self._storage.agent_knowledge_domains(agent_id, top_n)

    def drama_generate_scene(self, drama_id: str, scene_title: str,
                             characters: Optional[List[str]] = None,
                             mood: str = "neutral",
                             setting: str = "") -> Dict[str, Any]:
        """AI 短剧场景生成（v5.4.8）— 基于上下文生成新场景"""
        if not isinstance(drama_id, str) or not isinstance(scene_title, str):
            return {"error": "短剧 ID 和场景标题必须为字符串"}
        if not drama_id or not scene_title:
            return {"error": "短剧 ID 和场景标题不能为空"}
        import unicodedata
        drama_id = "".join(c for c in drama_id[:64]
                           if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        scene_title = "".join(c for c in scene_title[:256]
                              if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        mood = "".join(c for c in mood[:32]
                       if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        setting = "".join(c for c in setting[:512]
                          if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        if characters:
            characters = [str(c)[:64] for c in characters[:50] if c]
        return self._storage.drama_generate_scene(
            drama_id, scene_title, characters, mood, setting)

    def drama_emotion_timeline(self, drama_id: str) -> Dict[str, Any]:
        """短剧情感时间线（v5.4.8）— 分析情感走向和曲线"""
        if not drama_id or not isinstance(drama_id, str):
            return {"error": "短剧 ID 不能为空"}
        import unicodedata
        drama_id = "".join(c for c in drama_id[:64]
                           if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        return self._storage.drama_emotion_timeline(drama_id)

    # ===== v5.5.0 新增：Agent 记忆终极增强 =====

    def agent_memory_snapshot(self, agent_id: str,
                               label: str = "") -> Dict[str, Any]:
        """Agent 记忆快照（v5.5.0）— 创建指定时间点的记忆全量快照

        快照包含该 Agent 的所有记忆元数据与内容摘要，可用于备份、
        版本对比和回滚参考。快照本身为只读，不影响原记忆。

        Args:
            agent_id: Agent ID
            label: 快照标签（可选，用于标识快照用途）

        Returns:
            {snapshot_id, agent_id, label, memory_count, created_at, categories}
        """
        if not agent_id or not isinstance(agent_id, str):
            return {"error": "Agent ID 不能为空"}
        import unicodedata
        agent_id = "".join(c for c in agent_id[:128]
                           if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        label = "".join(c for c in (label or "")[:128]
                        if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        return self._storage.agent_memory_snapshot(agent_id, label)

    def agent_memory_deduplicate(self, agent_id: str,
                                   similarity_threshold: float = 0.85,
                                   dry_run: bool = False) -> Dict[str, Any]:
        """Agent 记忆去重（v5.5.0）— 识别并合并高度相似的重复记忆

        基于内容相似度、标签重叠和分类匹配检测重复记忆，
        保留访问次数最多/重要性最高的版本，将低版本标记为合并。

        Args:
            agent_id: Agent ID
            similarity_threshold: 相似度阈值（0.5-1.0），高于此值视为重复
            dry_run: 仅预览不执行合并

        Returns:
            {evaluated, duplicates_found, merged, groups, dry_run}
        """
        if not agent_id or not isinstance(agent_id, str):
            return {"error": "Agent ID 不能为空"}
        import unicodedata
        agent_id = "".join(c for c in agent_id[:128]
                           if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        similarity_threshold = max(0.5, min(1.0, float(similarity_threshold)))
        return self._storage.agent_memory_deduplicate(
            agent_id, similarity_threshold, dry_run)

    def agent_memory_health_check(self, agent_id: str) -> Dict[str, Any]:
        """Agent 记忆健康检查（v5.5.0）— 全面评估记忆系统健康状态

        评估维度：记忆总量、层级分布、重复率、遗忘风险、质量分布、
        访问活跃度、分类均衡度、加密状态、索引完整性。

        Args:
            agent_id: Agent ID

        Returns:
            {agent_id, overall_score, dimensions, issues, recommendations}
        """
        if not agent_id or not isinstance(agent_id, str):
            return {"error": "Agent ID 不能为空"}
        import unicodedata
        agent_id = "".join(c for c in agent_id[:128]
                           if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        return self._storage.agent_memory_health_check(agent_id)

    def agent_memory_importance_recalibrate(self, agent_id: str,
                                              dry_run: bool = False) -> Dict[str, Any]:
        """Agent 记忆重要度重校准（v5.5.0）— 基于实际使用模式重新评估重要性

        综合访问频率、最近访问时间、关联记忆数量、质量评分等因子，
        重新评估每条记忆的重要性等级，修正主观标注的偏差。

        Args:
            agent_id: Agent ID
            dry_run: 仅预览不执行更新

        Returns:
            {evaluated, upgraded, downgraded, unchanged, details, dry_run}
        """
        if not agent_id or not isinstance(agent_id, str):
            return {"error": "Agent ID 不能为空"}
        import unicodedata
        agent_id = "".join(c for c in agent_id[:128]
                           if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        return self._storage.agent_memory_importance_recalibrate(agent_id, dry_run)

    # ===== v5.5.0 新增：AI 短剧终极增强 =====

    def drama_generate_episode(self, drama_id: str,
                                episode_number: int,
                                theme: str = "",
                                num_scenes: int = 3,
                                mood: str = "neutral") -> Dict[str, Any]:
        """AI 短剧分集生成（v5.5.0）— 生成完整一集的多场景结构大纲

        基于短剧类型、已有剧情和角色设定，生成包含多个场景的
        分集大纲，每场景含标题、目的、情感基调和关键事件建议。

        Args:
            drama_id: 短剧 ID
            episode_number: 集数（≥1）
            theme: 本集主题（可选）
            num_scenes: 场景数量（1-10）
            mood: 整体情感基调

        Returns:
            {episode_id, drama_id, episode_number, theme, scenes, suggestions}
        """
        if not drama_id or not isinstance(drama_id, str):
            return {"error": "短剧 ID 不能为空"}
        import unicodedata
        drama_id = "".join(c for c in drama_id[:64]
                           if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        episode_number = max(1, min(10000, int(episode_number)))
        num_scenes = max(1, min(10, int(num_scenes)))
        theme = "".join(c for c in (theme or "")[:256]
                        if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        mood = "".join(c for c in (mood or "")[:32]
                       if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        return self._storage.drama_generate_episode(
            drama_id, episode_number, theme, num_scenes, mood)

    def drama_character_dialogue(self, drama_id: str,
                                   character_name: str,
                                   context: str = "",
                                   emotion: str = "neutral",
                                   num_lines: int = 3) -> Dict[str, Any]:
        """AI 短剧角色台词生成（v5.5.0）— 基于角色性格和情境生成台词建议

        分析角色的历史台词、性格设定和当前情境，生成符合角色人设的
        台词建议，支持指定情感倾向和台词数量。

        Args:
            drama_id: 短剧 ID
            character_name: 角色名称
            context: 当前情境/上下文描述
            emotion: 期望情感（neutral/happy/sad/angry/surprised/tense）
            num_lines: 生成台词数量（1-10）

        Returns:
            {character, drama_id, emotion, lines, context_analysis}
        """
        if not drama_id or not isinstance(drama_id, str):
            return {"error": "短剧 ID 不能为空"}
        if not character_name or not isinstance(character_name, str):
            return {"error": "角色名称不能为空"}
        import unicodedata
        drama_id = "".join(c for c in drama_id[:64]
                           if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        character_name = "".join(c for c in character_name[:64]
                                 if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        context = "".join(c for c in (context or "")[:512]
                         if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        emotion = "".join(c for c in (emotion or "")[:32]
                         if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        num_lines = max(1, min(10, int(num_lines)))
        return self._storage.drama_character_dialogue(
            drama_id, character_name, context, emotion, num_lines)

    def drama_plot_twist_suggest(self, drama_id: str,
                                   num_suggestions: int = 3) -> Dict[str, Any]:
        """AI 短剧剧情反转建议（v5.5.0）— 基于已有剧情生成反转创意

        分析当前剧情走向、角色关系和伏笔设置，生成可能的剧情反转
        建议，包括反转类型、铺垫方式和对后续剧情的影响评估。

        Args:
            drama_id: 短剧 ID
            num_suggestions: 建议数量（1-10）

        Returns:
            {drama_id, current_analysis, twists, recommendations}
        """
        if not drama_id or not isinstance(drama_id, str):
            return {"error": "短剧 ID 不能为空"}
        import unicodedata
        drama_id = "".join(c for c in drama_id[:64]
                           if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        num_suggestions = max(1, min(10, int(num_suggestions)))
        return self._storage.drama_plot_twist_suggest(drama_id, num_suggestions)

    def drama_script_export(self, drama_id: str,
                             format: str = "standard") -> Dict[str, Any]:
        """AI 短剧剧本导出（v5.5.0）— 将短剧数据导出为格式化剧本

        整合场次、角色、台词信息，生成标准格式的剧本内容，
        支持按集导出和全剧导出。

        Args:
            drama_id: 短剧 ID
            format: 导出格式（standard/condensed/detailed）

        Returns:
            {drama_id, title, format, script_content, total_scenes, total_lines}
        """
        if not drama_id or not isinstance(drama_id, str):
            return {"error": "短剧 ID 不能为空"}
        import unicodedata
        drama_id = "".join(c for c in drama_id[:64]
                           if unicodedata.category(c)[0] != "C" or c in "\n\r\t")
        valid_formats = {"standard", "condensed", "detailed"}
        if format not in valid_formats:
            format = "standard"
        return self._storage.drama_script_export(drama_id, format)

