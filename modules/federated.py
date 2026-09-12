"""
MindForge v5.0 联邦记忆网络
多 Agent 间安全共享记忆，端侧联邦学习
"""

import json
import hashlib
import hmac
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from enum import Enum


class PeerStatus(Enum):
    """节点状态"""
    ONLINE = "online"
    OFFLINE = "offline"
    TRUSTED = "trusted"
    UNTRUSTED = "untrusted"


@dataclass
class FederatedPeer:
    """联邦节点"""
    peer_id: str
    name: str
    status: PeerStatus = PeerStatus.OFFLINE
    trust_level: float = 0.5
    public_key: str = ""  # 预留：非对称签名公钥（Ed25519 等），不用于 HMAC
    shared_secret: str = ""  # HMAC 共享密钥，双方必须一致且保密
    last_seen: float = 0.0
    shared_categories: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "peer_id": self.peer_id,
            "name": self.name,
            "status": self.status.value,
            "trust_level": self.trust_level,
            "last_seen": self.last_seen,
            "shared_categories": self.shared_categories,
            "metadata": self.metadata,
        }


@dataclass
class SharedMemory:
    """共享记忆"""
    memory_id: str
    original_peer: str
    shared_with: List[str] = field(default_factory=list)
    access_count: Dict[str, int] = field(default_factory=dict)
    shared_at: float = 0.0
    expires_at: Optional[float] = None
    access_policy: str = "read_only"


