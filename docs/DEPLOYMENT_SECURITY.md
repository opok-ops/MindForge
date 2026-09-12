# 部署安全配置指南

本指南覆盖生产/多用户环境下的 MindForge 安全加固配置。**单用户本地使用可跳过**。

## 核心原则

- **Fail-Closed**：认证未配置时拒绝启动，而非默认开放
- **最小权限**：仅绑定必要的网络接口
- **纵深防御**：加密 + 认证 + 限流 + 审计多层防护

## 环境变量速查

| 变量 | 作用 | 生产推荐 | 说明 |
|------|------|----------|------|
| `MINDFORGE_API_KEY` | REST API 认证密钥 | **必须设置** | 非 localhost 绑定时强制要求 |
| `MINDFORGE_MCP_SECRET` | MCP Server 共享密钥 | **必须设置** | 启用后 initialize 握手须携带 `_meta.authSecret` |
| `MINDFORGE_ALLOW_NOAUTH` | 是否允许无认证启动 | `0` | 设为 `0` 强制 fail-closed，即使绑定 localhost 也须配密钥 |
| `MINDFORGE_RATE_LIMIT` | API 速率限制（次/分钟） | `60` | 默认 60，按实际流量调整 |

## 生产部署 Checklist

- [ ] 设置 `MINDFORGE_API_KEY`（强随机，≥32 字节）
- [ ] 设置 `MINDFORGE_MCP_SECRET`（强随机，≥32 字节）
- [ ] 设置 `MINDFORGE_ALLOW_NOAUTH=0`
- [ ] API 服务绑定 `127.0.0.1`，通过反向代理（Nginx/Caddy）对外暴露
- [ ] 反向代理启用 TLS（Let's Encrypt 或自建证书）
- [ ] 反向代理加 `X-Forwarded-For` 透传真实 IP（限流用）
- [ ] 数据库文件和密钥文件权限 `600`，属主仅运行用户
- [ ] 备份文件存储到独立加密卷
- [ ] 开启审计日志（`audit.log`）并定期轮转

## 密钥生成建议

```bash
# 32 字节 base64 随机密钥
python -c "import secrets; print(secrets.token_urlsafe(32))"

# 或 openssl
openssl rand -base64 32
```

## Docker / 容器部署

```dockerfile
ENV MINDFORGE_ALLOW_NOAUTH=0
ENV MINDFORGE_API_KEY=your-api-key
ENV MINDFORGE_MCP_SECRET=your-mcp-secret
```

⚠️ **不要**将密钥硬编码在 Dockerfile 中，使用 secrets 管理或运行时注入。

## systemd 服务示例

```ini
[Service]
Environment="MINDFORGE_ALLOW_NOAUTH=0"
Environment="MINDFORGE_API_KEY=..."
Environment="MINDFORGE_MCP_SECRET=..."
User=mindforge
Group=mindforge
ReadWritePaths=/var/lib/mindforge
NoNewPrivileges=true
PrivateTmp=true
```

## 验证部署

启动后检查日志确认：

1. 无 "Authentication disabled" 警告
2. API 未带密钥请求返回 `401`
3. MCP 未带 authSecret 的 initialize 返回 `-32001`
4. `health_dashboard` 接口正常返回

```bash
# API 认证测试（应返回 401）
curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/api/memories
```

## 安全边界说明

| 场景 | 认证要求 |
|------|----------|
| 本地 CLI 直接操作 | 无需（本地用户已有文件访问权） |
| REST API 绑定 127.0.0.1 | 默认允许，设 `ALLOW_NOAUTH=0` 则强制 |
| REST API 绑定 0.0.0.0 | **强制**要求 `MINDFORGE_API_KEY` |
| MCP stdio 传输 | 默认允许（同进程可信），生产环境建议设 secret |
| MCP TCP/网络传输 | **必须**设 `MINDFORGE_MCP_SECRET` |

## 故障排查

**启动报 "非本地绑定时必须启用 API 认证"**
→ 设置 `MINDFORGE_API_KEY`，或仅绑定 `127.0.0.1`

**启动报 "ALLOW_NOAUTH=0 but no API key configured"**
→ 设置 `MINDFORGE_API_KEY`，或确认是否真的需要 `ALLOW_NOAUTH=0`

**MCP 客户端报 "Authentication failed" (-32001)**
→ 检查 `_meta.authSecret` 是否与 `MINDFORGE_MCP_SECRET` 一致
