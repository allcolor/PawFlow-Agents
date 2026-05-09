# Security Model

PawFlow is self-hosted, but agents can still perform high-impact actions. Security depends on provider configuration, relay mode, tool permissions, auth, and deployment boundaries.

## Trust Boundaries

| Boundary | What crosses it |
|---|---|
| Browser/client -> PawFlow server | Messages, auth cookies/tokens, file uploads, SSE subscriptions. |
| PawFlow server -> LLM provider | Prompts, selected context, tool schemas/results depending on provider. |
| PawFlow server -> relay | Tool requests for filesystem, shell, screen, desktop, and local resources. |
| Relay -> host or Docker container | File reads/writes, shell commands, screen actions. |
| PawFlow -> media providers | Prompts and source media URLs for image/video/audio/3D/voice operations. |

## Relay Modes

| Mode | Host access | Recommended for |
|---|---|---|
| Native/local relay | Full selected filesystem and optional shell/screen access | Personal trusted workflows. |
| Docker relay | Mounted project directory and container tools | Untrusted code execution and public demos. |
| Standalone client relay | User-selected filesystem/desktop through PawFlow Relay CLI/Desktop | Client machine workflows. |
| Server filesystem service | Server-side files only | Controlled server assets and internal flows. |

## Permission Modes

Agents should run with the least privilege needed:

- read-only: inspect but do not edit or run commands;
- approve-edits: require confirmation for modifications;
- auto: allow configured tools to run without repeated prompts;
- full/local desktop access: treat as privileged.

Use approval gates for shell, edit, delete, desktop, VNC, and external network operations.

## Desktop and VNC Risk

`/desktop local` and `screen(local=true)` can act on the user's real desktop. This is equivalent to allowing an agent to see the screen and operate mouse/keyboard. Prefer Docker desktop unless local control is specifically required.

## Media and Voice Risk

Media tools may send prompts, source images, videos, audio, or voice samples to external providers. Voice clone tools must only be used with samples the user is allowed to clone. Store provider API keys as secrets and document provider data-retention policies separately if deploying for teams.

## Secrets

Use PawFlow secret storage or environment variables for API keys. Never hard-code secrets in flows, agent prompts, or docs. When writing examples, use `${SECRET_NAME}` placeholders.

## Private Gateway

The private gateway is configured as a `privateGateway` service and enabled for a listener through `httpListener.private_gateway_service_id`. Accepted challenge keys are explicit `secret_refs` on that service. The challenge skin is selected by the service `skin` field and resolved from repository resources under `data/repository/private_gateway_skin`. Each skin lives in a directory containing `skin.json` metadata and `template.html`; templates can use `{{ next_url }}`, `{{ error }}`, and `{{ cooldown }}` placeholders.

## Production Checklist

- Run with `PAWFLOW_PUBLIC_MODE=true` or `PAWFLOW_ENV=production` so unsafe boot settings become fatal.
- Set a strong `PAWFLOW_SECRET_KEY_B64` (preferred) or `PAWFLOW_SECRET_KEY`; do not rely on the dev-only on-disk fallback.
- Put PawFlow behind HTTPS / a trusted reverse proxy for public access.
- Enable the private gateway for internet-facing demos.
- Run untrusted workloads in Docker relay mode.
- Avoid local desktop mode for public demos.
- Configure per-agent tool restrictions.
- Set LLM budget caps.
- Review OAuth redirect URLs and provider scopes.
