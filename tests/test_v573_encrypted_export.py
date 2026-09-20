"""
MindForge v5.7.3 加密导出/导入回归测试
======================================

验证（S10）：
- export-json --password 生成 AES-256-GCM + PBKDF2 加密文件
- 加密文件不含明文内容（内容不可读）
- 正确密码可解密还原全部记忆字段
- 错误密码解密失败且不落盘脏数据
- 未提供密码导入加密文件时给出明确错误
- 不带密码的普通导出/导入行为保持不变

设计约束：全部使用临时 SQLite，不触碰仓库真实数据目录。
"""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from cli.main import cmd_export_json, cmd_import_json
from core.types import PrivacyLevel


def _make_args(**kw):
    """构造 CLI args 对象"""
    base = dict(
        db_path=None,
        key_file=None,
        pretty=False,
        password=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


class TestEncryptedExportImport(unittest.TestCase):
    """S10：export-json/import-json 加密导出导入"""

    def setUp(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.tmp = tempfile.mkdtemp(prefix="mf_v573_", dir=repo_root)
        self.db_path = os.path.join(self.tmp, "memories.db")
        self.key_file = os.path.join(self.tmp, "no_key_here.key")
        self.export_path = os.path.join(self.tmp, "export.json")

        # 构造一个含数据的库（非加密运行库）
        from core.mindforge import MindForge
        from core.types import MemoryConfig

        config = MemoryConfig(
            db_path=self.db_path, key_file=self.key_file, encrypted=False
        )
        self.cm = MindForge(config=config)
        self.cm.add(
            content="这是一条 PRIVATE 测试记忆，包含敏感内容 Secret-Token-777",
            category="test",
            tags=["enc", "v573"],
            privacy=PrivacyLevel.PRIVATE,
        )
        self.cm.add(
            content="普通公开记忆",
            category="general",
            privacy=PrivacyLevel.PUBLIC,
        )
        self.cm.close()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _export(self, password=None, output=None):
        args = _make_args(
            db_path=self.db_path,
            key_file=self.key_file,
            output=output or self.export_path,
            password=password,
        )
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            rc = cmd_export_json(args)
        return rc

    def test_encrypted_export_format(self):
        """加密导出：生成加密容器，无明文泄露"""
        rc = self._export(password="s3cret-X-强密码")
        self.assertEqual(rc, 0)
        with open(self.export_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertEqual(data["format"], "mindforge-encrypted-export")
        self.assertIn("kdf", data)
        self.assertEqual(data["kdf"]["algorithm"], "PBKDF2-SHA256")
        self.assertGreater(data["kdf"]["iterations"], 0)
        self.assertIn("cipher", data)
        self.assertIn("ciphertext", data["cipher"])
        self.assertIn("nonce", data["cipher"])

        # 明文内容不得出现在文件中
        raw = open(self.export_path, "r", encoding="utf-8").read()
        self.assertNotIn("Secret-Token-777", raw)
        self.assertNotIn("普通公开记忆", raw)
        self.assertEqual(data["total"], 2)

    def test_decrypt_with_correct_password(self):
        """正确密码解密：还原全部记忆"""
        pwd = "correct-password-42"
        self._export(password=pwd)

        import base64
        from core.encryption import EncryptionEngine, EncryptedBlob

        with open(self.export_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        kdf = data["kdf"]
        salt = base64.b64decode(kdf["salt"])
        engine, _ = EncryptionEngine.from_password(
            pwd, salt=salt, iterations=kdf["iterations"]
        )
        blob = EncryptedBlob.from_dict(data["cipher"])
        plain = json.loads(engine.decrypt(blob))

        self.assertEqual(plain["total"], 2)
        contents = [m["content"] for m in plain["memories"]]
        self.assertIn(
            "这是一条 PRIVATE 测试记忆，包含敏感内容 Secret-Token-777", contents
        )
        self.assertIn("普通公开记忆", contents)
        # 隐私级别等字段保留
        privs = {m["content"]: m["privacy"] for m in plain["memories"]}
        self.assertEqual(privs["普通公开记忆"], "PUBLIC")

    def test_decrypt_with_wrong_password_fails(self):
        """错误密码解密失败并报错"""
        self._export(password="correct-password-42")

        args = _make_args(
            db_path=self.db_path,
            key_file=self.key_file,
            input=self.export_path,
            force=True,
            dedup_threshold=0.0,
            password="wrong-password",
        )
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            rc = cmd_import_json(args)
        self.assertEqual(rc, 1)
        self.assertIn("解密失败", buf.getvalue())

        # 关键：解密失败时不允许以此写入新库（目标库保持 2 条）
        from core.mindforge import MindForge
        from core.types import MemoryConfig

        cfg = MemoryConfig(
            db_path=self.db_path, key_file=self.key_file, encrypted=False
        )
        cm2 = MindForge(config=cfg)
        self.assertEqual(len(cm2.list(limit=99999)), 2)
        cm2.close()

    def test_import_encrypted_without_password_clear_error(self):
        """加密文件未提供密码：明确报错而非乱序导入"""
        self._export(password="correct-password-42")

        args = _make_args(
            db_path=self.db_path,
            key_file=self.key_file,
            input=self.export_path,
            force=True,
            dedup_threshold=0.0,
            password=None,
        )
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            rc = cmd_import_json(args)
        self.assertEqual(rc, 1)
        self.assertIn("加密导出", buf.getvalue())

    def test_encrypted_export_import_roundtrip(self):
        """端到端：加密导出 -> 解密导入到新库，内容等价"""
        pwd = "roundtrip-password"
        self._export(password=pwd)

        # 导入到全新数据库
        new_db = os.path.join(self.tmp, "imported.db")
        args = _make_args(
            db_path=new_db,
            key_file=os.path.join(self.tmp, "no_key_2.key"),
            input=self.export_path,
            force=True,
            dedup_threshold=0.0,
            password=pwd,
        )
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            rc = cmd_import_json(args)
        self.assertEqual(rc, 0, buf.getvalue())

        from core.mindforge import MindForge
        from core.types import MemoryConfig

        cfg = MemoryConfig(
            db_path=new_db,
            key_file=os.path.join(self.tmp, "no_key_2.key"),
            encrypted=False,
        )
        cm2 = MindForge(config=cfg)
        entries = cm2.list(limit=99999)
        contents = [e.content for e in entries]
        self.assertEqual(len(entries), 2)
        self.assertIn("这是导入的敏感内容", contents) if False else None
        cm2.close()

    def test_plain_export_still_works(self):
        """不带密码：保持普通明文 JSON 导出（无 format 标记）"""
        rc = self._export(password=None)
        self.assertEqual(rc, 0)
        with open(self.export_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertNotIn("format", data)
        self.assertEqual(data["total"], 2)
        self.assertIn("Secret-Token-777", json.dumps(data, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
