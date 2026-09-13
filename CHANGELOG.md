# Changelog

All notable changes to MindForge will be documented in this file.


## [5.6.6] - 2026-09-13

修复一轮针对加密库与联邦并发的遗留问题（P2×1 / P3×2），其中加密跨进程搜索为 v5.5.7 起的遗留缺陷。

### Fixed
- **P2 加密记忆跨进程搜索 0 命中（v5.5.7 遗留，core/storage.py、core/query.py）**：加密库磁盘 `content` 列为空，且 FTS5 刻意不落明文以保持静态加密，导致进程重启后 TF-IDF 水合（`get_indexable_documents` 用 `WHERE encrypted=0` 排除了加密条目）与 fuzzy 逐行比对全部对加密记忆失效，`query.py` 结果构建处的解密因此成为永不触发的死路径。现新增 `_plaintext_for_index()`：水合与 `fuzzy_search`/`find_similar`/`check_duplicates` 在内存中逐条解密后再参与索引与打分，单条密文损坏只跳过该条、不中断整体检索。**安全边界不变**：解密明文仅驻留进程内存（运行期本就持有密钥与明文），绝不写回磁盘或 FTS5，磁盘 `content` 列仍为空，静态加密不被削弱。
- **P3 联邦共享字典并发竞态（modules/federated.py）**：`purge_expired_shared_memories()` 此前遍历并 pop `shared_memories` 未持锁，与重放校验/`share_memory`/`get_shared_memories`/`revoke_share` 并发时可能触发 `dictionary changed size during iteration` 或读写互相覆盖。现上述四个方法对共享字典的访问统一在既有 `_fed_lock` 临界区内完成（purge 快照+删除、get 快照、share 写入、revoke 检查+删除原子化）；非重入锁，节流清理均在加锁前调用，无嵌套死锁。新增 8 线程并发回归。
- **P3 版本号 docstring 漂移（core/storage.py）**：文件头版本由过期的 v5.6.1 同步为 v5.6.6（运行时版本真值仍以 `core/version.py` 为准）。

### Tests
- 新增 `tests/test_v566_encrypted_search.py`（7 项）：加密库“重启”后 `get_indexable_documents` 内存解密且磁盘明文为空、全新 QueryEngine 关键词（TF-IDF 水合）命中、fuzzy 命中、`find_similar`/`check_duplicates` 可见加密记忆、坏密文跳过不炸、联邦共享字典 8 线程并发无竞态且数据一致。


## [5.6.5] - 2026-09-13

第三轮安全与健壮性加固（共 24 项：P0×3 / P1×7 / P2×8 / P3×6）。本轮在不引入新依赖的前提下，把联邦跨节点信任从对称 HMAC 升级为真正的非对称签名，并补齐重放防护、计数落库持久性、内存结构上限与入口层一致性。

### Security (P0 — 必修)
- **#1 联邦签名升级为 Ed25519 非对称签名（modules/federated.py）**：此前 HMAC-SHA256 要求双方共享同一对称密钥，任一方泄露即可伪造对方全部消息。新增模块级 `generate_keypair()`（Ed25519，urlsafe-base64 无填充 seed/public key）；`FederatedMemory` 支持 `local_private_key`/`local_public_key`（未给则生成一次性密钥对）并暴露 `local_public_key`；`register_peer` 推荐传对端 `public_key`。配了公钥即走 Ed25519 验签（捕获 InvalidSignature/ValueError），仅有 shared_secret 时回退 HMAC 以向后兼容，两者皆无 fail-closed。`FederatedPeer.to_dict()` 导出公钥但永不导出 shared_secret。
- **#2 联邦通信增加重放攻击防护（modules/federated.py）**：新增 `sign_payload()`，在被签名载荷中注入 `_ts`（墙上时钟，跨节点必须用绝对时间）与 `_mid`（nonce）。`receive_memory` 增加 `_replay_check`：签名通道强制时间戳有效（偏差窗口 `federated_allowed_skew_seconds` 默认 300s）、nonce 去重（`_seen_nonces`，容量上限 `federated_replay_max_nonces` 默认 100000，满则拒收防止内存打满，加锁并按窗口清理），截获的合法消息无法二次重放。
- **#3 访问计数先 commit 成功后才扣减挂账（core/storage.py `_flush_access_locked`）**：此前先从 `_access_pending` pop 再 `commit()`，commit 失败（磁盘满/锁超时）这批计数永久丢失。改为 UPDATE+commit 成功后才做差额扣减；失败记录 error 并完整保留挂账等下轮重试。

