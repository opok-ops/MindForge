#!/usr/bin/env python3
"""
MindForge v5.6.0 测试套件 — 模块测试补盲

覆盖内容：
1. SkillExtractor - SkillTemplate 渲染、to_dict、多聚类、技能槽位
2. SharedConflictResolver - list_conflicts, dismiss, stats, detect_incoming 各场景
3. FederatedACLManager - 边界场景补充（通配符、多操作、批量 filter、rule 管理）
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

# 确保项目根目录在 path 中
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


class TestSkillExtractorMore(unittest.TestCase):
    """SkillExtractor 补充测试"""

    def test_skill_template_render_with_slots(self):
        """SkillTemplate.render() 正确填充 slot 变量"""
        from modules.skill_extractor import SkillTemplate, SkillSlot

        template = SkillTemplate(
            name="docker_deploy",
            description="部署 Docker 容器的标准流程",
            slots=[
                SkillSlot(name="image_name", examples=["nginx", "redis"], required=True),
                SkillSlot(name="container_name", examples=["myapp"], required=False, default="app"),
            ],
            steps=[
                {"action": "docker pull {{image_name}}", "note": "拉取镜像"},
                {"action": "docker run -d --name {{container_name}} {{image_name}}", "note": "启动容器"},
            ],
            triggers=["docker 部署", "容器启动"],
            examples=["部署 nginx 容器", "启动 redis 服务"],
        )

        rendered = template.render(image_name="nginx", container_name="web")
        self.assertIn("docker pull nginx", rendered)
        self.assertIn("docker run -d --name web nginx", rendered)
        self.assertIn("# 技能：docker_deploy", rendered)
        self.assertIn("## 步骤", rendered)

    def test_skill_template_render_default_values(self):
        """SkillTemplate.render() 未提供的 slot 使用默认值或占位符"""
        from modules.skill_extractor import SkillTemplate, SkillSlot

        template = SkillTemplate(
            name="test",
            description="test desc",
            slots=[
                SkillSlot(name="has_default", default="fallback"),
                SkillSlot(name="no_default"),
            ],
            steps=[{"action": "do {{has_default}} and {{no_default}}"}],
        )

        rendered = template.render()
        self.assertIn("fallback", rendered)
        self.assertIn("{{no_default}}", rendered)

    def test_skill_template_to_dict(self):
        """SkillTemplate.to_dict() 完整序列化"""
        from modules.skill_extractor import SkillTemplate, SkillSlot

        template = SkillTemplate(
            name="test_skill",
            description="A test skill",
            slots=[SkillSlot(name="s1", examples=["ex1"], required=True, default="d")],
            steps=[{"action": "step1"}, {"action": "step2"}],
            triggers=["t1", "t2"],
            examples=["ex_a", "ex_b"],
            tags=["tag1"],
            source_memory_ids=["mem1", "mem2"],
            confidence=0.85,
            cluster_size=5,
        )

        d = template.to_dict()
        self.assertEqual(d["name"], "test_skill")
        self.assertEqual(d["description"], "A test skill")
        self.assertEqual(len(d["slots"]), 1)
        self.assertEqual(d["slots"][0]["name"], "s1")
        self.assertEqual(len(d["steps"]), 2)
        self.assertEqual(len(d["triggers"]), 2)
        self.assertEqual(len(d["examples"]), 2)
        self.assertEqual(len(d["tags"]), 1)
        self.assertEqual(len(d["source_memory_ids"]), 2)
        self.assertEqual(d["confidence"], 0.85)
        self.assertEqual(d["cluster_size"], 5)

    def test_skill_slot_to_dict(self):
        """SkillSlot.to_dict() 序列化"""
        from modules.skill_extractor import SkillSlot

        slot = SkillSlot(name="my_slot", examples=["a", "b"], required=True, default="x")
        d = slot.to_dict()
        self.assertEqual(d["name"], "my_slot")
        self.assertEqual(len(d["examples"]), 2)
        self.assertTrue(d["required"])
        self.assertEqual(d["default"], "x")

    def test_extract_single_cluster_skill(self):
        """相关记忆聚类后提取技能"""
        from modules.skill_extractor import SkillExtractor

        extractor = SkillExtractor(min_cluster_size=2)
        memories = [
            {"content": "Docker 部署第一步：docker pull 拉取镜像",
             "tags": ["docker", "部署"], "category": "devops"},
            {"content": "Docker 部署第二步：docker run 启动容器",
             "tags": ["docker", "部署"], "category": "devops"},
            {"content": "Docker 部署第三步：docker logs 查看日志",
             "tags": ["docker", "部署"], "category": "devops"},
        ]

        skills = extractor.extract(memories)
        self.assertIsInstance(skills, list)
        # 至少提取到一个技能
        self.assertGreater(len(skills), 0)
        for s in skills:
            self.assertTrue(s.name)
            self.assertTrue(s.description)
            self.assertGreater(s.cluster_size, 0)

    def test_extract_returns_skill_template_objects(self):
        """extract() 返回 SkillTemplate 对象列表"""
        from modules.skill_extractor import SkillExtractor, SkillTemplate

        extractor = SkillExtractor()
        memories = [
            {"content": "Python 中用 os.listdir 列出目录文件",
             "tags": ["python", "文件"], "category": "python"},
            {"content": "Python pathlib 模块处理路径更优雅",
             "tags": ["python", "路径"], "category": "python"},
        ]

        skills = extractor.extract(memories)
        for s in skills:
            self.assertIsInstance(s, SkillTemplate)

    def test_extract_below_min_cluster_size(self):
        """少于 min_cluster_size 的记忆不提取技能"""
        from modules.skill_extractor import SkillExtractor

        extractor = SkillExtractor(min_cluster_size=10)
        memories = [
            {"content": f"记忆 {i}", "tags": [f"tag{i}"], "category": "test"}
            for i in range(3)
        ]

        skills = extractor.extract(memories)
        # 聚类大小不足，不应提取到技能
        self.assertEqual(len(skills), 0)

    def test_ngrams_helper(self):
        """_ngrams 静态方法正确生成 n-gram 集合"""
        from modules.skill_extractor import SkillExtractor

        result = SkillExtractor._ngrams("hello", n=2)
        self.assertIsInstance(result, set)
        self.assertIn("he", result)
        self.assertIn("el", result)
        self.assertIn("ll", result)
        self.assertIn("lo", result)

        # 短文本处理
        short = SkillExtractor._ngrams("a", n=2)
        self.assertEqual(short, {"a"})

        # 空文本
        empty = SkillExtractor._ngrams("", n=2)
        self.assertEqual(empty, set())

    def test_tags_of_helper(self):
        """_tags_of 静态方法正确解析各种 tag 格式"""
        from modules.skill_extractor import SkillExtractor

        # 列表格式
        mem1 = {"tags": ["python", "docker"]}
        self.assertEqual(SkillExtractor._tags_of(mem1), ["python", "docker"])

        # 字符串格式（逗号分隔）
        mem2 = {"tags": "python, docker, devops"}
        self.assertEqual(SkillExtractor._tags_of(mem2), ["python", "docker", "devops"])

        # None / 空
        mem3 = {"tags": None}
        self.assertEqual(SkillExtractor._tags_of(mem3), [])

        mem4 = {}
        self.assertEqual(SkillExtractor._tags_of(mem4), [])


class TestSharedConflictResolverMore(unittest.TestCase):
    """SharedConflictResolver 补充测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make(self):
        from core.storage import StorageEngine
        from modules.share_conflict import SharedConflictResolver
        storage = StorageEngine(db_path=self.db_path, encrypted=False)
        return storage, SharedConflictResolver(storage)

    def test_list_conflicts_empty(self):
        """空数据库 list_conflicts 返回空列表"""
        _, resolver = self._make()
        conflicts = resolver.list_conflicts()
        self.assertEqual(len(conflicts), 0)

    def test_stats_empty(self):
        """空数据库 stats 返回零值"""
        _, resolver = self._make()
        stats = resolver.stats()
        self.assertEqual(stats["total_conflicts"], 0)
        self.assertEqual(stats["open"], 0)
        self.assertEqual(stats["resolved"], 0)
        self.assertEqual(stats["dismissed"], 0)
        self.assertIn("by_type", stats)
        self.assertIn("by_resolution", stats)

    def test_dismiss_nonexistent_returns_error(self):
        """dismiss 不存在的冲突返回错误字典"""
        _, resolver = self._make()
        result = resolver.dismiss("nonexistent_id", "test_actor")
        self.assertFalse(result["success"])
        self.assertIn("error", result)

    def test_detect_incoming_conflict_detected(self):
        """detect_incoming 检测到内容冲突"""
        storage, resolver = self._make()
        entry = storage.add_memory("原始记忆内容", category="test")

        incoming = {
            "memory_id": entry.id,
            "content": "不同的记忆内容",
            "category": "test",
            "tags": ["new"],
            "from_peer": "peer_123",
        }

        result = resolver.detect_incoming(incoming)
        self.assertTrue(result["conflict"])
        self.assertIn("conflict_id", result)
        self.assertIn("conflict_type", result)

    def test_detect_incoming_no_conflict_same_content(self):
        """相同内容的入站记忆不产生冲突（noop）"""
        storage, resolver = self._make()
        entry = storage.add_memory("相同内容", category="test")

        incoming = {
            "memory_id": entry.id,
            "content": "相同内容",
            "category": "test",
            "tags": [],
            "from_peer": "peer_456",
        }

        result = resolver.detect_incoming(incoming)
        self.assertFalse(result["conflict"])
        self.assertEqual(result["action"], "noop")

    def test_detect_incoming_new_memory(self):
        """本地不存在的记忆 ID 返回 new 动作"""
        _, resolver = self._make()

        incoming = {
            "memory_id": "non-existent-id-12345",
            "content": "全新记忆",
            "category": "test",
            "tags": [],
            "from_peer": "peer_new",
        }

        result = resolver.detect_incoming(incoming)
        self.assertFalse(result["conflict"])
        self.assertEqual(result["action"], "new")

    def test_list_conflicts_filter_by_status(self):
        """list_conflicts 按状态筛选"""
        storage, resolver = self._make()
        entry = storage.add_memory("测试记忆", category="test")

        # 创建一个冲突
        incoming = {
            "memory_id": entry.id,
            "content": "修改后的内容",
            "category": "test",
            "tags": ["modified"],
            "from_peer": "peer_test",
        }
        result = resolver.detect_incoming(incoming)
        conflict_id = result["conflict_id"]

        # open 状态应该有 1 个
        open_list = resolver.list_conflicts(status="open")
        self.assertEqual(len(open_list), 1)

        # resolved 状态应该 0 个
        resolved = resolver.list_conflicts(status="resolved")
        self.assertEqual(len(resolved), 0)

        # 解决后变成 resolved
        resolver.resolve(conflict_id, strategy="lww", actor="test_user")
        resolved = resolver.list_conflicts(status="resolved")
        self.assertEqual(len(resolved), 1)

    def test_stats_after_create_and_resolve(self):
        """stats() 正确反映创建和解决后的数量"""
        storage, resolver = self._make()
        entry = storage.add_memory("统计测试", category="test")

        # 创建两个冲突
        for i in range(2):
            incoming = {
                "memory_id": entry.id,
                "content": f"版本 {i}",
                "category": "test",
                "tags": [f"v{i}"],
                "from_peer": f"peer_{i}",
            }
            resolver.detect_incoming(incoming)

        stats = resolver.stats()
        self.assertEqual(stats["total_conflicts"], 2)
        self.assertEqual(stats["open"], 2)
        self.assertEqual(stats["resolved"], 0)
        self.assertEqual(stats["dismissed"], 0)

    def test_dismiss_conflict(self):
        """dismiss 正确标记冲突为 dismissed"""
        storage, resolver = self._make()
        entry = storage.add_memory("忽略测试", category="test")

        incoming = {
            "memory_id": entry.id,
            "content": "被忽略的修改",
            "category": "test",
            "tags": [],
            "from_peer": "peer_bad",
        }
        result = resolver.detect_incoming(incoming)
        conflict_id = result["conflict_id"]

        dismissed = resolver.dismiss(conflict_id, actor="user_a")
        self.assertTrue(dismissed["success"])
        self.assertEqual(dismissed["status"], "dismissed")

        stats = resolver.stats()
        self.assertEqual(stats["dismissed"], 1)
        self.assertEqual(stats["open"], 0)

    def test_dismiss_already_resolved_conflict_fails(self):
        """dismiss 已解决的冲突返回错误"""
        storage, resolver = self._make()
        entry = storage.add_memory("已解决测试", category="test")

        incoming = {
            "memory_id": entry.id,
            "content": "修改",
            "category": "test",
            "tags": [],
            "from_peer": "peer_x",
        }
        result = resolver.detect_incoming(incoming)
        conflict_id = result["conflict_id"]

        # 先解决
        resolver.resolve(conflict_id, strategy="lww", actor="user")

        # 再 dismiss 应该失败
        dismiss_result = resolver.dismiss(conflict_id, actor="user")
        self.assertFalse(dismiss_result["success"])
        self.assertIn("已处于", dismiss_result["error"])

    def test_cleanup_branches_dry_run(self):
        """cleanup_branches dry_run 模式不实际删除"""
        _, resolver = self._make()

        result = resolver.cleanup_branches(days_old=30, dry_run=True, actor="test")
        self.assertIn("total_found", result)
        self.assertIn("cleaned", result)
        self.assertEqual(result["cleaned"], 0)
        self.assertTrue(result["dry_run"])