class FederatedMemory:
    """联邦记忆管理器"""

    def __init__(self, storage=None, local_peer_id: str = "",
                 acl=None, conflict_resolver=None,
                 config: Optional[Dict] = None):
        self.storage = storage
        self.local_peer_id = local_peer_id or str(uuid.uuid4())
        self.peers: Dict[str, FederatedPeer] = {}
        self.shared_memories: Dict[str, SharedMemory] = {}
        self._incoming_queue: List[Dict] = []
        self._outgoing_queue: List[Dict] = []
        # v5.4.2 修复：队列大小上限，防止恶意 peer 发送海量消息耗尽内存
        self._MAX_QUEUE_SIZE = 10000
        # v5.4.2 新增：细粒度 ACL（modules/federated_acl.py）与
        # 共享记忆冲突解析器（modules/share_conflict.py），均可选注入
        self.acl = acl
        self.conflict_resolver = conflict_resolver
        # P1-004: 安全加固 - 无签名节点默认拒绝（fail-closed）
        config = config or {}
        self.allow_unsigned_peers = config.get("allow_unsigned_peers", False)

    def register_peer(self, peer_id: str, name: str,
                      trust_level: float = 0.5,
                      shared_categories: Optional[List[str]] = None) -> FederatedPeer:
        """注册联邦节点"""
        peer = FederatedPeer(
            peer_id=peer_id,
            name=name,
            trust_level=trust_level,
            shared_categories=shared_categories or [],
            last_seen=time.time(),
        )
        self.peers[peer_id] = peer
        return peer

    def remove_peer(self, peer_id: str) -> bool:
        """移除节点"""
        if peer_id in self.peers:
            del self.peers[peer_id]
            return True
        return False

    def update_trust_level(self, peer_id: str, new_level: float) -> bool:
        """更新信任级别"""
        if peer_id in self.peers:
            self.peers[peer_id].trust_level = max(0.0, min(1.0, new_level))
            self.peers[peer_id].last_seen = time.time()
            return True
        return False

    def share_memory(self, memory_id: str,
                     peer_ids: List[str],
                     access_policy: str = "read_only",
                     expires_hours: Optional[float] = None) -> Optional[SharedMemory]:
        """共享记忆给其他节点

        v5.4.2 修复：此前信任过滤循环为空操作（dead code），未注册或
        低信任度（<0.3）节点仍会进入 shared_with。现实际过滤，并可叠加
        细粒度 ACL（注入 self.acl 时按 read 操作逐节点评估）。
        """
        if not self._verify_memory_exists(memory_id):
            return None

        # v5.4.2 修复：真实过滤未注册 / 低信任节点
        eligible: List[str] = []
        skipped: Dict[str, str] = {}
        for pid in peer_ids:
            if pid not in self.peers:
                skipped[pid] = "未注册的节点"
                continue
            if self.peers[pid].trust_level < 0.3:
                skipped[pid] = f"信任度不足（{self.peers[pid].trust_level:.2f} < 0.3）"
                continue
            eligible.append(pid)

        # v5.4.2 新增：细粒度 ACL 过滤（按 read 操作评估）
        if self.acl is not None and eligible:
            memory_category = None
            memory_tags: List[str] = []
            if self.storage:
                try:
                    entry = self.storage.get_memory(memory_id)
                    if entry is not None:
                        memory_category = entry.category
                        memory_tags = list(entry.tags or [])
                except Exception:
                    pass
            trust_map = {pid: self.peers[pid].trust_level for pid in eligible}
            verdict = self.acl.filter_peers(
                memory_id=memory_id, peer_ids=eligible, operation="read",
                trust_map=trust_map, memory_category=memory_category,
                memory_tags=memory_tags)
            for pid, reason in verdict["denied"].items():
                skipped[pid] = f"ACL 拒绝: {reason}"
            eligible = verdict["allowed"]

        self.last_share_skipped = skipped

        if not eligible:
            return None

        shared = SharedMemory(
            memory_id=memory_id,
            original_peer=self.local_peer_id,
            shared_with=eligible,
            shared_at=time.time(),
            expires_at=time.time() + expires_hours * 3600 if expires_hours else None,
            access_policy=access_policy,
        )

        self.shared_memories[memory_id] = shared

        for pid in eligible:
            if len(self._outgoing_queue) < self._MAX_QUEUE_SIZE:
                self._outgoing_queue.append({
                    "type": "memory_share",
                    "from": self.local_peer_id,
                    "to": pid,
                    "memory_id": memory_id,
                    "access_policy": access_policy,
                    "timestamp": time.time(),
                })

        return shared

    def revoke_share(self, memory_id: str, peer_ids: Optional[List[str]] = None) -> bool:
        """撤销共享"""
        if memory_id not in self.shared_memories:
            return False

        shared = self.shared_memories[memory_id]

        if peer_ids:
            shared.shared_with = [pid for pid in shared.shared_with if pid not in peer_ids]
            if not shared.shared_with:
                del self.shared_memories[memory_id]
        else:
            del self.shared_memories[memory_id]

        return True

    def receive_memory(self, from_peer: str,
                       memory_data: Dict,
                       signature: str = "") -> bool:
        """接收来自其他节点的记忆"""
        if from_peer not in self.peers:
            return False

        peer = self.peers[from_peer]
        # v5.4.4 修复 #3：统一信任阈值为 0.3，与 share_memory 保持一致
        if peer.trust_level < 0.3:
            return False

        if not self._verify_signature(memory_data, signature, from_peer):
            return False

        # v5.4.2 修复：队列大小上限，超限拒绝新消息
        if len(self._incoming_queue) >= self._MAX_QUEUE_SIZE:
            return False

        self._incoming_queue.append({
            "from": from_peer,
            "data": memory_data,
            "received_at": time.time(),
        })

        return True

    def accept_incoming(self, memory_index: int = -1,
                        resolve_strategy: str = "manual") -> Optional[str]:
        """接受传入的记忆

        v5.4.2 新增：注入 conflict_resolver 时，对指向本地已有记忆的
        传入更新做冲突检测与解决：
        - resolve_strategy="lww"       按（版本, 时间戳, peer）决胜，新者覆盖
        - resolve_strategy="keep_both" 传入内容另存分支记忆并建立关联
        - resolve_strategy="manual"    冲突挂起，等待人工处理（返回 None）
        未注入 conflict_resolver 时保持原有直接入库行为。
        """
        if not self._incoming_queue:
            return None

        item = self._incoming_queue.pop(memory_index)

        # v5.4.2 新增：冲突检测与解决
        if self.conflict_resolver is not None and self.storage:
            incoming = dict(item.get("data") or {})
            incoming.setdefault("from_peer", item.get("from", ""))
            try:
                detection = self.conflict_resolver.detect_incoming(incoming)
            except (ValueError, KeyError, TypeError) as e:
                # v5.4.4 修复 #2：只捕获预期异常，不吞掉数据库损坏等严重错误
                import logging
                logging.getLogger(__name__).warning(
                    "detect_incoming 异常（已降级为 new）: %s", e)
                detection = {"conflict": False, "action": "new",
                             "error": str(e)}
            if detection.get("conflict"):
                if resolve_strategy in ("lww", "keep_both"):
                    resolution = self.conflict_resolver.resolve(
                        detection["conflict_id"], resolve_strategy,
                        actor=str(item.get("from", "")))
                    if resolution.get("success"):
                        return resolution.get("resolved_memory_id")
                # manual 或解决失败：冲突挂起
                return None
            if detection.get("action") == "noop":
                return detection.get("local_memory_id")

        if self.storage:
            try:
                entry = self.storage.add_memory(
                    content=item["data"].get("content", ""),
                    category=item["data"].get("category", "federated"),
                    tags=item["data"].get("tags", []) + [f"from:{item['from']}"],
                    source_agent=f"federated:{item['from']}",
                    metadata={
                        "federated_origin": item["from"],
                        "received_at": item["received_at"],
                    }
                )
                return entry.id
            except (ValueError, TypeError):
                return None

        return None

    def federated_search(self, query: str,
                         peer_ids: Optional[List[str]] = None,
                         max_per_peer: int = 5) -> Dict[str, List[Dict]]:
        """联邦搜索（跨节点）

        v5.4.2 修复：返回实际的本地搜索结果 + 将搜索请求入队等待远端响应。
        之前仅写队列返回空 results，导致调用方永远拿不到搜索结果。
        """
        results = {}

        search_peers = peer_ids or list(self.peers.keys())

        # 先在本地 storage 中搜索
        local_results = []
        if self.storage and query:
            try:
                # v5.4.7 修复 H-3：使用 fuzzy_search 替代不存在的 search_memories
                local_results = self.storage.fuzzy_search(
                    query, limit=max_per_peer * len(search_peers) or max_per_peer)
            except Exception:
                pass

        for pid in search_peers:
            if pid not in self.peers:
                continue
            peer = self.peers[pid]
            if peer.status == PeerStatus.OFFLINE:
                continue
            if peer.trust_level < 0.3:
                continue

            # v5.4.2 修复：队列大小上限保护
            if len(self._outgoing_queue) < self._MAX_QUEUE_SIZE:
                self._outgoing_queue.append({
                    "type": "search_request",
                    "from": self.local_peer_id,
                    "to": pid,
                    "query": query,
                    "max_results": max_per_peer,
                    "timestamp": time.time(),
                })

            # 将本地结果在活跃 peer 间分片分配（而非每个 peer 拿相同的副本）
            active_peer_ids = [pid for pid in search_peers
                              if pid in self.peers
                              and self.peers[pid].status != PeerStatus.OFFLINE
                              and self.peers[pid].trust_level >= 0.3]
            if active_peer_ids:
                peer_idx = active_peer_ids.index(pid)
                chunk_size = max(1, len(local_results) // len(active_peer_ids))
                start = peer_idx * chunk_size
                end = start + chunk_size if pid != active_peer_ids[-1] else len(local_results)
                results[pid] = local_results[start:end]
            else:
                results[pid] = []

        return results

    def get_shared_memories(self, peer_id: Optional[str] = None) -> List[SharedMemory]:
        """获取共享记忆列表"""
        if peer_id:
            return [
                s for s in self.shared_memories.values()
                if peer_id in s.shared_with
            ]
        return list(self.shared_memories.values())

    def get_peers(self, status: Optional[PeerStatus] = None) -> List[FederatedPeer]:
        """获取节点列表"""
        peers = list(self.peers.values())
        if status:
            peers = [p for p in peers if p.status == status]
        return peers

    def get_queue_sizes(self) -> Dict[str, int]:
        """获取队列大小"""
        return {
            "incoming": len(self._incoming_queue),
            "outgoing": len(self._outgoing_queue),
        }

    def _verify_memory_exists(self, memory_id: str) -> bool:
        """验证记忆存在

        v5.6.2 安全修复：无 storage 时 fail-closed 返回 False，
        而非 fail-open 返回 True。
        """
        if not self.storage:
            return False  # fail-closed：无法验证则视为不存在
        try:
            entry = self.storage.get_memory(memory_id)
            return entry is not None
        except Exception:
            # v5.4.5 修复：移除顶层 import sqlite3，改为通用异常捕获
            # OperationalError 等数据库异常在此场景下统一返回 False
            return False

    def _compute_signature(self, data: Dict, peer_id: str) -> str:
        """计算 HMAC-SHA256 签名

        使用 peer 的 shared_secret 作为 HMAC 密钥（必须保密，双方共享）。
        注意：public_key 不用于 HMAC——公钥是公开的，用它做 HMAC 密钥任何人可伪造。
        """
        peer = self.peers.get(peer_id)
        if not peer or not peer.shared_secret:
            return ""
        key = peer.shared_secret.encode("utf-8")
        msg = json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hmac.new(key, msg, digestmod=hashlib.sha256).hexdigest()

    def _verify_signature(self, data: Dict, signature: str, peer_id: str) -> bool:
        """验证 HMAC-SHA256 签名

        P0 安全修复：使用 shared_secret（而非 public_key）做 HMAC 密钥。
        fail-closed 安全策略：
        - 未注册节点：直接拒绝
        - 无 shared_secret 节点：直接拒绝
        - 签名不匹配：拒绝
        - 无签名时：仅当 allow_unsigned_peers=True 时允许
        """
        peer = self.peers.get(peer_id)
        if not peer:
            return False  # fail-closed: 未注册节点直接拒绝

        if not signature:
            # 无签名时仅允许显式配置的未签名节点
            return self.allow_unsigned_peers

        if not peer.shared_secret:
            return False  # fail-closed: 无共享密钥节点直接拒绝

        expected = self._compute_signature(data, peer_id)
        if not expected:
            return False
        return hmac.compare_digest(signature, expected)

    def compute_federated_stats(self) -> Dict:
        """计算联邦统计"""
        return {
            "local_peer_id": self.local_peer_id,
            "total_peers": len(self.peers),
            "online_peers": sum(1 for p in self.peers.values() if p.status == PeerStatus.ONLINE),
            "trusted_peers": sum(1 for p in self.peers.values() if p.trust_level >= 0.7),
            "shared_memories": len(self.shared_memories),
            "incoming_queue": len(self._incoming_queue),
            "outgoing_queue": len(self._outgoing_queue),
            "avg_trust_level": (
                sum(p.trust_level for p in self.peers.values()) / len(self.peers)
                if self.peers else 0
            ),
        }
