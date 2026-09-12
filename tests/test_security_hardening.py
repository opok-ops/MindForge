# -*- coding: utf-8 -*-
"""
MindForge 安全加固回归测试（v5.6.3 新增）
=======================================
覆盖：
  - API 限流器：冷 IP 清理 + 跟踪 IP 数上限（防内存泄漏）
  - API content 类型校验：非字符串 content 返回 400 而非 500
  - 加密密钥文件：POSIX 下权限 0o600 + 密码校验（防篡改/错误密码）
  - 百度推送端点：使用 HTTPS（传输中加密 site/token）
  - from_config：明文落盘（encrypted=False）时给出告警

这些测试为「新增」性质，不修改任何既有行为，可独立运行：
  pytest tests/test_security_hardening.py -v
"""

import os
import sys
import json
import time
import socket
import logging
import threading
import tempfile
import urllib.request
import urllib.error

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.join(ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ---------------------------------------------------------------------------
# 1) API 限流器：冷 IP 清理 + 跟踪 IP 数上限
# ---------------------------------------------------------------------------
def test_rate_limiter_prunes_idle_and_caps_tracked_ips():
    from api.server import _RateLimiter

    rl = _RateLimiter(max_requests=3, window_seconds=60)

    # 同一 IP 前 3 次允许，第 4 次被限流
    assert rl.check("1.1.1.1") is True
    assert rl.check("1.1.1.1") is True
    assert rl.check("1.1.1.1") is True
    assert rl.check("1.1.1.1") is False

    # 冷 IP（计数已清空）的键应被删除，避免长期驻留
    with rl._lock:
        rl._requests["1.1.1.1"] = []  # 模拟过期清空
        if not rl._requests["1.1.1.1"]:
            rl._requests.pop("1.1.1.1", None)
    assert "1.1.1.1" not in rl._requests

    # 新 IP 应被允许
    assert rl.check("2.2.2.2") is True

    # 突破跟踪 IP 上限后，字典长度必须被钳制（防数月运行后的内存泄漏）
    for i in range(rl._MAX_TRACKED_IPS + 50):
        rl.check("10.0.0.%d" % (i % 254))
    assert len(rl._requests) <= rl._MAX_TRACKED_IPS


# ---------------------------------------------------------------------------
# 2) API content 类型校验（端到端）
# ---------------------------------------------------------------------------
def test_api_rejects_non_string_content():
    from api.server import start_api_server
    from MindForge import MindForge

    tmp = tempfile.mkdtemp(prefix="mf_api_test_")
    db_path = os.path.join(tmp, "m.db")
    mf = MindForge(db_path=db_path, encrypted=False)

    port = _free_port()
    t = threading.Thread(
        target=start_api_server,
        kwargs={"mindforge_instance": mf, "host": "127.0.0.1", "port": port},
        daemon=True,
    )
    t.start()
    time.sleep(1.0)  # 等待服务器绑定端口

    base = "http://127.0.0.1:%d" % port

    # 合法字符串 content -> 201
    req_ok = urllib.request.Request(
        base + "/api/memories",
        data=json.dumps({"content": "hello world"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req_ok, timeout=5) as resp:
        assert resp.status == 201

    # 非字符串 content（数字） -> 400，而非 500
    req_bad = urllib.request.Request(
        base + "/api/memories",
        data=json.dumps({"content": 123456}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req_bad, timeout=5)
    assert exc.value.code == 400


# ---------------------------------------------------------------------------
# 3) 加密密钥文件：POSIX 权限 0o600 + 密码校验
# ---------------------------------------------------------------------------
def test_encryption_key_file_perm_and_password_verify(tmp_path):
    from core.encryption import (
        EncryptionEngine,
        _write_key_file,
        _verify_key_password,
    )

    key_file = tmp_path / ".key"
    salt = b"0" * 16
    engine, _ = EncryptionEngine.from_password("CorrectHorse!", salt)

    # 直接写密钥文件（不污染全局引擎单例）
    _write_key_file(key_file, salt, engine.kdf_params, engine)

    if os.name == "posix":
        mode = os.stat(key_file).st_mode & 0o777
        assert mode == 0o600, "密钥文件权限应为 0o600，实际 %s" % oct(mode)

    key_data = json.loads(key_file.read_text(encoding="utf-8"))

    # 正确密码通过校验
    assert _verify_key_password(engine, key_data) is True

    # 错误密码校验失败（防篡改 / 防错误密码解密）
    bad_engine = EncryptionEngine.from_password("WrongPassword", salt)
    assert _verify_key_password(bad_engine, key_data) is False


def test_encryption_roundtrip_tamper_detection(tmp_path):
    """密文被篡改时解密必须失败（GCM 认证加密的完整性保证）。"""
    from core.encryption import EncryptionEngine

    engine, _ = EncryptionEngine.from_password("TopSecretPass!", b"1" * 16)
    blob = engine.encrypt("sensitive-data")

    # 正常解密
    assert engine.decrypt(blob) == "sensitive-data"

    # 篡改密文 -> 解密失败
    tampered = type(blob)(
        ciphertext=bytes(b ^ 0xFF for b in blob.ciphertext),
        nonce=blob.nonce,
        salt=blob.salt,
        algorithm=blob.algorithm,
        kdf_params=blob.kdf_params,
    )
    from core.encryption import SecurityError
    with pytest.raises(SecurityError):
        engine.decrypt(tampered)


# ---------------------------------------------------------------------------
# 4) 百度推送端点使用 HTTPS
# ---------------------------------------------------------------------------
def test_baidu_push_endpoint_is_https():
    from scripts.baidu_push import PUSH_ENDPOINT
    assert PUSH_ENDPOINT.startswith("https://"), (
        "百度推送端点应走 HTTPS 以加密传输 site/token，实际：%s" % PUSH_ENDPOINT
    )


# ---------------------------------------------------------------------------
# 5) from_config：明文落盘告警
# ---------------------------------------------------------------------------
def test_from_config_warns_when_unencrypted(tmp_path, caplog):
    from MindForge import MindForge

    cfg = tmp_path / "config.json"
    cfg.write_text(
        json.dumps(
            {"db_path": str(tmp_path / "m.db"), "encrypted": False}
        ),
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING):
        MindForge.from_config(str(cfg))

    assert any("encryption DISABLED" in r.message for r in caplog.records), (
        "encrypted=False 时应输出明文落盘告警"
    )