### Security (P1)
- **#4 `_access_last_flush` 定期回收（core/storage.py）**：新增 `_maybe_prune_access_locked()`，在挂账更新/全量刷盘成功后按阈值回收久未访问且无挂账的 id（间隔 1h），删除/过期路径同步清理，字典不再随记忆总量线性增长。
- **#5 IndexEngine 内存结构加上限（core/indexer.py）**：`_doc_texts` / 稀疏 `vectors` / 倒排链三者默认上限 `DEFAULT_MAX_DOCS=100000`（构造参数 `max_docs`，可用 `MINDFORGE_INDEX_MAX_DOCS` 覆盖，<=0 不限），超限按插入顺序近似 LRU 从三处一致淘汰（`evicted_count` 可观测）。多路召回中被淘汰文档仍由 SQLite FTS5/fuzzy 覆盖，最终召回不损失。
- **#6 auto_archive 失败不再静默吞掉（core/storage.py，用户原标 modules/evolution.py，实际实现位于 StorageEngine.auto_archive）**：循环异常改为收集 `failed`/`failed_ids` 并 `logger.error`，返回值暴露失败计数，磁盘满/锁超时不再被掩盖。
- **#7 过期 Privacy grants 定期清理（modules/privacy.py）**：新增 `purge_expired_grants()`（内存字典 + `DELETE FROM access_grants`）与节流 `_maybe_purge_expired_grants()`；grant/revoke/check 全部在 `_grants_lock` 内执行，check 遍历前 copy。
- **#8 Web UI 非法 Content-Length 不再崩溃（cli/main.py）**：非数值或负值返回 400 而非抛 ValueError 导致裸 500/线程退出。
- **#9 X-Agent-Id 默认不可伪造（api/server.py）**：新增 `_client_identity_trusted()`——无 `MINDFORGE_API_KEY` 时绝不信任客户端自报身份；还需显式 `MINDFORGE_TRUST_AGENT_HEADER=1` 才接受 `X-Agent-Id`/`agent`（适用于可信网关注入）。默认忽略自报身份、回落到服务端固定 `MINDFORGE_AGENT_ID`，杜绝冒充其他 Agent 绕过隐私隔离。完整端到端身份联邦仍属 v6.0。
- **#10 过期共享记录自动清理（modules/federated.py）**：新增 `purge_expired_shared_memories()` 与节流 `_maybe_purge_shared()`（`shared_purge_interval` 默认 300s），share/get/stats 路径触发；get_shared_memories 同步过滤过期。

### Robustness (P2)
- **#11 fuzzy_search 改游标惰性反序列化（core/storage.py）**：不再 `fetchall()` 后对全表构造完整对象列表，逐条游标打分，降低万条以上内存峰值。
- **#12 vector_search 分块流式 + 全局有界最小堆（core/storage.py）**：按 2048 行分块读取/反序列化，heapq 只保留 top_k*2 候选，消除约 10 万条 ~150MB 的瞬时峰值；有引擎走批量余弦，无引擎走 float32 fallback，排序语义不变（scan_limit=100000）。
- **#13 MemoryCache 锁（核验项）**：v5.6.2 已为 MemoryCache 配置 `threading.Lock`，get/set/invalidate/clear/stats 全部持锁，临界区为 O(1) 字典操作，不构成实质瓶颈；保留单一锁以确保正确性，8 线程并发正确性用例持续守护，故本轮不强行分片。
- **#14 备份文件自动轮转（core/storage.py）**：`create_backup(..., auto_rotate=True, keep_count=10)` 在备份成功后自动调用 `delete_old_backups`，返回新增 `rotated`；Path 版 `backup()` 同样自动轮转。
- **#15 CORS 头条件下发（api/server.py）**：`Access-Control-Allow-Methods/Allow-Headers` 仅在配置了 `Access-Control-Allow-Origin` 时才下发。
- **#16 根路径不再枚举端点（api/server.py）**：`/` 仅返回 name/version/health，未认证时不泄露完整 API 列表。
- **#17 MCP 认证状态线程安全（mcp/server.py）**：删除裸全局 bool，新增加锁的 `_AuthState`（reset/login/is_authed/authorize），读改写在锁内原子完成；空闲计时改用 `time.monotonic()`。
- **#18 入口默认数据库路径统一（新增 core/paths.py）**：CLI 与 MCP 默认库统一为 `~/.MindForge/data/store/memory.db`（可被 `MINDFORGE_DB_PATH` 覆盖），消除此前 CLI 用 cwd 相对 `./data/memory.db`、MCP 用用户目录导致的“不同入口看不到同一数据”。库级 StorageEngine/MemoryConfig 默认保持 `./data/memory.db` 不变。

### Code Quality (P3)
- **#19 `_safe_path` 单点实现（核验项）**：`core/mindforge.py` 的 `_safe_path` 已是对 `core.storage._safe_path` 的委托封装，不存在两份重复实现，新增回归锁定该委托关系。
- **#20 磁盘探测降量并缓存（核验项）**：探测写入已由 5MB 降至 `_probe_bytes = 1024*1024`（1MB），且有 `HardwareProfiler._cached_disk_type` 进程级缓存，仅每进程探测一次；新增回归锁定。
- **#21 Web UI 线程化优雅关闭（cli/main.py）**：改用 `ThreadingMixIn + HTTPServer`（`daemon_threads=True`、`allow_reuse_address=True`），KeyboardInterrupt 走优雅关闭，限流表加锁。
- **#22 /api/health 存储异常返回 503（api/server.py）**：区分“服务不可用（下游存储故障）”与通用 500。
- **#23 进程内间隔计时改用 monotonic**：`_RateLimiter`、MemoryCache TTL、Web 限流窗口、MCP 空闲计时、2FA 内存会话窗口改用 `time.monotonic()`，避免系统时钟回拨导致 TTL 逻辑异常；跨节点/持久化的绝对时间戳（created_at/expires_at/last_accessed_at、联邦 `_ts`、持久 grant/token 时间）保持 `time.time()` 不变。
- **#24 依赖策略（核验项）**：`requirements.txt` 精确锁定（供确定性安装/pip-audit）与 `pyproject.toml` 范围约束（含上界供应链护栏）是刻意分层；新增回归自动校验锁定版本落在声明范围内，防止二者漂移。

### Tests
- 新增 `tests/test_v565_security.py`（31 用例）：Ed25519 往返/篡改与乱签名拒绝/nonce 去重/不同消息可各过/超窗时间戳拒绝/签名通道强制信封/HMAC 旧链路兼容；访问计数 commit 失败保留挂账并补刷；last_flush 回收；IndexEngine LRU 三处一致淘汰与 0 不限；auto_archive 成功/失败计数；过期授权内存+DB 双清；共享过期清理；X-Agent-Id 三态信任；向量分块召回最佳匹配；备份轮转；CORS 条件下发；MCP `_AuthState` 状态机；默认路径统一（env/home/MCP 委托）；`_safe_path` 单点委托、磁盘探测 1MB+缓存、依赖锁与范围一致。
- 适配 2 个既有测试到加固后的时间基/认证持有者：`tests/test_v562_p2_fixes.py`（限流清理用 monotonic 种子）、`tests/test_v563_entry_coverage.py`（MCP 过期用负阈值触发，不再依赖已删除的全局时间戳）。

