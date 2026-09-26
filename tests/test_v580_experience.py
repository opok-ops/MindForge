# -*- coding: utf-8 -*-
"""v5.8.0 程序性记忆 / 经验蒸馏（Cases → Skills 自进化）回归测试

真实增量：
  1. modules/experience.py（新）：ExperienceCase + SkillStore（experience_cases /
     experience_skills 表持久化 + 蒸馏落库 + 匹配 + 渲染）
  2. core/mindforge.py：record_experience / distill_skills / skill_match /
     skill_render / skill_stats / skill_list / skill_cases / skill_case_delete
  3. CLI experience 子命令（record/distill/match/render/stats/list/cases/delete）
  4. REST：POST /api/experience、POST /api/skills/distill、
     GET /api/skills/{stats,match,render}、GET /api/skills、GET /api/experience/cases
  5. MCP +5（43→48）：memory_experience_record / memory_skill_distill /
     memory_skill_match / memory_skill_render / memory_skill_stats
  6. 审计白名单：experience_record / experience_distill

覆盖：案例记录/校验/审计、蒸馏落库/持久化恢复、匹配/渲染、CLI/API/MCP、白名单。
"""

import socket
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.request

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

from core.mindforge import MindForge  # noqa: E402
from core.types import MemoryConfig  # noqa: E402


def _make_mf(encrypted=False):
    tmp = tempfile.mkdtemp(prefix="mf_v580_", dir=_REPO)
    db = os.path.join(tmp, "m.db")
    key = os.path.join(tmp, "k.key")
    cfg = MemoryConfig(db_path=db, key_file=key, encrypted=encrypted)
    return MindForge(config=cfg), tmp


def _seed_deploy_cases(mf, n=3):
    """记录 n 条同主题成功案例（可蒸馏出技能）"""
    for i, svc in enumerate(["nginx", "mysql", "redis"]):
        mf.record_experience(
            task=f"部署 {svc}",
            content=f"首先 安装 {svc}，然后 配置 端口 {3306 + i * 100}，最后 启动 服务",
            result="部署成功", outcome="success",
            duration_seconds=12.5, tags=["deploy"], agent_id="agent-1")


class TestRecordExperience(unittest.TestCase):
    """案例记录 / 校验 / 审计"""

    def test_record_persists_and_audits(self):
        mf, tmp = _make_mf()
        try:
            case = mf.record_experience(
                task="部署 nginx", content="首先 安装 nginx", result="ok",
                tags=["deploy"], agent_id="agent-1")
            self.assertTrue(case["id"])
            self.assertEqual(case["task"], "部署 nginx")
            self.assertEqual(case["outcome"], "success")
            self.assertIn("deploy", case["tags"])
            rows = mf._storage.get_audit_log(memory_id=case["id"])
            self.assertTrue(any(r.action == "experience_record" for r in rows))
        finally:
            mf.close()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_invalid_outcome_and_empty_task(self):
        mf, tmp = _make_mf()
        try:
            with self.assertRaises(ValueError):
                mf.record_experience(task="x", outcome="bogus")
            with self.assertRaises(ValueError):
                mf.record_experience(task="  ")
        finally:
            mf.close()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_case_delete(self):
        mf, tmp = _make_mf()
        try:
            case = mf.record_experience(task="临时任务")
            self.assertTrue(mf.skill_case_delete(case["id"]))
            self.assertFalse(mf.skill_case_delete(case["id"]))
            self.assertEqual(mf.skill_cases(), [])
        finally:
            mf.close()
            shutil.rmtree(tmp, ignore_errors=True)


class TestDistillAndPersist(unittest.TestCase):
    """蒸馏落库 / 重启恢复"""

    def test_distill_saves_skills(self):
        mf, tmp = _make_mf()
        try:
            _seed_deploy_cases(mf)
            stats = mf.distill_skills()
            self.assertGreaterEqual(stats["cases_processed"], 3)
            self.assertGreater(stats["skills_found"], 0)
            self.assertGreaterEqual(stats["skills_saved"], stats["skills_found"])
            self.assertGreater(mf.skill_stats()["total_skills"], 0)
        finally:
            mf.close()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_skills_survive_restart(self):
        mf, tmp = _make_mf()
        try:
            _seed_deploy_cases(mf)
            mf.distill_skills()
            mf.close()
            mf2 = MindForge(config=MemoryConfig(
                db_path=os.path.join(tmp, "m.db"),
                key_file=os.path.join(tmp, "k.key"), encrypted=False))
            try:
                st = mf2.skill_stats()
                self.assertGreater(st["total_skills"], 0)
                self.assertGreaterEqual(st["total_cases"], 3)
                self.assertGreater(mf2.skill_list(), [])
            finally:
                mf2.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_no_success_cases_no_skills(self):
        mf, tmp = _make_mf()
        try:
            mf.record_experience(task="失败尝试", content="尝试安装失败",
                                 outcome="failure", tags=["deploy"])
            stats = mf.distill_skills()
            self.assertEqual(stats["skills_found"], 0)
        finally:
            mf.close()
            shutil.rmtree(tmp, ignore_errors=True)


