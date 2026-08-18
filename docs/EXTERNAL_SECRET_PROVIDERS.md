# External Secret Providers

PawFlow secrets keep one stable logical name regardless of where their value is
stored. A secret can contain a local encrypted value, as before, or reference an
entry owned by an external secret provider. Flows, expressions, services, PFP
packages, shell tools, and scripts continue to use the same name, for example
`${github_token}` or `GITHUB_TOKEN`.

The LLM does not choose the provider and does not receive provider connection
details. Provider selection, the remote locator, caching, and access restrictions
are operator configuration.

## Current delivery

The external-secret runtime supports:

- AWS Secrets Manager;
- AWS Systems Manager Parameter Store;
- HashiCorp Vault KV v1 and v2;
- Azure Key Vault;
- Google Cloud Secret Manager;
- Keeper Secrets Manager.

All adapters are read-only. PawFlow never creates, rotates, or deletes values in
the remote system.

The first delivery includes the provider service, encrypted external references,
bounded in-memory TTL caching with single-flight fetches, expression and tool
integration, PFP bindings, and conversation/per-agent allowlists. A dedicated
binding editor, provider health UI, audit metrics, and additional provider
adapters remain follow-up work. Until that editor ships, create the provider
through the normal service resource surface and bind aliases through the
authenticated management action.

## Configure a provider service

Create a service of type `secretProvider` at global, user, or conversation
scope. Its common parameters are:

| Parameter | Required | Description |
|---|---:|---|
| `provider` | yes | Registered adapter name from the table below. |
| `provider_config` | no | JSON object serialized as one sensitive string. PawFlow encrypts this scalar at rest. |
| `cache_ttl_seconds` | no | In-memory value-cache lifetime, default 300 seconds; use 0 to disable caching. |
| `timeout_seconds` | no | Maximum duration of one provider request, default 15 seconds. |

Example AWS service parameters:

```json
{
  "provider": "aws_secrets_manager",
  "provider_config": "{\"region_name\":\"eu-west-1\"}",
  "cache_ttl_seconds": 300,
  "timeout_seconds": 15
}
```

Prefer workload identity, instance roles, managed identity, or the provider's
default credential chain. Put explicit bootstrap credentials in
`provider_config` only when the deployment cannot use workload identity.

### Provider configuration and locators

| Provider | Adapter name | Provider configuration | Secret locator |
|---|---|---|---|
| AWS Secrets Manager | `aws_secrets_manager` | Optional AWS session fields: `region_name`, `profile_name`, access key/session fields, or `endpoint_url`. | `secret_id`; optional `version_id`, `version_stage`, `json_key`. |
| AWS SSM Parameter Store | `aws_ssm_parameter_store` | Same AWS session fields. | `name`; optional `json_key`. Values are requested with decryption enabled. |
| HashiCorp Vault KV | `hashicorp_vault_kv` or `vault` | `address`, `token`; optional `namespace`, `mount`, `kv_version`. | `path`; optional `mount`, `key`, `version`, `kv_version`. |
| Azure Key Vault | `azure_key_vault` | `vault_url`; optional complete `tenant_id`/`client_id`/`client_secret` triple. Otherwise DefaultAzureCredential is used. | `name`; optional `version`. |
| Google Cloud Secret Manager | `gcp_secret_manager` or `google_secret_manager` | Optional `project` and `api_endpoint`. Application Default Credentials are used. | `secret`; optional `project`, `version` (default `latest`). |
| Keeper Secrets Manager | `keeper` | `config_json`, containing the exported Keeper Secrets Manager configuration. | `record_uid` and either `field` or `custom_field`; optional `index`. |

Provider SDKs load lazily. AWS uses the core `boto3` dependency and Vault uses
the Python HTTP stack. Install the optional SDK in the PawFlow server image for
the adapters you enable:

```bash
pip install azure-identity azure-keyvault-secrets
pip install google-cloud-secret-manager
pip install keeper-secrets-manager-core
```

## Bind a logical secret name

Use the authenticated `set_external_secret` action (the
`bind_external_secret` alias is equivalent):

```json
{
  "action": "set_external_secret",
  "scope": "conversation",
  "conversation_id": "conversation-id",
  "key": "github_token",
  "provider_service": "corp-secrets",
  "locator": {
    "secret_id": "production/github",
    "json_key": "token"
  }
}
```

The stored external reference contains the logical name, provider-service id,
and encrypted locator. It never contains the materialized value. Writing a local
value to the same name replaces the external reference; moving the secret
between scopes preserves the reference without resolving it.

The normal precedence remains:

```text
conversation -> user -> global
```

PawFlow chooses the winning definition before authorization or provider access.
If that winning external reference is denied, unavailable, or invalid, resolution
fails closed. It never falls back to a lower-scope secret with the same name.

## Restrict access by conversation and agent

Access policies contain exact logical secret names, not provider paths. Set the
conversation envelope:

```json
{
  "action": "set_secret_access",
  "conversation_id": "conversation-id",
  "allow": ["github_token", "deploy_key"]
}
```

Optionally narrow one attached agent:

```json
{
  "action": "set_secret_access",
  "conversation_id": "conversation-id",
  "agent_name": "release-agent",
  "allow": ["deploy_key"]
}
```

The effective set for that agent is the intersection of the conversation and
agent allowlists. An omitted policy preserves the unrestricted legacy behavior;
an explicit empty list denies every secret. An agent policy can only narrow the
conversation envelope and can never expand it.

Shell and `executeScript` retain their existing environment behavior, but only
effective allowed secrets are injected. The same resolved snapshot is used for
result redaction.

## Caching and rotation

Materialized values exist only in process memory and are cached for the
configured TTL. Concurrent misses for the same provider entry share one fetch.
Changing a locator creates a distinct cache identity. Remote rotation and
provider-configuration changes are observed on the next cache miss, no later
than the configured TTL after the provider starts returning the new value.

Set a shorter TTL for rapidly rotated credentials. Use zero only when every
resolution should reach the provider; this increases latency and provider load.

## Operational security

- Give the provider identity read access only to the required remote entries.
- Prefer workload identity over long-lived bootstrap credentials.
- Never put provider credentials or remote values in flow JSON, PFP manifests,
  conversation prompts, or logs.
- Keep conversation and agent allowlists narrow for production systems.
- Treat provider failures as unavailable credentials; do not create a local
  fallback under the same name.
- Verify that optional provider SDKs are present in the deployed server image
  before enabling an adapter.

See [External Secret Providers Implementation Plan](EXTERNAL_SECRET_PROVIDERS_PLAN.md)
for design invariants, follow-up phases, and the broader provider roadmap.