class TestFederatedACLMore(unittest.TestCase):
    """FederatedACLManager 补充测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make(self):
        from core.storage import StorageEngine
        from modules.federated_acl import FederatedACLManager
        storage = StorageEngine(db_path=self.db_path, encrypted=False)
        return storage, FederatedACLManager(storage)

    def test_acl_stats_empty(self):
        """空数据库 acl_stats 返回零值"""
        _, acl = self._make()
        stats = acl.acl_stats()
        self.assertIn("total_rules", stats)
        self.assertEqual(stats["total_rules"], 0)
        self.assertIn("by_effect", stats)
        self.assertIn("by_resource_type", stats)

    def test_add_rule_success_returns_rule_id(self):
        """add_rule 成功返回包含 rule_id 的字典"""
        _, acl = self._make()
        result = acl.add_rule(
            principal="peer:alice",
            resource="category:work",
            operations="read",
            effect="allow",
            priority=50,
            created_by="admin",
        )
        self.assertTrue(result["success"])
        self.assertIn("rule_id", result)
        self.assertTrue(result["rule_id"])

    def test_add_rule_empty_principal_fails(self):
        """add_rule 空 principal 失败"""
        _, acl = self._make()
        result = acl.add_rule(
            principal="",
            resource="category:test",
            operations="read",
            effect="allow",
        )
        self.assertFalse(result["success"])
        self.assertIn("error", result)

    def test_add_rule_invalid_effect_fails(self):
        """add_rule 无效 effect 失败"""
        _, acl = self._make()
        result = acl.add_rule(
            principal="peer:x",
            resource="category:test",
            operations="read",
            effect="invalid_effect",
        )
        self.assertFalse(result["success"])

    def test_wildcard_resource_access(self):
        """通配符资源规则（all）匹配所有记忆"""
        storage, acl = self._make()
        entry = storage.add_memory("secret stuff", category="secret")

        acl.add_rule(
            principal="peer:bob",
            resource="all",
            operations="read",
            effect="allow",
            priority=10,
            created_by="admin",
        )

        result = acl.check_access("peer:bob", entry.id, "read")
        self.assertTrue(result["allowed"])

    def test_multiple_operations_list(self):
        """多操作权限（read,write）以列表形式传入"""
        storage, acl = self._make()
        entry = storage.add_memory("test", category="test")

        acl.add_rule(
            principal="peer:charlie",
            resource=f"memory:{entry.id}",
            operations=["read", "write"],
            effect="allow",
            priority=50,
            created_by="admin",
        )

        read_result = acl.check_access("peer:charlie", entry.id, "read")
        self.assertTrue(read_result["allowed"])

        write_result = acl.check_access("peer:charlie", entry.id, "write")
        self.assertTrue(write_result["allowed"])

    def test_check_access_no_rules_default_deny(self):
        """无规则时默认拒绝"""
        storage, acl = self._make()
        entry = storage.add_memory("default deny test", category="test")

        result = acl.check_access("peer:stranger", entry.id, "read")
        self.assertFalse(result["allowed"])
        self.assertIn("default-deny", result.get("reason", ""))

    def test_remove_nonexistent_rule_returns_error(self):
        """remove_rule 不存在的规则返回错误字典"""
        _, acl = self._make()
        result = acl.remove_rule("nonexistent_rule_id", actor="test")
        self.assertFalse(result["success"])
        self.assertIn("error", result)

    def test_remove_existing_rule_succeeds(self):
        """remove_rule 存在的规则成功删除"""
        _, acl = self._make()
        add_result = acl.add_rule(
            principal="peer:todelete",
            resource="category:temp",
            operations="read",
            effect="allow",
            priority=50,
        )
        rule_id = add_result["rule_id"]

        # 确认存在
        rules_before = acl.list_rules(principal="peer:todelete")
        self.assertEqual(len(rules_before), 1)

        # 删除
        remove_result = acl.remove_rule(rule_id, actor="admin")
        self.assertTrue(remove_result["success"])

        # 确认已删除
        rules_after = acl.list_rules(principal="peer:todelete")
        self.assertEqual(len(rules_after), 0)

    def test_list_rules_by_principal(self):
        """list_rules 按 principal 筛选（也匹配通配符 *）"""
        _, acl = self._make()

        acl.add_rule("peer:alice", "category:a", "read", "allow", 50)
        acl.add_rule("peer:bob", "category:b", "read", "allow", 50)
        acl.add_rule("peer:alice", "category:c", "read", "deny", 50)
        acl.add_rule("*", "category:public", "read", "allow", 10)

        # alice 的规则：2 个专属 + 1 个通配符
        alice_rules = acl.list_rules(principal="peer:alice")
        self.assertEqual(len(alice_rules), 3)

        # bob 的规则：1 个专属 + 1 个通配符
        bob_rules = acl.list_rules(principal="peer:bob")
        self.assertEqual(len(bob_rules), 2)

    def test_list_rules_by_effect(self):
        """list_rules 按 effect 筛选"""
        _, acl = self._make()

        acl.add_rule("peer:a", "category:x", "read", "allow", 50)
        acl.add_rule("peer:b", "category:y", "read", "deny", 50)
        acl.add_rule("peer:c", "category:z", "read", "allow", 50)

        allow_rules = acl.list_rules(effect="allow")
        self.assertEqual(len(allow_rules), 2)

        deny_rules = acl.list_rules(effect="deny")
        self.assertEqual(len(deny_rules), 1)

    def test_filter_peers_mixed(self):
        """filter_peers 正确区分允许和拒绝的 peer"""
        storage, acl = self._make()
        entry = storage.add_memory("test memory", category="test")

        # 只允许 alice
        acl.add_rule("peer:alice", f"memory:{entry.id}", "read", "allow", 50)

        result = acl.filter_peers(
            entry.id,
            ["peer:alice", "peer:bob", "peer:charlie"],
            "read",
        )
        self.assertIn("allowed", result)
        self.assertIn("denied", result)
        self.assertEqual(len(result["allowed"]), 1)
        self.assertIn("peer:alice", result["allowed"])
        self.assertEqual(len(result["denied"]), 2)
        self.assertIn("peer:bob", result["denied"])
        self.assertIn("peer:charlie", result["denied"])

    def test_filter_peers_all_denied(self):
        """filter_peers 全部拒绝时 allowed_list 为空"""
        storage, acl = self._make()
        entry = storage.add_memory("top secret", category="classified")

        # 没有 allow 规则
        result = acl.filter_peers(
            entry.id,
            ["peer:a", "peer:b", "peer:c"],
            "read",
        )
        self.assertEqual(len(result["allowed"]), 0)
        self.assertEqual(len(result["denied"]), 3)

    def test_acl_stats_after_adding_rules(self):
        """acl_stats 添加规则后正确计数"""
        _, acl = self._make()

        acl.add_rule("peer:a", "category:x", "read", "allow", 50)
        acl.add_rule("peer:b", "category:y", "read", "allow", 50)
        acl.add_rule("peer:c", "category:z", "read", "deny", 50)

        stats = acl.acl_stats()
        self.assertEqual(stats["total_rules"], 3)
        self.assertEqual(stats["by_effect"].get("allow", 0), 2)
        self.assertEqual(stats["by_effect"].get("deny", 0), 1)


if __name__ == "__main__":
    unittest.main()
