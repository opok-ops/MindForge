# Security Policy

## Reporting a Vulnerability

MindForge is a local-first encrypted memory system — security is core to the project.

If you discover a security vulnerability, please report it responsibly:

1. **Email**: 2638895480@qq.com
2. **Subject line**: `[SECURITY] MindForge — <brief description>`
3. **Include**: steps to reproduce, affected version, potential impact

## Response Timeline

| Stage | Target |
|-------|--------|
| Acknowledgment | 48 hours |
| Initial assessment | 7 days |
| Fix or mitigation | 30 days (severity-dependent) |

## Data Processing Boundary (合规边界)

MindForge is a **local-only** library. It does not:

- Send any memory content, queries, or telemetry to any remote server.
- Require an internet connection for normal operation.
- Bundle analytics, crash reporters, or phone-home mechanisms.

All data (SQLite database, keys, logs) stays on the user's machine under the user's control. The optional REST API binds to `127.0.0.1` by default; binding to a non-loopback interface requires `MINDFORGE_API_KEY` and is refused without it.

## Cryptography

- **Encryption**: AES-256-GCM (authenticated encryption, provides confidentiality + integrity).
- **Key derivation**: PBKDF2-HMAC-SHA256, 600,000 iterations (current), with versioned KDF params stored in the ciphertext header for backward compatibility.
- **Fail-closed**: if the `cryptography` library is unavailable, encryption initialization raises `SecurityError` rather than silently falling back to a weaker algorithm (HMAC-XOR fallback removed in v5.5.7).
- **Key file permissions**: written with mode `0600` (owner read/write only).

## API Security Baseline

- Authentication via `Authorization: Bearer <token>` using `hmac.compare_digest` (constant-time comparison).
- Rate limiting: 100 req/60s read, 30 req/60s write, 10 req/60s bulk import, per client IP.
- Security response headers on all JSON responses: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store` (sensitive memory content is never cached by browsers or proxies).
- `Strict-Transport-Security: max-age=31536000` is sent automatically **only when the server's own TLS is enabled** (`ssl_certfile`); not sent on plain-HTTP local deployments. For HTTPS reverse-proxy deployments, configure HSTS at the proxy layer.
- Header audit conclusions (v5.7.2): `Content-Security-Policy` not set — API-only service, low impact, optional; `X-XSS-Protection` not set — deprecated by modern browsers.
- CORS restricted to explicitly configured origin (same-origin by default).
- All unhandled exceptions return a generic `{"error": "Internal server error"}`; tracebacks go to debug logs only, never to the HTTP response.
- TLS optional (TLS 1.2+ minimum) when cert/key files are provided.

## Supply Chain

- Runtime dependency pinned: `cryptography>=50.0.1,<52` (above the line affected by CVE-2026-69249).
- CI runs `bandit -lll` (HIGH severity only) and `pip-audit --strict` on every push to master.
- Dependabot opens weekly update PRs; human review required before merge.

## Scope

- Encryption implementation (AES-256-GCM, PBKDF2-SHA256)
- API authentication and rate limiting
- MCP tool parameter validation
- Local storage and database access

## Out of Scope

- Third-party dependencies (report upstream)
- Social engineering attacks
- Attacks requiring physical access to the user's machine or root/Administrator privileges

## Disclosure

We follow coordinated disclosure. Please do not publish details publicly until a fix is released.