## [5.6.4] - 2026-09-13

### Performance
- **向量索引稀疏化 + 倒排链**：`core/indexer.py` 的 `VectorIndex` 此前把每篇文档向量稠密化到词表大小（中文 bigram 词表随语料涨到上万维），单次搜索退化为 O(N×V) 全表稠密点积（N=文档数、V=词表大小，二者随语料同时增长），3,000 条记忆的混合搜索延迟达 ~0.9s。改为向量稀疏 dict 存储，并维护维度 → {doc_id: 权重} 的倒排链 `_postings`，查询只累加共享非零维候选；覆盖写入先清旧链，稠密 list/tuple 输入继续兼容。优化前/后同机各 3 次独立运行取中位数（N=3,000）：混合检索 654ms→11ms（约 59×），1,000 条后延迟基本不随数据量增长。
- **fuzzy_search difflib 剪枝**：`core/storage.py` 模糊搜索此前全表 `SELECT *` 后对每条记忆的完整内容运行 `difflib.SequenceMatcher`。新增零成本字符门槛剪枝：长文本（n > max(2m,12)）与查询无共享 bigram 且共享单字不足 4 个时跳过 SequenceMatcher（数学上相似度不可能超过 0.4 阈值）；短文本与标签路径保留原逻辑，打分、高亮与阈值语义不变。3,000 条模糊检索中位数 514ms→432ms。
- **读路径缓存真正接入（修复 v5.5.5 死代码）**：`MemoryCache`（v5.5.5 建立、v5.6.2 加锁）此前从未被任何读路径使用，`get_memory` 每次都 SQL 查询 + 行反序列化。现读请求先查缓存（带 30s TTL 兜底），命中跳过 SQL；update/delete/restore/bulk_update/merge 等写路径主动失效。
- **访问计数写库节流**：此前每次 `get` 都执行 `UPDATE memories SET access_count=access_count+1 ... COMMIT`（读路径写放大、闪存磨损、与写事务互斥）。改为进程内挂账，距上次落库超过 60s 才单事务批量累加；most_accessed/recently_accessed/merge/衰减评分/自动分层/reinforce/去重等消费访问统计的入口在读取前主动刷盘，close 兜底刷盘；崩溃最多丢失一个窗口的启发式计数，语义契约（排序、合并相加、阈值筛选）保持不变；挂账与刷盘由 RLock 保护并采用 swap 字典刷盘，杜绝并发计数丢失与双写。热读（缓存命中）p50 0.71ms→0.0044ms（约 160×），冷读/写入/更新/删除在噪声范围内无回归。

### Tests
- 新增 `tests/test_v563_perf_fixes.py`（16 用例）：覆盖稀疏向量打分与稠密数学一致、覆盖写不残留旧倒排链、删除链维护、稠密输入兼容、IndexEngine 端到端排序、fuzzy 长文本剪枝不丢精确/近似命中、读缓存命中与写后失效、访问计数节流与 close 精确刷盘、并发 get 与批量刷盘交错下计数精确无丢失。全部 570 项测试通过。

### Tooling
- 新增 `benchmarks/perf_benchmark.py`：12 阶段可复跑性能基准（增删改查/分页/搜索延迟曲线/加密/AES-GCM 原语吞吐/备份恢复/8 线程并发正确性/RSS），结果写入 `benchmarks/results/perf_results.json`，支持 `MF_BENCH_N` 缩放规模。
- 新增 `benchmarks/generate_report.py`：汇总基准、pytest JUnit 与覆盖率数据，自动生成含图表的中文 PDF 测试与性能报告。
## [5.6.3] - 2026-09-13

### Security (P0 — 必修)
- **联邦签名密钥模型修复（#1）**：HMAC 密钥从 `public_key` 改为 `shared_secret`（公钥公开，用它做 HMAC 任何人可伪造签名）。`register_peer()` 新增 `shared_secret` / `public_key` 参数；无密钥节点 fail-closed 拒绝签名与验签并记录 WARNING。
- **共享冲突 LWW 数据丢失修复（#2）**：v5.4.4 回归——200 字符 `content_preview` 覆盖完整内容导致永久丢数据。现在快照缺完整 `content`（仅 preview）时拒绝自动解决，保持冲突 open 交由人工/重新同步。
- **rekey 全局引擎切换原子化（#3）**：`rekey_engine()` 在 `_init_lock` 内原子更新 key 文件与 `_global_engine`，消除多线程 rekey 的不一致窗口；`get_engine()` 改为锁内读取。
- **存储层限流器线程安全（#4）**：`_RateLimiter` 增加 `threading.Lock()`，修复多线程并发 `RuntimeError: dictionary changed size during iteration`。

