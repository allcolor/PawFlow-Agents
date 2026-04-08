# Security Policy

## Supported Versions

| Version | Supported |
|---|---|
| 1.0.0-alpha.x | :white_check_mark: |

## Reporting a Vulnerability

If you discover a security vulnerability:

1. **Do NOT open a public issue.**
2. Use [GitHub's private vulnerability reporting](https://github.com/allcolor/PawFlow-Agents/security/advisories/new).
3. Include: description, steps to reproduce, and impact assessment.

We aim to acknowledge reports within 48 hours and provide a fix within 7 days for critical issues.

## Security Architecture

### Self-hosted by Design

PawFlow runs entirely on your infrastructure. No data leaves your network unless you explicitly configure external LLM providers.

### Authentication

- JWT-based session tokens with configurable expiration
- 9 OAuth2 providers (Google, GitHub, Microsoft, X, Facebook, Amazon, Telegram, Generic OAuth2)
- API key authentication for programmatic access
- PBKDF2 password hashing (600K iterations, 32-byte salt)

### Secrets Management

- Secrets encrypted at rest (XOR + PBKDF2 + HMAC)
- Encryption key via environment variable or `config/secret.key` (gitignored)
- Secrets never logged or exposed via API responses

### Sandboxing

- Agent tools execute in Docker relay containers, not on the server
- `executeScript` task runs with restricted imports and filtered builtins
- Configurable `allowed_modules` whitelist per task
- Permission modes: `auto`, `approve_edits`, `read_only`

### Network

- CORS configurable via `PAWFLOW_CORS_ORIGINS` (defaults to localhost only)
- Rate limiting via `PAWFLOW_RATE_LIMIT=true`
- Request body size limit (default 10MB, configurable)
- SSL/TLS support for HTTP listener services

## Production Checklist

- [ ] Set a strong `PAWFLOW_SECRET_KEY` (random 32+ characters)
- [ ] Keep `config/secret.key` out of version control
- [ ] Enable authentication: `PAWFLOW_AUTH_ENABLED=true`
- [ ] Restrict CORS origins to your domain
- [ ] Enable rate limiting
- [ ] Run relay containers in Docker (not `local=true`) for untrusted workloads
- [ ] Use HTTPS in production (reverse proxy or SSL config)
- [ ] Set `max_budget_usd` on LLM services to prevent bill shock
