# GDPR 合规工具包（v5.7.6）

MindForge 是本地优先的 Agent 记忆系统。v5.7.6 起提供三件套 GDPR 工具，
面向「数据可携权」与「被遗忘权」场景，配合 Bi-temporal 事实时序
（`valid_from` / `valid_to`），使事实可以随时间正确演化、被取代或永久删除。

> 说明：本工具包是**工程能力**，帮助应用层满足 GDPR 的数据主体权利流程；
> 不构成法律意见。部署方仍需自行完成合规评估与留痕。

---

## 1. 数据报告：`gdpr-report`

盘点当前库内保存了哪些数据类别、各多少条、是否加密、本地存储状态，
以及本工具包可行使哪些权利。

```bash
MindForge gdpr-report --json
```

Python：

```python
from MindForge import MindForge
m = MindForge(db_path="./data/memory.db")
print(m.gdpr_report())
```

返回字段（节选）：

| 字段 | 含义 |
|---|---|
| `db_path` | 数据库路径（本地文件） |
| `local_only` | 是否仅本地存储（不依赖云） |
| `encrypted_at_rest` | 是否启用 AES-256-GCM 静态加密 |
| `data_categories.memories` | 记忆条目总数 |
| `data_categories.memory_versions` | 版本历史总数 |
| `data_categories.memory_links` | 关联关系总数 |
| `data_categories.audit_log` | 审计日志总数 |
| `data_categories.archived_memories` | 归档条目总数 |
| `retention_mechanisms` | 保留机制（含 Bi-temporal `valid_to` 窗口） |
| `rights_exercisable` | 可行使权利（`export_all` / `erase`） |

## 2. 数据可携权：`gdpr-export-all`

将全部个人数据以结构化、机器可读格式导出（JSON），涵盖
memories、memory_versions、memory_links、audit_log、archived_memories
五张表；`tags` / `metadata` 等 JSON 字段已反序列化，可直接转交数据主体。

```bash
MindForge gdpr-export-all ./gdpr_export.json
```

Python：

```python
data = m.gdpr_export_all()
# data["format"] == "mindforge-gdpr-export"
# data["memories"], data["memory_versions"], data["memory_links"],
# data["audit_log"], data["archived_memories"]
```

加密库导出的是密文元数据与引用（明文只存在于进程内解密路径），
不会把明文写进磁盘导出文件。

## 3. 被遗忘权：`gdpr-erase`

永久删除当前库中的**全部**数据，不可逆。擦除前自动创建完整备份
（数据库文件 + 密钥文件副本），便于部署方在确认无误前保留恢复手段。

```bash
# 不带 --force 会拒绝执行并提示确认
MindForge gdpr-erase
# 确认执行（保留备份）
MindForge gdpr-erase --force
# 确认执行（不保留备份，极端场景）
MindForge gdpr-erase --force --no-backup
```

Python：

```python
result = m.gdpr_erase_all(backup=True)
# result["deleted"]  各表删除计数
# result["backup"]   备份文件路径（含密钥副本时同目录下有 memory_backup.key）
```

擦除范围：

- `memory_links` / `memory_versions` / `audit_log` / `archived_memories` /
  `memories` 五张表按外键顺序清空；
- FTS5 全文索引同步清空（`search` 不再命中）；
- 进程内缓存与访问计数挂账一并清除；
- 写入 `gdpr_erase` 审计动作（审计动作白名单见 `core/storage.py`）。

## 4. Bi-temporal 事实时序

配合 GDPR 的生命周期治理，v5.7.6 为记忆条目增加两个时间维度：

| 字段 | 含义 |
|---|---|
| `valid_from` | 事实生效时间（0 = 始终生效） |
| `valid_to` | 事实失效时间（0 = 尚未失效） |

- `add(..., valid_from=ts, valid_to=ts)` / `update(..., valid_from=..., valid_to=...)`：
  写入时指定有效窗口；
- `valid_at(timestamp)`：as-of 查询，返回该时刻仍有效的事实（CLI
  `MindForge valid-at --ts <ts>`，API `GET /api/valid-at?ts=<ts>`，MCP
  `memory_valid_at`）；
- `supersede(old_id, new_content)`：**事实取代而非删除**——关闭旧事实的
  开放窗口（`valid_to = now`），创建继承旧字段的新事实，建立
  `supersedes` 关联，旧版本历史完整保留（CLI `MindForge supersede <id> <内容>`，
  MCP `memory_supersede`）。与 Zep / Graphiti 的
  「事实矛盾自动失效而非删除」实践对齐。

旧数据库首次以新版本打开时自动执行 `ALTER TABLE` 迁移补列，
并创建 `idx_valid_to` 索引，无需人工干预。

## 5. 限制与建议

- `gdpr-erase` 只作用于当前库；若存在 `export-json` 加密备份或外部派生数据，
  需要部署方按自己的数据地图另行清除；
- 备份文件（含密钥副本）本身就是敏感数据，请按密钥同等标准保管，
  完成合规确认后及时销毁；
- 建议将 `gdpr-report` 结果与删除凭证（备份文件名、时间戳、审计日志）
  一并归档，作为权利请求处理留痕。