### Security (P1)
- **backup() key_file 路径包含性校验（#5）**：除 `_safe_path` 外，追加「key_file 必须在项目根目录内」校验（默认拒绝打包 `~/.ssh/id_rsa` 等系统文件），可用 `MINDFORGE_ALLOW_EXTERNAL_KEY_FILE=1` 显式放行。
- **decrypt_content 绑定加密参数（#6）**：重建 `EncryptedBlob` 时补齐 `kdf_params`，保留密文级 KDF 参数版本化能力，rekey 中途失败回滚不完整时旧密文仍可按旧参数解密。
- **EmbeddingEngine 单例失败重试（#7）**：初始化失败时重置 `_instance`，后续实例化可重试，不再静默复用半初始化实例。
- **内容长度限制统一（#8）**：删除 `update_memory` 本地 50000 字符影子常量，统一走模块级 `MAX_CONTENT_LEN`（1MB），消除「能创建却永远无法更新」的不一致。
- **Webhook SSRF 深度防护（#9）**：注册与投递双阶段校验；支持非规范 IPv4 写法（127.1 / 0x7f000001 等）、DNS 全量解析（防 rebinding）、拒绝内网/链路本地/回环/组播；投递禁止跟随 3xx 跳转（防 302 → 169.254.169.254）。
- **grant_access 权限校验（#10）**：`granted_by` 必须是记忆所有者，非所有者拒绝授权 PRIVATE/STRICT 记忆。
- **evolution.consolidate 原子化（#11）**：双次 `update_memory` 合并为单次调用，消除中途崩溃导致的 layer/consolidation_count 不一致。
- **API 默认 fail-closed（#12）**：`MINDFORGE_ALLOW_NOAUTH` 默认 `"0"`；未设 `MINDFORGE_API_KEY` 时非 localhost 绑定直接拒绝启动（`SecurityError`）。
- **Web UI 模式安全加固（#13）**：Basic Auth（`MINDFORGE_API_KEY`）、请求体上限 10MB、速率限制 60 req/min、路径穿越拦截，且不再改变全局 cwd。

### Security (P2)
- **404 响应脱敏（#14）**：不再回显请求路径，统一返回 `{"error": "Not found"}`。
- **IPv6 限流 key 归一化（#15）**：IPv6 按 /64 子网前缀计数，堵住临时地址绕过。
- **MCP 认证过期（#16）**：30 分钟无活动自动登出（`_AUTH_IDLE_TIMEOUT`），不再永久 `_authenticated=True`。
- **MCP Content-Length 强校验（#17）**：缺失/≤0/非数值/超 10MB 一律拒绝；stdin EOF 直接退出，不再空转（修复此前 EOF 触发 100% CPU 死循环）。
- **CLI 全局 cwd 修复（#18）**：Web UI 通过 `directory=` 参数定位静态目录，不再 `os.chdir(web_dir)`。
- **generic_api 必需字段校验 + 异常脱敏（#19）**：`_require` 统一校验必需字段（`memory.add/get/update/delete`、`graph.related`），未预期异常返回脱敏 `Internal error`。
- **凭据防进程列表泄露（#20）**：`baidu_push.py` 与 `rekey` 命令支持环境变量/交互式不回显输入，CLI 参数使用时打印明确告警。
- **MemoryCache 线程安全（#21）**：LRU 缓存增 `threading.Lock()`。
- **_safe_json_loads 签名统一（#22）**：模块级函数新增 `default` 参数，与 `StorageEngine._safe_json_loads` 语义对齐，消除 `[]` 被当 `max_depth` 的 TypeError。
- **外部输入 JSON 解析统一走安全包装（#23）**：REST body / MCP 消息 / CLI import 与备份恢复 manifest（含核心层 `backup-restore`）/ personality 均改用 `_safe_json_loads`（深度+大小限制）。

### Quality (P3)
- **磁盘探测载荷降量（#24）**：`_detect_disk_type` 探测文件由 5MB 降至 1MB，结果缓存。
- **_safe_path 单点实现（#25）**：删除 mindforge.py 与 storage.py 的重复实现，统一委托 storage 层权威实现。
- **全局加密引擎读取加锁（#26）**：`get_engine()` 在 `_init_lock` 内读取 `_global_engine`。
- **F1 列表隐私过滤（#27）**：REST `/api/memories`、`/api/export`、MCP `memory_list`、GenericAPIAdapter 列表入口支持 `actor`/`session_id` 隐私过滤；`list_archived` 同步接入。设计见 `docs/F1_list_privacy_filtering.md`。
- **SQL 拼接白名单固化（#28）**：f-string SQL 全部走硬编码列名或 `ALLOWED_FIELDS` 白名单校验，值一律参数绑定。
- **personality.py 安全 JSON（#29）**：用户画像解析改用 `_safe_json_loads`。
- **入口层覆盖率提升（#30）**：新增 `tests/test_v563_entry_coverage.py`（30 用例），覆盖 GenericAPIAdapter 分发/脱敏/F1、MCP 帧协议与认证过期、REST 认证/限流/404/体积上限、CLI 入口分发。整体覆盖率 36% → 41%；入口层：adapters 0% → 80%、api 45% → 56%、mcp 20% → 36%、cli 4% → 16%。

### Fixed
- `modules/federated.py`：`register_peer` 引用未定义的 `logger`（模块级缺 `import logging`），签名/验签路径修复。
- `adapters/generic_api.py`：`_handle_search` 向核心层传了不存在的 `actor` 关键字（应为 `agent_id`），搜索接口实际不可用——已修复。

## [5.6.1] - 2026-09-11

### Security (P1)
- **审计事务竞态修复**：`delete_memory` / `purge_trash` 等写操作的审计日志并入同一事务（`commit=False` + `@_with_rollback`），堵住"数据已提交但审计失败"的竞态窗口
- **备份路径校验**：`backup()` 写入路径加 `_safe_path` 校验（目录校验 + 最终文件校验），防路径遍历写敏感目录
- **API TLS 支持**：`start_api_server` 新增 `ssl_certfile` / `ssl_keyfile` 参数，强制 TLSv1.2+；CLI 加 `--ssl-cert` / `--ssl-key` 参数

