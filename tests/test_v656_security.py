# -*- coding: utf-8 -*-
"""v6.5.6 安全修复包行为契约（双判官评审驱动）

来源：Qwen4-Max（豆包）与 Kimi K3.1 独立评审重叠指出的缺口：
1. GDPR 级联残留（knowledge_graph 实体表等 7 张表漏删）
2. legacy 密钥文件验证恒放行（rekey 可用错误旧密码改写密钥）
3. KDFParams.from_dict 无成本下限（降级攻击面）
4. 记忆压缩与静态加密默认互斥（产品级冲突）
"""
import os
import tempfile
import unittest

import mindforge
from mindforge.core.mindforge import MindForge


class TestGDPRCascadeComplete(unittest.TestCase):
    """被遗忘权：全部含个人数据的表都必须清空"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_v656g_")
        self.mf = MindForge(db_path=os.path.join(self.tmp, "m.db"),
                            encrypted=False)

    def tearDown(self):
        self.mf.close()

    def test_knowledge_graph_erased(self):
        """v6.5.6 核心修复：knowledge_graph 实体表级联清空"""
        self.mf.add("Alice 在巴黎学习绘画", category="person")
        conn = self.mf.storage._get_conn()
        # 模拟知识图谱已抽取实体（外部管线写入）
        conn.execute(
            "INSERT INTO knowledge_graph(entity, entity_type, description) "
            "VALUES ('Alice', 'person', '在巴黎学习绘画')")
        conn.commit()
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM knowledge_graph").fetchone()[0], 1)
        result = self.mf.gdpr_erase_all(backup=False)
        # 返回 {deleted: {table: n}, backup: path}
        self.assertIn("deleted", result)
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM knowledge_graph").fetchone()[0], 0,
            "knowledge_graph 实体残留：被遗忘权不完整")

    def test_snapshots_templates_drama_erased(self):
        """v6.5.6：快照/模板/剧作创作内容表级联清空"""
        conn = self.mf.storage._get_conn()
        # memory_snapshots 为懒建表（snapshot 功能首次使用时创建），
        # 模拟已使用状态
        conn.execute(
            "CREATE TABLE IF NOT EXISTS memory_snapshots ("
            "id TEXT PRIMARY KEY, memory_id TEXT, label TEXT)")
        # 按各表真实列名插入
        conn.execute(
            "INSERT INTO memory_snapshots(id, memory_id, label) "
            "VALUES ('s1', 'm1', 'x')")
        conn.execute(
            "INSERT INTO memory_templates(id, name, content_template) "
            "VALUES ('t1', 'n', 'c')")
        conn.execute(
            "INSERT INTO drama_series(id, title) VALUES ('d1', 'T')")
        conn.execute(
            "INSERT INTO drama_characters(id, drama_id, name) "
            "VALUES ('c1', 'd1', 'N')")
        conn.commit()
        self.mf.gdpr_erase_all(backup=False)
        for table in ("memory_snapshots", "memory_templates",
                      "drama_series", "drama_scenes",
                      "drama_characters", "drama_lines",
                      "knowledge_graph"):
            try:
                n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except Exception:
                continue  # 懒建表未创建——无残留可能
            self.assertEqual(n, 0, f"{table} 残留 {n} 行：被遗忘权不完整")


class TestLegacyKeyVerification(unittest.TestCase):
    """legacy 密钥文件：rekey 用错误旧密码必须被拒绝"""

    def _make_legacy_store(self, path, key_path, password):
        """构造 v5.5 时代 legacy 密钥文件（无 verify_blob）+ 加密数据

        正确构造：先按现行格式初始化（数据与密钥文件 salt 匹配），
        然后读回 key 文件、只删 verify_blob 写回——salt/KDF 参数
        与库内数据保持一致，仅缺验证令牌（legacy 特征）。
        """
        import json
        mf = MindForge(db_path=path, encrypted=True, key_file=key_path)
        mf.init_with_password(password)
        mf.add("加密数据内容", category="secret")
        mf.close()
        with open(key_path, "r", encoding="utf-8") as f:
            key_data = json.load(f)
        key_data.pop("verify_blob", None)  # legacy 特征：无验证令牌
        with open(key_path, "w", encoding="utf-8") as f:
            json.dump(key_data, f)

    def test_rekey_wrong_password_rejected(self):
        """v6.5.6 核心：库里有密文时，错误旧密码 rekey 被试探解密拒绝"""
        tmp = tempfile.mkdtemp(prefix="mf_v656k_")
        db = os.path.join(tmp, "e.db")
        key = os.path.join(tmp, "e.key")
        self._make_legacy_store(db, key, "correct-pass")
        mf = MindForge(db_path=db, encrypted=True, key_file=key)
        mf.init_with_password("correct-pass")
        from mindforge.core.encryption import SecurityError
        # 错误旧密码：旧实现恒放行（legacy 无 verify_blob），v6.5.6 拒绝
        with self.assertRaises(SecurityError):
            mf.rekey(old_password="WRONG-pass", new_password="new-pass")
        # 正确旧密码：rekey 成功
        result = mf.rekey(old_password="correct-pass", new_password="new-pass")
        # 返回 {rekeyed_count, old_iterations, new_iterations, ...}
        self.assertGreaterEqual(result.get("rekeyed_count", 0), 1)
        mf.close()


class TestKDFParamsFloor(unittest.TestCase):
    """KDF 成本下限（防降级攻击）"""

    def test_pbkdf2_below_floor_rejected(self):
        from mindforge.core.encryption import KDFParams, SecurityError
        with self.assertRaises(SecurityError):
            KDFParams.from_dict({"algorithm": "PBKDF2-SHA256",
                                 "iterations": 1000})

    def test_scrypt_low_n_rejected(self):
        from mindforge.core.encryption import KDFParams, SecurityError
        with self.assertRaises(SecurityError):
            KDFParams.from_dict({"algorithm": "scrypt", "n": 1024,
                                 "r": 8, "p": 1})

    def test_valid_params_pass(self):
        from mindforge.core.encryption import KDFParams
        # 全部历史真实参数必须能读（60k legacy / 600k / 2^16 scrypt）
        KDFParams.from_dict({"iterations": 60000})
        KDFParams.from_dict({"iterations": 600000})
        KDFParams.from_dict({"algorithm": "scrypt", "n": 65536,
                             "r": 8, "p": 1})
        KDFParams.from_dict({})


class TestCompressEncryptedCompat(unittest.TestCase):
    """压缩/加密兼容（解密水合）——契约见 test_v635_integration
    此处补 decompress 与 list_compressions 在加密库的可用性"""

    def test_list_and_decompress_on_encrypted(self):
        tmp = tempfile.mkdtemp(prefix="mf_v656c_")
        mf = MindForge(db_path=os.path.join(tmp, "c.db"),
                       encrypted=True, key_file=os.path.join(tmp, "c.key"))
        mf.init_with_password("pw")
        for t in ("深度学习需要大量数据", "深度学习需要海量数据", "深度学习依赖大量数据"):
            mf.add(t, category="ml")
        res = mf.compress_memories(threshold=0.3, dry_run=False)
        self.assertGreaterEqual(res.get("compressed_count", 0), 1)
        # list_compressions 不再抛 SecurityError
        comps = mf.list_compressions()
        self.assertTrue(comps)
        # decompress 恢复
        out = mf.decompress_cluster(comps[0]["cluster_id"])
        self.assertTrue(out.get("success", True))
        hits = mf.search("深度学习", max_results=10)
        self.assertTrue(hits.chunks)
        mf.close()


if __name__ == "__main__":
    unittest.main()
