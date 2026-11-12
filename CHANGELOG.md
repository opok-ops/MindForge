# Changelog

All notable changes to MindForge will be documented in this file.


## [6.5.1] - 2026-11-12

无 numpy 环境向量兜底修复（Python 3.10 支持矩阵内的正确性补丁）：

- **根因**：CI 的 requirements-dev 以 `matplotlib; python_version >= "3.11"` 环境标记间接携带 numpy——3.11/3.12/3.13 矩阵有 numpy 走快路径，**Py3.10 无 numpy 走兜底**，暴露两个真 bug：① `_score_vector_chunk` 兜底分支把 raw blob（字节长度 4×dim）直传余弦批量打分，维度校验静默过滤全部候选——vector 路在无 numpy 环境整体返回空；② 纯 Python 余弦批对零范数查询向量返回 0 分占位结果，与 numpy 路径的空返回行为不一致。
- **修复**：兜底分支先经 `engine.deserialize`/fallback 把 blob 解码回 list 再打分；纯 Python 路径零范数提前返回空。
- **验证**：本地 numpy import 拦截（模拟 Py3.10 CI 条件）26 项向量测试全绿；CI 真实 3.10 job 全绿（8/8 checks）；有 numpy 快路径复验一致。全量 **1273** 项通过（1272 passed + 1 环境性 skipped）。

## [6.5.0] - 2026-11-12

本地 ONNX 向量引擎 + LoCoMo 基准落版。向量检索从「接口就绪、引擎缺位」进入「本地零依赖可用」，语义召回路（第四路）正式常驻检索融合：

- **FastEmbedBackend（embedding.py）**：本地 ONNX 推理（fastembed + onnxruntime，零 torch 依赖），默认 BAAI/bge-small-en-v1.5（384 维，~33MB，经 HF_ENDPOINT 镜像下载）。查询向量 LRU 缓存（1024 条）：命中 0.019ms，冷编码 ~12ms/句。与 sentence_transformers/openai/ollama/http 后端平级注册（MINDFORGE_EMBEDDING_BACKEND=fastembed）。
- **numpy 批量向量扫描**：cosine_similarity_batch 矩阵乘快路径（修复一个真实缩放 bug：查询向量预归一化后又除 qn，分数被放大 ‖q‖ 倍——现与纯 Python 路径一致性 7e-08，已加守卫测试）；deserialize 换 array.array C 级解包；StorageEngine._score_vector_chunk 增加 raw-blob 零拷贝路径（frombuffer 直接成 (N,dim) 矩阵）。LoCoMo conv-26 实测：向量扫描 p50 46ms → 23.6ms（−49%）。
- **LoCoMo 基准套件（benchmarks/）**：locomo_deepseek_eval.py 支持 --embedding/--extract/--seats 实验矩阵（断点续跑）。四轮消融（conv-26，199 问/轮）：
  - 词法基线 4.43/10 → +向量 4.88（adversarial +0.23、temporal +0.31，语义召回报酬确认）
  - +LLM 抽取（419 轮 → 697 条原子事实）temporal 大胜（+1.30 分、正确率翻倍 15.4%→30.8%），但总分被融合挤兑吃掉
  - 分席+时间锚负优化（temporal −2.30），保留 --seats 供复现
  - 结论入库：single-hop/multi-hop 的失分在证据组装层，非召回层——v6.5 下半场 AdaptiveContextEngine 的靶子
- 全量 10 会话词法基线（1986 问）落盘：4.40/10，判官分布双峰（936×1 分 + 479×10 分）证实「捞到证据即满分、捞不到即零分」。
- 15 项新守卫测试（FastEmbedBackend 契约 / numpy 一致性 / blob 路径一致性）；全量 **1273** 项通过（1272 passed + 1 环境性 skipped）。

## [6.4.9] - 2026-11-11

千万字毫秒级收尾版（v6.4.8 记录的 FTS5 剩余瓶颈本版收割）：

- **FTS5 查询侧高频短语剪枝（trigram df 预查）**：v6.4.8 分路实测
  定位——trigram 索引下全库高频短语（模板语料的「陈述性记忆」）
  的 bm25 全匹配行评分 50.5ms/查询（30k），是 100k mixed 134.9ms
  的主要剩余构成。修复与 TF-IDF max_df 对称：短语 df 上界 =
  min(全部 trigram df)（短语匹配行 ⊆ 每个 trigram 行集交集），
  上界超库容 5% 的词从 MATCH 剔除；全被剔除时保留上界最小的一个
  （防空 MATCH）。df 经 fts5vocab 只读视图批查（**坑：vocab 是
  流式虚拟表，逐 term 点查 = 每次全表扫——combined 查询 17→45ms
  回退的根因，改一次性 IN 批查后回到 16ms**）。视图运行时惰性
  建立，兼容既有库。
- **病态回退时跳过 FTS5 路**：全库词查询（tfidf_fallback）的 FTS5
  补充无区分度信号且高频短语全库命中不可剪，与 v6.4.8 的 fuzzy
  门控同语义。正常查询（needle/topic/评测场景）不受影响——质量
  基线测试钉住 strategy_used 契约。
- **AI 味注释治理**：v6.4.6–6.4.8 新增代码段的论文化 docstring
  （考古叙事/哲学措辞/防御性冗余）压缩为工程师人话——信息保留
  （结论+关键 why），叙事删除。FTS5 df 剪枝/max_df/回退/fuzzy
  预算四处大段各 13–20 行压至 4–8 行。
- **实测（30k bench 语料，v6.4.8 → v6.4.9）**：FTS5 分路 37.3→
  5.9ms（-84%）；mixed 全管线 37.6→6.8ms；病态 4.5ms。
- **100k 复测（1 千万字全管线，干净环境）**：病态 122→**0.4ms**
  （-99.7%）、mixed 134.9→**3.0ms**（-98%）、rare 6.8ms、
  combined 17.8ms、add 546/s——千万字库全部查询类型毫秒级。
- **守卫**：`tests/test_v649_fixes.py` 5 项——高频短语剔除契约
  （MATCH 表达式级断言）、低频不剪（召回零变化）、真实库 FTS5
  召回保持、病态回退 strategy 契约、版本钉。全量 **1258** 项通过
  （1257 passed + 1 环境性 skipped）。

## [6.4.8] - 2026-11-11

病态查询毫秒级攻坚版（v6.4.6 如实记录的「已知 O(N) 特性」本版消灭），
并揪出一个比性能更严重的**祖传索引失明 bug**。五刀 + 一个深坑修复：

- **刀 1（TF-IDF max_df 收紧 + 回退最短链优先）**：全库词查询触发
  v6.4.6 回退分支 `list(q.items())` 遍历全部倒排链 = O(N×查询维)，
  100k 实测 p50 209ms；且 0.5×N 的 max_df 阈值仍放半库词链
  （数万条目）进 active（mixed 查询 134ms 的第一构成）。修复：
  ① max_df 0.5×N → **0.05×N**（常见词点积贡献近似常数，top-K
  排序由区分维承载——100k 库 active 链长上界 5k）；② 回退语义从
  「全维度」改为「**最短链优先**」（按链长升序取 ≤3 维 = 查询中
  区分度最高的信号）；③ 单链遍历截断 5000 条（islice 免建副本）。
- **刀 2（fuzzy 候选集预算 + 区分度优先排序）**：fuzzy 的候选 SQL
  第 4 路 `LENGTH(content) < 4×len(query)` 对 16 字级中文查询失效
  ——短条目库全体进候选（30k 复现语料候选=全库，逐条反序列化+
  SequenceMatcher 实测 1642ms）。修复：候选加 `ORDER BY (content
  LIKE ?) DESC, rowid LIMIT 2000` 预算——精确子串命中（最强 fuzzy
  信号，打错字场景同路保真）排最前，短文本兜底路按 rowid 截断。
- **刀 3（fuzzy 触发门控收紧）**：触发条件从「TF-IDF 召回不足
  （positive < max_results）」收紧为「**TF-IDF 零有效命中**」。
  语义依据：fuzzy 的设计价值是拼写纠错兜底；TF-IDF 已有有效命中时
  fuzzy 补充的是 min_relevance 压线的词重叠条目——宁可少而准。
  病态回退（新信号 `index.last_fallback`）时同样跳过（全库词查询
  对 fuzzy 边际价值为零）。encrypted 模式不受影响（仍全跑供加密
  兜底复用）。空查询（query_norm=0）显式复位 fallback 信号，
  防止继承上一查询的 True 误杀拼写兜底场景。
- **深坑修复（增量文档 TF-IDF 失索引，比性能问题更严重）**：
  `index_memory` 的「前 5 条文档即 fit」把词表锁死在前 5 条的
  token——此后所有增量 add 的 transform 全部 vocab miss → **空
  向量入索引（文档对 TF-IDF 路不可见）**。影响面：v6.4.6 起所有
  100k 压测的 TF-IDF 路实际半瘫痪（延迟数字由 FTS5/fuzzy 扛着，
  rare_en 6.6ms 实为 FTS5 延迟）。修复：新增
  `TFIDFVectorizer.incremental_fit()`（单文档增量词表扩展：新
  token 补 vocab + df/idf 增量；旧文档向量不回算——新词在旧文档
  tf=0 无分量贡献，旧词 IDF 漂移为可接受近似），fit 后的增量 add
  先扩词表再 transform。LRU 三结构一致契约（v5.6.5 P1 #5）保持。
- **实测（30k 病态语料，修复前 → 后）**：病态查询稳态 p50
  3825→**4.0ms**（-99.9%）；mixed（稀有锚+常见词）全管线
  1607→**1.2ms**（-99.9%）；fuzzy 单路 1642→112ms（-93%）。
- **100k 复测（全管线真数字）**：病态 218→**122ms**（-44%）、
  rare 6.8ms / combined 17.2ms 持平、add 582/s。**诚实口径**：
  mixed 134.9ms 与 v6.4.6 的 143ms 表面持平，但含金量不同——v6.4.6
  的数字是 TF-IDF 索引失明（前 5 条 fit 深坑）时 FTS5/fuzzy 半管线的
  结果，v6.4.8 是 TF-IDF 真干活的全管线数字。剩余大头是 **FTS5 高频
  短语的全库行 bm25 评分**（30k 分路实测：FTS5 37.3ms vs tfidf 0.0ms
  vs fuzzy skip 后 full 37.6ms）——v6.5 刀位（ORDER BY rank top-k
  优化 / 查询侧短语预算）。
- **语义变化（诚实记录）**：全库词查询在 `mf.search` 层从「凑数
  返回 10 条」（旧行为由 fuzzy 词重叠兜底给分）变为「返回 0 条
  （相关度不足 min_relevance）」——数学上诚实（0.16 分=无区分度），
  IndexEngine 层回退契约仍保证非空 top-K。行为差异已由守卫钉住。
- **守卫**：`tests/test_v648_fixes.py` 9 项——遍历预算确定性契约
  （回退 ≤3×5000、active ≤0.05×N，不依赖 CI 机器速度）、回退
  最短链语义（独有信号置顶）、全库词非空、fuzzy 门控行为契约
  （有命中不跑/零命中必跑）、fallback 信号门面透传、版本钉值。
  全量 **1253** 项通过（1252 passed + 1 环境性 skipped）。

## [6.4.6] - 2026-11-10

规模验证版（三短板攻坚：基准分数 / 规模验证 / 规模证据入文档）：

- **100k 规模实测入档**：100,000 条记忆全量压测——add 吞吐 ~600 条/s
  （线性）、db ~98MB（~1KB/条）、查询延迟分档：英文锚 p50 7.4ms
  （两层剪枝后 −92%）、真实感混合查询（稀有锚+常见词）p50 143ms、
  病态全库词查询 421→218ms（−48%）、TF-IDF 降级模式
  进程内存峰值 ~700MB。**规模数据进 README**（此前仅有 200 条基线
  1.8ms，无规模档位数据——对外口径不完整）。
- **倒排索引两层剪枝 perf（真瓶颈修复）**：100k 压测发现中文长查询
  p50 421ms，profile 定位到低区分度词的倒排链全量遍历退化。修复：
  ① eps 相对阈值（qval/query_norm < 0.02 的维不遍历）；② max_df
  停用词（信息检索标准做法，df > 0.5×N 的全库词跳过——平滑 IDF 下
  qval≈tf×1 无区分度，全文档点积同加近似常数，top-K 排序不变）；
  ③ heapq.nlargest 替代全量 sort。**病态边界如实记录**：合成模板
  数据的全库词查询（词表熵过低）触发回退分支仍 O(N)——已知特性
  非缺陷，真实查询（含稀有词）剪枝有效（8k A/B 验证 + 30k 对照
  实测：混合查询 43ms vs 病态 86ms）。
- **检索质量基准入仓（`benchmarks/quality_bench.py`）**：可复现固定
  种子基准——needle 查询 Recall@1 = 1.0 / MRR = 1.0（2k 条）、topic
  多 gold 查询 Recall@1 = 1.0、干扰查询 0 误召回。**完整 LLM 判官
  基准（LoCoMo/LongMemEval）留接口未跑**（依赖外部 LLM API key，
  下版议题）。
- **规模基准入仓（`benchmarks/scale_bench.py`）**：延迟曲线/吞吐/
  体积可复现脚本，查询集区分「真实感」与「病态」档位。
- **守卫**：`tests/test_v646_fixes.py` 7 项——剪枝数学安全性（top-K
  保序、退化回退、常见词显著相关）、基准脚本随仓、LRU 上限文档化、
  版本钉值。全量 **1244** 项通过（1243 passed + 1 环境性 skipped）。

## [6.4.5] - 2026-11-10

审计驱动修复版（同日第二发；版本号 6.4.4 经维护者决策跳过，无对应
发布）。发版前全仓审计两类「文档-代码反向不一致」——v6.4.3 修的是
「宣称了没做」，本版修的是「做了没跟上」：

- **User-Agent 版本谎报（P1）**：`connectors.py` 对外 HTTP 请求的
  UA 硬编码 `MindForge-Connector/5.8.9`——5.8.9 时代写死后 20+
  个版本未跟上，一直在向目标站点谎报版本。`event_bus.py` 曾在
  v5.6.8 修过同款病（当时硬编码 5.4.9），此处漏网。修复：动态
  引用 `mindforge/core/version.py` 唯一真值（与 event_bus / CLI
  URL Importer 同款已验证模式）。
- **官网「核心模块」数过时（P2）**：正文 17 / 统计卡 15 互相矛盾，
  实测 `mindforge/modules/` 为 31 个模块文件。两处统一修正为 31；
  MCP 工具数 87 经 import 实测精确无误，不动。
- **审计存档（非缺陷）**：WAL auto-checkpoint 行为验证——四轮 ×
  400 次混合操作（add + search），WAL 稳定收敛于 ~4.1MB（默认
  1000 页阈值）无无限膨胀；主动 `wal_checkpoint(TRUNCATE)` 归零。
  v6.4.3 的 synchronous=NORMAL 在 daemon 长连接场景健康。
- **守卫**：`tests/test_v645_fixes.py` 5 项——UA 动态形态 + 全包
  运行时标识扫描（防再硬编码）、官网模块数 = modules/ 实测钉值、
  官网 MCP 工具数 = TOOL_SCHEMAS 实测、版本钉值。
  全量 **1233** 项通过（1232 passed + 1 环境性 skipped）。

## [6.4.3] - 2026-11-10

文档-代码一致性修复版：v6.4.2 宣称的「WAL + synchronous=NORMAL」
从未落地，本版补齐。

- **问题**（发版前全仓审计发现）：v6.4.2 的 CHANGELOG / README /
  官网与 commit message 均宣称五刀第一刀「WAL +
  synchronous=NORMAL（commit 免逐次 fsync）」已上线；实测
  `_get_conn()` 仅设置 `journal_mode=WAL`（v5.5.7 起即有），
  `synchronous` 维持 SQLite 默认 FULL——**该优化只存在于文档里**。
  v6.4.2 实测的 search 2.1× 由其余四刀独立达成：延迟数字真实、
  归因失真。
- **修复**：WAL 分支补 `PRAGMA synchronous=NORMAL`。synchronous 是
  per-connection 参数，已在每连接创建点设置；网络 FS 降级 DELETE
  journal 的分支维持默认 FULL（远程盘耐久性语义从严，不做削弱）。
- **实测**（A/B 同机同时段，400 条 / 4.7 万字，各 3 轮取中位）：
  search p50 1.8 → 1.8ms、add p50 0.4 → 0.4ms——SSD 快态下无统计
  显著差异（fsync 开销 ~0.1ms 量级，叠入 search 尾单事务不显著）。
  收益面在慢盘 / 高 IO 负载容器环境；更核心的是语义兑现：v6.4.2
  文档声明的掉电窗口（未 checkpoint 的访问计数 / search 审计可能
  丢失，进程崩溃不丢事务，合规类审计仍即时落盘）自此真实成立。
- **守卫**：`tests/test_v643_fixes.py` 4 项——WAL 分支
  `synchronous==1`、`journal_mode==wal`、跨线程新连接同样 NORMAL
  （REST / daemon 多线程场景防静默回退）、版本钉值。
  全量 **1228** 项通过（1227 passed + 1 环境性 skipped）。

