# 发布接口（PyPI）

本文件记录 MindForge 的 PyPI 发布启用步骤与当前状态，供后续任何版本发布复用。

## 发布链路（v6.1.2 起）

```
改 core/version.py → CHANGELOG 补小节 → 文档守卫绿
        ↓
git tag vX.Y.Z && git push --tags
        ↓
release.yml      ：校验 tag ↔ version.py ↔ CHANGELOG 一致，
                  从 CHANGELOG 抽取正文创建 GitHub Release（--latest）
                  补发历史版本：workflow_dispatch 填 tag →
                  checkout 到该 tag、不带 --latest（不抢走 Latest）
        ↓
publish-pypi.yml ：Release 工作流成功（workflow_run，仅 v* 标签触发的 run）
                  或手动 release.published → build → twine check →
                  PyPI（OIDC；已在 PyPI 的版本幂等跳过，重跑不 409）
```

在此之前 Release 全靠手动创建，而 `publish-pypi.yml` 又**依赖** Release 存在，
于是出现「代码与 README 已到 v6.1.1、git tag 也已推送，但 Releases 页面的
Latest 仍停在 v5.8.13」的漂移。v6.1.2 用 `release.yml` 把这一环自动化了。

## 当前状态（v6.1.2）

- 发行名：**`mindforge-memory`**（PyPI 项目名大小写不敏感，规范名 `mindforge`
  已被 mindforge.ai 的第三方客户端占用，故改名发布；Python 模块名
  `mindforge.*`、CLI 入口、`MindForge.py` 兼容层均不受影响）。
- 版本真值：`mindforge/core/version.py` 的 `__version__` = `6.1.2`；
  `pyproject.toml` 经 dynamic version 读取同一处，打包元数据与运行时一致。
- **标签已全部补齐**（2026-11-01）：CHANGELOG 声明的 50 个发布版本现在均有
  对应标签，最新为 `v6.1.1`（`d60f5b4`）。此前缺失的 13 个历史标签
  （v5.5.2–v5.5.7、v5.5.10、v5.6.8、v5.6.9、v5.7.0、v5.7.1、v6.0.0、v6.1.0）
  已按「版本行最后一个声明该版本的 first-parent 提交」补打，
  目标均经 version 声明（`version.py` / `pyproject.toml` / `setup.py`）与
  该提交 CHANGELOG 顶部条目双重校验（v5.5.3–v5.5.5、v5.5.7 四枚因当年
  bump 提交未同步 CHANGELOG，改以版本行边界判定，标注于此备查）。
  注意：推送旧标签会按各提交当时的 `ci.yml` 触发历史配置的运行
  （含已 EOL 的 3.9 与当时的工具数断言），出现红色记录属预期，
  不代表当前 master 回归。
- **Release 补齐进度**：`v6.1.2` 的 Release 已由 `release.yml` 在标签推送时
  自动创建并置为 Latest（2026-11-02）。首次 tag 触发的 run 曾因校验 job 只装
  `requirements.txt`（缺 pytest）而失败，已修复并重打标签（`v6.1.2` →
  `829b778`）。v6.0.0 / v6.1.0 / v6.1.1 仍待补发：Actions 页面
  `Release → Run workflow` 填对应 tag 各跑一次；补发走 master 的最新
  workflow 文件（checkout 到该 tag、不带 `--latest`，不会把 Latest 从
  v6.1.2 抢走，正文取自该 tag 自己的 CHANGELOG）。
- 测试状态：本地在与 CI 等价的依赖环境下全量 1120 项通过
  （`pytest tests/`，Python 3.12.15 + cryptography 50.0.1 + pytest 8.4.2
  + pytest-cov 6.3.0；覆盖率沿用 v6.1.1 的 71%，本版未调整门槛）。
- **尚未发布**：PyPI 项目未注册（`mindforge-memory` 在 pypi.org 为 404），
  发布工作流失败属预期（`invalid-publisher`）。

## 启用发布（一次性）

1. 在 [pypi.org](https://pypi.org) 登录账号，进入
   `Manage → Publishing`，添加 Trusted Publisher：
   - Project name：`mindforge-memory`（若项目不存在，表单提交时会自动创建）
   - Owner / GitHub account：`opok-ops`
   - Repository：`MindForge`
   - Workflow name：`publish-pypi.yml`
   - Environment：`pypi`
2. 重跑最新发布 run，或在 Actions 页面手动 run `publish-pypi.yml`
   （v6.1.2 起该 workflow 不再监听 tag push，见上文「发布链路」）。
   - 最新被拒 run：`41278717905`（v6.1.1 标签触发，失败点为
     `Publish package to PyPI` 一步；build 与 twine check 均通过，
     确认是 Trusted Publisher 未配置，非打包问题）
   - 之后的新版本：更新 `mindforge/core/version.py`（版本唯一真值）与
     CHANGELOG → `git tag vX.Y.Z` 推送 → `release.yml` 自动发 Release →
     `publish-pypi.yml` 自动上传 PyPI。

## 验证发布成功

- 检查 run 结论为 success；
- `https://pypi.org/pypi/mindforge-memory/json` 返回 200 且
  `info.version` 等于目标版本；
- 安装验证：`pip install mindforge-memory` 后
  `python -c "import mindforge; print(mindforge.__version__)"`。