### Security (P2)
- **XSS 未闭合标签绕过**：`_XSS_RE` 正则从 `<[^>]*>` 改为 `<[a-zA-Z!/?][^>\s]*[^>]*>?`，匹配无 `>` 的不完整标签（如 `<img onerror=alert(1)`）
- **API fail-closed 启动**：非 localhost 绑定且未设 `MINDFORGE_API_KEY` 时直接 `raise SecurityError` 拒绝启动
- **写操作限流**：POST/PUT/DELETE 全部过 `_check_rate_limit()`，此前仅 GET 限流
- **storage.py docstring 版本**：v5.5.8 → v5.6.1

### Performance (P2)
- **N+1 写入优化**：新增 `StorageEngine.bulk_update_memory_fields()` 单事务批量更新标量字段（strength/forgetting_score/metadata），字段白名单安全校验。`evolution.update_forgetting_scores` 和 `memory_decay.compute_decay_scores` 从 N 次事务降为 1 次

## [5.6.0] - 2026-09-09

### Security
- **PBKDF2 版本化 KDF 参数（P0 安全债偿还）**：60k → 600k 迭代次数可平滑迁移。密文头存储 `iterations`，解密时按头参数走，新加密使用 600k。提供 `mindforge rekey` 命令一键升级（支持 `--upgrade-only` 仅升级参数不改密码），旧密文自动兼容。密码验证使用 key 文件内存储的加密 token，防止误输密码后批量重加密
- **rekey 失败恢复链完整化**：`rekey_memories` 抛异常时自动恢复 `.key.bak` 到 `.key`，同步恢复新旧引擎和全局引擎引用，确保旧密文 + 旧密钥一致（此前仅回滚数据库事务但密钥文件已换成新的，会导致全部解密失败）

### Added
- **记忆衰减/遗忘引擎（P2 特性）**：`modules/memory_decay.py` 实现基于 Ebbinghaus 遗忘曲线的记忆生命周期管理。三套预设策略（conservative/balanced/aggressive），支持衰减评分 → 强度归档 → 过期清除的完整 GC 周期。`mindforge gc` 命令支持 `--dry-run` 预览、`--status` 统计、`--skip-purge` 保守模式
- **记忆库备份/恢复（P1）**：`mindforge backup` 创建 ZIP 完整快照（数据库+密钥+配置+清单），`mindforge backup-restore` 从 ZIP 恢复，支持加密模式、force 覆盖。加密模式下 CLI 明确警告备份包含密钥文件
- **按 ID 批量归档**：`StorageEngine.archive_memories_by_ids()` 支持按强度阈值选择性归档（原有 `auto_archive` 仅支持按时间）
- **CI sitemap lastmod 自动更新**：部署流程中自动将 sitemap.xml 的 lastmod 更新为当天日期
- **woff2 字体自托管**：Sora/Inter/JetBrains Mono 的 14 个 woff2 文件本地托管（315KB），Google Fonts 仅保留 Noto Sans SC
- **测试补盲（P1）**：新增 34 个测试覆盖 skill_extractor（模板渲染/序列化/聚类）、share_conflict（detect_incoming/dismiss/stats/cleanup）、federated_acl（通配符/多操作/filter_peers/规则管理）
- **衰减/GC 测试**：24 个测试覆盖 DecayConfig 策略、compute_strength 衰减计算、保护规则、归档/删除流程、完整 GC 周期

### Changed
- CLI 命名修正：备份恢复命令从 `restore`（与回收站恢复冲突）改为 `backup-restore`，保持 `restore` 专用于回收站

## [5.5.10] - 2026-09-08

### Fixed
- **XML ParseError not caught**: `import-xml` only caught `(ValueError, TypeError)` but `ET.ParseError` inherits from `SyntaxError`; malformed XML now shows friendly error instead of traceback
- **serve command bind host**: Web UI mode ignored `--host` and always bound `0.0.0.0`; now respects `--host` (default `127.0.0.1`), matching API mode behavior
- **pip-audit CI red**: cryptography 49.0.0 had CVE-2026-69247; bumped minimum to cryptography>=50.0.1 (0 known vulnerabilities). Also narrowed audit scope to project direct dependencies only (`-r requirements` mode) to eliminate runner pre-installed package CVE noise and the `--strict --skip-editable` interaction bug

### Added
- **XXE protection in import-xml**: DOCTYPE/ENTITY declarations are rejected before XML parsing, preventing billion-laughs and external entity attacks
- **Dynamic version assertion**: website version numbers injected from JSON-LD `softwareVersion` single source; no more hardcoded version drift

### Changed
- **PBKDF2 comment corrected**: 60,000 iterations is below OWASP 2023 recommendation of 600,000; comment now accurately reflects the performance tradeoff
- **setup.py classifiers**: added Python 3.13 (was only in pyproject.toml)
- **Removed deprecated `MindForge_combined.py`**: 90KB dead code that was marked DEPRECATED; also eliminates bandit scan noise
- **CI security job**: setuptools and wheel upgraded to latest before pip-audit to clear runner-bundled CVE versions

### Security
- **CI security gate is now real**: removed `pytest || test_core.py` and `|| true` fallbacks in v5.5.9; CI now genuinely fails red on breakage. All 5 Python versions + security job pass green as of this release

## [5.5.9] - 2026-09-04

### Fixed
- **CI fallback removed (P0)**: `pytest || test_core.py` and `|| true` on smoke test were masking failures; CI now fails red when tests actually break
- **CLI main() signature (P0)**: `main()` did not accept `argv` parameter, causing `main(['--help'])` TypeError in CI smoke test; fixed to `main(argv=None)` with `parse_args(argv)`
- **CHANGELOG typo**: lowercase `mindforge export` → `MindForge export` (Linux command-not-found)