## [6.4.2] - 2026-11-09

万字级延迟压深版。目标：几万字库 search 延迟向 1ms 逼近。同机 A/B
实测（400 条 / 4.7 万字，各 3 轮取中位，bench_scale.py）：
**search p50 6.3→3.0ms（2.1×）、p95 11.7→4.2ms（2.8×）**；2.4 万字
小库 p50 2.5ms；机器快态（IO 冷缓存刷净后）p50 1.8ms。add 全程无
退化。剩余 ~2.5ms 为每查询固定开销（7.5 次 SQL 往返 + 行反序列化 +
TF-IDF/FTS 打分本体），压入 1ms 属 v6.5 架构活（结果缓存/SQL 合并）。
手段（五刀，全部幂等可回退）：

- **WAL + synchronous=NORMAL**（SQLite 官方推荐组合）：commit 免逐次
  fsync，checkpoint 统一刷盘。进程崩溃不丢事务；仅主机掉电可能丢未
  checkpoint 的访问计数/search 审计（均非合规数据，与 v5.6.3 挂账
  哲学一致）。
- **枚举降级 memo**（`_ENUM_DOWNGRADE_MEMO`）：行反序列化每行 4 次
  枚举逐值匹配 → dict 命中。值域天然有界（成员数 × 类数）；Enum
  单例不可变共享安全；跨模块路径同名类身份不同，key 天然隔离。
- **`_row_to_entry` 列集合复用**：每行 5 次 `row.keys()` 分配 + 线性
  `in` 扫描 → 单次 set 构造。
- **首访访问计数挂账 + search 尾批量 flush**（`get_memory(_batch_access)`
  分流）：批量检索上下文首访只挂账，QueryEngine.search 尾部
  `flush_access()` 单事务 executemany 统一落库；直连 `get_memory`/
  `mf.get()` 首访仍立即落库——**v5.6.3 节流契约与 v5.5.4
  recently_accessed 排序语义均保持**（测试钉住：23 项契约测试全绿）。
- **search 审计 INSERT 合并进批量事务**：`record_search` 挂账
  （`commit=False`，v5.6.0 既有机制），与 flush_access 的 UPDATE 同
  一次 commit 提交。search 审计为非合规数据（v6.2.0 prune 同一分类），
  崩溃丢失窗口 ≤ 单次 search 时长；合规类审计（写/删/导出）不受
  影响仍即时 commit。

全量 1224 项通过。排查（修 bug 面）：cProfile 三轮热点定位未发现存量
功能 bug；分批 893 项 + 契约 53 项全绿。过程中发现 v6.4.2 开发自身
引入的一处方法名笔误（`flush_all_access` → `flush_access`，AttributeError
被兜底 except 静默吞掉导致 search 尾部批量落库从未执行）——已修复并
将兜底语义收紧为显式调用。

## [6.4.1] - 2026-11-09

百万字毫秒级检索修复版。规模化实测（8000 条 / 98.7 万字）发现 fuzzy
兜底路严重长尾：search p95 881ms——根因两层：① SequenceMatcher 对
「短 query vs 长文本」白跑（ratio = 2M/(m+n) 且 M ≤ m，n ≥ 4m 时
数学上不可能超过 0.4 门槛，v5.6.3 剪枝对中文失效）；② Python 逐条
反序列化+打分 ~50μs/条 的全库税。修复：

- **数学上限剪枝**：content 长度 ≥ 4×query 时直接跳过 SequenceMatcher
  （语义无损——该比对本来就不可能过阈值）；中长文本退回 bigram 门槛。
- **SQLite 层候选集预筛**：子串 LIKE（content/category/tags）∪ 短文本
  LENGTH 条件 ∪ 中文 tag 字符 LIKE（拼写容错保真，钉测
  test_tag_fuzzy_match_kept）；词级召回归还 FTS5 路（消除重复劳动）；
  英文 query 不走逐字 LIKE（字母撞 tag 曾把候选炸回全库、p50 回 218ms）。
- **实测收益**：search p50 33.8→22.5ms，**p95 881→30.7ms（28×）**，
  max 910→67ms；add p50 2.6ms 全程无退化（万条级写入稳定）。
- **守卫固化**：scripts/bench_scale.py 入库（MF_SCALE_N 环境变量控
  规模，CI 3000 条档），CI 新增 Scale guard 步骤（search p95 < 150ms
  契约）；新增 4 项行为级回归测试（子串召回/近似语义/候选集不退化/
  毫秒级契约），全量 1224 项通过（1219+4 passed、1 skipped）。

## [6.4.0] - 2026-11-08

常驻守护进程（daemon）与 CI 性能守护版。延迟实测（2026-11-08）：
引擎直连 add p50 2.0ms / search p50 2.9ms，但 CLI 子进程路径
p50 698ms——差距全部来自 Python 解释器启动税。daemon 把引擎
实例常驻在长生命周期进程里，宿主通过行式 JSON over stdio 调用。
全量 1220 项通过（1219 passed + 1 skipped，无新增 pytest 项）。

### 新增：常驻守护进程 `mindforge daemon`

- 行式 JSON 协议：每行请求 `{"id","cmd","args"}`，每行响应
  `{"id","ok","data"|"error"}`；stdout 纯净无横幅污染，日志走 stderr。
- 单请求延迟 p50 2.5ms（对照 CLI 子进程 698ms，约 280 倍）。
- 命令集 v1：add / search / memory-recall / memory-context / stats /
  compress / compress-list / decompress / adaptive-search，输出契约
  与 CLI `--json` 完全对齐（同一套键名，宿主可无缝切换）。
- 生命周期：`ping` 握手（含 pid/uptime）、`shutdown` 优雅退出、
  EOF 退出；单请求异常不杀进程。

### 新增：CI 性能守护（性能回归也是回归）

- `scripts/bench_latency.py`：引擎 in-process 基准进 CI，断言
  add p95 < 50ms、search p50 < 30ms、search p95 < 150ms（阈值≈实测
  10 倍余量，容忍 CI 抖动、拦数量级退化）。
- `scripts/e2e_daemon.py`：daemon 13 步真实子进程守护（协议握手 →
  种子 → 检索链 → 压缩链 → adaptive → 延迟契约 p50 < 50ms），
  与 `scripts/e2e_cli.py` 共用 P0/P1 历史缺陷断言。

### 新增：CLI E2E 守护（v6.3.6 后补强）

- `scripts/e2e_cli.py`：16 步真实子进程调用进 CI（v6.3.6 引入，
  此前未提交），每步断言钉住历史实测缺陷。

## [6.3.6] - 2026-11-07

E2E 实测修复版。对 dsh-mindforge v3.0 插件做真实桥接测试
（15 工具逐一真 CLI 调用）时发现 5 个缺陷，本版全部修复。
测试规模 1210 → 1220（新增 10 项 v6.3.6 回归），全量 1220 项通过。

### 修复：记忆压缩在真实库上恒为 0 簇（P0）

`MemoryCompressor.auto_compress` 依赖 `IndexEngine._doc_texts` 提供
候选，但门面/CLI 的搜索主路径走 FTS5，索引从未被填充——
`find_similar_clusters` 静默返回空列表，**compress 在任何真实
库上都报告 0 簇**。单元测试用手工填充的索引未能暴露此问题。
修复：空索引时从 storage 现场加载明文记忆并索引
（`_hydrate_index_from_storage`，过滤 trash/已压缩源/STRICT，
上限 10000 条）。实测 9 条种子库在低阈值下聚出 2 簇，管线全通。

### 修复：四个 v6.3.5 命令无视 --json（P0）

`compress` / `compress-list` / `decompress` / `adaptive-search`
的 parser 挂了 `json_parser`，但 handler 只打印人类格式——插件
与脚本集成只能拿到装饰字符文本，无法解析。四个 handler 补齐
`json_output` 分支（decompress 未挂 json_parser，主 parser 的
前置 `--json` 同样生效）。

### 修复：adaptive_search 恒为空结果（P1）

门面 `adaptive_search` 把 `session_id`（会话统计维度）透传给
`search` 的隐私访问控制。INTERNAL 级记忆（写入默认级别）对
非空 session 一律精确匹配 `source_session`（v5.7.3 防越权语义），
而记忆通常无来源 session——**自适应检索在默认配置下永远返回
空结果**。修复：不再把 session_id 传给访问控制，会话统计
（`record_search`）照常记录。实测同查询从 0 条恢复至 3 条。

### 修复：key_file 空串误判加密模式（P2）

`_get_memory` 对 `key_file=""` 执行 `Path("")` 解析为 cwd
（恒存在），明文库被误判为加密库并索要密码。空值现在跳过
存在性检查。库用户以空串显式关闭加密时不再触发。

### 说明：压缩阈值与中文语义相似

TF-IDF 压缩为**字面相似**（字 bigram + 词级）。中文同义改写
（如"喜欢/偏好/爱用"）两两相似度实测仅 0.12-0.21，默认阈值
0.75 是 fail-closed 设计（宁缺勿误聚，误聚可用 decompress 还原
但消耗位序）。CLI help 已注明：中文近重复建议 0.30-0.45；
语义相似需安装 sentence-transformers 启用向量召回。


## [6.3.5] - 2026-11-07

记忆压缩与自适应上下文。两个新模块（MemoryCompressor、
AdaptiveContextEngine，作者于 2026-11-06 夜间提交，本版完成集成与
安全修复）、一项除零修复，以及 CLI 四条新命令。工具数不变（87，
新能力先接门面与 CLI 层，MCP/REST 暴露列入下版计划）。测试规模
1180 → 1210（功能 23 项 + 集成 7 项）。

### 版本号勘误

作者夜间提交时将版本写为 6.1.5——低于已发布的 6.3.2（版本倒流），
触发全仓版本守卫 7 项红灯。本版重订为 **6.3.5**（新增功能本应为
minor 号，从作者指定值）。

### 新增：记忆压缩（`mindforge/modules/memory_compressor.py`）

- **聚类检测**：TF-IDF 余弦相似度贪心聚类（`find_similar_clusters`），
  阈值 / 最小簇 / 最大簇 / 类别过滤可调
- **摘要生成**：簇内关键词（TF-IDF top-8）+ 首句要点抽取
- **压缩语义**：源记忆打 `compressed_into` 标记后**不再独立参与召回**
  （由摘要代表，检索管线在 chunk 构建点过滤，对 query / recall /
  CLI / REST / MCP 全路径生效）；`decompress` 一步还原
- **隐私红线**：STRICT 记忆永不参与压缩（自动跳过并计数）
- **dry_run**：只报告将压缩的簇，不动数据

### 修复：压缩写路径五项集成缺陷（P1，含一项安全缺陷）

作者原实现（v6.1.5 草稿）直插 memories 表：
1. `created_at` 以 TEXT 写入 REAL 列（类型错配）
2. FTS 索引不同步——摘要对 FTS5 检索路**不可见**
3. **加密库明文直插**（安全缺陷：摘要绕过加密层直写明文行）
4. 不生成嵌入向量——向量路不可见
5. 不落审计日志

重写为走存储层标准通道：摘要经 `add_memory`（id 分配 / FTS / 嵌入 /
审计自动正确），源标记经 `update_memory`（读-合并-写回），解压时
摘要经 `delete_memory` 级联清理（FTS / 嵌入 / 子表）。加密库在
`auto_compress` / `compress_group` 入口即显式拒绝（防明文直插）。

### 修复：`_keyword_search` 空文本 ZeroDivisionError（Bug-2）

`IndexEngine._keyword_search` 遍历 `_doc_texts` 时空文本触发除零
崩溃。修复：空文本 `continue` 跳过（一行守卫，作者提交，保留原样）。

### 新增：自适应上下文引擎（`mindforge/modules/context_window.py`）

- 会话窗口追踪查询模式（QueryPattern：时间戳 / 结果数 / 命中类别）
- **自适应阈值**：连续快速查询（≥5 次/30s，探索模式）→ 阈值降
  0.08 多召回；长时间空闲（>60s，聚焦模式）→ 阈值升高精准召回
- 有界调整：floor 0.15 / ceiling 0.5，防失控

### 接线（v6.3.5 完成：门面 + CLI）

- 门面：`compress_memories()` / `list_compressions()` /
  `decompress_cluster()` / `adaptive_search()`
- CLI：`compress [--dry-run --threshold --category --min-cluster-size]`、
  `compress-list`、`decompress <cluster_id>`、`adaptive-search <query>`
- MCP / REST 暴露：列入下一版本计划（保持三层一致暴露承诺的推进）

### 测试

- `tests/test_v635_features.py` 23 项（作者提交，原名
  test_v615_fixes，随版本勘误更名；行为契约层面全部保留通过）
- `tests/test_v635_integration.py` 7 项：压缩后源隐藏/摘要可见、
  解压完整还原、dry_run 零写入、加密库入口拒绝、STRICT 排除
  （存储层通道）、CLI 冒烟
- 全量 **1210** 项通过（`pytest tests/` 收集数与通过数一致）。


## [6.3.2] - 2026-11-06

检索可解释性全接口对齐。v6.3.0 把 `search --explain` 给了 CLI 与
Python 门面，但 REST 与 MCP 两层未暴露——违背项目「三层接口一致
暴露」的自身承诺。本版补齐：MCP `memory_search` 与 REST
`/api/search` 均支持 explain。工具数不变（87，参数加在既有工具上），
测试规模 1172 → 1180（新增 8 项接口回归）。

### MCP：`memory_search` 新增 `explain` 参数

- schema 增加布尔参数（默认 `false`，描述注明零开销）
- handler 透传至门面 `search(..., explain=)`；`explain=true` 时每条
  结果携带 `score_breakdown`（四路召回原始分、`winner` 胜出路径、
  `final` 最终分、双命中时 `fusion` 融合明细）
- 非布尔值按 `false` 处理（类型安全，不炸）
- **不传 explain 时响应键集与 v6.3.1 完全一致**——不新增 null 键，
  既有客户端零感知

### REST：`/api/search` 新增 `explain` 查询参数

- `?explain=1/true/yes/on` 开启，其余值（含缺省）关闭
- 开启时每条结果携带 `score_breakdown`；响应顶层结构不变
- 路由文档字符串同步（`?q=&limit=&explain=`）

### 测试

- 新增 `tests/test_v632_explain_interfaces.py` 8 项：MCP schema
  声明、explain off/on 键集与分解字段完整性（final 与 winner 一致）、
  非布尔容错、REST 缺省/`0`/`1`/`true` 四种参数行为。
- 全量 **1180** 项通过（`pytest tests/` 收集数与通过数一致）。


## [6.3.1] - 2026-11-06

加密系统升级 + 安全加固 + 门面 rekey 修复。三件事：默认 KDF 从
PBKDF2 升级为 scrypt（内存难解，抗 GPU/ASIC 并行暴力破解）、修复
门面 rekey 自 v5.8.13 起不可用的死路径、cryptography 依赖地板升至
50.0.2。工具数不变（87），测试规模 1149 → 1172（新增 22 项 scrypt /
rekey 回归与 1 项 PBKDF2 兼容语义钉，另有 4 项 v5.6.0 KDF 测试改钉新默认值）。

### 加密系统升级：默认 KDF = scrypt（N=2^16, r=8, p=1）

- **为什么**：PBKDF2 无论迭代多少次都可被 GPU/ASIC 高度并行暴力
  破解；scrypt 是内存难解函数（本次参数 64 MiB 内存墙），攻击者
  每秒可尝试的密码数被内存带宽而非算力限制，离线暴破成本提升
  数个量级。实测单次派生约 0.3s（进程启动一次的开销）。
- **库层**：`KDFParams` 扩展 `n / r / p` 字段，`algorithm` 支持
  `"scrypt"`；`from_password()` 新增 `kdf_params` 参数按算法分派
  派生（`_derive_key` 统一分派器）；`_get_crypto()` 懒加载元组增加
  `Scrypt`（第 4 位，下标兼容）。
- **默认值变化**：`KDFParams()` / `from_password()` 不带参 /
  `init_engine` 新建密钥文件 / `rekey`（不指定 `new_iterations`）/
  加密导出——全部默认 scrypt。**显式传 `iterations`（非 None）仍为
  PBKDF2 兼容路径，行为与旧版一致**。
- **密钥文件**：格式版本 `5.6` → `6.3`（`kdf_params` 含 n/r/p）。
  历史密钥文件（PBKDF2 600k / 60k / 仅 iterations 极旧格式）解密
  兼容不变——升级后旧库照常打开，无需任何迁移。
- **加密导出**：payload `version` 1 → 2，`kdf` 字段携带完整
  KDF 参数（含 n/r/p）；导入端按 `algorithm` 分派派生，v1（PBKDF2）
  与仅 iterations 的极旧导出均可继续导入。注意：v2（scrypt）导出
  文件无法被 ≤6.3.0 的旧版本导入（派生参数缺失，报解密失败）。
- **rekey**：密码不变仅升级 KDF 时，默认升级到 scrypt——
  「rekey 即 KDF 迁移通道」，旧 PBKDF2 库一步换到内存难解派生。

