# F1 技术方案：list() 隐私过滤统一守卫

> 版本：v0.1 Draft
> 目标版本：v6.0
> 关联：多 Agent 协作安全护栏

## 问题现状

`MindForge.list()` / `list_archived()` / `list_trash()` 直接调用 `StorageEngine.list_memories()`，**完全不经过 PrivacyEngine**。这意味着：

- 通过 REST API `/api/memories` 可以列出所有记忆（含 PRIVATE/CONFIDENTIAL）
- 通过 MCP `list_memories` 工具可以列出所有记忆
- 多 Agent 场景下，子 Agent 可以列出不属于自己空间的记忆

**对比**：`get()`、`search()`、`update()`、`delete()` 已集成隐私检查。

## 影响面分析

### 对外入口（必须修复）

| 入口 | 位置 | 当前状态 |
|------|------|----------|
| REST API `GET /api/memories` | `api/server.py` | ❌ 无过滤 |
| REST API `GET /api/export` | `api/server.py` | ❌ 无过滤（全量导出） |
| MCP `list_memories` 工具 | `mcp/server.py` | ❌ 无过滤 |
| GenericAPIAdapter `list` | `adapters/generic_api.py` | ❌ 无过滤 |
| CLI `list` / `search` | `cli/main.py` | ⚠️ 本地 CLI 可接受（文件级权限） |

### 内部模块（系统级，需评估）

| 模块 | 用途 | 是否需要过滤 |
|------|------|-------------|
| `memory_decay.compute_decay_scores` | 衰减计算 | ❌ 不需要（系统内部全量扫描） |
| `evolution.update_forgetting_scores` | 遗忘曲线 | ❌ 不需要 |
| `integrator.run_integration` | 整合聚类 | ❌ 不需要 |
| `personality.PersonalityEngine` | 人格画像 | ❌ 不需要（读 user_profile） |
| `query.HybridSearchEngine` | 搜索底层 | ⚠️ 上层已过滤 |
| `privacy.compliance_scan` | 合规扫描 | ❌ 不需要（自身就是隐私引擎） |

### 分类原则

1. **对外入口**（API/MCP/Adapter）：必须加 actor/session_id，走隐私过滤
2. **系统内部模块**（decay/evolution/integrator）：直接调 `_storage.list_memories()`，跳过 MindForge 层，不过滤
3. **CLI 本地操作**：保持现状（本地用户有文件级权限），但多用户部署时建议加

## 实施方案

### 阶段一：MindForge 层加隐私守卫（核心）

```python
# core/mindforge.py — list() 加 actor/session_id
def list(self,
         category: Optional[str] = None,
         layer: Optional[MemoryLayer] = None,
         starred: Optional[bool] = None,
         pinned: Optional[bool] = None,
         created_after: Optional[float] = None,
         created_before: Optional[float] = None,
         limit: int = 50,
         offset: int = 0,
         sort_by: str = "created_at",
         sort_order: str = "desc",
         actor: str = "",           # 新增
         session_id: str = "") -> List[MemoryEntry]:  # 新增
    entries = self._storage.list_memories(...)
    # 隐私过滤（批量检查，避免 N+1）
    if actor:  # 未传 actor 时不过滤（兼容内部调用）
        entries = [e for e in entries
                   if self.privacy_engine.check_access(e, actor, session_id)[0]]
    return entries
```

**关键设计决策**：
- `actor` 为空时不过滤 → 内部模块调用无需改动
- 对外入口强制传 actor → 由入口层保证
- 批量过滤（一次 check_access 批量），性能友好

### 阶段二：对外入口传 actor

| 文件 | 修改点 |
|------|--------|
| `api/server.py` | `_extract_mem_id` 旁边加 `_extract_actor`，所有 list 调用传 actor |
| `mcp/server.py` | `list_memories` 工具加 `actor` 参数，从调用上下文取 |
| `adapters/generic_api.py` | `_handle_list` 从 params 取 actor/session_id |

### 阶段三：list_archived / list_trash 同步

`list_archived()` 和 `list_trash()` 同样需要加 actor 参数。

### 阶段四：分页总数问题

当前 `list()` 返回 `List[MemoryEntry]`，没有 total 计数。过滤后：
- 如果 offset/limit 在过滤前应用 → 页数不准
- 如果先全量过滤再分页 → 大数据量性能差

**v6.0 方案**：引入 `ListResult`（类似 `SearchResult`），含 `entries` + `total_found` + `limit` + `offset`。SQL 层加 `privacy_level` 过滤条件，数据库内分页。

## 性能考虑

| 方案 | 复杂度 | 适用场景 |
|------|--------|----------|
| Python 层批量过滤（阶段一） | O(n) 内存 + O(n) 检查 | n < 10k，快速上线 |
| SQL 层 privacy_level 条件（阶段四） | O(log n) 索引查询 | n > 10k，生产级 |

v6.0 直接上 SQL 层方案，在 `memories` 表加 `privacy_level` 列索引，`WHERE privacy_level <= ?` 直接数据库过滤。

## 多 Agent 关联

v6.0 多 Agent 协作中，每个 Agent 有自己的 `agent_id` 和权限域。list 过滤是多 Agent 空间隔离的基础：

- Agent A 只能 list 到自己空间 + 共享空间的记忆
- 联邦 peer 只能 list 到 ACL 允许的分类/标签
- 子 Agent 继承父 Agent 的部分权限（最小权限原则）

## 测试计划

- [ ] `list()` 不传 actor → 返回全部（兼容内部调用）
- [ ] `list()` 传 actor="user1" → 只返回允许的
- [ ] `list()` PRIVATE 记忆对非 owner 不可见
- [ ] `list_archived()` 同样过滤
- [ ] REST API `/api/memories` 按 actor 过滤
- [ ] MCP `list_memories` 按 actor 过滤
- [ ] 分页：过滤后 total 正确
- [ ] 内部模块（decay/evolution）调用不受影响