### Changed
- **License unified**: README and MindForge.py changed from "MIT + Privacy Addendum" to pure MIT, matching LICENSE file
- **Dynamic version injection**: website version numbers read from JSON-LD `softwareVersion` via JS, no longer hardcoded in 6 places
- **Dynamic number injection**: toolCount/moduleCount/testCount in JSON-LD as single source; inline text uses `data-num` attrs
- **Number fuzzing**: inline mentions use approximate display (30+ tools, 370+ tests) via JS injection
- **Async font loading**: Google Fonts loaded asynchronously with `media="print" onload` pattern + local fallback CSS
- **robots.txt**: merged duplicate User-agent blocks; removed Disallow (page-level noindex is canonical)
- **Action versions upgraded**: checkout@v5, setup-python@v6, upload-pages-artifact@v4 (Node.js 20 deprecation)

### Added
- **SECURITY.md**: vulnerability reporting policy (email, response timeline, scope)
- **LICENSE file**: standard MIT text, GitHub now recognizes and displays badge
- **CI security job**: `bandit` (AST security scan) + `pip-audit` (dependency vulnerability check)
- **Python 3.13**: added to CI matrix and pyproject classifiers
- **v5.5.8 GitHub Release**: first official release with release notes

## [5.5.8] - 2026-09-01

### Added
- **Memory Version Diff (记忆版本差异对比)**: `memory_diff(version_a, version_b)` — compare two historical versions of a memory, returning structured diff for content (unified diff), category, tags (added/removed), and importance changes
  - API: `mf.memory_diff(version_id_a, version_id_b)`
  - CLI: `MindForge memory-diff <version_a> <version_b> [--json]`
  - MCP: `memory_diff` tool with `version_a` / `version_b` parameters
- **MCP Parameter Validation**: All 33 MCP tool handlers now validate required parameters before execution, returning clear `{"ok": false, "error": "Missing required parameter(s): ..."}` instead of crashing with `KeyError`

### Fixed
- **P0: storage.py `__version__` NameError**: `export_agent_memories()` referenced undefined `__version__` variable, causing crash when exporting agent memories. Added `__version__` import with fallback.
- **P1: Falsy enum values silently overridden in `add()`**: `privacy or default` pattern caused valid falsy values like `PrivacyLevel.NONE` to be replaced by config defaults. Changed to explicit `is not None` checks for `privacy`, `importance`, `layer`, and `memory_type` parameters.
- **P1: CLI error message string concatenation**: Multi-line password error message was concatenated without spaces, producing mangled output. Fixed with proper string formatting.
- **P1: `cmd_list` showed global total instead of filtered count**: When using category/layer/starred/date filters, the displayed total was always the global database count instead of the filtered result count.
- **P2: REST API JSON body type validation**: `POST /api/memories`, `POST /api/import`, and `PUT /api/memories/{id}` now validate that the request body is a JSON object (dict), returning 400 error for arrays/strings/numbers instead of crashing with `AttributeError`.
- **P2: Encryption engine input validation**: `encrypt()`, `decrypt()`, and `hash()` now validate input types, raising `SecurityError` for non-string / non-EncryptedBlob inputs instead of bare `AttributeError`.
- **P2: MCP `_handle_tools_call` params type validation**: JSON-RPC `params` and `arguments` fields are now validated as dicts before accessing nested keys, preventing `AttributeError` on malformed requests.
- **P2: Stale docstrings**: Updated version strings in `core/storage.py`, `core/mindforge.py`, `core/encryption.py`, `cli/main.py`, `api/server.py` docstrings from stale versions to v5.5.8.

### Fixed (Comprehensive Audit, 2026-09-01)
- **P0: `storage.py` character network NameError**: `get_character_network()` referenced undefined `a` / `b` variables (should be loop variables `primary_char` / `partner_char`) when building the co-occurrence edge list, crashing the character relationship network feature with `NameError`. Fixed variable references.
- **P1: CLI `sqlite3` not imported**: Several `except sqlite3.Error` branches referenced the unimported `sqlite3` module, so real database errors were masked by a secondary `NameError`. Added `import sqlite3` to `cli/main.py`.
- **P1: CLI crash on unknown command**: `_main_dispatch()` called `parser`, a local variable of `main()`, when the command was not found, raising `NameError`. Replaced with `commands`-dict lookup and a clean exit code 1.
- **P2: CLI missing typing imports**: `Dict` / `List` / `Any` used in local function annotations without `from typing import ...`, failing static checks. Added `from typing import Any, Dict, List`.
- **P1: Intent router level broken**: `IntentResult.level` used `2 if self.fallback else 2` (both branches identical), so fallback routing was indistinguishable from normal LLM routing. Changed to `3 if self.fallback else 2` (0=rule / 1=keyword / 2=LLM / 3=fallback).
- **P1: Recall engine mutated original memory**: Context trimming did `truncated = chunk` (alias, not copy), so truncating `truncated.content` mutated the caller's `MemoryChunk` in place, permanently truncating stored memory content. Changed to `copy.copy(chunk)`.
- **P1: REST API concurrency slot leak**: When a worker thread `t.start()` failed, the concurrency counter was not decremented, leaking slots until the server permanently returned 503. Added `try/except` rollback with a proper 503 response.

### Tests
- Added `tests/test_v558_features.py` with test cases covering:
  - `memory_diff` basic diff, identical versions, nonexistent version, content/category/tags/importance changes
  - MCP parameter validation: missing required params for memory_add, memory_search, memory_context
  - Falsy enum fix: `PrivacyLevel.NONE` preserved through `add()`
  - CLI `cmd_list` filtered total count
  - Version verification: `__version__` and `pyproject.toml` consistency

## [5.5.7] - 2026-08-30

### Added
- **Single-file combined CLI**: `MindForge_combined.py` — all-in-one build bundling core, modules, adapters and CLI with AI drama features
- **REST API concurrency limit**: configurable cap on in-flight requests, returning 503 when exceeded to prevent resource exhaustion