### 修复：门面 `MindForge.rekey()` 自 v5.8.13 起不可用（P0）

- 症状：包安装模式下调用门面 rekey 必然抛
  `ModuleNotFoundError: No module named 'core'`——密钥文件写入前
  的 `_write_key_file = __import__("core.encryption", ...)` 用的是
  v5.8.13 命名空间迁移前的顶层路径。v5.8.13（2026-10-02）起顶层
  `core.*` 已不存在，该行成为死代码；既有测试只覆盖模块级
  `rekey_engine`，门面路径漏网 1149 项测试全绿。
- 修复：改为 `from mindforge.core.encryption import _write_key_file`
  包内绝对导入；同时门面旧密码验证路径改传完整 `kdf_params`（原
  `iterations=` 强制 PBKDF2，scrypt 密钥文件会误报「旧密码错误」）。
- 回归：`tests/test_v631_scrypt.py::TestFacadeRekeyFixed` 端到端钉死
  （创建 → 加数据 → rekey → 新密码下读回）。

### 安全加固

- **cryptography 依赖地板 50.0.1 → 50.0.2**（Dependabot #17），
  `requirements.txt` 锁定同步；pyproject 与 CI 安装路径一致。
- `_derive_key` 派生失败统一转 `SecurityError`，不向调用方泄露
  KDF 参数细节。
- 门面 `get_encryption_info()`：`is_current` 判定改为与当前默认
  KDF（scrypt）全参数比对（原 `iterations == 600000` 对 scrypt 密钥
  文件恒为 False）；返回值新增 `kdf_params` 完整字段。
- CLI `rekey` 命令状态区与结果区改按算法展示（scrypt N/r/p vs
  PBKDF2 迭代次数），移除对 scrypt 无意义的「迭代次数提升 %」展示。

### 测试

- 新增 `tests/test_v631_scrypt.py` 22 项：scrypt 默认值与往返、
  密钥文件 v6.3 创建/重载/错密码、三类历史密钥文件兼容
  （600k / 60k / 仅 iterations）、`rekey_engine` 升级路径与显式
  iterations 兼容路径、门面 rekey 端到端与错密码拒绝、
  `get_encryption_info().is_current`、加密导出 v2/v1/v0 三代
  payload 导入兼容、错密码导入干净失败。
- 更新 `tests/test_v560_kdf_versioning.py` 4 项：默认值断言从
  PBKDF2-600k 改钉 scrypt（`test_default_values`、
  `test_from_password_carries_kdf_params`、
  `test_init_engine_creates_current_params`、
  `test_rekey_engine_upgrades_iterations`），并新增
  `test_from_password_explicit_iterations_is_pbkdf2` 钉死兼容语义。
- 全量 **1172** 项通过（`pytest tests/` 收集数与通过数一致）。


## [6.3.0] - 2026-11-05

检索可解释性（search --explain）。六路混合检索一直是黑盒：用户只看到
最终排名，看不到「为什么这条排第一」。本版把融合过程拆开给你看。
工具数不变（87），测试规模 1143 → 1149（新增 6 项 explain 回归）。

### 新增：检索得分分解（`search --explain`）

- **库层**：`QueryEngine.search(..., explain=False)` 新增 explain 参数；
  `MemoryChunk.score_breakdown`（默认 `None`，序列化兼容）携带：
  四条召回路（tfidf / fts5 / fuzzy / vector）各自的原始分数、
  最终胜出路径 `winner`、最终分 `final`；文本+向量双命中时附带
  加权融合明细（`fusion: {text, vector, text_weight, vector_weight,
  fused}`）。explain=False 时热路径零额外开销。
- **Facade**：`MindForge.search(..., explain=False)` 透传。
- **CLI**：`mindforge search "查询词" --explain` 每条结果追加
  「路径: winner | tfidf 0.35 | fts5 0.50 | fuzzy 0.80 | vector 0.00
  | final 0.80」；JSON 模式（`--json --explain`）输出含
  `score_breakdown` 字段。
- **适用场景**：检索质量调试（某条记忆为什么排第一/为什么没召回）、
  权重调优验证、检索教学的直观教具。

### 测试

- 新增 `tests/test_v630_search_explain.py` 6 项：默认 None 兼容、
  四路字段与 winner/final 齐全、final 与胜出分一致、tfidf 分与
  单路召回一致、facade 透传、CLI 文本/JSON 双模式输出。
- 全量 **1149** 项通过（`pytest tests/` 收集数与通过数一致）。


## [6.2.0] - 2026-11-05

可复现基准 + 审计增长治理。本版聚焦「让性能结论可信、让长期运行不腐化」：
把等参数基准方法学收编成正式工具，给 audit_log 装上增长刹车。发布前第三轮
全面审计（吞错模式 64 处逐一核对、并发共享态、双时间戳边界、刁钻输入
炮弹）未发现 patch 级缺陷，故按语义发 minor。工具数不变（87），测试规模
1136 → 1143（新增 7 项治理回归）。

### 新增：等参数可复现基准（`benchmarks/repro_benchmark.py`）

背景：v6.1.3 发布当天抓到一个典型基准伪影——「加密写入 616 条/s vs
明文 526 条/s」的假对比，真因是两侧语料规模与预热状态不同，叠加
「加密库刻意不维护 FTS5 索引」这一真实机制。本工具把正确方法学固化：

- **明文 vs 加密写入对照**：同 N、同内容模板、交替各两轮取均值，
  消除顺序偏差与缓存状态差；
- **检索延迟分位**：同查询词 50 轮采样，报 P50/P95（关键词/中文/
  未命中/模糊/加密库五路）；
- **并发压测**：8 线程 × 50 条，吞吐与错误数；
- **孤儿修复验证**：注入 → 修复 → 复查，报残留数。

`--quick` 减半采样、`--json` 机器可读输出；引用口径约定写在文件头，
杜绝「数字脱离环境被引用」。

### 新增：audit_log 增长治理（搜索审计去抖 + 裁剪）

问题：`record_search` 每次搜索落一条审计，交互式会话下 audit_log 以
每小时数万行的速度膨胀，且无任何清理路径（v6.2.0 发布前的第三轮
审计发现）。三件套治理：

- **写入端去抖**：同一查询词 60 秒窗口内只落一条审计（线程安全，
  词表超 1024 清最旧一半）；搜索历史的 count 语义随之从「检索调用
  次数」修正为「独立查询会话次数」。合规类审计（写/删/导出/GDPR）
  不采样、不裁剪，留痕义务完整保留。
- **`prune_audit_log(keep_days=90)`**：存储层裁剪 API，默认只裁
  `search` 流水；`actions=[]` 为显式无操作守卫。
- **CLI `audit-prune --days N [--dry-run]`** + **doctor 第 7 项
  检查**（`[7/7] 审计日志增长`，超 50000 行提示裁剪建议）。

### 测试

- 新增 `tests/test_v620_growth_governance.py` 7 项：去抖落一条/词表
  防御回退、裁剪只动 search 且保留 400 天前的合规审计、keep_days≤0
  无操作守卫、doctor 第 7 项健康路径、CLI dry-run/实裁。
- 全量 **1143** 项通过（`pytest tests/` 收集数与通过数一致）。


## [6.1.3] - 2026-11-05

数据完整性收口 + 被遗忘权补全。本版做三件事：给 v6.1.2 级联删除修复
补上「历史脏数据」的修复工具（代码修了，存量数据也得能修）、堵上
GDPR 删除权的数据残留缺口、修掉预览通道的序列化缺陷。工具数不变
（87），测试规模 1130 → 1136（新增 6 项完整性回归）。

### 修复：gdpr_erase_all 被遗忘权数据残留（隐私缺陷）

问题现场：`gdpr_erase_all`（被遗忘权）只清理 memory_links /
memory_versions / audit_log / archived_memories / memories 五张表，
**memory_embeddings / memory_notes / review_schedules /
memory_evolutions / graph_relations 全部残留**。嵌入是从记忆内容
派生的向量数据——GDPR 视角下同样属于个人数据，「名义上已行使删除权、
实际派生数据仍残留」构成合规缺口；孤儿图谱关系与复习计划则持续污染
图查询与调度。修复：清理列表补齐上述五表。

### 修复：预览通道 tags 以 Python repr 落库

`MemoryPreviewPort.snapshot_memory` 用 `str(entry.tags)` 存标签，
产出 `"['a', 'b']"` 这类 Python repr——下游 `json.loads` 必然失败，
与主库 tags 列的 JSON 约定不一致。修复：改用 `json.dumps`。

### 修复：doctor 文案错误指引

FTS 孤立记录的建议写的是「运行 vacuum 命令」，但 `--fix` 的实际
修复动作是 `rebuild_fts()`。文案已更正。

### 新增：doctor 孤儿数据检测与修复（`repair_orphaned_data`）

背景：v6.1.2 之前级联删除存在 4 处 bug（kg_edges 表名错误、
passport 裸删、agent_purge FTS 空值删除、模板死代码），历史硬删除
留下的脏数据不会自愈。本版在存储层新增
`repair_orphaned_data(dry_run)`，并在 `doctor` 中作为第 6 项检查
集成（`--fix` 即修复）：

- **图谱修复**：`graph_relations.memory_ids` 中指向已删除记忆的
  失效引用被移除；支撑记忆全部失效的关系整体删除；原本就为空数组
  的关系不受牵连（只修复「可证明发生过引用失效」的行）；孤儿实体
  保守不删（实体可被独立创建）。
- **子表孤儿行清理**：memory_versions / memory_notes /
  memory_embeddings / review_schedules / memory_evolutions /
  memory_links 中引用不存在记忆的行——孤儿嵌入一并清理，不留
  派生数据残留。
- **dry_run 模式**：默认只统计不动数据，便于评估影响面。

### 测试

- 新增 `tests/test_v613_integrity.py` 6 项：gdpr 全表清理、tags
  JSON 序列化、repair 的 dry_run 不动数据 / 移除失效引用并保留
  部分有效关系 / 空关系删除而原生空数组保留 / 干净库零报告。
- 全量 **1136** 项通过（`pytest tests/` 收集数与通过数一致）。


## [6.1.2] - 2026-11-02

发布链路补全 + 静默错误集中修复。本版做三件事：把 tag → GitHub Release 变成
自动的（修掉「代码到 v6.x、Releases 页 Latest 还停在 v5.8.13」的漂移根因）、
清掉一批**不崩溃但给出错误答案**的缺陷、并把三个「写在文档里却从未被实现」的
参数真正接通。工具数不变（87），测试规模 1063 → 1130（新增 57 项回归，
另有发布后审计补充的 10 项级联删除回归）。

### 新增：tag → GitHub Release 自动化（`.github/workflows/release.yml`）

问题现场：README 与架构图已写到 v6.1.1，git tag `v6.0.0` / `v6.1.0` / `v6.1.1`
也都在远端，但 GitHub Releases 页面上标注 Latest 的正式版本仍是 v5.8.13
（10 月 3 日），全部只有 4 个 Release。外部读者从 Releases 页判断，会认为
最新版是 v5.8.13。

根因不是忘了点按钮，而是**仓库里没有任何创建 Release 的 workflow**：
唯一的发布类文件 `publish-pypi.yml` 触发条件是 `on: release: published`，
即它**依赖 Release 已经存在**，而不是创建 Release；Release 全靠人工在网页上
手建，于是打 tag 与发 Release 这两步之间的漂移反复发生。

- 新增 `release.yml`：推 `v*` 标签即执行
  1) 校验 tag 与 `core/version.py` 的 `__version__` / `VERSION_INFO` 一致；
  2) 校验 `CHANGELOG.md` 中存在对应版本小节（否则 Release Notes 会是空的）；
  3) 运行 `scripts/check_docs_consistency.py` 文档守卫；
  4) 从 CHANGELOG 抽取该版本小节作为正文，`gh release create --latest`
     创建（已存在则 `release edit` 更新，重跑幂等）。
- Release 创建后，`publish-pypi.yml` 才真正接力，形成
  tag → Release → PyPI 的单链路。实际首跑发现一个平台限制：
  `release.yml` 用 `GITHUB_TOKEN` 创建的 Release，其 `release` 事件会被
  GitHub 抑制（防递归设计），不会触发其他 workflow；因此 `publish-pypi.yml`
  改挂 `workflow_run`（Release 工作流成功完成、且该 run 由 `v*` 标签触发时
  才发布；补发历史版本的 dispatch run 以 master 为 head_branch，不会误发），
  并内置「PyPI 已有同版本则跳过」的幂等守卫（重打标签重跑也不会 409）。
  手动在网页上发布 Release 时仍由 `release: published` 触发。
- `release.yml` 同时支持 `workflow_dispatch` 手动填 tag，用于补发历史版本
  （补齐 v6.0.0 / v6.1.0 / v6.1.1 即走这条路；v6.1.2 由标签推送自动创建）。
  补发时会 checkout 到该 tag 本身（版本/CHANGELOG 校验与 Release 正文都取自
  那个版本自己的代码与文档），跳过文档守卫重放（当年发布时其时代的 CI 已
  验证过，重放旧 commit 只会引入工具链漂移的失败面），且不带 `--latest`——
  补发历史版本不会把 Latest 从当前版本抢走；Release 已存在时直接跳过，
  连 edit 都不做，避免降级既有 Release。
- 相应地，`publish-pypi.yml` 移除 `on: push: tags` 触发：那条触发本是
  「Release 没人创建」时的兜底，现在 tag 已能自动生成 Release，两者并存会让
  同一版本被两条流水线重复上传，第二条必然被 PyPI 以 409 拒绝，留下红色记录。
  需要绕过 Release 直接补发时，改用该 workflow 新增的手动 `workflow_dispatch`。
- 首次真实触发暴露一处 CI 配置缺陷：校验 job 只安装 `requirements.txt`
  （仅 cryptography），而文档守卫第 [4] 组需要 `pytest --collect-only`
  核对测试数，导致校验失败、Release 未创建。已改为同时安装
  `requirements-dev.txt`（与 `ci.yml` 的 docs job 同口径），
  v6.1.2 标签指向修复后的提交。

### 修复：不崩溃、但答案错误的一批（静默失效）

这类缺陷比崩溃更难发现——接口正常返回，返回值却是错的。

- **多模态过滤恒空 / 不过滤即崩**（`modules/multimodal.py`）：
  `entry.memory_type` 是 `MemoryType` 枚举，代码却拿它和 `[t.value for t in
  content_types]` 这个字符串列表比对，枚举永远不等于字符串，传了 `content_types`
  就全部丢弃；不传时又走到 `MultimodalType(枚举对象)` 抛 ValueError。
  现在两侧统一归一到 `.value`，无法识别的类型按 TEXT 暴露而不是丢结果。
- **重排不支持 dataclass**（`modules/hybrid_search.py`）：`rerank()` 其余字段
  一律经 `_get_field()` 读取（v5.4.7 H-5 明确承诺「支持 dict 和 dataclass」），
  唯独 `memory_id` 直接 `c.get(...)`，传 `MemoryChunk` 在首个候选即
  AttributeError——对外承诺的 dataclass 支持此前一直是失效的。
- **嵌入可用性恒为 False**（`modules/v6.py`）：`incremental_index_stats()`
  调的是 `storage.get_embedding_status()`，而那是**门面**上的方法，
  `StorageEngine` 并没有；AttributeError 被宽 except 吞掉，于是健康报告里
  「嵌入后端就绪」这一项永远显示不可用。改读 `storage.embedding_engine.is_available`。
- **技能列表不是全局 top-N**（`modules/experience.py`）：`list_skills()` 先按
  插入顺序截前 `limit` 条、再排序，返回的是「插入序前 N 名内部有序」；
  置信度更高但入库较晚的技能会被整批漏掉。改为先排序后截断。
- **时间线活跃数失去意义**（`modules/timeline_viz.py`）：读
  `getattr(m, "is_archived", False)`，但 `MemoryEntry` 没有该字段（本项目的
  「归档」实为 `category == 'trash'`，`list_memories` 早已排除），
  于是 `active_memories` 恒等于 `total_memories`。改用真实信号：过 `valid_to`
  边界的不再计入活跃。
- **遗忘 GC 的噪音关键词元组重复**（`modules/principled_forgetting.py`）：
  `("工作","喜欢","住在","住在","是","目标")` 里 "住在" 写了两遍（笔误），已去重。
- **文档口径与实现不符**（`modules/conflict_detector.py`）：docstring 写的
  加减分与常量不一致，改为 `+0.08 (auto_boost_resolve_winner)` /
  `-0.20 (auto_decay_resolve_loser)`。

### 修复：崩溃与数据损坏

- **MCP 把失败当成功返回**（`mcp/server.py`）：`tools/call` 无论工具成败都返回
  同一个成功信封，客户端只能解析文本才知道失败，自动化流程会以为写入成功。
  新增 `_is_tool_error()`（只认显式 `ok`/`success is False`，避免误判正常结果），
  失败时在信封上置 `isError: true`（JSON-RPC 层仍为成功，符合 MCP 规范）。
