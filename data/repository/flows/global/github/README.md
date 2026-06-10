# github — GitHub integration flows

## ci_autofix (`github.ci_autofix:1.0.0`)

Receives GitHub `workflow_run` webhooks on the **main PawFlow HTTP listener**
(the one already exposed by Caddy — no extra port, no extra Caddy route),
verifies the HMAC signature, and when a CI run **fails** on the configured
branch, injects a message into a **configurable conversation** asking the
selected agent to fix the failure and push the correction.

The webhook route is **per-instance**: the path carries a `${webhook_slot}`
segment that defaults to the deploy-time unique `${_instance_id}`. Every deploy
(different project, different user) therefore registers a **distinct** path on
the shared main listener — no route collision, no manual coordination.

```
webhook_in (POST /webhooks/github/${webhook_slot}, public)
  -> inject_secret   (loads ${github_webhook_secret} into an attribute)
  -> decide          (HMAC verify + parse workflow_run; decision = bad_sig|ignore|fix)
  -> route
       bad_sig -> finalize_ack (401)
       ignore  -> finalize_ack (200)
       fix     -> build_prompt -> spawn_fix (async) -> finalize_ack (200)
  -> respond
```

### Why the main listener (not WebhookTrigger)

The flow declares an `httpListener` service on `"${port}"`. `HTTPListenerService`
is a **singleton per port**, so setting `port` to the main listener port makes
the `/webhooks/github` route register on the existing server that Caddy already
proxies. The engine `WebhookTrigger` (engine/triggers.py) is deliberately NOT
used: it opens its own server on :9090, which would need a separate Caddy route.

## Deploy parameters

| Parameter | Required | Description |
|---|---|---|
| `port` | yes | Main PawFlow listener port (must equal the running listener so the route lands behind Caddy). |
| `ci_branch` | yes (default `main`) | Branch whose failed runs trigger an auto-fix request. |
| `target_conversation_id` | yes | Conversation the fix request is injected into. |
| `target_user_id` | yes | User that owns `target_conversation_id` (agent resolution). |
| `target_agent` | yes (default `claude`) | Agent woken up in that conversation. |
| `webhook_slot` | no (default `${_instance_id}`) | Path segment that makes the route unique: `POST /webhooks/github/<webhook_slot>`. Leave it on the default to get the auto-generated, collision-free deploy id, or set a friendly value (e.g. the repo name) you control. |

### Resolving the actual webhook path

`${_instance_id}` is a reserved flow parameter injected by the deploy layer; it
equals the `instance_id` returned when the flow is deployed (e.g.
`github-ci-autofix__a1b2c3`). So with the default `webhook_slot`, the live path
is `POST /webhooks/github/<instance_id>`. Read it back from the deploy result or
the deployed-flows list, or pin a known value via `webhook_slot` at deploy time.

## Required secret

- **`github_webhook_secret`** — shared secret used to verify GitHub's
  `X-Hub-Signature-256`. Store it as a PawFlow secret (global, or scoped to the
  flow's user). Resolved at runtime via the secrets cascade. If it is missing or
  wrong, every request is answered `401` and nothing is injected (fail-closed).

## GitHub side (option A — the webhook is created on GitHub)

Repo → **Settings → Webhooks → Add webhook**:

- **Payload URL**: `https://<your-pawflow-domain>/webhooks/github/<webhook_slot>`
  (use the resolved slot from above — the deploy `instance_id` by default)
- **Content type**: `application/json`
- **Secret**: the same value as the `github_webhook_secret` PawFlow secret
- **Events**: *Let me select individual events* → **Workflow runs** only
  (do NOT use *Pushes* — `push` fires before CI runs, so the result is unknown).

PawFlow ACKs every delivery quickly (200/401); the agent's fix work happens
asynchronously after the ACK.

## Known limitations / enhancements

- **Loop guard is advisory**: the injected prompt asks the agent to stop after
  3 failed attempts. There is no persistent per-`run_id` dedup yet, so a flapping
  CI can produce repeated requests. Add a dedup step (persisted last-notified
  `run_id`) if you need a hard cap.
- **No log prefetch**: the agent is given the run URL/commit and investigates the
  logs itself. Add a `fetchHTTP` step with `Authorization: Bearer ${github_token}`
  against the Actions logs API if you want the error pre-extracted.