### Security
- **Encryption fail-closed (P1-008)**: Removed the HMAC-XOR fallback encryption path. If `cryptography` was unavailable in an earlier version and encrypted memories were created, those `EXPERIMENTAL_HMAC_XOR` blobs are now **permanently undecryptable**. Back up with `MindForge export --json > backup.json` before upgrading.
- **WAL network-filesystem downgrade**: `_get_conn()` now detects network filesystems (including `fuseblk` / `fuse.*`) and falls back to `journal_mode=DELETE`, avoiding SIGBUS crashes on network-mounted databases.
- **Webhook signature consistency**: unified HMAC signature computation; request timeout now honours `config.timeout`
- **`add_tags_to_ids` XSS sanitization**: tag values sanitized before being persisted and rendered

### Fixed
- **P0/P1 security hardening batch**: fail-closed encryption, concurrency limiting, and audit coverage gaps
- **P2: `agent-insight` crash**: fixed case-sensitivity mismatch that raised on some agent identifiers
- **P2: banner pollution**: CLI banner no longer leaks into `--json` output; password errors emit valid JSON on stdout in JSON mode
- **P2: `purge_trash` FK constraint**: purge order corrected to satisfy foreign key constraints
- **P3: 4xx logging**: failed webhook attempts now log `actual_attempts`
- **P3: JSON password error**: `--json` mode emits a well-formed JSON error object instead of raw text

### Integration Notes
- When using the `dsh-mindforge` bridge, `MINDFORGE_PASSWORD` must be exported (`export MINDFORGE_PASSWORD="your-password"`), otherwise the CLI exits with an error on encrypted databases.

## [5.5.6] - 2026-08-26

### Added
- **Memory Pinning (置顶)**: Full pin/unpin lifecycle with priority sorting
  - `add(..., pinned=True)` — create memory already pinned
  - `pin(memory_id)` / `unpin(memory_id)` — toggle pin status with audit logging
  - `list(pinned=True/False)` — filter by pin status
  - `list_pinned(limit)` — list all pinned memories
  - Pinned memories always appear first in `list()` results (`ORDER BY pinned DESC`)
  - `update(memory_id, pinned=True/False)` — update pin status
- **Batch Get Memories**: `batch_get(memory_ids)` — fetch multiple memories in a single SQL query, preserving input order, with automatic dedup and expired-memory filtering
- **Memory Timeline View**: `timeline(category, layer, limit)` — group memories by time period (today / yesterday / this_week / this_month / earlier)
- **Search Suggestions**: `search_suggestions(prefix, limit, category)` — autocomplete suggestions based on existing tags and categories (case-insensitive prefix matching)
- **Duplicate Detection on Add**: `check_duplicates(content, threshold, category, limit)` — detect near-duplicate memories before adding, using Jaccard + SequenceMatcher hybrid similarity
- **Batch Tag Operations by ID**: `add_tags_to_memories(ids, tags)` and `remove_tags_from_memories(ids, tags)` — bulk add/remove tags for specific memory IDs
- **Stats Enhancement**: `stats()` now includes `pinned_count`

### Fixed
- **fuzzy_search crash on None/empty query**: Added defensive checks for `None`, non-string, and whitespace-only queries; returns `[]` instead of raising `AttributeError`
- **rename_tag case-insensitive matching**: Tags now match case-insensitively (`"MyTag"` renamed via `"mytag"` works); post-rename deduplication prevents duplicate tags
- **rename_tag empty/None input**: Returns `0` instead of crashing on empty or `None` tag names
- **batch_add missing fields**: Now supports `pinned`, `expires_at`, and `metadata` fields (previously silently ignored)
- **Facade `list()` missing `pinned` filter**: Now passes `pinned` parameter through to storage layer
- **Facade `update()` missing `pinned` parameter**: Now supports updating pin status via `update()`
- **P1: Conflict decay methods missing**: Added `adjust_importance()` and `append_tags()` to `StorageEngine`, which were called by the conflict resolution module but never implemented, causing silent failures in conflict decay functionality.
- **P1: Package naming conflict in pytest**: Removed root-level `__init__.py` that caused pytest to load the `MindForge` package from two different paths, resulting in enum classes with different identities and `isinstance()` returning `False`. Updated `setup.py` to read version from `MindForge.py` instead.
- **P2: Version hardcoded test failure**: Changed `test_version_is_555` in v5.5.5 tests to `test_version_at_least_555` to avoid breaking on minor version bumps.

### Performance
- `batch_get()` reduces N+1 query pattern to a single `IN` query for bulk memory retrieval
- Timeline view uses single sorted query instead of multiple date-range queries
- Search suggestions use in-memory set aggregation after one DB scan

### Tests
- Added `tests/test_v556_features.py` with 60+ test cases covering:
  - Pinning (10 tests): basic pin/unpin, add-with-pin, list filter, priority sorting, delete/restore persistence, update pin
  - Batch get (5 tests): basic, empty, nonexistent, order preservation, dedup
  - Timeline (4 tests): basic, empty, category filter, total count
  - Search suggestions (6 tests): tags, categories, empty/None prefix, no match, limit
  - Duplicate detection (6 tests): exact, high similarity, no match, empty, category filter, threshold
  - Batch tag ops (6 tests): add, no-duplicate, empty, nonexistent, remove, case-insensitive remove
  - adjust_importance (5 tests): lower, raise, zero delta, nonexistent, range clamping
  - append_tags (4 tests): basic, duplicate no-change, empty list, nonexistent
  - Package naming fix (3 tests): no root __init__.py, import consistency, isinstance cross-import
  - Bug fixes (12 tests): fuzzy_search None/empty/whitespace, rename_tag case/empty/dedup, batch_add pinned/expires/metadata, stats pinned_count
  - Version verification (3 tests): semver format, exact 5.5.6 match, pyproject.toml consistency
