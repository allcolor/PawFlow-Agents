# x402 Integration Design

This document describes how PawFlow should implement x402 support for payment-gated agent, tool, flow, package, and HTTP endpoints.

Sources used for this design:

- x402 project site: https://www.x402.org/

## Goal

PawFlow should support x402 in two directions:

1. Server-side: PawFlow can require payment before serving selected HTTP endpoints, published flows, tools, packages, and A2A agents.
2. Client-side: PawFlow agents can call x402-protected external resources, handle `402 Payment Required`, pay through configured payment services, and retry safely.

The first implementation should be conservative: payment policy, verification, accounting, and auditability should be explicit. Do not allow an agent to spend money unless a budget, wallet, and policy have been configured for that user, conversation, agent, or flow.

## Existing PawFlow Fit

PawFlow already has several relevant foundations:

- HTTP listener service and request/response flows.
- Auth gateway, API keys, JWT, OAuth, and RBAC.
- Cost caps and usage tracking.
- Secret storage for API keys and service credentials.
- Package runtime proxies and package registries.
- A future A2A server surface where remote agents can target PawFlow agents.
- Tool handlers and service providers with runtime context for user, conversation, and agent.

x402 should be implemented as an HTTP/payment policy layer, not as an LLM provider feature.

## Server-Side Use Cases

### Published HTTP flows

A flow exposed through the HTTP listener can require payment before the FlowFile enters the DAG. This is the simplest and highest-value server-side use case.

Example policy:

```json
{
  "enabled": true,
  "routes": {
    "GET /api/weather": {
      "amount": "0.001",
      "asset": "USDC",
      "network": "base",
      "description": "Weather data lookup"
    },
    "POST /api/research": {
      "amount": "0.25",
      "asset": "USDC",
      "network": "base",
      "description": "Research agent request"
    }
  }
}
```

### A2A agents

A published PawFlow A2A agent can require payment before accepting `SendMessage` or before returning premium artifacts.

Suggested policy levels:

- Per-message payment: charge before accepting a task.
- Per-artifact payment: charge before downloading a generated artifact.
- Subscription or allowlist bypass: allow trusted clients without x402.
- Free tier: allow the first N calls or small tasks without payment.

### Tools and package endpoints

Package registries, package downloads, published tools, and model/media service proxies can be payment-gated through the same middleware.

Do not put x402 checks inside every tool handler. Use a shared policy resolver and middleware so the behavior is consistent.

## Client-Side Use Cases

### Web fetch and browser/tool calls

When a PawFlow tool receives `402 Payment Required`, it should expose that payment requirement to the agent in a structured way. The agent can then decide whether to pay if policy allows it.

Recommended flow:

1. Tool sends HTTP request.
2. Server returns `402 Payment Required` with x402 payment requirements.
3. Tool returns a structured `payment_required` result if no auto-pay policy is configured.
4. If auto-pay is configured, PawFlow checks budget and policy.
5. Payment service prepares and submits payment.
6. Tool retries the original request with the payment proof/header.
7. The payment and retried request are audited.

### Remote A2A calls

When targeting a remote A2A agent, the A2A client should support x402-protected Agent Cards and task endpoints. This allows PawFlow agents to pay remote agents directly for work.

### Flow tasks

HTTP client tasks inside flows should support the same payment behavior, but they must be deterministic and policy-driven. A flow should not prompt a user interactively unless explicitly configured for human approval.

## Payment Policy Model

Add a payment policy model that can be attached to user, conversation, agent, flow, package, or endpoint scope.

Suggested fields:

```json
{
  "enabled": true,
  "mode": "deny|ask|auto",
  "max_amount_per_request": "0.10",
  "max_amount_per_day": "5.00",
  "accepted_assets": ["USDC"],
  "accepted_networks": ["base"],
  "wallet_service": "coinbase_wallet",
  "settlement_account": "merchant_usdc",
  "allowed_domains": ["api.example.com"],
  "blocked_domains": [],
  "require_user_approval_above": "0.05"
}
```

Policy resolution should follow PawFlow's existing scope style:

```text
flow -> conversation -> user -> global
```

For agent-originated requests, include the agent override as part of the conversation/user decision. Missing policy means deny, not default allow.

## Server-Side Architecture

### Components

- `X402PolicyStore`: stores endpoint and spending policies.
- `X402PaymentService`: provider abstraction for verifying incoming payments and creating outgoing payments.
- `X402Middleware`: checks route policies before protected handlers run.
- `X402Accounting`: records payment attempts, settled payments, spending, refunds if supported, and failures.
- `X402Audit`: appends audit events without logging secrets or payment private material.

### Request Flow

For protected incoming requests:

1. Match method and path against x402 policy.
2. If no policy matches, continue normally.
3. If policy matches and no valid payment is present, return HTTP `402 Payment Required` with the x402 payment requirements.
4. If payment is present, verify it through `X402PaymentService`.
5. Enforce replay protection and idempotency.
6. Record payment accounting.
7. Continue to the underlying PawFlow handler.

### Response Shape

