# 发布接口（PyPI）

本文件记录 MindForge 的 PyPI 发布启用步骤与当前状态，供后续任何版本发布复用。

## 当前状态（v5.8.16）

- 发行名：**`mindforge-memory`**（PyPI 项目名大小写不敏感，规范名 `mindforge`
  已被 mindforge.ai 的第三方客户端占用，故改名发布；Python 模块名
  `mindforge.*`、CLI 入口、`MindForge.py` 兼容层均不受影响）。
- 代码/标签/CI 全部就绪：`master` 与标签 `v5.8.15` 指向同一发布提交；
  本地全量测试 938 通过；`python -m build --wheel` 产出
  `mindforge_memory-5.8.16-py3-none-any.whl`（METADATA `Name: mindforge-memory` /
  `Version: 5.8.15`）。
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
2. 重跑最新发布 run，或推送任意 tag 触发发布工作流。
   - 最新被拒 run：`37007607564`
   - 之后的新版本：更新 `mindforge/core/version.py`（版本唯一真值）→
     `git tag vX.Y.Z` 推送 → 发布工作流自动上传。

## 验证发布成功

- 检查 run 结论为 success；
- `https://pypi.org/pypi/mindforge-memory/json` 返回 200 且
  `info.version` 等于目标版本；
- 安装验证：`pip install mindforge-memory` 后
  `python -c "import mindforge; print(mindforge.__version__)"`。