- **传 `add()` 返回值即崩**（v6.1.1 同类缺陷收口）：`save_version` /
  `save_evolution` / `evolve_memory` / `list_evolutions` / `list_versions` 五处
  都写着 `if not memory_id or len(memory_id) > 128`，把 `MemoryEntry` 传回即
  `TypeError: object of type 'MemoryEntry' has no len()`。v6.1.1 为三个入口
  各写了一份私有归一函数，本版收口为 `core/types.coerce_memory_id()` 单一实现，
  门面 `evolve_memory` 也一并归一（此前它在 `get_memory` 处就抛 ValueError）。
- **非字符串 content 写坏数据后才崩**（`core/storage.py`）：`evolve_memory`
  把 dict 交给 `_strip_control()`，被静默 `str()` 成 Python repr 写进库；
  随后 `save_evolution` 里 `to_preview[:200]` 对原始 dict 切片抛 TypeError，
  该内部异常串又被当作对外 error 返回——形成「内容已被改写、却又报失败」的
  半途失败。现在校验阶段直接拒绝（`content 必须是字符串，收到 dict`），
  并保证原内容不变、不留 evolution 记录。
- **经验链路对外 500**（`modules/experience.py`）：`record_case` 的
  `task/content/result/agent_id` 直传 SQLite 绑定，非字符串抛
  `sqlite3.ProgrammingError`——它既不是 ValueError 也不是 TypeError，
  REST/MCP 的 400/友好错误分支都接不住。现统一 ValueError；None 按空串处理，
  非字符串一律拒绝而非 `str()` 强转。
- **降级分支自身 NameError**（`intent_router.py` / `personality.py` /
  `share_conflict.py`）：三模块共 7 处 except 分支调用 `logger.warning(...)`，
  但模块从未 `import logging`、也未定义 `logger`——「LLM 失败回退启发式」
  「画像加载失败降级」「冲突解决前备份失败」这些兜底路径本身会崩。已补
  模块级 logger。
- **蒸馏列表混入对象即整次失败**（`modules/v6.py`）：`distill_context` 逐个
  `get_memory(mid)`，混入 `MemoryEntry` 会抛 ValueError 使整次蒸馏失败；
  现按 `coerce_memory_id` 归一，识别不了的跳过。

### 新增：三个「文档承诺但从未实现」的参数落地（三层一致）

- **`forget_run(days=30)` 冷却窗口**（`modules/principled_forgetting.py`）：
  `days` 自 v6.0.0 起就在签名与门面里一路传进来，函数体却从未引用它——调用者
  以为在调「GC 年龄窗口」，实际改了个寂寞。现在是真正的冷却窗口：创建不足
  `days` 天的记忆不归档，单独计入 `skipped_too_fresh`；`days=0` 关闭冷却。
  三层接入：CLI `forget-run --days`、REST `POST /api/forget/run`（body.days）、
  MCP `forget_run`（schema 补 days 及说明）。
- **`close_preview_channel(purge=True)`**（`modules/v6.py`）：文档自 v6.0.0 起
  就写着「可逆：仅标记时用 purge=False 保留文件」，但签名里没有这个参数，
  函数体无条件删库文件。现在参数真实存在并生效，返回值补 `purged` 与
  `db_path_kept`。三层接入：CLI `preview-close --keep-files`、
  REST `POST /api/preview/close`（body.purge）、MCP `preview_close`（schema 补 purge）。
- **`MAX_EXPORT_SIZE` 护栏**（`modules/importer.py`）：常量 200MB 自 v6.0.0 就
  定义并写在模块文档里（「超大导出直接拒绝」），但全项目零引用，几百 MB 的
  导出文件照样被 `json.load` 整份读进内存。新增 `export_size_error()`，
  CLI 在 `os.path.getsize()` 之后、打开文件之前拦截；facade 对未解析的原始
  str/bytes 同样拦截。另修正 `skipped_noise` 的语义说明（它统计的是被过滤掉的
  非用户消息数，不是「错误数」）。

### 测试

- 新增 `tests/test_v612_fixes.py` 56 项，按「崩溃 / 静默错误 / 文档承诺落地 /
  三层接线」四组覆盖上述每一条缺陷；其中降级分支用 `assertLogs` 验证
  「日志确实发出且不再 NameError」，参数落地用真实 CLI argparse `--help`
  与 MCP `_handle_tools_call` 走通，而非只看源码文本。
- 全量 **1130** 项通过（`pytest tests/` 收集数与通过数一致）。
- 发布后审计（2026-11-05）：修复级联删除 4 处 bug——`kg_edges` 表名
  错误（实为 `graph_relations`，报错被 except 吞掉导致硬删除后图谱
  关系从未清理）、`agent_purge` FTS 空值删除（rowid 复用后搜索串台）、
  `memory_templates` 引用不存在的 `memory_id` 列（死代码）、passport
  replace/revoke 裸删无级联。新增 `tests/test_v612_cascade_fixes.py`
  10 项回归，全量 1130 项通过。
- 新增两组文档守卫（`scripts/check_docs_consistency.py`）：
  [7] MCP 工具数——真值取自 `TOOL_SCHEMAS` 并校验 schema/handler 自洽，
  再比对 README 中英文 4 处 + 官网 4 处（本版即抓到官网架构图与生态卡
  仍写 86 的两处漏改）；[8] REST 路由数——从 `do_*` 方法体解析分发点数
  （60：GET 37 / POST 21 / PUT 1 / DELETE 1），要求 `api/server.py` 文档
  字符串声明同一口径。此前该表只列 24 条，读代码的人会低估接口规模一半以上。
- `ci.yml` 的 MCP 冒烟断言仍为 87（本版未增删工具）。

### 其他

- 版本号推进 6.1.1 → 6.1.2：`core/version.py` 真值、`test_v568_fixes.py` 的
  `_EXPECTED_VERSION`、两份 README 徽章、英文 README 架构图标题行、
  官网 6 处受守卫位置 + 测试数 5 处 + 更新日志新条目。
- 官网 `website/index.html` 新增 v6.1.2 更新日志条目，v6.1.1 的「最新」标记
  相应下移为「发布」（守卫要求全页仅一条 latest，且日期与 CHANGELOG 一致）。

## [6.1.1] - 2026-11-01

演示链路修复 + 功能补全。本版修复两个 v6.0.0 引入、且被 1032 项测试漏过的
真实缺陷，并把「记忆主动帮你收尾」从被动查询补全为可自动触发的催办闭环。
新增 MCP 工具 1 个（86 → 87），测试规模 1032 → 1063（新增 31 项回归）。

### 修复：按结果学习的记忆权重恒为 0（P0，影响演示）

`mindforge/modules/outcome_learning.py` 的 `search_weighted()` 读取召回得分时
用了字段名 `score`，但 RecallEngine 实际写入的是 `relevance_score`
（见 `mindforge/modules/recall.py:145/168/182`）。`getattr(chunk, "score", 0.0)`
取不到属性便落回默认值，导致**返回的 relevance 永远是 0.0**。

影响：「左右分屏对比」这类演示拿不到任何权重数字可展示；排序虽仍正确
（outcome 分组优先），但分值列全为零。

- 新增 `_chunk_score()`：按 `relevance_score` → `score` → `similarity` 顺序
  读取，并兼容 dict 形态；取不到才返回 0.0。
- 实测修复后同一场景得到 0.222 / 0.250 / 0.0925 的真实分值。

### 修复：传入 add() 返回值即崩溃（P0）

`mf.add()` 返回 `MemoryEntry` 对象，但三个 v6.0.0 入口直接执行
`if not memory_id or len(memory_id) > 128`，把返回值原样传回会抛
`TypeError: object of type 'MemoryEntry' has no len()`——对外表现为崩溃而非
友好报错。v5.9.0 曾为 `get()` 修过同类问题（ValueError），v6.0.0 新代码漏了。

- `learn_from_outcome` / `follow_up_create` / `forget_explain` 统一改为接受
  字符串或 `MemoryEntry`（自动取 `.id`），其余类型返回明确错误文案。
- 崩溃点：`outcome_learning.py`、`follow_up.py`、`principled_forgetting.py`。

### 新增：跟进事项主动催办与回执（功能 8 的「主动」部分）

`mindforge/modules/follow_up.py` 自 v6.0.0 起，模块文档就写着
「与 EventBus 联动：到期时 publish `memory.follow_up.due` 事件」，但全文
**没有任何 publish 调用**；`follow_ups` 表也只有 open / resolved 两态，
无处记录「已催了几次」。所以「记忆主动帮你收尾」当时只能靠外部轮询，
无法演示「AI 自己弹出回执」。

- `follow_ups` 幂等补三列：`nag_count` / `last_nagged_at` / `receipt`
  （`ALTER TABLE ... ADD COLUMN`，旧库首次访问时自动升级，无需迁移）。
- 新增 `nag_due()`：筛出到期且未办结事项 → 递增催办次数 → 生成中文回执
  （如「「跟进和老王的合同」到期 3 天 仍无结果，已催了一次。」）→ 审计
  `follow_up_nag` → 发布 `memory.follow_up.due` 事件。
- 防骚扰设计：两次催办之间有 `interval_hours`（默认 24h）间隔守卫，
  单条事项最多催 `max_nags`（默认 3）次；已办结、未到期均跳过。
  事件发布失败不影响催办落库（事件仅为通知面）。
- 三层接口接入：CLI `follow-up-nag`、REST `POST /api/follow-ups/nag`、
  MCP `follow_up_nag`（工具数 86 → 87）；`follow_up_due()` / `follow_up_list()`
  返回值新增 `nag_count` 与 `receipt`，回执已持久化，进程重启后仍可取用。
- 审计动作白名单补 `follow_up_nag`，否则会被降级记为 `other`。

### 测试

- 新增 `tests/test_v611_fixes.py` 31 项，覆盖：字段名回归（权重不得全零）、
  对象入参（含 6 种非法类型不得崩溃）、催办全流程（首次必执行、间隔守卫、
  上限守卫、已办结/未到期跳过、回执持久化与重启存活）、事件发布与
  「事件失败不影响催办」、以及 87 工具数与三层注册自洽性。
- 全量 1063 项通过（`pytest tests/` 收集数与通过数一致）。
- 修复过程中该文件自身曾有 3 处断言写错（误以为 `export_audit` 返回 dict、
  未注意事件信封的 `data` 嵌套、tearDown 关闭已被置空的实例），均已按
  接口实际契约修正，非实现问题。
- 官网四处测试数与工具数、`ci.yml` 冒烟断言、四份 v5.7x/v5.8 回归测试的
  工具数断言一并同步（86 → 87，1032 → 1063）。

### 其他

- 版本号推进 6.1.0 → 6.1.1：版本真值、两份测试的版本断言、README 中英文
  徽章、英文 README 架构图标题行、dsh 插件徽章、官网 6 处受守卫校验位置。
- 补一处上一轮（6.1.0）漏改：`website/index.html` 开发者正文「从第一行代码
  到现在 v6.0.0」未被守卫覆盖，本轮一并更正为 v6.1.1。

## [6.1.0] - 2026-11-01

质量修复版，不新增功能。本版全部改动集中在 **CI 配置、对外数字一致性、
联系方式正确性与命名残留**：产品代码在 v6.0.0 即已通过全量测试，本版未修改
任何运行时行为、数据库 schema 或接口签名。

### 修复：CI（test 作业自 v6.0.0 起持续判红）

- **MCP 冒烟断言过期（P0）**：`.github/workflows/ci.yml` 的 `tools/list`
  冒烟步骤仍断言 `len(tools) == 56`，而工具数自 v6.0.0 起为 86。该断言与
  1032 项测试是否通过无关，却使 test 作业在全部 Python 版本上判红。
  已更正为 `== 86`，与 `TOOL_SCHEMAS` 实际长度一致。
- **矩阵包含已 EOL 的 Python 3.9（P0）**：`ubuntu-latest` 自 2026-10-19 起
  指向 Ubuntu 26.04，该镜像已移除 Python 3.9（3.9 于 2025-10 结束官方支持），
  `setup-python` 报 `The version '3.9' ... was not found for Ubuntu 26.04`，
  `test (3.9)` 必然失败且与代码无关。矩阵移除 `"3.9"`，保留 3.10–3.13。

### 变更：放弃 Python 3.9 支持

与上述 CI 决定保持一致，避免「声明支持却从不验证」：

- `pyproject.toml`：`requires-python` 由 `>=3.9` 提升至 `>=3.10`，
  并移除 `Programming Language :: Python :: 3.9` 分类标签。
- README / README.zh-CN：Python 徽章由 `3.9+` 改为 `3.10+`。
- `requirements-dev.txt`：`bandit==1.8.6` 的固定理由原为「3.9 上需装本文件」，
  现已失效；本版保持版本不动（升级 1.9.x 会引入新扫描规则、可能使 security
  作业出现新告警），仅订正注释说明。`matplotlib` 的
  `python_version >= "3.11"` 标记**保留**——矩阵仍含 3.10，
  而 matplotlib 3.11 要求 3.11+。

### 修复：对外内容错误

- **官网反馈邮箱数字错位（P0）**：`website/index.html` 页脚两处
  （链接列表与版权行）写作 `2638898280@qq.com`，正确地址为
  `2638895480@qq.com`。该错误地址已随 GitHub Pages 部署，此前访客点击反馈
  会发往错误邮箱；README 与 SECURITY.md 中的地址一直正确，仅官网错。
- **JSON-LD 工具数过期**：结构化数据 `"toolCount": 56` → `86`，与页面正文
  及 README 对齐（该字段不被守卫覆盖，故此前未被发现）。
- **hero 徽标状态过期**：第二个徽标仍标「多 Agent 协作 · 下一代记忆引擎 ——
  开发中」，而该能力已随 v6.0.0 发布，改为「已发布」。
- **v6.0.0 条目端点数错误**：官网更新日志称 v6.0.0「REST 23 端点」，实测为
  28 个处理条目（八项功能那批 20 + 预览那批 8），已更正。同行 CLI 22 命令
  与 MCP 30 工具经核对无误，保持不变。

### 修复：命名与文案残留

- `cli/main.py` 辅助函数 `_print_experimental_banner` 更名为
  `_print_multi_agent_banner`：函数名声称「实验性」，但其打印的横幅文案实为
  「多 Agent 记忆空间（v6.0.0 正式版）」，且底层 `EXPERIMENTAL` 已为 `False`。
  同步更新 7 处调用点。
- 移除 7 条 `space-*` 命令 docstring 与 `--help` 文本中的「实验性」
  「v6.0.0 预览」措辞，以及 3 处区块注释中的同类表述，使 CLI 对外描述
  与能力定级一致。
- `tests/test_v557_banner_insight_wal.py`：同步被改名的测试方法与导入，
  校验意图（横幅函数检查 `_json_mode`）不变。
- `core/version.py`：`NEXT_MAJOR_TARGET` 由 `6.0.0` 改为 `7.0.0`
  （6.0.0 已发布，作为「下一个大版本」目标已过期）；两处函数 docstring
  示例版本由 `5.6.1` 更新为当前版本。
  注意 `V6_TARGET_VERSION` **保持 `6.0.0`**——它是 v6 能力线的目标版本，
  6.1.0 仍属该线，不应随之变动。

### 修复：更新日志结构

- 移除文件中部（v5.8.2 条目之前）误留的第二个 `# Changelog` 标题。该重复
  标题由历史一次性脚本写入，会使更新日志结构在中段断裂。
- 订正 v6.0.0 条目中「CLI 的 `space-*` 命令仍会打印实验性横幅」的表述：
  横幅文案实际已是正式版，遗留的是函数名与 docstring / `--help` 措辞；
  该残留在本版已清理。

### 仓库整洁

- `.gitignore` 补充三条规则：`mf_v*/`（旧规则只覆盖 579/580 两版，后续版本
  的测试临时目录未被忽略）、`preview_channels/`（6G 预览库目录，内含 `.db`）、
  `scripts/_*`（发版辅助一次性脚本与日志）。
- 清理 `scripts/` 下 15 个未跟踪的一次性脚本与日志（含曾误覆盖
  CHANGELOG 头部的 `_sync_version.py`）。它们此前未入库，不影响 GitHub
  主页显示，但会持续堆积并可能被误提交。

### 版本与文档同步

- 版本唯一真值升至 6.1.0；`tests/test_v568_fixes.py` 的 `_EXPECTED_VERSION`
  与 `tests/test_v575_release_consistency.py` 的版本断言同步。
- README / README.zh-CN 版本徽章、英文 README 架构图标题行同步至 v6.1.0。
- 官网 6 处受守卫校验的版本串（JSON-LD、顶栏 brand-ver、hero 当前徽标、
  安装示例输出、开发者板块标题、更新日志导语）全部同步；更新日志新增
  v6.1.0 条目并迁移「最新」标记。
- `plugins/dsh/README.md` 主版本徽章同步。
- `RELEASE.md`：标题升至 v6.1.0，并移除两处不实陈述——原文称「`master` 与
  标签 `v5.8.15` 指向同一发布提交」，实测仓库最新标签为 `v5.9.5`，
  **v6.0.0 与 v6.1.0 均未打标签**；原文称「本地全量测试 938 通过」与
  「METADATA `Version: 5.8.15`」也与实际版本矛盾，已按实测值改写并注明
  发版前需补打标签。

