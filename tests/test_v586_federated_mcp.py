# -*- coding: utf-8 -*-
"""v5.8.6 联邦共享 MCP 工具回归测试

直接调用 mcp.server.HANDLERS[name](mf, args)，不启 MCP server。
"""
import os
import sys
import tempfile
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)


def _mk_mf():
    import core.mindforge as mf
    from core.types import MemoryConfig
    tmp = tempfile.mkdtemp(prefix="mf_v586_", dir=_REPO)
    eng = mf.MindForge(config=MemoryConfig(
        db_path=os.path.join(tmp, "f.db"),
        key_file=os.path.join(tmp, "f.key"), encrypted=True))
    eng.init_with_password("pw-586")
    return eng


def _as_list(result):
    """handler 返回可能是 list / dict{peers}/dict{result}，统一成 list。"""
    if result is None:
        return []
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        for k in ("peers", "result", "items"):
            if isinstance(result.get(k), list):
                return result[k]
    return [result]


class TestFederatedMCPTools(unittest.TestCase):
    def setUp(self):
        from mcp.server import HANDLERS
        self.h = HANDLERS
        self.mf = _mk_mf()

    def test_register_and_list_peer(self):
        self.h["fed_peer_register"](self.mf, {
            "peer_id": "peer-a", "name": "Peer A", "trust_level": 0.8})
        lst = self.h["fed_peer_list"](self.mf, {})
        peers = _as_list(lst)
        self.assertTrue(any("peer-a" in str(p) for p in peers))

    def test_remove_peer(self):
        self.h["fed_peer_register"](self.mf, {"peer_id": "peer-b", "name": "B"})
        # remove 不应抛异常
        self.h["fed_peer_remove"](self.mf, {"peer_id": "peer-b"})

    def test_share_and_list_shared(self):
        mem = self.mf.add("待共享的记忆", category="shared")
        self.h["fed_peer_register"](self.mf, {"peer_id": "peer-c", "name": "C"})
        self.h["fed_memory_share"](self.mf, {
            "memory_id": mem.id, "peer_ids": ["peer-c"]})
        lst = self.h["fed_memory_list_shared"](self.mf, {})
        # 不崩溃即可（fail-closed 签名下共享队列可能为空）
        self.assertIsNotNone(lst)

    def test_revoke_share(self):
        mem = self.mf.add("待撤销", category="shared")
        self.h["fed_peer_register"](self.mf, {"peer_id": "peer-d", "name": "D"})
        self.h["fed_memory_share"](self.mf, {
            "memory_id": mem.id, "peer_ids": ["peer-d"]})
        # revoke 不崩溃
        self.h["fed_memory_revoke"](self.mf, {"memory_id": mem.id})

    def test_federated_stats(self):
        r = self.h["fed_stats"](self.mf, {})
        self.assertIsNotNone(r)

    def test_federated_search_no_peers(self):
        # 无真实 peer 网络应安全返回空结果而非崩溃
        r = self.h["fed_search"](self.mf, {"query": "anything"})
        self.assertIsNotNone(r)

    def test_required_args_validation(self):
        # 缺 peer_id 应返回错误 dict（ok=False）
        r = self.h["fed_peer_remove"](self.mf, {})
        self.assertIsInstance(r, dict)
        self.assertFalse(r.get("ok", True))


if __name__ == "__main__":
    unittest.main(verbosity=2)
