"""
MindForge v5.0 联邦记忆网络
多 Agent 间安全共享记忆，端侧联邦学习

v5.6.5 安全加固：
- P0 #1：跨节点签名由对称 HMAC 升级为真正的非对称 **Ed25519** 数字签名。
  发送方用自己的私钥签名，接收方只用发送方注册的公钥验签，公钥即便公开也
  无法伪造签名，消除“一方泄露共享密钥即可冒充对方”的根本问题。为平滑迁移，
  仍保留 HMAC 作为仅配置了 shared_secret 的旧节点的回退方案。
- P0 #2：receive_memory 增加重放攻击防护。签名消息必须携带发送方时间戳与
  随机 nonce（且均被签名覆盖）：超出时钟偏差窗口的旧消息拒收，同一节点同一
  nonce 只接受一次（带容量上限与过期清理的去重表），截获重放无法重复入队。
- P1 #10：shared_memories 中已过 expires_at 的共享记录由
  purge_expired_shared_memories() 定期清理（节流触发，线程安全），不再无限膨胀。
"""

import json
import hashlib
import hmac
import base64
import binascii
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


def generate_keypair() -> Tuple[str, str]:
    """生成一对 Ed25519 密钥（v5.6.5 P0 #1）

    Returns:
        (private_key_b64, public_key_b64)，均为 32 字节原始密钥的
        URL 安全 base64 编码（无填充）。私钥是签名种子，必须保密；
        公钥可安全地分发给所有联邦对端用于验签。
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    priv = Ed25519PrivateKey.generate()
    seed = priv.private_bytes_raw()
    pub = priv.public_key().public_bytes_raw()
    return (_b64e(seed), _b64e(pub))


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


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
    public_key: str = ""  # Ed25519 验签公钥（b64），公开即可，配置后优先使用非对称验签
    shared_secret: str = ""  # 旧版 HMAC 共享密钥（迁移回退用），配置 Ed25519 后不再需要
    last_seen: float = 0.0
    shared_categories: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "peer_id": self.peer_id,
            "name": self.name,
            "status": self.status.value,
            "trust_level": self.trust_level,
            # 公钥不是秘密，可随节点信息导出；shared_secret 永不导出。
            "public_key": self.public_key,
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

        # v5.6.5 P0 #2：重放防护参数
        # 允许的收发双方时钟偏差（秒）。消息 _ts 与本地时间相差超过该值即视为
        # 过期/伪造而拒绝。
        self.allowed_skew_seconds = float(config.get("federated_allowed_skew_seconds", 300))
        # 每个节点保留的已用 nonce 上限，超出直接拒收新消息（防止内存被打满）。
        self._replay_max_nonces = int(config.get("federated_replay_max_nonces", 100000))
        # peer_id -> {nonce: 该消息时间戳}，仅保留偏差窗口内的 nonce
        self._seen_nonces: Dict[str, Dict[str, float]] = {}
        self._fed_lock = threading.Lock()

        # v5.6.5 P0 #1：本地 Ed25519 签名私钥。优先取 config 注入（持久化身份），
        # 否则生成一次性密钥对（重启后身份变化，仅适合临时进程）。
        seed_b64 = config.get("local_private_key", "")
        pub_b64 = config.get("local_public_key", "")
        if seed_b64:
            self._local_priv_b64 = seed_b64
            if not pub_b64:
                try:
                    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
                    priv = Ed25519PrivateKey.from_private_bytes(_b64d(seed_b64))
                    pub_b64 = _b64e(priv.public_key().public_bytes_raw())
                except (ValueError, binascii.Error, TypeError) as e:
                    logger.error("本地联邦私钥无效，已改为临时密钥: %s", e)
                    seed_b64, pub_b64 = generate_keypair()
                    self._local_priv_b64 = seed_b64
        else:
            seed_b64, pub_b64 = generate_keypair()
            self._local_priv_b64 = seed_b64
        self._local_pub_b64 = pub_b64

        # v5.6.5 P1 #10：共享记录过期清理的节流时间戳
        self._last_shared_purge = 0.0
        self._shared_purge_interval = float(config.get("shared_purge_interval", 300))

    @property
    def local_public_key(self) -> str:
        """本节点对外公布的 Ed25519 验签公钥（b64）"""
        return self._local_pub_b64

    def register_peer(self, peer_id: str, name: str,
                      trust_level: float = 0.5,
                      shared_categories: Optional[List[str]] = None,
                      shared_secret: str = "",
                      public_key: str = "") -> FederatedPeer:
        """注册联邦节点

        v5.6.5（P0 #1）：推荐传入对端的 Ed25519 ``public_key``（验签公钥）。
        配置公钥后，与该节点的消息签名/验签走非对称 Ed25519；仅配置
        ``shared_secret`` 的旧节点继续走 HMAC（迁移回退）。两者都未配置时，
        节点仍会被注册（向后兼容），但签名相关操作 fail-closed，并记录告警。
        """
        peer = FederatedPeer(
            peer_id=peer_id,
            name=name,
            status=PeerStatus.OFFLINE,
            trust_level=trust_level,
            shared_categories=shared_categories or [],
            public_key=(public_key or "").strip(),
            shared_secret=shared_secret or "",
            last_seen=time.time(),
        )
        if not peer.public_key and not peer.shared_secret:
            logger.warning(
                "联邦节点 %s 未配置 public_key/shared_secret，签名/验签将 fail-closed "
                "拒绝（推荐 register_peer(public_key=<对端 Ed25519 公钥>)）",
                peer_id,
            )
        elif peer.shared_secret and not peer.public_key:
            logger.info(
                "联邦节点 %s 仍使用 HMAC shared_secret（对称）。建议尽快交换 "
                "Ed25519 公钥并改用非对称签名，避免单点密钥泄露即被冒充。",
                peer_id,
            )
        self.peers[peer_id] = peer
        with self._fed_lock:
            self._seen_nonces.setdefault(peer_id, {})
        return peer

    def remove_peer(self, peer_id: str) -> bool:
        """移除节点"""
        if peer_id in self.peers:
            del self.peers[peer_id]
            with self._fed_lock:
                self._seen_nonces.pop(peer_id, None)
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
        self._maybe_purge_shared()

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

        # v5.6.6 P3：与 purge/get/revoke 在同一把锁内变更共享字典
        with self._fed_lock:
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
        # v5.6.6 P3：检查与删除需在同一临界区原子完成，避免与 purge 竞态
        with self._fed_lock:
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

    def sign_payload(self, data: Dict[str, Any], peer_id: str
                     ) -> Tuple[Dict[str, Any], str]:
        """构造带防重放信封的消息并签名（v5.6.5 P0 #2）

        在业务数据中注入 ``_ts``（发送时间戳）与 ``_mid``（随机 nonce），
        两者随后被签名覆盖，攻击者无法在不破坏签名的前提下篡改或重放。

        Returns:
            (envelope, signature)，调用方应把两者原样传给对端 receive_memory。
        """
        envelope = dict(data)
        envelope["_ts"] = time.time()
        envelope["_mid"] = uuid.uuid4().hex
        return envelope, self._compute_signature(envelope, peer_id)

    def receive_memory(self, from_peer: str,
                       memory_data: Dict,
                       signature: str = "") -> bool:
        """接收来自其他节点的记忆

        v5.6.5 P0 #2：对**已签名**消息强制重放防护——必须含被签名覆盖的
        ``_ts``/``_mid``，时间戳超窗或 nonce 重复一律拒绝。未签名（显式
        allow_unsigned_peers）的旧链路保持兼容：携带信封则同样校验，缺失则
        按旧行为放行。
        """
        if from_peer not in self.peers:
            return False

        peer = self.peers[from_peer]
        # v5.4.4 修复 #3：统一信任阈值为 0.3，与 share_memory 保持一致
        if peer.trust_level < 0.3:
            return False

        signed = bool(signature)
        if not self._verify_signature(memory_data, signature, from_peer):
            return False

        # 重放防护：签名通道强制；未签名通道尽力而为
        if signed:
            if not self._replay_check(from_peer, memory_data, strict=True):
                return False
        elif isinstance(memory_data, dict) and ("_ts" in memory_data or "_mid" in memory_data):
            if not self._replay_check(from_peer, memory_data, strict=False):
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

    def _replay_check(self, peer_id: str, data: Dict, strict: bool) -> bool:
        """校验时间戳新鲜度与 nonce 唯一性（v5.6.5 P0 #2）

        strict=True（已签名通道）：缺少 _ts/_mid、时间戳超窗、nonce 重复均拒绝。
        strict=False（未签名旧链路）：仅当字段可解析时校验，缺失不拦。
        """
        if not isinstance(data, dict):
            return not strict
        ts = data.get("_ts")
        mid = data.get("_mid")
        now = time.time()
        if ts is None or mid is None:
            return not strict
        try:
            ts = float(ts)
        except (TypeError, ValueError):
            return not strict
        if not isinstance(mid, str) or not mid or len(mid) > 128:
            return not strict
        # 跨节点必须用墙上时钟（time.monotonic 各进程基准不同，无法比较）
        if abs(now - ts) > self.allowed_skew_seconds:
            logger.warning("联邦消息时间戳超窗，拒绝重放: peer=%s skew=%.1fs",
                           peer_id, now - ts)
            return False

        with self._fed_lock:
            seen = self._seen_nonces.setdefault(peer_id, {})
            # 先清理超出偏差窗口的旧 nonce（它们不可能再被合法重放）
            stale = [n for n, t in seen.items() if now - t > self.allowed_skew_seconds]
            for n in stale:
                del seen[n]
            if mid in seen:
                logger.warning("检测到联邦重放消息，已拒绝: peer=%s mid=%s", peer_id, mid)
                return False
            # 容量保护：异常暴涨时拒收新消息而非无限占用内存
            if len(seen) >= self._replay_max_nonces:
                logger.error("节点 %s 的 nonce 去重表已满（%d），暂时拒收",
                             peer_id, self._replay_max_nonces)
                return False
            seen[mid] = ts
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
                logger.warning("detect_incoming 异常（已降级为 new）: %s", e)
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

    def purge_expired_shared_memories(self, now: Optional[float] = None) -> int:
        """清理已过期的共享记忆记录（v5.6.5 P1 #10）

        expires_at 已过的记录从内存字典删除，返回清理条数。线程安全。
        """
        if now is None:
            now = time.time()
        # v5.6.6 P3：遍历 + pop 必须与 _replay_check/share/revoke/get 互斥，
        # 否则并发迭代字典时可能 RuntimeError 或与写入相互覆盖。统一用 _fed_lock。
        with self._fed_lock:
            expired = [mid for mid, s in self.shared_memories.items()
                       if s.expires_at is not None and s.expires_at <= now]
            for mid in expired:
                self.shared_memories.pop(mid, None)
        if expired:
            logger.info("清理 %d 条过期联邦共享记录", len(expired))
        return len(expired)

    def _maybe_purge_shared(self) -> None:
        """节流触发共享记录过期清理（避免每次操作都全表扫描）"""
        now = time.time()
        if now - self._last_shared_purge < self._shared_purge_interval:
            return
        self._last_shared_purge = now
        try:
            self.purge_expired_shared_memories(now)
        except Exception as e:  # 清理失败不应影响正常共享
            logger.error("purge_expired_shared_memories failed: %s", e)

    def get_shared_memories(self, peer_id: Optional[str] = None) -> List[SharedMemory]:
        """获取共享记忆列表（自动过滤掉已过期记录）"""
        self._maybe_purge_shared()
        now = time.time()
        # v5.6.6 P3：在锁内快照，避免与 purge/share/revoke 并发迭代竞态
        with self._fed_lock:
            if peer_id:
                return [
                    s for s in list(self.shared_memories.values())
                    if peer_id in s.shared_with
                    and (s.expires_at is None or s.expires_at > now)
                ]
            return [s for s in list(self.shared_memories.values())
                    if s.expires_at is None or s.expires_at > now]

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

    @staticmethod
    def _canonical_message(data: Dict) -> bytes:
        """确定性序列化待签名内容（HMAC 与 Ed25519 共用，保持历史兼容）"""
        return json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")

    def _compute_signature(self, data: Dict, peer_id: str) -> str:
        """计算签名（v5.6.5 P0 #1）

        - 对端配置了 Ed25519 public_key：用**本节点私钥**做非对称签名（b64）。
        - 否则对端仅有 shared_secret：回退 HMAC-SHA256（旧版对称，迁移用）。
        - 两者皆无：返回 ""（验签端 fail-closed）。
        """
        peer = self.peers.get(peer_id)
        if not peer:
            return ""
        msg = self._canonical_message(data)

        if peer.public_key:
            try:
                from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
                priv = Ed25519PrivateKey.from_private_bytes(_b64d(self._local_priv_b64))
                return _b64e(priv.sign(msg))
            except (ValueError, binascii.Error, TypeError) as e:
                logger.error("Ed25519 签名失败: %s", e)
                return ""

        if not peer.shared_secret:
            return ""
        key = peer.shared_secret.encode("utf-8")
        return hmac.new(key, msg, digestmod=hashlib.sha256).hexdigest()

    def _verify_signature(self, data: Dict, signature: str, peer_id: str) -> bool:
        """验证签名（v5.6.5 P0 #1）

        按对端注册的密钥材料选择 Ed25519 或 HMAC 验签。fail-closed：
        - 未注册节点：拒绝
        - 无签名：仅当 allow_unsigned_peers=True 时允许
        - Ed25519 公钥存在：非对称验签，非法/损坏签名一律拒绝
        - 仅 shared_secret：HMAC 恒定时间比较
        """
        peer = self.peers.get(peer_id)
        if not peer:
            return False  # fail-closed: 未注册节点直接拒绝

        if not signature:
            # 无签名时仅允许显式配置的未签名节点
            return self.allow_unsigned_peers

        msg = self._canonical_message(data)

        if peer.public_key:
            try:
                from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
                from cryptography.exceptions import InvalidSignature
                pub = Ed25519PublicKey.from_public_bytes(_b64d(peer.public_key))
                try:
                    pub.verify(_b64d(signature), msg)
                    return True
                except (InvalidSignature, ValueError, binascii.Error):
                    return False
            except (ValueError, binascii.Error) as e:
                logger.error("节点 %s 的 Ed25519 公钥无效: %s", peer_id, e)
                return False

        if not peer.shared_secret:
            return False  # fail-closed: 无任何密钥材料的节点直接拒绝

        expected = self._compute_signature(data, peer_id)
        if not expected:
            return False
        return hmac.compare_digest(signature, expected)

    def compute_federated_stats(self) -> Dict:
        """计算联邦统计"""
        self._maybe_purge_shared()
        return {
            "local_peer_id": self.local_peer_id,
            "local_public_key": self._local_pub_b64,
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