### 测试

- 全量 1032 项通过（在与 CI 等价的依赖环境下实测：Python 3.13 +
  cryptography 50.0.1 + pytest 8.4.2 + pytest-cov 6.3.0，
  `pytest tests/` 收集数与通过数一致）。本版未新增用例，数量与 v6.0.0 持平。
- 文档一致性守卫 `scripts/check_docs_consistency.py` 51 项断言全部通过，
  含官网四处测试数、各处版本号与 CHANGELOG 声明值的交叉校验。
- CI 的 `security` / `docs` / `typecheck` / `deploy` 四个作业在 v6.0.0
  即为绿色，本版改动不影响其判定。

## [6.0.0] - 2026-10-31

v6.0.0 是本项目的第一个大版本：路线图规划的八项 Agent 工作流能力全部落地；
两项 v6 旗舰能力（多 Agent 记忆空间、6G AI 记忆预览）由实验性端口升级为正式支持能力；
上述能力统一接入 CLI / REST / MCP 三层接口。版本唯一真值升至 6.0.0，
MCP 工具数由 56 增至 86，CLI 子命令增至 241 条。

本版未新增运行时依赖（`dependencies` 仍为 `cryptography`）。除下文「兼容性说明」中
列出的 `space_stats()` 响应字段调整外，既有存储格式、加密方案与对外接口签名保持不变，
既有数据库文件无需迁移即可直接打开。

### 新增：八项 Agent 工作流能力

1. **一键导入记忆（冷启动）**：`import_chat_history` 解析 ChatGPT（mapping 树结构）
   与 Claude（accounts / conversations 结构）的对话导出文件，自动识别第一人称画像
   信号并标记为 HIGH 重要度偏好；内置噪音过滤、`dry_run` 试运行与审计记录。
2. **记忆时间线可视化**：`timeline_visual` 输出按日聚合的统计视图
   （层级分布、分类、偏好主题、偏好趋势、知识图谱规模、四层摘要）。
   仅返回聚合结果（aggregate-only），不含任何记忆明文。
3. **可分享技能包**：`skill_pack_export` / `skill_pack_validate` / `skill_pack_import`
   支持技能包的导出、校验与安装，包格式 schema 为 `mindforge-skill-pack/1.0`，
   导入支持 `merge` 与 `replace` 两种模式。
4. **记忆演变**：`evolve_memory` 在更新记忆时保留旧版本并记录演变原因
   （新增 `memory_evolutions` 表），复用 v5.2.7 的自动版本存档机制；
   配套 `list_evolutions` 与 `evolution_stats` 查询接口。
5. **按结果学习的记忆**：`learn_from_outcome` 为记忆标记 success / partial / failure，
   成功结果上调重要度、失败结果降权；`search_weighted_by_outcome` 在检索时
   优先返回曾带来良好结果的记忆。
6. **记忆护照（跨模型迁移）**：`passport_export` / `passport_import` /
   `passport_revoke` / `passport_status` 实现一次授权、记忆随用户迁移、
   随时可撤销；撤销按 `passport_id` 删除对应来源记忆，操作可逆。
7. **有原则的遗忘**：`forget_explain` 输出遗忘评分的因子分解
   （时效、访问频率、重要度保护、结果标记、噪音判定），`forget_run` 据此执行
   可解释的垃圾回收；HIGH 重要度记忆受保护，不参与回收。
8. **记忆主动收尾**：`follow_up_create` / `follow_up_due` / `follow_up_resolve` /
   `follow_up_list` 支持登记跟进事项、到期提醒与办结闭环（新增 `follow_ups` 表）。

### 接口接入

八项能力在三层接口上保持一致暴露，均经过参数校验与审计埋点：

- **CLI**：新增 15 条子命令（`import-chat` / `timeline-viz` /
  `skill-pack-export` / `skill-pack-import` / `evolve-memory` / `evolution-history` /
  `learn-outcome` / `passport-export` / `passport-import` / `passport-revoke` /
  `forget-explain` / `forget-run` / `follow-up-create` / `follow-up-due` /
  `follow-up-resolve`）。
- **REST**：新增 20 个端点处理条目（11 个 GET + 9 个 POST，共涉及 19 个路径；
  `/api/follow-ups` 同时承载 GET 列表与 POST 创建）。
  GET：`/api/timeline`、`/api/skill-pack/export`、`/api/passport/export`、
  `/api/passport/status`、`/api/evolution/stats`、`/api/evolution/history`、
  `/api/outcome/stats`、`/api/forget/stats`、`/api/forget/explain`、
  `/api/follow-ups`、`/api/follow-ups/due`。
  POST：`/api/import-chat`、`/api/skill-pack/import`、`/api/passport/import`、
  `/api/passport/revoke`、`/api/evolve`、`/api/learn-outcome`、`/api/forget/run`、
  `/api/follow-ups`、`/api/follow-ups/resolve`。
- **MCP**：新增 15 个工具（`chat_import` / `timeline_viz` / `skill_pack_export` /
  `skill_pack_import` / `memory_evolve` / `memory_evolution_history` /
  `memory_learn_outcome` / `passport_export` / `passport_import` / `passport_revoke` /
  `forget_explain` / `forget_run` / `follow_up_create` / `follow_up_due` /
  `follow_up_resolve`）。

### 多 Agent 记忆空间正式化

- **能力定级**：`multi_agent.py` 由实验性模块升级为正式支持能力，
  `EXPERIMENTAL` 置为 `False`，`TARGET_VERSION` 定为 `6.0.0`；模块文档字符串同步移除
  「API 在全量发布前可能发生变化」的实验性声明。
- **v6 端口正式发布**：`MultiAgentAdapterPort` 将既有 `MultiAgentMemoryManager`
  （三级角色 owner / editor / reader、last-write-wins 冲突仲裁、PRIVATE / STRICT
  隐私护栏）挂载至 `v6_status()["ports"]`，状态由 `PLANNED` 变为 `AVAILABLE`。
- **门面接口**：facade 新增 `v6_status()`（v6 端口总览）与 `v6_port(capability)`
  （按能力注入就绪实例），调用方可依赖端口抽象而非具体模块。
- **审计埋点**：审计动作白名单补齐 `space_create` / `space_delete` / `space_join` /
  `space_leave` / `space_share` / `space_unshare`，相关操作不再被降级记录为 `other`。
- **接口补齐**：MCP 新增 7 个工具（`space_create` / `space_list` / `space_join` /
  `space_add_member` / `space_share` / `space_memories` / `space_stats`）；
  CLI 新增 `v6-status` 命令，展示双端口的就绪状态。
- **已知遗留**：CLI 的 7 条 `space-*` 命令所用辅助函数名为
  `_print_experimental_banner`，其 docstring 与 `--help` 文本也仍标注
  「实验性 v6.0.0 预览」；而实际打印的横幅文案已是「多 Agent 记忆空间（v6.0.0 正式版）」。
  底层能力已正式化（`EXPERIMENTAL=False`），但 CLI 层的命名与文案尚未同步，
  已在 v6.1.0 清理（函数改名 `_print_multi_agent_banner`，并移除「实验性」措辞）。

### 兼容性说明

- **`space_stats()` 响应字段变更（唯一破坏性调整）**：移除 `experimental` 与
  `target_version` 两个字段，改为返回 `version`（值为 `"6.0.0"`）。
  该字段此前仅用于标注实验状态，能力正式化后已无意义。
  影响面：仓库内除测试外无其他消费方；官网、插件与文档均未读取这两个字段。
  如外部代码断言了 `stats["experimental"]`，请改读 `stats["version"]`。
- 数据库 schema、加密格式、既有 CLI / REST / MCP 接口签名均未变更，无需数据迁移。

### 6G AI 记忆预览完整实现

`MemoryPreviewPort` 的四项能力全部由占位实现（`NotImplementedError`）补全为可用功能，
预览库与稳定库物理隔离：

- **Preview Channel**：`open_preview_channel` / `close_preview_channel` /
  `list_preview_channels` / `channel_stats` / `snapshot_memory`。
  每个通道使用独立的 SQLite 预览库，与主库互不污染；关闭通道即清理其预览库文件。
- **上下文蒸馏**：`distill_context` 按重要度分层组织上下文
  （HIGH 保留全文、MEDIUM 保留摘要、LOW 仅保留标签与首句），
  并施加 Token 预算配额（HIGH 40% / MEDIUM 40% / LOW 20%）。
- **增量索引统计**：`incremental_index_stats` 提供写入即索引的近实时召回指标
  （记忆总量、近 24 小时、近 7 天、FTS 与嵌入覆盖率、最新写入延迟）。
- **记忆时间线**：`timeline` 输出单条记忆的版本历史、演变记录与审计足迹。

**接口接入**：CLI 新增 7 条命令（`v6-status`、`preview-open`、`preview-close`、
`preview-list`、`preview-distill`、`preview-index`、`preview-timeline`）；
REST 新增 8 个端点（GET `/api/v6/status`、`/api/preview/channels`、
`/api/preview/index`、`/api/preview/timeline`；POST `/api/preview/open`、
`/api/preview/close`、`/api/preview/snapshot`、`/api/preview/distill`）；
MCP 新增 8 个工具（`v6_status`、`preview_open`、`preview_close`、`preview_channels`、
`preview_snapshot`、`preview_distill`、`preview_index`、`preview_timeline`）。

### 修复

- **审计动作白名单补齐 v6 动作**：新增 `import_chat`、`outcome_learn`、
  `passport_import`、`passport_revoke`、`follow_up_create`、`follow_up_resolve`、
  `principled_forgetting`，以及 `space_create` / `space_delete` / `space_join` /
  `space_leave` / `space_share` / `space_unshare` 与 `preview_channel_open` /
  `preview_channel_close` / `preview_snapshot`。此前这些动作不在白名单内，
  审计记录会被统一降级为 `other`，导致无法按动作维度检索与取证。
- **测试断言同步工具数**：v5.7.6 / v5.7.7 / v5.7.9 / v5.8.0 四份回归测试中
  硬编码的 MCP 工具数断言由 56 更新为 86，避免工具数增长后回归测试误报。
- **版本同步**：版本唯一真值（`mindforge/core/version.py`）升至 6.0.0，
  MCP 工具数 56 → 86；README、README.zh-CN、RELEASE.md、dsh 插件文档与
  官网统计数字同步至 v6.0.0。

### 测试

- 新增 6 份测试模块覆盖本版能力：`test_v600_import_timeline`（9 项）、
  `test_v600_outcome_passport`（9 项）、`test_v600_skillpack_evolution`（8 项）、
  `test_v600_forgetting_followup`（8 项）、`test_v600_multi_agent_formal`（11 项），
  以及重写后的 `test_v6_ports`（22 项，由原 17 项扩展）。
- 全量 1032 项通过。本版测试规模经 `pytest tests/ --collect-only` 实测核对为
  1032 项，较 v5.9.5 的 982 项净增 50 项；官网四处测试数与该值一致。
- 文档一致性守卫 `scripts/check_docs_consistency.py` 51 项断言全部通过，
  含官网四处测试数与 CHANGELOG 声明值的交叉校验。

## [5.9.5] - 2026-10-14

### 稳定性（P0 数据完整性修复）
- **WAL 在线备份一致性**：`backup()` / `create_backup()` 此前直接
  `shutil.copy2` 主库文件。本库默认 WAL 模式运行，未 checkpoint 到主库
  的 -wal 事务会被丢失，恢复后数据回退。改用 SQLite 官方在线备份 API
  `Connection.backup()` 读取一致性快照，写完 `.tmp` 后 `os.replace`
  原子改名；备份落盘前对目标库跑 `PRAGMA integrity_check`，损坏备份
  自动丢弃不污染目录。

### 测试
- 新增 tests/test_v595_features.py（3 项：WAL 备份含全部已提交行、
  无 .tmp 残留、create/restore 往返数据完整）；全量 982 项通过。

## [5.9.3] - 2026-10-12

### 安全加固
- **知识图谱导出隐私过滤（fail-closed）**：`export_graph(max_privacy)`
  新增隐私过滤，默认 `INTERNAL`——STRICT/PRIVATE 来源的实体关系结构
  不随导出外泄；孤立节点一并裁剪。CLI `mf export-graph --privacy`、
  REST `GET /api/meta/graph?max_privacy=` 同步支持。
- **图谱导出 schema 修复**：边输出增加 `source_name`/`target_name`
  （此前仅实体 UUID，外部无法识别）；节点裁剪改为按实体 ID 匹配。
- **隐私报告未知等级归类**：`privacy_report()` 将未知/异常隐私等级
  （含 NULL）归入 `unknown_privacy_count`，不泄漏原始等级名、不污染
  四档分布，仍计入活跃记忆总数。

### 新功能
- 新增 REST 端点 `GET /api/meta/graph`（鉴权网关后，图谱结构化导出，
  不含明文）。

### 测试
- 新增 tests/test_v593_features.py（12 项：图谱隐私过滤 / schema /
  未知等级归类 / CLI --privacy / REST 端点与鉴权）；全量 979 项通过。

## [5.9.2] - 2026-10-12

### 新功能
- **隐私健康体检 `privacy_report()`**：隐私等级分布 / STRICT 保护 /
  回收站可擦除 / 审计事件概览（总量、近 7/30 天）/ 加密与密钥文件状态 /
  联邦共享面；不含任何明文内容（REST `GET /api/meta/privacy`，CLI
  `mf privacy-report`）。
- **知识图谱导出 `export_graph()`**：节点（实体）与边（关系）结构化
  JSON，schema `mindforge-knowledge-graph/1.0`，含关联记忆 ID，不含
  记忆明文（CLI `mf export-graph [--output file]`）。

### 安全加固
- `register_second_factor` 标记弃用（P3-1）：同值哈希为静态口令语义，
  新代码一律使用 RFC 6238 标准 TOTP（`register_totp_secret` +
  `verify_totp_code`）；旧入口保留兼容并发警告日志。

### 测试
- 新增 tests/test_v592_features.py（13 项：报告结构 / 无明文 / 加密态 /
  图谱导出 / REST 鉴权 / CLI / 弃用告警）；全量 967 项通过。

## [5.9.0] - 2026-10-08

### 新功能
- **记忆健康报告 `memory_report()`**：四层规模 / TTL 过期 / 回收站 / 冲突审计 /
  FTS 一致性 / 数据库健康（page_count / journal_mode / WAL）/ 加密状态，
  不含任何明文内容，可安全暴露给已认证端点（REST `GET /api/meta/report`）。
- **审计日志导出 `export_audit()`**：机器可读 JSON（limit / since / actor 过滤），
  用于 GDPR 取证与审计（REST `GET /api/audit`）。

### Bug 修复
- `get()` / `get_memory()` 传非 str memory_id 报友好 ValueError（替代
  unhashable TypeError 崩溃）。
- REST 超大 body：先有界 drain 再响应 413，修复客户端 Broken pipe（P3-3）。

### 安全加固
- memory_report / export_audit 均不含密钥与明文内容；新增端点位于鉴权网关之后。

### 测试
- 新增 tests/test_v590_features.py（16 项：类型校验 / 报告结构 / 过滤 / REST 端点）；
  全量 954 项通过。

## [5.8.16] - 2026-10-08

### 审计基线确认版：全面检查 bug / 安全 / 一致性后发布

- 全面审计（静态危险模式、SQL 注入面、硬编码凭证、路径遍历、SSRF、加密、并发、版本一致性）：全部通过，无新增问题
- 守卫 51 项 PASS；全量 938 项通过

## [5.8.15] - 2026-10-04

### 合成发布：v5.8.13 之后的未发版改动汇总

- **docs**：新增 `RELEASE.md`（PyPI 发布接口，发行名 `mindforge-memory`，
  Trusted Publisher 配置与验证步骤）。
- **feat(dsh)**：`mindforge-dsh-plugin` v0.1.1 → v0.3.0 → v0.5.0。
  适配 v5.8.13 命名空间重构：CLI 启动命令改为
  `python -m mindforge.cli.main --db-path <path> serve --api --port <port>`；
  DeepSeek Harness 依赖升级至 `@deepseek-ai/* 0.1.7-rc.2`。
- **perf(tests)**：`test_v568_fixes` 守卫用例对 `_code_text` 加 `lru_cache`，
  同环境耗时 42.56s → 24.18s（-43%）；AST 全仓扫描排除 `build/`。
- **docs**：官网新增 social preview card 与架构图；发布 v5.8.13 性能测试报告。
- 全量 938 项通过（938 passed）。

## [5.8.13] - 2026-10-02

### 重构：全部源码迁入 mindforge.* 命名空间 + 打包/文档一致性修复

- **命名空间重构**：`core` / `modules` / `mcp` / `api` / `cli` / `adapters`
  全部物理迁入 `mindforge/` 子包，不再以顶层包名安装进 site-packages，
  消除与官方 `mcp` SDK 等第三方库的顶层冲突；仓库根目录保留 `MindForge.py`
  作为向后兼容入口（`from MindForge import MindForge` 仍可用），新代码统一
  `from mindforge import ...`。