class TestMatchAndRender(unittest.TestCase):
    """技能匹配与渲染"""

    def test_match_and_render(self):
        mf, tmp = _make_mf()
        try:
            _seed_deploy_cases(mf)
            mf.distill_skills()
            # 触发词/标签命中
            hits = mf.skill_match("安装 nginx")
            self.assertGreater(len(hits), 0)
            hits2 = mf.skill_match("deploy")
            self.assertGreater(len(hits2), 0)
            # 渲染
            name = mf.skill_list(limit=1)[0]["name"]
            rendered = mf.skill_render(name)
            self.assertIsNotNone(rendered)
            self.assertIn("安装", rendered)
            self.assertIsNone(mf.skill_render("no-such-skill"))
        finally:
            mf.close()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_distill_audited(self):
        mf, tmp = _make_mf()
        try:
            _seed_deploy_cases(mf)
            mf.distill_skills()
            rows = mf._storage.get_audit_log()
            self.assertTrue(any(r.action == "experience_distill" for r in rows))
        finally:
            mf.close()
            shutil.rmtree(tmp, ignore_errors=True)


class TestCLI(unittest.TestCase):
    """CLI experience 冒烟"""

    def _args(self, **kw):
        import argparse
        base = dict(db_path="", key_file="", json_output=False, task="", content="",
                    result="", outcome="success", duration=0.0, tags="", agent="",
                    memory_ids="", query="", limit=10, min_cluster=2, name="",
                    param=[], case_id="")
        base.update(kw)
        return argparse.Namespace(**base)

    def test_cli_record_distill_stats(self):
        from cli.main import cmd_experience
        mf, tmp = _make_mf()
        try:
            mf.close()
            dbp = os.path.join(tmp, "m.db")
            kp = os.path.join(tmp, "k.key")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = cmd_experience(self._args(
                    exp_action="record", task="部署 nginx",
                    content="首先 安装 nginx，然后 配置 端口", tags="deploy",
                    db_path=dbp, key_file=kp))
            self.assertEqual(code, 0)
            self.assertIn("已记录", buf.getvalue())
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = cmd_experience(self._args(
                    exp_action="stats", db_path=dbp, key_file=kp))
            self.assertEqual(code, 0)
            self.assertIn("案例数", buf.getvalue())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestAPI(unittest.TestCase):
    """REST：experience / skills 端点"""

    def _start(self):
        from api.server import start_api_server
        os.environ["MINDFORGE_ALLOW_NOAUTH"] = "1"
        mf, tmp = _make_mf()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as _sock:
            _sock.bind(("127.0.0.1", 0))
            port = _sock.getsockname()[1]
        srv = threading.Thread(
            target=start_api_server,
            kwargs={"mindforge_instance": mf, "host": "127.0.0.1", "port": port},
            daemon=True,
        )
        srv.start()
        time.sleep(1.5)
        return mf, tmp, port

    def test_experience_and_skills_endpoints(self):
        mf, tmp, port = self._start()
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/experience",
                data=json.dumps({"task": "部署 nginx",
                                 "content": "首先 安装 nginx，然后 配置 端口",
                                 "tags": ["deploy"], "agent_id": "a1"}).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=5) as resp:
                case = json.loads(resp.read().decode())
            self.assertTrue(case["id"])
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/skills/distill",
                data=b"{}",
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=5) as resp:
                dist = json.loads(resp.read().decode())
            self.assertEqual(dist["status"], "ok")
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/skills/stats", timeout=5) as resp:
                stats = json.loads(resp.read().decode())
            self.assertIn("total_skills", stats)
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/skills/match?query=install",
                    timeout=5) as resp:
                match = json.loads(resp.read().decode())
            self.assertIn("skills", match)
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/experience/cases", timeout=5) as resp:
                cases = json.loads(resp.read().decode())
            self.assertGreaterEqual(len(cases["cases"]), 1)
        finally:
            mf.close()
            shutil.rmtree(tmp, ignore_errors=True)


class TestMCP(unittest.TestCase):
    """MCP：48 工具 + 5 新工具 + handler 冒烟"""

    def test_mcp_tool_schemas(self):
        from mcp.server import TOOL_SCHEMAS, HANDLERS
        names = [t["name"] for t in TOOL_SCHEMAS]
        self.assertEqual(len(names), 56)
        for n in ("memory_experience_record", "memory_skill_distill",
                  "memory_skill_match", "memory_skill_render", "memory_skill_stats"):
            self.assertIn(n, names)
            self.assertIn(n, HANDLERS)

    def test_mcp_handler_smoke(self):
        from mcp.server import HANDLERS
        mf, tmp = _make_mf()
        try:
            r = HANDLERS["memory_experience_record"](
                mf, {"task": "部署 redis", "content": "首先 安装 redis",
                     "tags": ["deploy"]})
            self.assertTrue(r["ok"])
            _seed_deploy_cases(mf)
            r2 = HANDLERS["memory_skill_distill"](mf, {})
            self.assertTrue(r2["ok"])
            r3 = HANDLERS["memory_skill_match"](mf, {"query": "安装"})
            self.assertTrue(r3["ok"])
            r4 = HANDLERS["memory_skill_stats"](mf, {})
            self.assertTrue(r4["ok"])
            self.assertIn("total_skills", r4["stats"])
        finally:
            mf.close()
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
