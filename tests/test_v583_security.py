# -*- coding: utf-8 -*-
"""v5.8.3 安全修复回归测试

覆盖外部安全审计报告 7 项发现中的 6 项确认问题修复：
  P1-1 SSRF 字符串前缀黑名单绕过（ipaddress 标准判定）
  P1-2 开放重定向绕过 SSRF（逐跳复核）
  P2-1 URL 连接器响应读取无上限（5MB 分块上限）
  P2-2 未签名链路无信封消息可重放（内容哈希去重）
  P3-1 2FA 非标准 TOTP 语义（RFC 6238 标准实现）
  P3-2 gdpr_report 泄露 db_path 绝对路径（basename）
"""
import os
import sys
import tempfile
import time
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)


def _tmp():
    return tempfile.mkdtemp(prefix="mf_v583_", dir=_REPO)


class TestP11SsrfiPBlacklist(unittest.TestCase):
    """P1-1：_is_private_ip 必须拒绝全部编码绕过向量"""

    @classmethod
    def setUpClass(cls):
        from modules.connectors import _is_private_ip
        cls.check = staticmethod(_is_private_ip)

    def test_常规内网地址全部拒绝(self):
        for host in ["127.0.0.1", "10.0.0.5", "192.168.1.1",
                     "169.254.169.254", "172.16.0.1", "172.31.255.255",
                     "::1"]:
            self.assertTrue(self.check(host), "%s 应被拒绝" % host)

    def test_编码绕过向量全部拒绝(self):
        # 十进制整数 IP == 169.254.169.254；八进制同值；IPv4-mapped
        for host in ["2852039166", "0251.0376.0251.0376",
                     "::ffff:169.254.169.254"]:
            self.assertTrue(self.check(host), "%s 应被拒绝" % host)

    def test_ipv6内网与CGNAT_0_0_0_0全部拒绝(self):
        for host in ["fe80::1", "fc00::1", "fd00::1",
                     "100.64.0.1", "100.127.255.254", "0.0.0.0"]:
            self.assertTrue(self.check(host), "%s 应被拒绝" % host)

    def test_公网地址放行(self):
        # IP 字面量（不依赖沙箱 DNS）
        self.assertFalse(self.check("8.8.8.8"))
        self.assertFalse(self.check("1.1.1.1"))


class TestP12RedirectSsrfi(unittest.TestCase):
    """P1-2：_SafeRedirectHandler 重定向逐跳复核"""

    @classmethod
    def setUpClass(cls):
        from modules.connectors import _SafeRedirectHandler
        cls.handler = _SafeRedirectHandler()

    def _req(self, url):
        class _Req:
            def __init__(self, u):
                self.full_url = u
                self.headers = {}
                self.unredirected_hdrs = {}

            def get_method(self):
                return "GET"

            def add_unredirected_header(self, k, v):
                self.unredirected_hdrs[k] = v

            @property
            def origin_req_host(self):
                return "a.com"

            def get_full_url(self):
                return self.full_url

        return _Req(url)

    def test_拒绝重定向到内网(self):
        with self.assertRaises(ValueError):
            self.handler.redirect_request(
                self._req("http://a.com/x"), None, 302, "Found", {},
                "http://127.0.0.1/steal")

    def test_拒绝重定向到云元数据(self):
        with self.assertRaises(ValueError):
            self.handler.redirect_request(
                self._req("http://a.com/x"), None, 301, "Moved", {},
                "http://169.254.169.254/latest/meta-data/")

    def test_拒绝非http协议重定向(self):
        # 返回 None = 不跟随（urllib 停止跳转），不允许 file:// 等协议
        r = self.handler.redirect_request(
            self._req("http://a.com/x"), None, 302, "Found", {},
            "file:///etc/passwd")
        self.assertIsNone(r)

    def test_公网跳转放行(self):
        # 用 IP 字面量避免依赖沙箱 DNS（主机名解析失败会 fail-closed 误拒）
        r = self.handler.redirect_request(
            self._req("http://a.com/x"), None, 302, "Found", {},
            "http://8.8.8.8/y")
        self.assertIsNotNone(r)


class TestP21ResponseLimit(unittest.TestCase):
    """P2-1：_read_limited 响应体上限"""

    @classmethod
    def setUpClass(cls):
        from modules.connectors import _read_limited
        cls.read_limited = staticmethod(_read_limited)

    class _FakeResp:
        def __init__(self, size):
            self.left = size

        def read(self, n):
            if self.left <= 0:
                return b""
            take = min(n, self.left)
            self.left -= take
            return b"x" * take

    def test_超限中止(self):
        with self.assertRaises(ValueError):
            self.read_limited(self._FakeResp(6 * 1024 * 1024),
                              max_bytes=5 * 1024 * 1024)

    def test_正常读取(self):
        data = self.read_limited(self._FakeResp(2048),
                                 max_bytes=5 * 1024 * 1024)
        self.assertEqual(len(data), 2048)