- **版本号唯一真值**：`pyproject.toml` 改为 `dynamic = ["version"]`，
  通过 `[tool.setuptools.dynamic]` 读取 `mindforge.core.version.__version__`，
  发版只需改一处；移除 CLI/MCP 旧的「三级回退」硬编码版本兜底。
- **打包配置收敛**：`setup.py` 收敛为薄壳；移除已失效的 package-dir 映射；
  `MindForge.py` 兼容模块随 wheel 发布（py-modules）。
- **PyPI 发行名改为 `mindforge-memory`**：`mindforge` 规范名已被
  mindforge.ai 的第三方客户端占用（最后发布 2024-10），PyPI 项目名大小写
  不敏感导致 `MindForge` 无法发布；改为 `mindforge-memory` 后 Python 模块
  名 `mindforge.*`、CLI 与 `MindForge.py` 兼容层均不变。
- **文档一致性**：README / README.zh-CN 导入示例统一为 `mindforge.*`；
  官网 index.html 版本号、更新日志、统计数字同步至 v5.8.13；修正
  `check_docs_consistency.py` 对 dynamic version 的校验，并仅在 tag
  触发时比对 git tag（避免 master 推送处于「版本已 bump、tag 未打」窗口期误报）。
- **CI 依赖升级**：`actions/checkout` v4/v5 → v7（所有工作流均未使用
  `pull_request_target`，不存在 fork PR 检出行为变更风险）。
- **Dependabot 冲突收敛**：关闭 #8（sentence-transformers ≥6.0.1，与 Python
  3.9 兼容性冲突），保留 #9（≥2.7.0）；#2（checkout 5→7）已关闭，本次直接
  在工作流中完成升级。
- 全量 938 项通过（938 passed）。

## [5.8.12] - 2026-10-02

### 修复：搜索/召回边界、API 更新语义与 LangGraph Store 协议

- **权限过滤后 top-k 漏召回**：不可见的高分记忆不再挤掉可访问结果；候选不足时自适应扩大窗口，且只更新最终可见结果的访问统计。拒绝负数、布尔值和非整数 `max_results`。
- **召回配置与会话隔离**：实际应用 `exclude_categories` / `min_importance`；会话相关召回保留当前会话上下文，不混入其他会话专属记忆。
- **REST / MCP**：PUT 未提供 `valid_from` / `valid_to` 时保持原有效期；冲突自动调和只接受 JSON 布尔值；嵌入状态异常不再把内部异常详情返回客户端。
- **LangGraph Store**：支持 namespace 前缀搜索及 `ListNamespacesOp`，按协议使用绝对 `max_depth`；精确区分斜杠分隔段不同的 namespace，并忽略非 Store 记忆；返回可按属性或映射访问的 Item，`PutOp(value=None)` 正确删除并遵循 `None` 返回协议。
- 新增 11 个回归用例；全量 938 项通过（938 passed）。

## [5.8.11] - 2026-09-28

### 修复：LangGraph batch() 未按 Op 类型分发，GetOp 覆盖数据（P1，鱼刃审计）

- `MindForgeStore.batch()` 旧实现把每个 item 都当 PutOp 解包并 `put()`，
  遇到 LangGraph 内部传入的 `GetOp(namespace, key, refresh_ttl=True)` 时，
  会把 `refresh_ttl=True` 当 value 写入，覆盖原有数据。
- 修复：按 `type(it).__name__` 分发——
  `GetOp → get()`、`SearchOp → search()`、`ListNamespacesOp → []`、
  `PutOp/tuple/dict → put()`。
- 补回归测试 `test_batch_getop_does_not_overwrite`：写入 `{"data":"secret"}`
  后经 `batch([GetOp(...)])` 调用，原数据不被覆盖。
- 全量 927 项通过（926 passed + 1 skipped）。

## [5.8.10] - 2026-09-27

### 修复：HTTPS 固定 IP 直连 context 回退 + 文档数字对齐

- `_FixedIPHTTPSConnection.connect()` 改用 `self._context or create_default_context()`，
  尊重调用方传入的 SSL context（此前硬编码新建，忽略自定义 context）。
- CHANGELOG v5.8.9 测试数对齐到 926（collected 口径）。
- 全量 926 项通过（925 passed + 1 skipped）。

## [5.8.9] - 2026-09-27

### 修复：BaseStore 抽象方法缺失 + URL 连接器 DNS rebinding TOCTOU

- **langgraph_store batch/abatch 缺失（P1）**：BaseStore 协议要求 `batch(items)` / `abatch(items)`，
  未实现时 langgraph 环境下实例化 MindForgeStore 即 TypeError。补 batch/abatch + aget/aput/asearch/adelete/alist
  异步镜像（asyncio.to_thread 包装同步版）。补 test_batch_put / test_abatch_async_put。
- **URL 连接器 DNS rebinding TOCTOU（P2）**：原流程先 `_is_private_ip(host)` 解析校验，opener 连接时
  再解析一次，两次解析之间 DNS 记录可切换（rebinding）。改为：解析一次、校验全部 A/AAAA 记录均为公网、
  用校验过的 IP 直连（自定义 HTTP/HTTPSConnection，TCP 连 IP，TLS SNI/证书校验仍用原域名，Host header 保留原 host）。
  重定向逐跳仍走同一套校验。
- 全量 926 项通过（925 passed + 1 skipped）。

## [5.8.8] - 2026-09-26

### 修复：审计观察项收尾

- **locomeval_eval 临时目录泄漏**：run_eval() 加 try/finally，跑完 rmtree 清理临时数据库目录（即使异常也清理）。
- **langgraph_store.get() 前缀误命中**：改为先 search_by_tag 精确定位 memory_id，再在 search().chunks 里按 id 过滤，
  避免同 namespace 下 key 前缀相似（如 `prefs` 与 `prefs_backup`）时误命中。
- 补 test_get_no_prefix_cross_hit 回归测试；全量 924 项通过（923 passed + 1 skipped）。

## [5.8.7] - 2026-09-26

### 修复：v5.8.4 适配器两个加密库下的 bug

- **langgraph_store.list/search(query=None)**：加密库下 list() 返回的 MemoryEntry.content 为空（解密只发生在 search().chunks），
  原实现直接读 r.content 导致 list 返回空 value。改为从 tags 反解 mf_key:{key} 后逐个走 get() 解密。
- **crewai_memory.reset()**：原实现 list(limit=500) 只删第一批，>500 条残留。改为循环删直到空（100 轮安全上限）。
- 补 test_list_decrypts_in_encrypted_mode 回归测试；全量 923 项通过（922 passed + 1 skipped）。

## [5.8.6] - 2026-09-26

### 新增：联邦共享 MCP Server（P1）

- mcp/server.py 新增 8 个联邦工具（48→56）：fed_peer_register/remove/list、
  fed_memory_share/revoke/list_shared、fed_search、fed_stats，
  把 modules/federated.py 的对等节点管理与跨节点共享/检索暴露给 MCP 客户端。
- schema/handler 一一对应，参数校验 fail-closed；未配置对端公钥时签名验签按既有安全模型拒绝。
- tests/test_v586_federated_mcp.py +7；全量 922 项通过（921 passed + 1 skipped）。

## [5.8.5] - 2026-09-26

### 新增：LoCoMo/LongMemEval 对话记忆评测脚手架（P0）

- **benchmarks/locomeval_eval.py**：长对话 → 多跳/时序问答的可复现评测管道。
  内置 12 条冒烟样本（singlehop/multihop/temporal 三类），keyword 确定性裁判，
  指标 Recall@K / MRR@K / latency p50/p95，输出 JSON 报告含环境与数据集版本声明。
- 当前基线：Recall@5 = 0.75（9/12），暴露 3 条检索差距（如"食物过敏"被"猫毛过敏"挤占 top-K）——
  后续扩充 500 条查询集与优化召回时以此为对照基线。
- tests/test_v585_benchmark.py +4；全量 915 项通过（914 passed + 1 skipped）。

## [5.8.4] - 2026-09-26

### 新增：LangGraph / CrewAI 框架适配器（P1，扩大接入面）

- **adapters/langgraph_store.py**：MindForgeStore 实现 LangGraph BaseStore 协议——
  put/get/search/delete/list 五方法；namespace tuple 映射为 category、key 映射为
  tag、value 序列化为 JSON；零硬依赖（未装 langgraph 时基类回退 object，安装后自动继承）。
- **adapters/crewai_memory.py**：CrewAIMemory 实现 CrewAI Memory 协议——
  save/search/reset；reset 仅清空本 namespace，不影响其他应用数据。
- 两个适配器统一走 `search().chunks`（唯一返回解密明文的 facade 路径），
  加密模式下读取语义正确。
- tests/test_v584_adapters.py +8 项；全量 911 项通过（910 passed + 1 skipped）。

## [5.8.3] - 2026-09-26

安全修复版：修复外部安全审计（7 项发现中的 6 项确认问题）。

### Security
- P1-1 SSRF 前缀黑名单绕过：`_is_private_ip` 改用 ipaddress 标准属性判定，
  拒绝十进制/八进制编码 IP、IPv4-mapped IPv6（解包）、CGNAT 100.64.0.0/10、
  0.0.0.0、IPv6 链路本地 fe80::/10 与 ULA fc00::/7（此前全部可稳定绕过，可打云元数据）。
- P1-2 开放重定向绕过 SSRF：URL 连接器新增 `_SafeRedirectHandler`，
  每个 3xx 跳转目标重新执行 scheme/host 校验（≤3 跳），302 到内网直接拒绝。
- P2-1 响应读取无上限：`_read_limited` 流式分块读取，累计超 5MB 中止（防内存放大）。
- P2-2 未签名链路无信封消息可重放：未签名旧链路（allow_unsigned_peers）也按内容哈希
  去重，同一消息无法无限重放入队（默认 fail-closed 语义不变）。
- P3-1 2FA 非标准 TOTP 语义：新增 RFC 6238 标准 TOTP（register_totp_secret /
  verify_totp_code，HMAC-SHA1、30s 步长、±1 窗口），密钥 AES-256-GCM 加密落库可恢复。
- P3-2 gdpr_report 泄露绝对路径：db_path 仅返回 basename。

### Changed
- 测试：新增 tests/test_v583_security.py 18 项回归（P1-1/P1-2/P2-1/P2-2/P3-1/P3-2），
  全量 903 项通过（902 passed + 1 skipped）。
- 已知限制：P3-3（超限 body 的 413 Broken pipe 客户端体验）审计方确认安全无害，保持现状。

## [5.8.2] - 2026-09-25

发布修复：测试端口动态化 + 版本号对齐（修复 v5.8.1/v5.8.2 挂 5.8.0 版本号的历史瑕疵）。

### Fixed

- tests/test_v576、test_v577、test_v578、test_v579、test_v580 写死端口 18876-18880 起真服务器，端口被占即假失败 → 改为 socket 动态分配空闲端口（与 v5.8.1 中 test_v546 同款修复）
- 版本号 5.8.0 → 5.8.2 全量对齐（core/version.py、pyproject.toml、CLI/MCP __version__、README/官网），守卫脚本新增「version.py == 最近 git tag」断言，杜绝再次挂错版本号
- 全量 885 项全部通过；一致性守卫 PASS

## [5.8.1] - 2026-09-25

P1 修复：跨版本 import 崩溃。

### Fixed

- core/mindforge.py `graph_related` 返回注解 `List[Tuple[str, str, float]]` 缺少 `Tuple` 导入，Python 3.9-3.13 急切注解求值下 `import core.mindforge` 即 NameError（3.14 惰性注解掩盖）；补 `Tuple` 导入
- 删除 v5.7.9 图谱方法重复块（81 行完全相同拷贝，后一份静默覆盖前一份）
- tests/test_v546_features.py 写死端口 18799 → socket 动态分配
- 验证：全库 35 模块 473 处注解急切求值扫描零 NameError；全量 885 项通过

## [5.8.0] - 2026-09-24


程序性记忆 / 经验蒸馏（Cases → Skills 自进化）：补齐「声明性事实记忆 + 程序性技能
记忆」双记忆的最后一块拼图。此前 skill_extractor 只能从记忆「抽取展示」技能模板，
本版本打通记录→蒸馏→落库→匹配→渲染的完整闭环，Agent 越用越强。

### Added

- **modules/experience.py（新文件）**：
  - `ExperienceCase`：一次任务经验（task/content/result/outcome/duration_seconds/
    tags/source_memory_ids/agent_id/created_at）
  - `SkillStore`：experience_cases + experience_skills 两表持久化；
    record_case / list_cases / delete_case / save_skills（按 name UPSERT 合并
    来源）/ distill / match_skills / render_skill / stats；重启自动恢复内存态
- **core/mindforge.py facade**：`record_experience`（审计 experience_record）/
  `distill_skills`（审计 experience_distill）/ `skill_match` / `skill_render` /
  `skill_stats` / `skill_list` / `skill_cases` / `skill_case_delete`
- **CLI**：`experience` 子命令（record / distill / match / render / stats / list /
  cases / delete），支持 --task/--content/--outcome/--tags/--param 等参数
- **REST**：`POST /api/experience`、`POST /api/skills/distill`、
  `GET /api/skills/stats`、`GET /api/skills/match?query=`、`GET /api/skills?limit=`、
  `GET /api/skills/render?name=&param=k:v`、`GET /api/experience/cases`
- **MCP**：`memory_experience_record` / `memory_skill_distill` /
  `memory_skill_match` / `memory_skill_render` / `memory_skill_stats`
  （工具数 43 → 48）
- **审计白名单**：`experience_record` / `experience_distill`

### Tests

- 新增 `tests/test_v580_experience.py`（12 项）：案例记录/校验/审计、删除、
  蒸馏落库、重启恢复、无成功案例不蒸馏、匹配/渲染、CLI、REST、MCP 48 工具、
  handler 冒烟。
- 全量 **885 项全部通过**（873 基线 + 12 新增）；文档一致性守卫 PASS。

## [5.7.9] - 2026-09-24


知识图谱自动管道：从「有结构没闭环」到「写入即建图、重启可恢复、接口可查询」。
此前图谱引擎仅存在内存态 + 手工调用；本版本补齐持久化加载、add 自动抽取、
STRICT 隐私隔离与三面查询接口。

### Added

- **自动抽取入图（core/mindforge.py + core/types.py）**：`MemoryConfig` 新增
  `auto_extract_graph`（默认 False）；开启后 `add()` 完成写入即自动调用
  `extract_graph(memory_id=...)`，失败不阻断主流程（try/except + warning）。
- **图谱持久化（modules/knowledge_graph.py）**：`__init__(storage, auto_load=True)`
  自动建表 + 从 knowledge_graph / graph_relations 表重建内存态（重启后实体/关系/
  邻接可用）；同进程 `_processed_memory_ids` 防抖，避免全库增量重复建关系；
  旧库缺表时 `_ensure_tables()` 容错升级。
- **中文实体模式**：technology 补充中文技术栈词，organization 补充中文组织后缀
  （公司/集团/大学/研究院/实验室/工作室/团队/中心），新增 product 类型
  （微信/支付宝/飞书/钉钉/抖音/浏览器/编辑器 等）。
- **STRICT 隐私隔离**：`extract_graph` 对 STRICT 记忆直接跳过（返回
  `skipped: strict_privacy`），隐私记忆永不入图。
- **查询接口**：
  - facade：`graph_stats` / `graph_related`（BFS）/ `graph_path` / `graph_entities`
  - CLI：`graph path --from-name X --to-name Y` 新子命令；`graph extract --memory-id`；
    统一走 facade（此前每次新建空实例导致重启即失）
  - REST：`GET /api/graph/stats`、`GET /api/graph/related?entity=&depth=`、
    `GET /api/graph/path?from=&to=`、`POST /api/graph/extract`
  - MCP：`memory_graph_stats` / `memory_graph_related` / `memory_graph_extract`
    （工具数 40 → 43）
- **审计**：`graph_extract` 加入 ACTION_WHITELIST（不再降级 other），
  单条与批量抽取均写审计。

### Tests

- 新增 `tests/test_v579_graph.py`（15 项）：自动抽取开关/审计、重启持久化恢复、
  防抖、显式抽取、STRICT 跳过、全库增量、BFS 相关与路径、CLI、API、MCP 43 工具、
  handler 冒烟。
- 全量 **873 项全部通过**（858 基线 + 15 新增）；文档一致性守卫 PASS。

## [5.7.8] - 2026-09-24


向量语义检索点亮：可复现评测基线 + 确定性伪向量后端。此前 README 宣称混合检索
但无外部可复现证据，本版本把「宣称」变成「可验证」——固定数据集、固定种子、
四路口径与指标全部落盘。

### Added

- **可复现评测基线（benchmarks/embedding_eval.py，新文件）**：内置 20 条
  中英双语标注查询（锁定，勿改）；四路对比 hybrid（0.6 文本 + 0.4 向量融合）/
  vector / FTS5 / TF-IDF；指标 Recall@5 / MRR@10 / NDCG@10；`--backend` /
  `--seed` / `--limit` / `--json`；同 backend + 同 seed 输出确定一致。