The exact wire fields should follow the active x402 SDK/spec at implementation time. Internally, normalize to this shape:

```json
{
  "x402": true,
  "amount": "0.10",
  "asset": "USDC",
  "network": "base",
  "pay_to": "...",
  "description": "Research agent request",
  "expires_at": "...",
  "nonce": "..."
}
```

Keep this normalized model internal so PawFlow can adapt if the x402 SDK changes.

## Client-Side Architecture

### HTTP Tool Integration

Add an x402-aware HTTP client helper used by web fetch, HTTP client tasks, A2A client calls, package registry clients, and any future external API tools.

The helper should return one of three outcomes:

- Normal HTTP response.
- Structured payment requirement.
- Paid and retried HTTP response.

Suggested structured tool result when payment is not automatic:

```json
{
  "type": "payment_required",
  "protocol": "x402",
  "amount": "0.10",
  "asset": "USDC",
  "network": "base",
  "description": "Research agent request",
  "payment_policy": "ask",
  "approval_action": "approve_x402_payment"
}
```

### Approval Flow

For web chat, show a payment approval dialog similar to tool approvals. For CLI, ask for explicit confirmation unless policy is `auto`. For flows, fail fast unless the flow has a configured auto-pay policy.

### Budgets

Integrate with existing cost tracking. x402 payments are not LLM token costs, but they should appear in the cost dashboard as external payment spend with dimensions:

- user
- conversation
- agent
- flow
- endpoint/domain
- asset/network
- payment service

Budget checks should happen before creating payment. Spending records should be idempotent by payment ID or nonce.

## Security Requirements

- Never store private keys in plain config. Use the secrets system or a dedicated wallet service.
- Default client-side mode is deny.
- Require explicit policy for auto-pay.
- Enforce per-request and per-period caps.
- Validate domains for auto-pay to prevent prompt-injection-driven spending.
- Bind payment proofs to method, URL, amount, asset, and nonce where the x402 SDK supports it.
- Prevent replay with nonce tracking.
- Do not log wallet secrets, authorization headers, payment proofs if sensitive, or full signed payloads.
- Apply SSRF protections before paying for or fetching remote URLs.
- Audit every payment challenge, approval, payment submission, verification, retry, and failure.

## Integration With A2A

x402 and A2A should compose cleanly:

- A public PawFlow A2A Agent Card can advertise that task endpoints require x402 payment.
- `SendMessage` can return `402 Payment Required` before creating a task.
- `GetTask` and `SubscribeToTask` should not require repeated payment for the same paid task unless policy says so.
- Artifact downloads can be separately payment-gated.
- Remote A2A clients should handle x402 challenge, policy approval, payment, and retry before surfacing an error to the agent.

This makes paid agent-to-agent work possible without adding account setup or API keys for every remote agent provider.

## Implementation Phases

### Phase 1: Server-side middleware for HTTP flows

- Add x402 policy data model.
- Add policy matching by method and path.
- Add `X402PaymentService` interface with a mock/test implementation.
- Return `402 Payment Required` for protected routes without payment.
- Verify mock payments and pass through to the flow.
- Add tests for policy match, missing payment, verified payment, replay, and disabled policy.

### Phase 2: Accounting and approvals

- Add payment accounting store.
- Add budget checks and cost dashboard dimensions.
- Add web chat and CLI approval surfaces.
- Add audit events.

### Phase 3: Client-side x402 HTTP helper

- Add shared helper for outbound HTTP requests.
- Integrate with fetch/web tools and HTTP client tasks.
- Support deny, ask, and auto modes.
- Add tests for challenge parsing, approval denial, budget denial, paid retry, and idempotent accounting.

### Phase 4: A2A and package integration

- Apply server-side x402 policies to A2A `SendMessage`, task polling, streaming, and artifact download routes.
- Add x402 support to remote A2A client calls.
- Add optional payment gates for package catalog, package download, and published service-provider endpoints.

### Phase 5: Production providers

- Add real provider adapters for the x402 SDK/payment networks selected by the project.
- Add operator documentation for wallet setup, secrets, policy scopes, and reconciliation.
- Add operational metrics and exportable payment history.

## Test Plan

- Protected route without payment returns HTTP 402 and a valid challenge payload.
- Protected route with invalid payment returns 402 or 403 without executing the flow.
- Protected route with valid payment executes exactly once.
- Replay of the same payment proof is rejected or treated idempotently according to provider rules.
- Agent auto-pay is denied when policy is absent.
- Agent auto-pay is denied when amount exceeds per-request or daily cap.
- Agent ask mode emits a user approval request before payment.
- Outbound paid retry preserves the original method, URL, body, and relevant headers.
- A2A `SendMessage` payment creates no task until payment is verified.
- Payment records appear in usage/cost reporting with user, conversation, agent, and endpoint dimensions.

## Open Questions

- Which x402 SDK and payment network should be the first production provider.
- Whether settlement accounts are global, per user, per package publisher, or per published endpoint.
- Whether paid package downloads should use x402 directly or package registry-level payment policies.
- How refunds, disputes, and failed downstream work should be represented in PawFlow accounting.