- Full suite: **260+ passed**

## [5.5.5] - 2026-08-25

### Added
- **Four-tier memory layering**: Sensory / Short-term / Long-term / Permanent layers with independent capacity and retention policies, propagating upward via the Ebbinghaus forgetting curve
- **Hardware-adaptive tuning**: `HardwareProfiler` detects machine capability and dynamically sizes the memory cache and recommended retrieval limits
- **Conflict detection**: antonym, attribute-value, and timeline contradiction detection with automatic confidence decay
- **Query pre-filtering**: `QueryEngine` entry cache to avoid redundant fetches

### Fixed
- **FTS index sync defect (7 methods)**: soft-deleted memories remained searchable because the index was not pruned after deletion; all seven call sites now clean up the index
- **Enum double-import**: enum classes loaded from two module paths broke `isinstance()` checks
- **Vector threshold fusion**: similarity thresholds no longer double-applied across fusion stages
- **Audit parameter plumbing**: corrected misrouted audit arguments
- **Synonym expansion / soft-delete filtering**: 20+ P0/P1/P2 issues resolved in the hardening pass

## [5.5.4] - 2026-08-24

### Added
- **Memory merging**: `merge_memories()` consolidates several memories into one, preserving provenance
- **Access analytics**: `most_accessed()` and `recently_accessed()`
- **Bulk update by filter**: `bulk_update_by_filter()` pushes filtering down to SQL instead of loading the full table, fixing a large-dataset performance problem
- **Tag statistics**: `tag_stats()`
- **Index consistency check**: `check_index_consistency()` detects and repairs drift between the index and storage layers

## [5.5.3] - 2026-08-24

### Fixed
- **Expired memories stayed searchable**: `purge_expired()` did not prune the index
- **Stale index after batch delete**: `batch_delete_by_category()` and `batch_delete_by_tag()` now clean up the index so deleted memories cannot be returned by search
- **API double-write**: `api/server.py` returned `None` on error, causing the caller to write a second response body
- **Embedding parameter mismatch**: warn instead of silently overriding when parameters differ
- Removed a BOM accidentally introduced in v5.5.3 that broke `core/embedding.py` parsing

## [5.5.2] - 2026-08-23

### Added
- **Memory TTL/Expiration System**: New `expires_at` field on memories with full lifecycle management
  - `add(..., expires_at=timestamp)` — create memory with expiration
  - `set_ttl(memory_id, ttl_seconds)` — set or cancel TTL on existing memory
  - `list_expired()` — list all expired memories pending cleanup
  - `purge_expired()` — batch-move expired memories to trash
  - Auto-expiry: `get()` automatically moves expired memories to trash and returns None
  - Database migration: `ALTER TABLE memories ADD COLUMN expires_at REAL DEFAULT 0` with index
- **Multi-keyword Search Highlighting**: Enhanced `highlight()` method
  - Space-separated multi-keyword support (e.g., "hello world" highlights both)
  - Chinese keyword support
  - Case-insensitive matching with original case preservation in output
  - Custom highlight tags (before_tag / after_tag)
  - Nested-replacement safe: long keywords processed first, placeholder-based replacement
- **Batch Delete by Category/Tag**: New bulk operations
  - `batch_delete_by_category(category, permanent=False)` — soft or permanent delete by category
  - `batch_delete_by_tag(tag, permanent=False)` — soft or permanent delete by tag (exact tag match, no false positives)
  - Full audit logging for all batch operations
  - Cascade deletion for permanent mode (versions, links, notes, embeddings, FTS index)

### Fixed
- **Critical: UTF-8 BOM in pyproject.toml**: Removed BOM from `pyproject.toml` that caused `tomllib.TOMLDecodeError: Invalid statement`, breaking `pytest` collection and `pip install`. Also removed BOM from 7 other source files (`api/server.py`, `cli/main.py`, `core/mindforge.py`, `core/storage.py`, `MindForge.py`, `__init__.py`).
- **FTS5 bm25 score overflow**: Added clamping (`max(-50, min(50, score))`) before `math.exp()` in `fts_search()` to prevent `OverflowError` on anomalous large positive bm25 scores. Also added `None` score handling.
- **Query engine double-fetch**: Added entry cache in `QueryEngine.search()` to avoid redundant `get_memory()` calls between the pre-filter phase and result-building phase, reducing DB queries by up to 2x for filtered searches.
- **Version consistency**: Updated README version badge from stale 5.5.0 to 5.5.2; updated version test to match current release.

### Performance
- Query engine filtered searches now use a single fetch per memory entry instead of two
- FTS5 search no longer risks exception on edge-case scores

### Tests
- Added `tests/test_v552_features.py` with 33 test cases covering:
  - TTL expiration (12 tests): add with expires_at, set_ttl, cancel TTL, auto-expire on get, list_expired, purge_expired, expired exclusion from search
  - Multi-keyword highlight (10 tests): single/multi keyword, Chinese, case-insensitive, empty inputs, custom tags, no nested replacement
  - Batch delete (7 tests): by category soft/permanent/empty, by tag soft/permanent/no-match/partial-match-no-false-positive
  - FTS5 score fix (2 tests)
  - Query engine cache (2 tests): category filter, layer filter
  - Version verification (2 tests): __version__ and pyproject.toml consistency
- Full suite: **189 passed** (155 existing + 33 new + 1 updated)

### Migration
- Existing databases are automatically migrated on first connection: `expires_at` column added with `DEFAULT 0` (never expires), with an index for efficient expired-item queries. No data loss.