- **确定性伪向量后端（core/embedding.py）**：`FakeBackend`——字符 bigram
  哈希桶 + L2 归一化，同文本同向量、共享词面者相似度高；注册进
  `_BACKEND_REGISTRY`（`create_backend("fake")` 或
  `MINDFORGE_EMBEDDING_BACKEND=fake`），CI/无模型环境可确定性跑通完整
  向量链路；评测基线人人可复现。
- **引擎模型名真实化（core/embedding.py）**：`EmbeddingEngine._try_load`
  成功后以实际后端模型名同步 `_model_name`，`get_embedding_status()`
  返回真实模型（此前固定显示默认模型）。
- **REST API**：新增 `GET /api/embedding/status`（available / model_name /
  dimension / embedding_count）。

### Tests

- 新增 `tests/test_v578_embedding.py`（11 项）：FakeBackend 确定性 / 归一化 /
  维度 / 注册表 / 未知后端报错列可用名；评测基线同 seed 两次结果一致 +
  指标结构合法；FakeBackend 注入后 search 走 vector 融合路由；rebuild 链路；
  无后端降级；API 状态端点；CLI embedding-status 冒烟。
- 全量 **858 项全部通过**（847 基线 + 11 新增）；文档一致性守卫 PASS。

## [5.7.7] - 2026-09-23


Agent 记忆治理 + 冲突自动调和 + 多源连接器框架：Agent 可自主 pin / forget /
加速衰减并显式关闭事实窗口；冲突按重要度与时效自动失效旧一方；连接器框架
统一 json / csv / markdown / file / url 摄取入口，MCP 工具 35 → 40。

### Added

- **Agent 记忆治理（core/storage.py、core/mindforge.py）**：新增 `agent_pin()`
  （pinned=1 + forgetting_score=0 + 可选提升重要度，冻结衰减引擎豁免）、
  `agent_forget()`（软删除移入回收站并记录原因，可 restore 恢复）、
  `boost_forgetting()`（forgetting_score 增量封顶 10，联动遗忘曲线）、
  `expire_memory()`（关闭 valid_to 开放窗口，Bi-temporal 显式失效）。
  四者均经 facade 隐私引擎检查并写入独立审计动作。
- **冲突自动调和（Bi-temporal 版）**：`reconcile_conflicts(memory_ids, auto)`——
  `keep_newer` 自动失效较旧一方、`keep_higher_importance` 自动失效低重要度
  一方（均为关闭有效窗口而非删除），`merge` / `review_needed` 归入
  needs_review 人工确认；`auto=False` 仅报告不执行；写 `conflict_reconcile`
  总账审计。
- **多源连接器框架（modules/connectors.py，新模块）**：`BaseConnector` 契约 +
  `register_connector` / `get_connector` / `list_connectors` 注册表；
  内置 json（复用 import_json）、csv（复用 import_csv）、markdown（按段落摄取）、
  file（按扩展名分派）、url（抓取文本，内置 SSRF 防护拒绝内网/回环地址）五类；
  facade `connector_ingest()` 统一入口并写 `connector_ingest` 审计。
- **CLI 新命令**：`agent-pin`（--importance）、`memory-forget`（--reason）、
  `memory-decay-boost`（增量）、`connector-list`、`connector-ingest`
  （--category/--source-agent）、`conflict-reconcile`（--dry-run/--id/--json）。
- **REST API**：新增 `GET /api/connectors`、`POST /api/agent/forget`
  （id/reason）、`POST /api/conflicts/reconcile`（auto/memory_ids）。
- **MCP**：新增 `memory_agent_pin`、`memory_agent_forget`、
  `memory_agent_decay_boost`、`memory_connector_ingest`、`conflict_reconcile`，
  工具总数 35 → 40（ci.yml 断言同步）。

### Fixed

- **审计动作白名单（core/storage.py）**：补入 `agent_pin` / `agent_forget` /
  `agent_decay_boost` / `conflict_expire` / `conflict_reconcile` /
  `connector_ingest` 六个动作，避免治理与连接器操作被降级为 `other`。

### Tests

- 新增 `tests/test_v577_agent_governance.py`（20 项）：Agent 治理语义
  （pin 冻结衰减 / forget 可恢复 / decay 封顶 / expire 幂等 / 隐私访问控制）、
  冲突调和（keep_higher_importance / keep_newer 自动失效、review_needed、
  auto=False）、连接器（注册表 / json 摄取 / file 分派 / URL SSRF / 未知报错）、
  CLI / MCP / API 三面接通。
- 全量 **847 项全部通过**（827 基线 + 20 新增）；文档一致性守卫 PASS。

## [5.7.6] - 2026-09-22


Bi-temporal 事实时序 + GDPR 合规工具包：事实取代自动失效（而非删除）、
按有效时间 as-of 查询、数据可携 / 被遗忘权三件套，MCP 工具 33 → 35。

### Added

- **Bi-temporal 事实时序（core/storage.py、core/mindforge.py）**：memories 表
  新增 `valid_from` / `valid_to` 列（含旧库自动迁移与 `idx_valid_to` 索引）；
  `add` / `update` 支持有效窗口参数；新增 `valid_at(timestamp)` as-of 查询
  （排除回收站，支持分类与上限）；新增 `supersede()`——关闭旧事实开放窗口
  并保留版本历史，以新事实继承旧字段并建立 `supersedes` 关联，写入审计
  （对齐 Zep/Graphiti 的「事实矛盾自动失效而非删除」实践）。
- **GDPR 合规工具包**：`gdpr_report()`（数据类别/数量/加密状态/可行使权利）、
  `gdpr_export_all()`（memories/versions/links/audit/archived 全量可携导出，
  tags/metadata 反序列化）、`gdpr_erase_all()`（先备份数据库与密钥文件，再
  按外键顺序永久删除全部数据并清理 FTS 与缓存，写入 `gdpr_erase` 审计）。
- **CLI 新命令**：`supersede`、`valid-at`（--ts/--category/--limit）、
  `gdpr-report`（--json）、`gdpr-export-all`、`gdpr-erase`
  （--force/--no-backup）；`add` / `update` / `supersede` 支持
  --valid-from / --valid-to。
- **REST API**：`POST / PUT /api/memories` 支持 valid_from / valid_to；
  新增 `GET /api/valid-at`（ts/limit/category/actor/session），
  非法时间戳返回 400。
- **MCP**：新增 `memory_supersede`、`memory_valid_at`，工具总数 33 → 35。

### Fixed

- **审计动作白名单（core/storage.py）**：补入 `supersede` / `gdpr_erase`，
  此前会因白名单缺失被降级为 `other`，导致取代与擦除操作无精确审计记录。

### Tests

- 新增 `tests/test_v576_bitemporal.py`（18 项）与 `tests/test_v576_gdpr.py`
  （8 项）：schema 迁移 / 旧库补列、as-of 查询、supersede 语义（窗口关闭 /
  继承 / 关联 / 审计 / 加密库）、CLI / MCP / API 三面接通、GDPR 报告 / 导出 /
  擦除（含备份与密钥副本）。
- 全量 **827 项全部通过**（801 基线 + 26 新增）；文档一致性守卫 PASS。

## [5.7.5] - 2026-09-22


发布治理修复：修复 README 重写导致的回归测试失败与官网版本漂移，
版本号真值统一到 5.7.5，文档一致性守卫恢复全绿。

### Fixed

- **README HMAC_XOR 迁移提示回归（README.md）**：v5.7.3 发布前 README 重写
  丢失 `EXPERIMENTAL_HMAC_XOR` 精确标志名与「降级加密」说明，导致
  `tests/test_v557_webhook_sig_export.py::test_readme_has_hmac_xor_migration_notice`
  失败——v5.7.3 实际未全绿。已恢复完整提示（含精确配置标志名与中文说明）。
- **官网版本漂移（website/index.html）**：v5.7.3 发版时官网仍停留在
  v5.7.2 / 751 测试，文档一致性守卫 8 项不一致。现已全部对齐 v5.7.5 与
  最新测试数（JSON-LD、顶栏、hero 徽标、安装示例、开发者板块、更新日志）。
- **README 架构图版本标题缺失（README.md）**：架构图重写后丢失版本标题行，
  一致性守卫架构图断言失配；已恢复 `MindForge v5.7.5` 标题行。
- **版本号兜底同步（cli/main.py、mcp/server.py）**：兜底 `__version__`
  随 core/version.py 真值升到 5.7.5，消除兜底漂移风险。
- **pyproject.toml 版本同步**：`project.version` 升到 5.7.5，与唯一真值一致。

### Tests

- 新增 `tests/test_v575_release_consistency.py`（12 项）：版本真值唯一源、
  README 徽章/架构图/HMAC_XOR 提示、官网版本与测试数、CHANGELOG/官网条目对齐。
- 全量 **801 项全部通过**（789 基线 + 12 新增）；文档一致性守卫 PASS。

## [5.7.3] - 2026-09-20

隐私边界加固 + 加密导出发布：修复 API 与核心层的越权读改写路径，
新增 `export-json` / `import-json` 密码加密传输能力。

### Security（高危修复）

- **INTERNAL 记忆空值越权修复（modules/privacy.py）**：`check_access` 重构为
  「本机无身份调用（CLI/单用户）放行；显式身份（含远程 anonymous）必须与来源
  agent/session 精确匹配，空值不参与匹配」。此前空 `actor`/`session_id` 与
  INTERNAL 级来源恒等，任何调用者可越权读取 INTERNAL 记忆。
- **API 读改写路径 fail-closed（api/server.py）**：已配置 `MINDFORGE_API_KEY` 的
  部署中，未声明调用者回落为 `anonymous`（仅可访问 PUBLIC），不再回落为空身份
  放行隐私记忆；`GET/PUT/DELETE /api/memories/{id}`、`/api/search`、`/api/export`
  全部透传 `actor`/`session_id`，杜绝持 Key 越权改删他人 PRIVATE/STRICT 记忆。
- **2FA 在线穷举防护（modules/privacy.py）**：验证码校验新增失败计数与
  60 秒锁定期（5 次失败触发），防暴力破解；仅内存态、进程重启自动清零。
- **访问日志脱敏（api/server.py）**：`log_message` 剥离 query string 与路径中的
  记忆 ID，搜索词与记忆 ID 不再写入日志。
- **legacy 密钥错误密码锁死修复（core/encryption.py）**：旧版密钥文件（无
  `verify_blob`）输入错误密码不再静默放行并重写密钥文件，避免正确密码被永久
  锁死的可用性事故。
- **rekey 环境变量残留清理（cli/main.py）**：`cmd_rekey` 以 try/finally 包裹，
  确保明文旧密码从环境变量清理。

### Features

- **加密导出/导入（v5.7.3 新增）**：`mindforge export-json --password <pwd>`
  以 AES-256-GCM（PBKDF2-SHA256，600,000 次迭代）加密导出记忆备份；
  `mindforge import-json <file> --password <pwd>` 解密导入。密文不落明文，
  导出文件含版本化 KDF 参数，密码错误时拒绝导入并保留目标库原状。

### Fixed

- **`/api/import` 非 dict 条目崩溃（api/server.py）**：不再抛 AttributeError-500，
  计入 failed 列表继续处理其余条目。

### Tests

- 新增 `tests/test_v573_security.py`（15 项）：INTERNAL 越权、API 写路径身份
  透传、2FA 限速、非 dict 导入等回归覆盖。
- 新增 `tests/test_v573_encrypted_export.py`（6 项）：加密导出格式、密文不可读、
  正确/错误密码、无密码提示、端到端往返。
- 全量测试 **789 项全部通过**（768 基线 + 21 新增）。

## [5.7.2] - 2026-09-18

一轮**安全响应头审计与加固**发布：对 API 全部响应头做系统性审计并落地可执行项，
其余项给出明确结论与依据。不涉及新依赖、不改变对外数据契约（新增响应头属纯增量）。

### Security
- **统一安全响应头（api/server.py）**：所有 JSON 响应新增
  `X-Content-Type-Options: nosniff`（防 MIME 嗅探）、`X-Frame-Options: DENY`（防点击劫持）、
  `Referrer-Policy: no-referrer`（不泄漏来源 URL）。
- **`Cache-Control: no-store`（新增）**：敏感记忆 API 禁止缓存，防止记忆内容落入
  浏览器或中间缓存。
- **HSTS 条件下发（新增）**：服务端启用 TLS（`ssl_certfile`）时自动下发
  `Strict-Transport-Security: max-age=31536000`；纯 HTTP 本地部署不下发，避免浏览器
  强记策略后拒绝明文调试。HTTPS 反向代理部署时建议在代理层配置。
- **API Key 强度校验**：`MINDFORGE_API_KEY` 少于 16 字符时启动告警，并给出
  `secrets.token_urlsafe(32)` 生成建议。
- **SECURITY.md 合规说明**：新增「API Security Baseline」章节（认证/限流/响应头/
  CORS/错误脱敏/TLS 基线）。

### 安全响应头审计结论
| 响应头 | 审计前 | 结论 | 处理 |
|--------|--------|------|------|
| Strict-Transport-Security | 未设置 | HTTPS 部署可加 | TLS 启用时自动下发；反代部署在代理层加 |
| Content-Security-Policy | 未设置 | 纯 API 服务影响低，可选 | 暂不设置（结论记录于 SECURITY.md） |
| X-XSS-Protection | 未设置 | 已被现代浏览器废弃 | 不设置 |
| Cache-Control | 未设置 | 敏感 API 可加 no-store | 已全局下发 `no-store` |

### Tests
- 新增 `tests/test_v572_security.py`（4 项）：响应头齐全性、纯 HTTP 不下发 HSTS、
  TLS 启用时下发 HSTS、类默认开关为 False。
- 全量测试 **765 项全部通过**。

## [5.7.1] - 2026-09-17

### Fixed
- **P1 加密兜底性能去重（core/query.py）**：v5.7.0 引入的加密兜底在主路径 fuzzy_search
  已跑过时二次调用 `search_encrypted_fallback`，加密模式下每次搜索跑两遍全表+逐条解密。
  重构为：加密模式下路 2 fuzzy_search 必然执行，结果直接复用；兜底段只判断本次召回
  是否含加密条目来设置 `approximate` 标记，不再二次扫描。结果构建段对 fuzzy 已回填明文
  的条目跳过重复解密，消除每命中一条加密记忆就多一次 AES-GCM 解密。
- **P2 FTS5 多词召回（core/indexer.py）**：`_escape_fts5_query` 此前把整个 query 包成单短语，
  英文多词（如 "search bug"）必须按顺序出现，召回率低。改为按空白分词后各短语用 `OR` 连接；
  中文无空格仍为单短语查询，行为不变。
- **P2 限流空键死代码（api/server.py）**：`_RateLimiter.check()` 原在 append 后才判断空键
  （此时 times 必非空，清理逻辑永不执行）。改为过滤后立即清理空键——某 IP 历史全部过期时
  删除该键，避免冷 IP 永久驻留字典。
- **P2 请求线程 SQLite 连接泄漏（core/storage.py + api/server.py）**：ThreadingHTTPServer
  每请求新建线程，thread-local 连接靠 GC 回收。新增 `StorageEngine.close_thread_conn()`，
  在 `_process_request_thread` finally 中显式关闭本线程连接，避免长期高并发下连接对象堆积。
- **P3 向量维度不匹配静默跳过（core/storage.py）**：换 embedding 模型后旧嵌入维度不匹配被
  静默跳过，用户无感知。新增一次性 `logger.warning`，提示跳过条数与建议 `rebuild_embeddings()`。

## [5.7.0] - 2026-09-17

### Added
- **P1 加密搜索兜底（方案 A，core/storage.py + core/query.py）**：加密模式下 search 路径对
  `encrypted=1` 条目批量解密后在内存做关键词匹配，结果标记 `approximate: true`；
  CLI JSON 与 `/api/search` 响应同步透传。牺牲性能换正确性：启用加密后关键词/模糊搜索不再 0 命中。
- **记忆健康仪表盘（`StorageEngine.memory_health_dashboard` + `MindForge.memory_health_dashboard`）**：
  一站式健康视图——总记忆数、分类/层级分布、衰减桶、冲突数、加密比例、FTS 一致性、数据库完整性。
- **FTS 一致性自检（`check_fts_consistency`）**：扫描 memories 与 memory_fts 差异
  （孤儿条目/缺失条目），支持 `repair=True` 自动修复；加密模式返回 `fts_enabled=False` 由兜底负责。
- **批量导入进度回调（`MindForge.import_json` 新增 `progress_callback` / `progress_interval`）**：
  每 N 条回调一次 `callback(已处理, 总数)`，便于 CLI 进度条与 API 进度上报；回调异常不阻断导入。
- **`add_drama` 新增 `current_episode` 参数**：修复 `import_dramas_from_json` 因 kwargs 与
  `add_drama`/`add_scene`/`add_character` 签名不符导致导入 100% 失败（drama/scene/character 三条路径）。

### Fixed
- **search-history 端到端修复**：`get_search_history` SQL 引用不存在的 `audit_log.query` 列且全仓库无写入
  路径；新增 `record_search`（audit action='search'）+ ACTION_WHITELIST 扩展 + SQL 按查询词聚合，
  `mf.search()` 后 `mf.search_history()` 正常返回。
