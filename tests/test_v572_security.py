# -*- coding: utf-8 -*-
"""v5.7.2 回归测试：统一安全响应头 · Cache-Control no-store · HSTS 条件下发

覆盖 v5.7.2 安全加固的响应头行为：

  - 所有 JSON 响应默认携带 X-Content-Type-Options / X-Frame-Options /
    Referrer-Policy / Cache-Control: no-store（敏感记忆数据禁止缓存）；
  - 服务端 TLS 未启用时不发送 Strict-Transport-Security（纯 HTTP 本地部署
    不误伤明文调试）；
  - 服务端 TLS 启用（start_api_server 设置 _tls_enabled=True）时自动下发
    Strict-Transport-Security。

审计结论不设项（CSP 可选、X-XSS-Protection 已废弃）在 SECURITY.md 与
CHANGELOG 中记录，不在本文件断言。
"""

import types
import unittest

from api.server import MindForgeAPIHandler


class _Stub:
    """最小 HTTP 响应桩：记录 send_header 调用（键值对）"""

    def __init__(self):
        self.sent = []

    def send_response(self, code):
        self.code = code

    def send_header(self, k, v):
        self.sent.append((k, v))

    def end_headers(self):
        pass

    @property
    def wfile(self):
        return types.SimpleNamespace(write=lambda data: None)


class TestSecurityHeaders(unittest.TestCase):
    def setUp(self):
        # 类属性跨用例共享，先复位为默认（start_api_server 之外为 False）
        MindForgeAPIHandler._tls_enabled = False

    def _call(self, tls=False):
        stub = _Stub()
        # 真实请求中 self 是 MindForgeAPIHandler 实例（命中类属性 _tls_enabled）；
        # 测试桩为普通对象，等价地在实例上设置该开关。
        stub._tls_enabled = tls
        MindForgeAPIHandler._send_json(stub, {"ok": True})
        return dict(stub.sent)

    def test_required_headers_present_by_default(self):
        h = self._call(tls=False)
        for name in ("X-Content-Type-Options", "X-Frame-Options",
                     "Referrer-Policy", "Cache-Control"):
            self.assertIn(name, h)
        self.assertEqual(h["X-Content-Type-Options"], "nosniff")
        self.assertEqual(h["X-Frame-Options"], "DENY")
        self.assertEqual(h["Referrer-Policy"], "no-referrer")
        self.assertEqual(h["Cache-Control"], "no-store")

    def test_hsts_absent_on_plain_http(self):
        # 纯 HTTP 本地部署：不下发 HSTS，避免浏览器强记策略后拒绝明文调试
        h = self._call(tls=False)
        self.assertNotIn("Strict-Transport-Security", h)

    def test_hsts_present_when_tls_enabled(self):
        # 服务端 TLS（ssl_certfile）启用时自动下发
        h = self._call(tls=True)
        self.assertEqual(h["Strict-Transport-Security"], "max-age=31536000")

    def test_hsts_class_flag_default_false(self):
        # 默认类属性必须为 False（未显式启用 TLS 时不下发 HSTS）
        self.assertFalse(MindForgeAPIHandler._tls_enabled)


if __name__ == "__main__":
    unittest.main()