class TestP22UnsignedReplay(unittest.TestCase):
    """P2-2：未签名链路无信封消息按内容哈希去重"""

    def _mk(self, name):
        import core.mindforge as mf
        from core.types import MemoryConfig

        tmp = _tmp()
        eng = mf.MindForge(config=MemoryConfig(
            db_path=os.path.join(tmp, name + ".db"),
            key_file=os.path.join(tmp, name + ".key"), encrypted=True))
        eng.init_with_password("test-pw-583")
        return eng

    def test_无信封消息不可重放(self):
        eng = self._mk("f1")
        fed = eng.federated
        fed.allow_unsigned_peers = True
        fed.register_peer("peer_x", "dummy-pubkey-placeholder")
        msg = {"text": "unsigned replay test", "type": "memory"}
        self.assertTrue(fed.receive_memory("peer_x", msg, signature=None))
        self.assertFalse(fed.receive_memory(
            "peer_x", dict(msg), signature=None))

    def test_不同内容仍可入队(self):
        eng = self._mk("f2")
        fed = eng.federated
        fed.allow_unsigned_peers = True
        fed.register_peer("peer_y", "dummy-pubkey-placeholder")
        self.assertTrue(fed.receive_memory(
            "peer_y", {"text": "m1", "type": "memory"}, signature=None))
        self.assertTrue(fed.receive_memory(
            "peer_y", {"text": "m2", "type": "memory"}, signature=None))


class TestP31StandardTotp(unittest.TestCase):
    """P3-1：标准 TOTP（RFC 6238）"""

    def _mk(self, name):
        import core.mindforge as mf
        from core.types import MemoryConfig

        tmp = _tmp()
        eng = mf.MindForge(config=MemoryConfig(
            db_path=os.path.join(tmp, name + ".db"),
            key_file=os.path.join(tmp, name + ".key"), encrypted=True))
        eng.init_with_password("test-pw-583")
        return eng

    def test_注册生成base32密钥(self):
        eng = self._mk("t1")
        secret = eng.privacy_engine.register_totp_secret("alice")
        self.assertTrue(secret)
        self.assertEqual(len(secret), 32)  # 20 字节 -> base32 32 字符

    def test_验证正确码与错误码(self):
        eng = self._mk("t2")
        p = eng.privacy_engine
        secret = p.register_totp_secret("bob")
        code = p._totp_value(secret, time.time())
        self.assertTrue(p.verify_totp_code("bob", code))
        self.assertFalse(p.verify_totp_code("bob", "000000"))

    def test_时间窗口plus1(self):
        eng = self._mk("t3")
        p = eng.privacy_engine
        secret = p.register_totp_secret("carol")
        next_code = p._totp_value(secret, time.time() + 30)
        self.assertTrue(p.verify_totp_code("carol", next_code))

    def test_密钥加密落库可恢复(self):
        import core.mindforge as mf
        from core.types import MemoryConfig

        tmp = _tmp()
        db = os.path.join(tmp, "t4.db")
        key = os.path.join(tmp, "t4.key")
        eng = mf.MindForge(config=MemoryConfig(
            db_path=db, key_file=key, encrypted=True))
        eng.init_with_password("test-pw-583")
        p = eng.privacy_engine
        secret = p.register_totp_secret("dave")
        code = p._totp_value(secret, time.time())
        # 新实例（同 key 文件）：从 SQLite 读取并解密
        eng2 = mf.MindForge(config=MemoryConfig(
            db_path=db, key_file=key, encrypted=True))
        eng2.init_with_password("test-pw-583")
        self.assertTrue(eng2.privacy_engine.verify_totp_code("dave", code))

    def test_非法base32密钥拒绝(self):
        eng = self._mk("t5")
        r = eng.privacy_engine.register_totp_secret("eve", secret_b32="!!!")
        self.assertIsNone(r)


class TestP32GdprBasename(unittest.TestCase):
    """P3-2：gdpr_report 不泄露绝对路径"""

    def test_db_path仅返回basename(self):
        import core.mindforge as mf
        from core.types import MemoryConfig

        tmp = _tmp()
        eng = mf.MindForge(config=MemoryConfig(
            db_path=os.path.join(tmp, "gdpr.db"), encrypted=False))
        report = eng.gdpr_report()
        self.assertEqual(report["db_path"], "gdpr.db")
        self.assertNotIn(os.sep, report["db_path"])
        self.assertNotIn("/", report["db_path"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