- **复习系统**：`complete_review` 更新后缓存失效（补 `_memory_cache.invalidate`）；
  `list_due_reviews` 排除 TTL 过期记忆；`integrator.py` 补 `logging` import。
- **静默异常审计（P2）**：storage.py 15 处、mindforge.py 10 处、modules 层 13 处
  `except...pass` 升级为 `logger.warning` 或加说明注释（缓存/OSError 类统一降级日志）。
- **docstring 版本漂移（P2）**：17 个模块头部与类 docstring 移除旧版本号引用，
  历史性逐函数注解（如 `# v5.6.2 安全修复`）保留。
- **`bulk_update_memory_fields` 白名单扩展（P2）**：6 → 16 字段
  （importance/privacy/layer/memory_type/starred/pinned/expires_at/access_count/source_agent/source_session），
  值归一化（枚举取 .value、starred/pinned 转 0/1、expires_at 转 float）；
  category/tags 仍走 batch_update/update_memory 的 FTS 同步路径（刻意设计，避免索引漂移）。

### Security
- **API 统一参数校验层**：`_sanitize_category`/`_validate_tags`/`_validate_importance`/
  `_validate_starred`/`_validate_source_agent`，接入 POST /api/memories、POST /api/import、PUT /api/memories/{id}。
- **分级限流**：读 100 req/60s、写 30 req/60s、批量导入 10 req/60s（per IP，IPv6 /64 归一化）。
- **审计完整性**：batch_update 审计记录完整 ID 列表（上限 100）+ total_ids/ids_audited + 超限 sha256。
- **错误信息脱敏**：全端点确认 500 走通用响应，异常路径不泄露文件路径/SQL/堆栈。

### Changed
- **API 路由文档（P2）**：server.py docstring 补充完整路由表（方法/路径/说明/分发位置/认证/限流）。
- **CHANGELOG 归档**：v5.6.7 及更早归入「历史版本」折叠区，首页仅展示最近 3 个版本。

### Maintenance
- **v6.py 占位实现审查**：保留（接口预留 + `NotImplementedError` 快速失败设计，不启用新行为）。
- **CI**：测试 job 增加 pytest-cov 覆盖率输出；新增实验性 type-check job（mypy，不阻断）。
- **benchmarks/generate_report.py**：`build()` 拆分（458 → 各 `_section_*` 渲染函数）。

### Tests
- 新增 `tests/test_v570_encrypted_fallback.py`（10 例）、`tests/test_v570_coverage_expansion.py`
  （42 例）、`tests/test_v570_drama_and_utils.py`（34 例）：短剧全生命周期、四模块冒烟、
  storage/mindforge 方法三态、多 Agent、FTS 一致性自检与修复、健康仪表盘、导入进度回调。
- 全量测试 751 项全部通过；行覆盖率总体 58% → 66%（core/storage 51%→66%、
  mindforge 49% 保持不变）。方法级"被测试引用"覆盖率 storage 112→191/224（85%）、
  mindforge 115→168/232（72%），核心模块未测公开方法 219→99（<100，达成计划目标）。

## [5.6.9] - 2026-09-16

一轮**全文检索（FTS）同步**缺陷修复：批量更新与自动过期两条路径此前会改写
`category` / `tags`（均为 FTS 索引列）却不同步 `memory_fts` 表，导致搜索结果
滞后；另对若干低风险项做了加固。不涉及新依赖、不改变对外数据契约。

### Fixed
- **P2 `batch_update()` 缺 FTS 同步（core/storage.py）**：批量更新可改写 `category` 与 `tags`
  （均为 `memory_fts` 索引列），但原实现只在 UPDATE 后 commit，未同步 FTS 索引。
  通过 `batch_update` 改分类/标签后，全文搜索结果不会立即反映变化。
  现于 UPDATE 之后、commit 之前，按与 `update()` 一致的「先 `delete` 旧条目、再以新值
  重新插入」逻辑同步 `memory_fts`；仅当涉及 FTS 列且未启用加密时触发。
- **P3 `get()` 自动过期路径缺 FTS 同步（core/storage.py）**：TTL 过期将 `category` 置为
  `'trash'` 时未同步 FTS。实际影响低（搜索经 `WHERE category != 'trash'` 过滤，过期记忆本
  就不会出现在结果里），但会在 FTS 索引中留下无用条目。现已在置 trash 后同步 `memory_fts`，
  与 `update()` 行为对齐。
- **低优先级加固：URL 导入器未做标准化 XSS 清洗（cli/main.py `cmd_import_url`）**：原实现仅做
  基础 HTML 标签剥离，未调用存储层统一的 `_sanitize_html`。现于标签剥离后再过一遍
  `_sanitize_html`，额外清掉 `<script>` 等标签内的残留脚本文本（纵深防御，风险低，内容仍截断至 5000 字符）。
- **低优先级加固：event_bus urllib 降级路径无重试/退避（modules/event_bus.py）**：`requests`
  不可用时降级到 urllib 仅单次尝试、无退避，而 `requests` 路径有 3 次指数退避。现已为 urllib
  路径补齐与 `requests` 一致的重试次数、指数退避，并对 `timeout` 元组做标量兜底。

### Tests
- `tests/test_v568_fixes.py` 的版本期望值由 `5.6.8` 升至 `5.6.9`，随本轮发版同步。

- **`tests/test_v547_fixes.py` 从「打印脚本」重写为可收集的测试用例（10 用例 / 8 子测试）**：
  该文件此前 0 个 `test_` 函数、0 个 `assert`、无 `__main__` 块，且 CI 从不调用它，
  导致 v5.4.7 的三处修复（`get_embedding_status()` 计数回退、FTS5 特殊字符转义、
  `vector_search(query_vector=...)` 回退路径）长期处于**零回归保护**状态。
  现改为标准 `unittest`，由 `pytest tests/` 正常收集执行，并已用变异测试验证其确实能捕获回归。
  全量测试数由 653 升至 **663**。

### Changed
- **CI 工具数断言由松阈值改为精确值（.github/workflows/ci.yml）**：原 `assert len(tools) >= 30`
  与官网/README 对外宣称的 33 个工具不符，误删 3 个工具 CI 也不会报警。现改为
  `assert len(tools) == 33`，让 MCP 工具数量回归可被守护。
- **官网测试数同步为实际值（website/index.html）**：对外展示的 650 更新为 663（5 处），
  与实际收集数一致。

### Removed
- **`.dsh_ref/`（15 个文件）从版本库移除并加入 `.gitignore`**：该目录为 DSH/Cordis 插件参考
  脚手架（`ref-events` / `ref-tool` / `ref-webui` 各含 README / `cordis.patch.yml` /
  `package.json` / `src/index.ts` / `tsconfig.json`），属临时产物，与已忽略的 `dsh-mindforge/`
  性质一致，不应随 Python 源码仓库分发。

## [5.6.8] - 2026-09-14

一轮**命令入口可用性**缺陷修复：3 个 CLI 命令（`init` / `rekey` / `backup-restore`）与 3 个适配器
此前因「越界相对导入」与「未定义名称」在调用时**必然崩溃**，本轮全部修复。另修复 POSIX 下恢复
加密备份的 NameError（兼具安全影响），并收敛若干残留的版本号硬编码。收尾阶段又清理了
`core/mindforge.py` 里 10 处「恒抛异常再兜底」的相对导入死代码，并把官网同步到 v5.6.8。
不涉及新依赖、不改变对外数据契约。

### Fixed
- **P0 `init` 加密模式必然 NameError（cli/main.py）**：`cmd_init` 读取 `os.environ["MINDFORGE_PASSWORD"]`
  但模块顶层从未 `import os`（仅个别函数内有局部导入），因此默认的加密初始化
  `MindForge init` 一进入密码分支就抛 `NameError: name 'os' is not defined`——新手第一步即失败。
  已补模块级 `import os`。
- **P0 `rekey` 命令三重崩溃（cli/main.py）**：该命令此前**任何情况下都不可用**，三个独立缺陷叠加：
  ① `from ..core.encryption import ...` 越界相对导入（`core` 已是顶层包）直接抛 `ImportError`；
  ② 调用从未定义的 `_build_config(args)` 抛 `NameError`；③ 函数体内 `import os` 使 `os` 成为
  本函数局部名，导致前面的 `os.environ.get(...)` 抛 `UnboundLocalError`。现改为绝对导入、
  复用与 `_get_memory` 一致的配置构造、移除局部导入。
- **P0 `backup-restore` 越界相对导入（cli/main.py）**：`from ..core.mindforge` / `from ..core.types`
  在函数被调用时才求值，导致灾备恢复命令完全不可用。`MindForge` / `MemoryConfig` 已在模块顶部导入，
  直接删除这两行重复且越界的导入。
- **P1 POSIX 恢复加密备份 NameError + 密钥权限未收紧（core/mindforge.py）**：`restore_backup()`
  在非 Windows 平台执行 `os.chmod(target_key, 0o600)` 时缺少 `import os`，既让恢复流程在 Linux/macOS
  必然失败，又使刚还原的密钥文件以默认（可能组/其他可读）权限落盘。已补齐导入，与
  `create_backup` 的 0600 收紧逻辑对齐。
- **P1 三个适配器全部方法不可用（adapters/）**：`claude_adapter` / `generic_api` / `openclaw_adapter`
  中的 `from ..core` / `from ..modules.*` 均为越界相对导入且**无 try/except 兜底**（`core/mindforge.py`
  的同名写法有兜底，故未暴露），任何调用都抛 `ImportError: attempted relative import beyond top-level package`。
  现统一改为与 `modules/*`、`core/*` 一致的绝对导入。
- **P2 混合检索大小写保留分支恒等（modules/hybrid_search.py）**：`new_parts[idx] = s if chunk.isupper() else s`
  两个分支相同，全大写原词被替换为小写同义词，大小写保留逻辑形同虚设。改为 `s.upper() if chunk.isupper() else s`。
- **P2 版本号硬编码漂移（modules/event_bus.py、cli/main.py、adapters/openclaw_adapter.py）**：
  webhook payload 与 `User-Agent` 长期硬编码 `5.4.9`、`export-json` 输出写死 `5.1.5`、
  OpenClaw 适配器上报 `5.0.1`、URL 导入器的 `User-Agent` 停在 `MindForge/5.4`。改为引用唯一真值
  `core/version.py`（adapter/event_bus 新增 `from core.version import __version__`）。
- **P3 `cmd_backup` 重复定义（cli/main.py）**：模块级存在两份 `cmd_backup`（旧简版被后定义的
  v5.6.0 版本静默遮蔽），删除已失效的旧定义；同时移除 `_main_dispatch` 分发表中重复的
  `"restore"` / `"backup"` 键。
- **P3 `cmd_agent_purge` 无效条件分支（cli/main.py）**：`c(..., "bold" if not dry_run else "bold")`
  两个分支取值相同，简化为 `"bold"`。
- **P3 死代码：`core/mindforge.py` 10 处「try 相对导入 → except 绝对导入」（core/mindforge.py）**：
  `from ..modules.*` 写在 `try` 首分支，因 `core` 已是顶层包而**恒抛** `ImportError`，于是 10 条
  懒加载路径（记忆演化、多 Agent、联邦 ACL、共享冲突、联邦网络、意图路由、冲突检测、技能抽取、
  混合检索、会话焦点）每次首次访问都要靠抛异常改走 `except` 兜底。功能虽被兜住，但属异常驱动的
  控制流，且会掩盖模块内部真实的 ImportError。现全部折叠为直接绝对导入，10 条路径对外行为不变。

### Docs
- **官网未随 v5.6.7 / v5.6.8 发版同步（website/index.html）**：品牌徽标、hero 徽标、JSON-LD 的
  `softwareVersion`、安装示例输出、开发者板块文案、首页统计数字与更新日志均仍停留在 v5.6.6，
  测试用例数也还是 608。现同步至 v5.6.8，补齐 v5.6.7 / v5.6.8 两条更新日志，测试用例数更新为 650。

### Tests
- 新增 `tests/test_v568_fixes.py`（27 项）：`init`/`rekey`/`backup-restore` 三条命令的**真实子进程端到端**回归
  （这 3 条命令此前 100% 崩溃，纯静态扫描抓不到）、`restore_backup` 作用域内 `import os` 校验、
  适配器导入、event_bus / openclaw 版本号取自真值、**全仓库 AST 越界相对导入扫描**、
  CLI 分发表重复键与「引用了未定义命令函数」AST 校验、模块级重复定义校验、
  以及 hybrid_search 大小写保留的行为断言。测试套件由 623 项增至 **650 项，全部通过**。
  注：越界相对导入扫描早前只覆盖 4 个显式列出的文件，因此漏掉了 `core/mindforge.py` 的 10 处
  兜底写法；本轮改为遍历全仓库 `.py` 并按语法树（`ImportFrom.level >= 2`）判定，避免再次漏检。


<details>
<summary><b>历史版本（v5.6.7 及更早）</b></summary>

## [5.6.7] - 2026-09-14

一轮针对公开审查发现（P1×2 / P2×5 / P3×4，共 11 项）的安全与健壮性加固，不涉及新依赖、不改变对外数据契约。

### Security
- **P2-02 `/api/health` 未认证信息泄露（api/server.py）**：该端点在 `do_GET` 分发时被排除在鉴权之外，此前一旦配置了 `MINDFORGE_API_KEY` 反而会把 `health_check()` 的完整负载（`total_memories`、`db_size_bytes`、缺失索引名、`recommendations` 等）返回给任意未认证调用者。现无论是否配置 API Key，公开健康端点一律只回最小化 `status`，详细诊断走已认证端点。
- **P2-04 `/api/tags` 标签未消毒（api/server.py）**：逗号分隔 fallback 与 JSON 两条解析路径都可能带回非字符串 / 含控制字符 / 超长的标签。新增 `_sanitize_tag()`：统一转字符串、去除首尾空白、剥离 ASCII 控制字符、截断到 64 字符后再计数聚合，异常标签不再进入 JSON 响应。
- **P3-03 未捕获异常回溯泄露内部路径（api/server.py）**：四处 `logger.exception("API error")` 会把含绝对路径的完整回溯写入常规日志。改为 `_log_unhandled_api_error(e)`：常规级别仅记异常类型名，完整回溯降级到 DEBUG 级按需输出；HTTP 响应始终是通用 `Internal server error`。
- **P3-07 MCP 工具错误响应含异常详情（mcp/server.py）**：`h_memory_add` 的 `ValueError` 处理直接把 `str(e)` 回给客户端，可能夹带存储层绝对路径。新增 `_safe_error_msg()`：抹平 Windows/POSIX 路径为 `<path>` 并截断到 200 字符。
- **P2-09 MCP 认证信任模型加固（mcp/server.py）**：MCP 走 stdio、传输层不认证对端，此前无密钥时只告警放行。新增受管/CI fail-closed 开关 `MINDFORGE_MCP_REQUIRE_AUTH`：置真且无密钥时拒绝启动（退出码 2）；并把信任模型与安全建议写清在告警文本里。
- **P2-11 cryptography 依赖 CVE 核查（requirements.txt）**：核对 2026 公告——`cryptography==50.0.1` 为当前最新版且高于 `CVE-2026-69249`（区间止于 48.0.0，49.0.0 修复）等公告的修复版本线，不受未修复 CVE 影响；已在锁定文件中记录核查结论与下次 `pip-audit` 复核要求。

### Fixed
- **P1-01 版本号兜底漂移（cli/main.py、mcp/server.py）**：两处最后兜底 `__version__` 仍硬编码过期的 `5.6.1`（真值为 `core/version.py`）。随本轮发版同步为 `5.6.7`，并注明必须与 `core/version.py` 一起更新。
- **P1-08 2FA 会话计时语义不清（modules/privacy.py）**：澄清并注释 `time.monotonic()` 的选择——monotonic 不受墙上时钟回拨/NTP 校正影响，可防止通过调慢系统时间延长已授予的验证窗口；会话表仅存内存、进程重启即失效，缺省 `0` 在 monotonic 基准下恒判过期。行为不变，仅补注释消除歧义。
- **P2-13 `_detect_disk_type` 类变量竞态（core/storage.py）**：读-改-写缓存未加锁，多线程下会重复探测并竞争回写。改为双检锁定（新增类级 `_disk_type_lock`），探测逻辑抽入 `_measure_disk_type()` 保持临界区精简，磁盘探测只做一次。
- **P3-05 联邦队列操作缺锁（modules/federated.py）**：`receive_memory` 入队、`accept_incoming` 出队与 `federated_search` 出队未与 `_fed_lock` 协同，并发可越过容量上限或触发越界。将容量检查+入队、空队列检查+出队统一收敛到 `_fed_lock` 临界区原子完成（出队越界返回 None）。
- **P3-10 `accept_incoming` 空内容静默丢弃（modules/federated.py）**：空 / 非字符串内容与入库异常此前被 `return None` 静默吞掉，条目无声消失。现显式 `logger.warning` 记录丢弃/失败原因，并把对端传来的非列表 `tags` 归一为列表，避免 `TypeError` 掩盖问题。


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

</details>
