# External Secret Providers Implementation Plan

Status: **proposed** (implementation plan only; no implementation yet).

## 1. Executive decision

PawFlow will extend the existing secret entry model instead of introducing a new
secret-consumption model.

Each secret name has exactly one of two storage forms:

1. **local value** — the current AEAD-encrypted value or encrypted sidecar;
2. **external reference** — a typed reference to one entry in a configured
   external secret provider.

Both forms resolve to the existing runtime contract:

~~~text
secret name -> ConfigValue
~~~

After scope resolution, an optional conversation allowlist and an optional
per-agent-instance allowlist filter that mapping. Expressions, flows, services,
tool argument substitution, shell environment injection, and result redaction
then consume the same name-to-value mapping they consume today.

The LLM does not select a provider and does not choose an injection strategy.
It only uses the existing logical secret name, for example
${github_token} or $GITHUB_TOKEN. Whether that name is backed by a local
ciphertext, a cloud vault, an enterprise vault, or a SaaS secret manager is an
operator/user configuration detail.

This plan deliberately does **not** ask the LLM to select individual injections.
bash and executeScript still receive the effective secret environment, but
"effective" now means the secrets allowed by the conversation policy,
intersected with the current agent-instance policy when an agent is present.
Secret profiles, per-tool declarations, and a runtime broker are unnecessary
for this delivery.

## 2. Goals

1. Let a user create a secret whose value is stored locally exactly as today.
2. Let a user create the same logical secret name as a reference to an external
   provider entry.
3. Preserve the existing expression syntax and scope cascade:
   conversation -> user -> global.
4. Preserve the current runtime result type: Dict[str, ConfigValue].
5. Let a conversation owner restrict which exact secrets are available inside
   that conversation.
6. Let the owner further restrict each agent instance without allowing an agent
   to exceed the conversation envelope.
7. Hide provider details from flows, tools, agents, and LLM prompts.
8. Cache remote reads so normal resolution does not make one network request per
   expression or per redaction pass.
9. Pick up remote rotation within a bounded, configurable interval.
10. Keep the core provider-neutral and support the major cloud, enterprise, and
    SaaS secret managers through isolated adapters.
11. Keep provider credentials out of secret values, logs, UI responses, flow
    definitions, and LLM context.
12. Fail closed: an external reference must never fall through to a lower-scope
    secret with the same name when its provider fails.
13. Keep all provider network I/O off asyncio/UI event loops.
14. Add the feature with one filtering seam rather than rewriting individual
    secret consumers.

## 3. Non-goals

The first release does not provide:

- a new expression language;
- per-tool secret allowlists;
- LLM-selected secret injection modes;
- LLM-created or LLM-expanded conversation/agent grants;
- a secret broker socket or capability tokens;
- remote secret creation, update, deletion, or rotation;
- remote vault browsing or unrestricted record enumeration;
- arbitrary provider dependency chains;
- provider credentials obtained from the same provider they unlock;
- cross-user provider access;
- persistent plaintext caches;
- a generic dynamic-credential bundle exposed as a new runtime type;
- automatic migration or upload of local secret values to a remote provider;
- changes to the current global/user/conversation precedence;
- changes to bash and executeScript environment behavior.

Remote providers are read-only in V1. PawFlow stores references and resolves
values; the external provider remains the source of truth.

## 4. Existing architecture

### 4.1 Local secret persistence

core/config_store.py currently stores each inline secret as an AEAD ciphertext:

~~~json
{
  "github_token": "enc:v2:..."
}
~~~

Values at or above the spill threshold use an encrypted sidecar descriptor:

~~~json
{
  "large_secret": {
    "$type": "spilled",
    "$ref": "secrets__large_secret.dat.enc",
    "size": 1200000
  }
}
~~~

ConfigStore.load_secrets decrypts these representations and returns
Dict[str, ConfigValue]. Its current cache is keyed by file path, kind, and file
mtime.

### 4.2 Resolution and precedence

core/expression.py loads global, user, and conversation secrets and resolves:

~~~text
conversation -> user -> global
~~~

The exact-scope important modifier remains supported. No flow-scoped secrets
exist.

### 4.3 Tool relay behavior

services/_tool_relay_base.py currently:

- loads all effective secrets into the environment for bash and executeScript;
- uppercases environment names;
- loads the same secret values for output redaction.

services/_tool_relay_cache_req.py caches those materialized mappings using local
configuration fingerprints.

External references must fit behind this boundary. The relay must still receive
an ordinary dictionary of plaintext values for the duration of a tool call.

### 4.4 Secret management surfaces

Local secrets are currently managed through:

- StoreSecretHandler and ListSecretsHandler in
  core/handlers/help_secrets.py;
- actions in tasks/ai/actions/secrets_variables.py;
- slash commands and the CLI secret commands;
- the runtime UI, which reads and writes raw encrypted entries.

Every mutation surface must preserve external-reference objects instead of
assuming that every raw entry is a string.

### 4.5 Service infrastructure

ServiceRegistry already persists service definitions at global, user, and
conversation scope. Sensitive service-schema fields are encrypted at rest. A
new secretProvider service can therefore reuse:

- service discovery and lifecycle;
- scope resolution;
- sensitive-field encryption;
- validation;
- UI installation and editing;
- global/user/conversation persistence.

## 5. Non-negotiable invariants

1. **One logical namespace.** Local and external entries use the same secret
   names and the same scope precedence.
2. **One resolved contract.** Consumers receive ConfigValue objects and do not
   branch on provider type.
3. **No provider syntax in expressions.** Keeper notation, ARNs, record UIDs,
   and provider service IDs never appear in ${...}.
4. **No LLM provider decision.** The provider is chosen when the secret entry is
   created, not when the secret is consumed.
5. **No implicit fallback.** Once an alias matches at a scope, a provider error
   cannot cause lookup at a lower scope.
6. **No default provider.** An external entry names one explicit provider
   service.
7. **No anonymous ownership fallback.** User-scoped mutations require a real
   user identity; conversation-scoped mutations require an owned conversation.
8. **Scope compatibility.** A global entry may reference only a global provider;
   a user entry may reference a user or global provider; a conversation entry
   may reference a conversation, same-user, or global provider.
9. **No cross-user lookup.** A provider belonging to another user is never a
   candidate.
10. **No plaintext persistence.** Remote values exist only in process memory.
11. **No value disclosure.** Logs, audit events, UI payloads, API responses,
    exceptions, cache keys, and metrics never contain resolved values.
12. **Bounded freshness.** Cache configuration defines the maximum normal delay
    before a remote rotation is observed.
13. **Expiration wins.** Temporary credentials are never returned after their
    provider expiration, regardless of configured TTL.
14. **Read-only V1.** Creating an external PawFlow entry never writes to the
    external provider.
15. **Lazy startup.** Provider values are not eagerly fetched when PawFlow
    starts or when a service is registered.
16. **No self-bootstrap.** Provider authentication cannot reference a secret
    served by that provider.
17. **Explicit errors.** Missing provider parameters raise validation errors;
    there is no hidden provider or credential fallback.
18. **Async-safe I/O.** Provider HTTP/SDK work never runs directly on an
    asyncio/UI event loop.
19. **Existing local format remains valid.** No rewrite of current ciphertexts
    is required.
20. **Conversation policy is the ceiling.** An agent policy can only remove
    grants from the conversation envelope; it can never add one.
21. **Authorization precedes materialization.** A denied entry is filtered
    before local decryption, provider cache lookup, remote fetch, environment
    injection, or redaction collection.
22. **Exact grants.** A grant identifies both the exact alias and winning source
    scope. A same-name secret introduced at another scope is not silently
    authorized.
23. **No self-grant.** An LLM or agent cannot modify its own allowlist through a
    normal tool call. Policy mutation is an owner/admin operation behind an
    explicit approval boundary.
24. **Identity propagates.** Nested tool calls, delegated work, and background
    work retain the originating canonical agent identity when they can resolve
    secrets.
25. **Existing consumers remain provider-blind.**

## 6. Target architecture

~~~text
secrets.json / conv_secrets
         |
         | raw entry
         v
+---------------------+
| SecretEntryCodec    |
| local | external    |
+---------------------+
         |
         v
+-----------------------------+
| Scope precedence            |
| conversation > user > global|
+-----------------------------+
         |
         v
Winning candidates:
Dict[SecretIdentity, SecretEntry]
         |
         v
+-----------------------------+
| SecretAccessPolicy          |
| conversation ∩ agent        |
+-----------------------------+
         |
         v
Authorized entries
         |
         v
+---------------------+      +----------------------+
| SecretResolver      |----->| ExternalSecretCache  |
| local | provider    |      | TTL + single-flight  |
+---------------------+      +----------------------+
         |                           |
         | cache miss                v
         |                    +----------------------+
         +------------------->| secretProvider       |
                              | registered adapter   |
                              +----------------------+
                                        |
                                        v
                              external source of truth

Authorized resolved output:
Dict[str, ConfigValue]
         |
         +--> expression.py
         +--> service configuration
         +--> tool relay environment
         +--> result redaction
~~~

Only the entry codec, scope/access filter, resolver, cache, provider service, and
existing secret load/mutation seams know that external references exist.

## 7. Persistent entry format

### 7.1 Local entry

The existing formats remain byte-for-byte valid:

~~~json
{
  "api_key": "enc:v2:..."
}
~~~

and:

~~~json
{
  "document": {
    "$type": "spilled",
    "$ref": "secrets__document.dat.enc",
    "size": 1200000
  }
}
~~~

### 7.2 External entry

Use a versioned tagged object:

~~~json
{
  "github_token": {
    "$type": "external_secret",
    "version": 1,
    "provider_service": "keeper-production",
    "locator": "enc:v2:..."
  }
}
~~~

locator is an AEAD-encrypted canonical JSON object. Encrypting it avoids leaking
record UIDs, secret ARNs, account structure, field labels, or credential-source
details from a copied configuration file.

The non-secret envelope contains only:

- $type;
- schema version;
- explicit provider service ID;
- encrypted locator.

Provider type is obtained from the referenced service, not duplicated in the
entry. This prevents the entry and service configuration from disagreeing.

### 7.3 Provider locators

Keeper example before locator encryption:

~~~json
{
  "record_uid": "3fR7...",
  "field": {
    "kind": "standard",
    "name": "password",
    "index": 0
  }
}
~~~

AWS Secrets Manager example:

~~~json
{
  "secret_id": "arn:aws:secretsmanager:eu-west-1:123456789012:secret:app/prod-AbCd",
  "json_key": "api_key",
  "version_stage": "AWSCURRENT"
}
~~~

AWS credential-chain example:

~~~json
{
  "source": "default_chain",
  "field": "session_token"
}
~~~

Rules:

- Keeper record_uid is required in V1; title lookup is not accepted because
  titles can be duplicated or renamed.
- AWS cross-account references require a complete ARN.
- version_id and version_stage are mutually exclusive.
- json_key is optional and uses a single JSON object key in V1; JSONPath is
  deferred.
- Binary provider values are supported only when the existing ConfigValue
  consumer path can carry bytes. Environment injection remains UTF-8 text only.
- Locators are validated by the selected provider before persistence.

### 7.4 Conversation entries

Conversation extras currently hold conv_secrets. They adopt the same union:

~~~text
encrypted local string | external_secret object
~~~

The same SecretEntryCodec handles file-backed and conversation-backed entries.
No second external-reference format is allowed.

## 8. Core types and contracts

New module core/secret_entries.py:

~~~python
@dataclass(frozen=True)
class LocalSecretEntry:
    value: ConfigValue

@dataclass(frozen=True)
class ExternalSecretRef:
    version: int
    provider_service: str
    locator: Mapping[str, Any]

SecretEntry = LocalSecretEntry | ExternalSecretRef
~~~

New module core/secret_provider.py:

~~~python
@dataclass(frozen=True)
class SecretResolveContext:
    scope: str
    scope_id: str
    user_id: str
    conversation_id: str
    agent_name: str
    consumer: str
    request_id: str
    execution_target: str
    access_policy_revision: str

@dataclass(frozen=True)
class ProviderValue:
    value: bytes
    version: str
    fetched_at: float
    expires_at: float | None = None

@dataclass(frozen=True)
class ProviderCapabilities:
    versioned: bool = False
    binary: bool = False
    record_fields: bool = False
    expiring: bool = False
    workload_identity: bool = False

class ExternalSecretProvider(Protocol):
    provider_name: str
    capabilities: ProviderCapabilities

    def config_schema(self) -> Mapping[str, Any]: ...
    def locator_schema(self) -> Mapping[str, Any]: ...
    def validate_locator(self, locator: Mapping[str, Any]) -> None: ...
    def cache_identity(self, locator: Mapping[str, Any]) -> Hashable: ...
    async def fetch(
        self,
        locator: Mapping[str, Any],
        context: SecretResolveContext,
    ) -> ProviderValue: ...
~~~

cache_identity identifies the remote object before field extraction. This is
important for multi-field values and AWS credentials: several PawFlow aliases
can share one fetched record or one temporary credential set without making
separate network requests or observing inconsistent versions.

New module core/secret_resolver.py:

~~~python
class SecretResolver:
    async def resolve_entry(
        self,
        name: str,
        entry: SecretEntry,
        context: SecretResolveContext,
    ) -> ConfigValue: ...

    async def resolve_mapping(
        self,
        entries: Mapping[str, SecretEntry],
        context: SecretResolveContext,
    ) -> dict[str, ConfigValue]: ...

    def invalidate(
        self,
        *,
        provider_service: str | None = None,
        scope: str | None = None,
        scope_id: str | None = None,
        name: str | None = None,
    ) -> None: ...
~~~

A synchronous facade may be retained for existing worker-thread call sites.
It must submit provider work to the bounded secret-resolution executor. It must
detect and reject direct use from an event-loop thread; async call sites use the
async API.

New module core/secret_access_policy.py:

~~~python
@dataclass(frozen=True)
class SecretIdentity:
    name: str
    source_scope: str
    source_scope_id: str

@dataclass(frozen=True)
class SecretGrant:
    name: str
    source_scope: str

@dataclass(frozen=True)
class EffectiveSecretPolicy:
    conversation_grants: frozenset[SecretGrant] | None
    agent_grants: frozenset[SecretGrant] | None
    revision: str

    def allows(self, identity: SecretIdentity) -> bool: ...
~~~

None means that this policy layer adds no restriction, preserving current
behavior for existing records. An explicit empty set means that no secret is
allowed. source_scope_id is derived from the authenticated conversation/user
context and is never accepted as an arbitrary caller-provided owner ID.

## 9. secretProvider service

### 9.1 Service shape

Add services/secret_provider_service.py with:

~~~text
TYPE = secretProvider
CATEGORY = security
~~~

Common configuration:

| Field | Required | Sensitive | Purpose |
|---|---:|---:|---|
| provider | yes | no | Adapter name registered in SecretProviderFactory |
| execution_target | yes | no | server in V1; relay reserved for a later phase |
| cache_ttl_seconds | no | no | Fresh-cache TTL, default 300 |
| timeout_seconds | no | no | Per-fetch deadline, default 10 |
| max_concurrency | no | no | Provider-local concurrency bound, default 8 |
All vendor fields come from the selected adapter's config_schema. The service
editor composes the common schema with that adapter schema; it does not contain
an ever-growing hard-coded union of AWS, Keeper, Azure, Vault, or SaaS fields.
The same rule applies to locator forms through locator_schema.

validate_config must reject:

- unknown providers;
- missing provider-specific fields;
- unsupported execution targets;
- non-positive or excessive TTL/timeouts;
- static AWS access keys in V1;
- a Keeper one-time token in persisted configuration;
- provider references inside bootstrap credential fields.

### 9.2 Provider registry

A small SecretProviderFactory maps provider names to adapters. It is distinct
from ServiceFactory:

~~~text
ServiceFactory:
  secretProvider -> SecretProviderService

SecretProviderFactory:
  keeper                  -> KeeperSecretAdapter
  aws_secrets_manager     -> AwsSecretsManagerAdapter
  aws_ssm_parameter_store -> AwsParameterStoreAdapter
  aws_credentials         -> AwsCredentialChainAdapter
  azure_key_vault         -> AzureKeyVaultAdapter
  gcp_secret_manager      -> GcpSecretManagerAdapter
  hashicorp_vault_kv      -> HashicorpVaultKvAdapter
  onepassword             -> OnePasswordAdapter
  ...                     -> additional vetted adapters
~~~

The service owns configuration and lifecycle. The adapter owns vendor-specific
validation, cache identity, fetch, and field extraction.

The registry is the extension seam. Adding a provider must not change
SecretEntry, SecretResolver, expression handling, access policies, tool relay,
or cache semantics. It adds one adapter, its optional dependency extra, contract
tests, locator/config UI metadata, and documentation.

Provider adapters may become a vetted PFP extension point later. Initial
adapters are built in; arbitrary package code must not silently become a
server-side credential provider.

## 10. Resolution algorithm

For each existing consumer:

1. Load and decode the raw global, user, and conversation entry mappings without
   decrypting values or contacting providers.
2. Apply the current conversation -> user -> global precedence to select one
   winning SecretIdentity and SecretEntry per alias.
3. Load the conversation policy and, when agent_name is present, the policy of
   that canonical conversation agent instance.
4. Filter winning entries through conversation ∩ agent authorization.
5. Materialize only the authorized winning entries:
   1. local entries follow the current decrypt/sidecar path;
   2. external entries validate their version;
   3. resolve the named secretProvider service under the binding's scope;
   4. verify scope compatibility and owner identity;
   5. verify execution_target compatibility;
   6. decrypt and validate the locator;
   7. calculate the cache key;
   8. return the fresh cached value, or fetch through the provider;
   9. extract the selected field;
   10. wrap the bytes/string in ConfigValue.
6. Return the same Dict[str, ConfigValue] shape as today.

Selecting the winning entry before materialization avoids decrypting or fetching
an overridden lower-scope entry. A matching external entry remains authoritative
for that name. Provider failure produces an unavailable value and never reveals
or uses a lower-scope entry with the same name.

For bulk callers such as environment construction and redaction, an unavailable
external entry is represented as ConfigValue(value="") and a sanitized
resolution event is recorded. This matches the current corrupted-ciphertext
behavior and prevents one unrelated broken reference from aborting every shell
call. Direct validation/test operations return the typed provider error.

The resolver must never return the encrypted locator, provider error body, SDK
request object, or ciphertext as a secret value.

### 10.1 Conversation and agent allowlists

Store the conversation policy in conversation extras:

~~~json
{
  "secret_access": {
    "mode": "allowlist",
    "revision": "uuid",
    "grants": [
      {"name": "github_token", "source_scope": "user"},
      {"name": "deploy_key", "source_scope": "conversation"}
    ]
  }
}
~~~

Store the agent restriction on the conversation agent instance, not on the
global agent template:

~~~json
{
  "secret_access": {
    "mode": "allowlist",
    "revision": "uuid",
    "grants": [
      {"name": "github_token", "source_scope": "user"}
    ]
  }
}
~~~

Semantics:

- missing secret_access means unrestricted by that layer, preserving existing
  conversations and agent instances;
- mode=allowlist with an empty grants array means no secrets;
- conversation grants define the maximum set C;
- absent agent policy means no additional reduction;
- an explicit agent set A produces effective grants C ∩ A;
- when the conversation policy is absent, an explicit agent set produces
  universe ∩ A;
- comparison uses the exact stored alias and source scope before environment
  uppercasing;
- the user owner is implicit for a user grant and the current root conversation
  is implicit for a conversation grant;
- a grant never authorizes a same-name value from a different source scope;
- duplicate aliases that collide after environment uppercasing are rejected or
  diagnosed before injection rather than resolved nondeterministically.

The policy applies identically to local and external entries. It filters:

- expression resolution in that conversation;
- tool argument substitution;
- bash and executeScript secret environments;
- secret values collected for result redaction;
- nested/delegated/background work carrying the same origin context.

Parameters and ordinary environment variables are not secret grants and retain
their current behavior.

Calls without a conversation context, such as global server-service startup,
have no conversation or agent policy and retain their existing scope rules.
Conversation-bound work always uses the root conversation policy. A delegated
call uses the target agent instance's restriction. Background work records the
originating canonical agent name so dropping the live request context does not
drop the restriction.

The allowlist governs access to secret values, not use of an already-connected
service that internally holds credentials. Restricting which agents may invoke
such a service is a service/tool ACL concern and must not be implied by this
feature.

### 10.2 Policy changes and caches

Every policy mutation creates a new UUID revision and invalidates materialized
tool-relay environment/redaction snapshots for the root conversation and
affected agent.

ExternalSecretCache may retain a provider value for another authorized context,
but authorization is checked before every cache lookup. A cache hit can never
bypass a newly narrowed policy. access_policy_revision participates in
materialized mapping cache keys, not in provider value cache keys.

## 11. Cache design

External provider caching is mandatory.

### 11.1 Separation from ConfigStore cache

The current ConfigStore mtime cache may cache:

- parsed entry envelopes;
- decrypted local values;
- decrypted locator metadata.

It must **not** indefinitely cache resolved remote values. Every materialization
passes external references through ExternalSecretCache, even when the raw file
mtime is unchanged.

### 11.2 Cache key

The key includes:

~~~text
provider service scope
provider service scope_id
provider service ID
provider service definition revision
provider adapter name
provider cache_identity(locator)
effective principal/owner
execution target
~~~

The selected field may be excluded when the provider fetches a whole record and
extracts fields locally. This lets several aliases share one fetch.

No value, secret field, token, or plaintext locator appears in a cache key,
metric label, or log line. Hash a canonical locator identity where necessary.

### 11.3 Freshness

Defaults:

- positive TTL: 300 seconds;
- negative/transient-error TTL: 5 seconds;
- single-flight per cache key;
- in-memory only;
- maximum 1,024 entries with LRU eviction;
- no stale value after TTL by default.

ProviderValue.expires_at caps the cache expiry:

~~~text
effective expiry = min(fetched_at + configured TTL, provider expires_at - skew)
~~~

Use a safety skew for temporary AWS credentials. An expired credential is never
returned.

Within the fresh TTL, a provider outage does not matter because the cached value
is still valid. After expiry, a failed refresh yields an unavailable entry.
Optional stale-on-error is deferred until it has a provider-specific revocation
model.

### 11.4 Rotation behavior

For unpinned references such as AWS AWSCURRENT, a rotated value is observed no
later than the configured TTL after the provider exposes it.

Version-pinned references remain pinned until the PawFlow entry changes.

Each tool call already captures the values used for its redaction pass. An
in-flight call therefore continues to redact the exact cached value injected
into that call even if a later call observes a rotation.

### 11.5 Invalidation

Invalidate matching entries when:

- a secret reference is created, replaced, or deleted;
- its provider service is updated, disabled, or removed;
- the user invokes refresh/test with refresh=true;
- the provider adapter reports a terminal expiration;
- tests reset registries or configuration stores.

Do not rely on filesystem mtime to detect provider-side rotation.

## 12. Provider implementations

### 12.1 Local values

Local values remain the existing implementation. They do not require a
secretProvider service and do not enter ExternalSecretCache.

Local decrypt failure keeps the existing fail-closed behavior: never return
ciphertext as a credential.

### 12.2 Keeper Secrets Manager

Add services/secret_providers/keeper.py.

V1 behavior:

- read-only Keeper application;
- locator by record UID;
- standard field or custom field selection;
- optional zero-based value index;
- record-level cache identity so several fields share one fetch;
- provider revision recorded when available;
- no record listing;
- no update, create, delete, or rotation.

Enrollment is a special transaction:

1. Receive an explicit Keeper one-time access token.
2. Create an in-memory client configuration.
3. Perform the binding fetch required by Keeper.
4. Validate access with a non-secret metadata-only result.
5. Atomically install/update the secretProvider service with keeper_config marked
   sensitive.
6. Discard the one-time token.
7. If any step fails, persist neither the token nor partial client
   configuration; the user must supply a new token.

The ordinary service-install path must reject one-time tokens so they cannot be
stored accidentally.

The Keeper SDK is an optional dependency. Missing dependency errors name the
required install extra without including configuration values.

### 12.3 AWS Secrets Manager

Add services/secret_providers/aws_secrets_manager.py.

Authentication uses the standard AWS SDK credential chain. Preferred sources
are workload roles, web identity, IAM Identity Center, container credentials,
and instance metadata. PawFlow does not add a static-access-key field to the
service schema in V1.

Supported locator fields:

- secret_id;
- json_key;
- version_id or version_stage.

Fetch with GetSecretValue. Support SecretString and SecretBinary. When json_key
is present, SecretString must parse as a JSON object and contain that exact key.

The returned VersionId becomes ProviderValue.version. CloudTrail provides the
provider-side access audit.

Optional role_arn uses STS AssumeRole before Secrets Manager access. The assumed
credential cache is internal to the adapter and expiration-aware.

### 12.4 AWS credential chain

Add services/secret_providers/aws_credentials.py after AWS Secrets Manager.

This adapter exposes the current SDK credential set through ordinary scalar
secret references, preserving the single-value runtime contract. A user creates
three PawFlow entries if a program needs the usual AWS variables:

~~~text
AWS_ACCESS_KEY_ID     -> field access_key_id
AWS_SECRET_ACCESS_KEY -> field secret_access_key
AWS_SESSION_TOKEN     -> field session_token
~~~

All fields share one atomic provider cache identity so they come from the same
credential generation and expire together. The adapter uses the SDK's refresh
mechanism and sets ProviderValue.expires_at.

This adapter is intended for existing programs that require environment
variables. Code using an AWS SDK should normally use the native credential chain
directly instead of exporting credentials through PawFlow.

### 12.5 AWS Systems Manager Parameter Store

Add services/secret_providers/aws_ssm_parameter_store.py.

Support String and SecureString by exact parameter name or ARN, with an optional
version/label selector and WithDecryption for SecureString. Use the same AWS SDK
credential chain and optional AssumeRole helper as AWS Secrets Manager.

Parameter Store is useful for deployments that already keep configuration and
some encrypted values there. AWS recommends Secrets Manager for credentials
that need purpose-built rotation, so the UI should describe the distinction
rather than present both products as identical.

### 12.6 Azure Key Vault

Add services/secret_providers/azure_key_vault.py.

Locator:

~~~json
{
  "vault_url": "https://example.vault.azure.net",
  "secret_name": "github-token",
  "version": ""
}
~~~

Use Azure Identity DefaultAzureCredential, which supports local developer
identity and managed identity without provider-specific PawFlow expressions.
An empty version selects the current version. ProviderValue.version uses the
returned secret version/ID.

V1 reads secrets only. Keys and certificates are separate Key Vault object
types and must not be coerced into this adapter.

### 12.7 Google Cloud Secret Manager

Add services/secret_providers/gcp_secret_manager.py.

Locator:

~~~json
{
  "project": "my-project",
  "secret": "github-token",
  "version": "latest"
}
~~~

Use Application Default Credentials and workload identity. Accept a numeric
version or provider alias such as latest. Preserve raw payload bytes, record the
returned version resource name, and let ConfigValue enforce the consumer's text
requirements.

The adapter does not cover Google Cloud KMS keys or Parameter Manager; those are
different products/contracts.

### 12.8 HashiCorp Vault KV

Add services/secret_providers/hashicorp_vault_kv.py.

Support KV v1 and KV v2 explicitly:

~~~json
{
  "mount": "secret",
  "path": "applications/payments",
  "field": "api_key",
  "kv_version": 2,
  "version": 0
}
~~~

Authentication options may include an operator-supplied token, AppRole,
Kubernetes auth, JWT/OIDC auth, or another adapter-supported workload login.
Tokens, Secret IDs, and wrapped responses are sensitive service fields.

KV v2 version metadata maps cleanly to ProviderValue.version. KV v1 is
unversioned. The locator must separate mount and logical path so the adapter,
not the caller, handles the KV v1/v2 API path difference.

Vault dynamic secret engines are not silently treated as KV. They return leased
credentials with renewal/revocation semantics. ProviderValue.expires_at is
enough for expiring SDK credentials but not for a renewable lease. A separate
lifecycle contract, adapter, and tests are required before enabling database,
cloud, PKI, or other dynamic engines.

### 12.9 1Password Secrets Automation

Add services/secret_providers/onepassword.py.

Support item and field lookup by stable vault/item identifiers. Prefer service
accounts or the current 1Password workload credential mechanism; optionally
support a configured Connect server. Service-account/Connect credentials are
sensitive service fields.

Item titles are display metadata, not stable locators. The canonical locator
uses vault ID, item ID, and field ID or an unambiguous field selector. Fetch one
item and share its cache identity across multiple PawFlow aliases selecting
different fields.

### 12.10 Provider portfolio and delivery priority

The following portfolio should be considered. "Built-in" means a vetted PawFlow
adapter with contract tests, not that every adapter must ship in the first
commit.

| Provider/source | Canonical locator | Preferred authentication | Capabilities relevant to PawFlow | Delivery |
|---|---|---|---|---|
| Keeper Secrets Manager | application/record UID/field | bound KSM client config | record fields, revision | Wave A |
| AWS Secrets Manager | ARN/name/version stage or ID | AWS SDK chain/workload role | versions, binary, JSON fields, rotation labels | Wave A |
| Azure Key Vault Secrets | vault URL/name/version | DefaultAzureCredential/managed identity | versions, string values | Wave A |
| Google Cloud Secret Manager | project/secret/version | ADC/workload identity | versions/aliases, binary payload | Wave A |
| HashiCorp Vault KV v1/v2 | endpoint/namespace/mount/path/field/version | workload auth, AppRole, Kubernetes, JWT, token | maps, KV v2 versions | Wave A |
| AWS SSM Parameter Store | name or ARN/version/label | AWS SDK chain/workload role | String/SecureString, versions, hierarchy | Wave B |
| 1Password Secrets Automation | vault ID/item ID/field | service account, Connect, credential broker | item fields, usage audit | Wave B |
| Infisical | project/environment/path/name/version | universal or cloud/workload identity | paths, versions, references | Wave B |
| Akeyless | gateway/path/name/version | cloud identity or access credentials | static and rotating secrets | Wave B |
| Doppler | project/config/secret name | service token/workload identity where available | configuration-scoped values | Wave B |
| Bitwarden Secrets Manager | project/secret ID | machine account/access token | project-scoped scalar secrets | Wave B |
| CyberArk Conjur / Secrets Manager | account/policy path/variable | workload authenticator/identity | enterprise policy paths, rotation ecosystem | Wave C |
| Delinea Secret Server | server/secret ID/field | application account/OAuth | enterprise records and fields | Wave C |
| OCI Secret Management | vault/secret/version | OCI resource or instance principal | cloud-native versions/stages | Wave C |
| IBM Cloud Secrets Manager | instance/secret ID/version | trusted profile/service identity | cloud-native secret types and versions | Wave C |
| Alibaba Cloud KMS Secrets Manager | region/secret/version stage | RAM role/workload identity | cloud-native versions/stages | Wave C |

Wave rules:

- **Wave A** validates the generic design across record-oriented, cloud-native,
  binary, map, versioned, self-hosted, and workload-identity variants.
- **Wave B** broadens SaaS and configuration-store coverage after the contract
  suite is stable.
- **Wave C** targets enterprise/customer demand and may require access to vendor
  test tenants.
- An adapter can move earlier when a deployment requires it, because no wave
  changes the persistent external_secret format.

Each adapter must declare:

- config_schema and locator_schema;
- capabilities;
- sensitive bootstrap fields;
- supported execution targets;
- normalized cache identity;
- version and expiration mapping;
- safe error mapping;
- optional dependency extra;
- official documentation URL;
- fake/stub contract tests;
- an opt-in live integration test recipe.

### 12.11 Operational sources with separate adapters

Some common "secret sources" should not be presented as remote vault vendors,
but can still implement the same read-only adapter contract:

| Source | Recommended treatment |
|---|---|
| Kubernetes Secret | Exact cluster context/namespace/name/data key; use service-account or kubeconfig identity; warn that Kubernetes Secrets are not encrypted in etcd by default unless configured. |
| Docker/Kubernetes mounted secret file | Local-file adapter restricted to an operator-approved root; file mtime/revision participates in cache identity. |
| Mozilla SOPS encrypted file | SOPS-file adapter using operator-configured KMS/age/PGP identity; decrypt in memory and select an exact document key. |
| Environment variable | Keep as bootstrap/config input, not a remotely managed secret provider. |
| Generic HTTP URL | Do not provide a generic provider: it creates an SSRF and authentication-policy bypass. Add a named, validated adapter instead. |

Kubernetes and SOPS can be useful for self-hosted installations, but they should
ship after the remote-provider contract is proven. Their locator and filesystem
security tests differ from HTTP SDK adapters.

## 13. Async and concurrency model

Provider SDKs may expose synchronous clients. Wrap their network calls in a
dedicated bounded executor; do not run them on the server event loop.

Requirements:

- async resolver API for async consumers;
- bounded worker pool for synchronous SDK calls;
- provider and global concurrency limits;
- timeout around every fetch;
- cancellation stops waiting and discards late results;
- single-flight prevents duplicate concurrent reads;
- no lock is held during network I/O;
- cache insertion is atomic;
- service update/disconnect cannot publish a result under the old service
  revision;
- shutdown clears value references from cache and joins the executor within a
  bounded deadline.

Python cannot guarantee physical zeroization of immutable strings. Minimize
copies, retain values only for the cache lifetime, and never serialize them.

## 14. Management API and UI

### 14.1 Store local secret

store_secret remains backward-compatible:

~~~json
{
  "key": "github_token",
  "value": "..."
}
~~~

Writing a local value over an external reference replaces the reference after
the normal approval gate and invalidates its cache entry.

### 14.2 Bind external secret

Add bind_external_secret:

~~~json
{
  "key": "github_token",
  "provider_service": "keeper-production",
  "locator": {
    "record_uid": "3fR7...",
    "field": {
      "kind": "standard",
      "name": "password",
      "index": 0
    }
  },
  "scope": "user"
}
~~~

Behavior:

1. validate identity and scope;
2. resolve and authorize the provider service;
3. validate the locator;
4. optionally perform one test fetch;
5. encrypt the locator;
6. atomically store the external entry;
7. invalidate relevant caches;
8. return alias, scope, provider ID, and status only.

Never return the resolved value.

This mutation remains approval-gated and should not be marked read-only.

### 14.3 List secrets

Extend list_secrets without resolving values:

~~~text
github_token   external  keeper-production
smtp_password  local
aws_session    external  aws-workload
~~~

The LLM may see alias names and provider-neutral descriptions, but does not need
the provider information to consume a secret. The default compact tool result
may continue to list only aliases; UI/admin detail may show backing type and
provider ID.

Listing must parse raw entries and must not contact external providers.

### 14.4 Test and refresh

Add test_external_secret:

- validates the reference;
- bypasses or optionally refreshes the cache;
- returns reachable, version, fetched_at, expiry, latency, and error code;
- never returns length, hash, prefix, suffix, JSON keys, or value.

Add refresh_external_secret_cache for an alias/provider/scope. It invalidates;
the next normal resolution fetches. An optional test_after_refresh performs a
fetch and returns metadata only.

### 14.5 Delete and replace

Every existing delete/edit route must operate on the raw entry union. Deleting a
reference never deletes the external record. Replacing it never writes to the
external provider.

### 14.6 Runtime UI

The secret editor offers two mutually exclusive modes:

- Local value;
- External reference.

External mode selects a visible secretProvider service and renders its
provider-specific locator fields. The UI sends locators only to PawFlow; values
are never previewed.

The list shows:

- alias;
- scope;
- Local or External;
- provider service for external entries;
- last metadata-only test status, if explicitly requested.

It does not automatically test every reference when opening the page.

### 14.7 Conversation and agent access management

Add an owner/admin management surface for the two policy layers. This is not a
normal LLM tool and is not included in the routine agent tool catalog.

Conversation operation:

~~~json
{
  "action": "set_conversation_secret_access",
  "conversation_id": "conv-id",
  "mode": "allowlist",
  "grants": [
    {"name": "github_token", "source_scope": "user"}
  ]
}
~~~

Agent-instance operation:

~~~json
{
  "action": "set_agent_secret_access",
  "conversation_id": "conv-id",
  "agent_name": "developer",
  "mode": "allowlist",
  "grants": [
    {"name": "github_token", "source_scope": "user"}
  ]
}
~~~

The server derives ownership and source scope IDs; callers cannot grant a secret
owned by an arbitrary user or conversation. It rejects an agent grant outside
the explicit conversation allowlist. If the conversation layer is unrestricted,
the agent may still be restricted to any secret currently visible to that
conversation.

The UI presents the effective secret catalog with:

- alias;
- winning source scope;
- local/external backing type;
- conversation checkbox;
- per-agent checkbox enabled only when the conversation permits that grant.

Changing a higher-precedence entry so that an alias now resolves from a
different source scope leaves the old grant non-matching and shows it as stale;
it does not silently retarget the grant. The owner must review and replace it.

Policy read APIs return names, scopes, mode, and revisions only. Policy updates
require conversation ownership or administrator authority, an approval gate,
and a compare-and-swap revision to prevent concurrent lost updates.

## 15. Provider scope and lifecycle

Provider resolution uses ServiceRegistry's established scope model.

Allowed references:

| Secret entry scope | Allowed provider scopes |
|---|---|
| global | global |
| user U | user U, global |
| conversation C owned by U | conversation C, user U, global |

If multiple visible providers share an ID, resolve by the binding scope chain,
but persist enough scope identity in the validated entry or canonical service
reference to avoid later ambiguity. The implementation must follow the same
stable service-reference rule already used by other service_ref fields.

A disabled, missing, incompatible, or disconnected provider produces a
sanitized unavailable result. There is no provider-name fallback.

Removing a provider service must:

1. identify references to it;
2. require explicit confirmation if references exist;
3. show alias names and scopes, never locators or values;
4. either refuse removal or leave clearly broken references according to the
   existing service-removal policy;
5. invalidate its entire cache partition.

Provider connect verifies configuration shape and SDK availability but does not
fetch all referenced secrets.

## 16. Error model

Define stable codes:

- SECRET_ENTRY_INVALID
- SECRET_ENTRY_VERSION_UNSUPPORTED
- SECRET_PROVIDER_NOT_FOUND
- SECRET_PROVIDER_DISABLED
- SECRET_PROVIDER_SCOPE_DENIED
- SECRET_PROVIDER_TARGET_MISMATCH
- SECRET_LOCATOR_INVALID
- SECRET_FETCH_TIMEOUT
- SECRET_FETCH_AUTH_FAILED
- SECRET_FETCH_FORBIDDEN
- SECRET_REMOTE_NOT_FOUND
- SECRET_REMOTE_VERSION_NOT_FOUND
- SECRET_REMOTE_VALUE_INVALID
- SECRET_REMOTE_VALUE_EXPIRED
- SECRET_PROVIDER_UNAVAILABLE
- SECRET_ENROLLMENT_FAILED

Provider exception text is converted to a bounded safe message. Do not include
HTTP bodies, request parameters, record contents, tokens, credentials, or SDK
object repr output.

Bulk resolution records the code and produces an empty ConfigValue for the
failed alias. Direct test/bind operations return the code to the authorized
caller.

## 17. Audit and observability

Emit metadata-only events:

- secret.external.bind;
- secret.external.unbind;
- secret.external.resolve;
- secret.external.cache_hit;
- secret.external.cache_miss;
- secret.external.refresh;
- secret.provider.enroll;
- secret.provider.error.

Each event has:

- UUID;
- UTC timestamp;
- actor/principal;
- user and conversation scope where applicable;
- alias;
- provider service ID and scope;
- provider type;
- locator identity hash or provider-safe remote ID;
- provider version;
- consumer category;
- request ID;
- cache outcome;
- latency;
- success/error code;
- expiry metadata.

Never emit the value, serialized locator, credential material, raw external
error, or authorization header.

Metrics use bounded labels. Alias, user ID, record UID, and ARN must not become
unbounded metric labels. Appropriate counters/histograms include provider type,
outcome code, cache result, and latency bucket.

Provider health and entry health are separate:

- provider health checks configuration/SDK reachability;
- entry tests check a particular locator only when requested;
- startup does neither remote enumeration nor all-entry fetch.

## 18. Security considerations

### 18.1 Bootstrap credentials

Preferred authentication:

- Keeper bound client configuration encrypted as a sensitive service field;
- AWS workload role, web identity, container credentials, instance profile, or
  IAM Identity Center;
- operator-mounted environment/file where the SDK requires it.

Static AWS access-key fields are not introduced in V1.

Provider bootstrap cannot be a PawFlow external reference. This prevents cycles
and lockout where the credential needed to open a vault lives in that vault.

### 18.2 Permissions

Authorization is checked when the reference is created and again on every cache
miss/fetch. A cached value does not bypass a revoked PawFlow service/scope
permission: service visibility and revision are verified before cache lookup.

Conversation and agent allowlists are checked before materialization and before
cache lookup. Their intersection is computed at the trusted core boundary, not
in the UI, prompt, handler description, or relay client.

An agent cannot broaden either policy. A global agent definition may declare
requested secret aliases as installation metadata for the owner's review, but
that declaration is never a grant. Actual agent grants live on the instance
inside the conversation.

### 18.3 Locator confidentiality and SSRF

Locators are encrypted. Provider adapters construct requests from validated
fields; they do not fetch arbitrary locator URLs.

Custom endpoints:

- disabled by default in production;
- administrator-only when enabled;
- HTTPS required outside explicit test mode;
- subject to hostname/IP validation and egress policy;
- never accepted from an LLM-generated expression.

### 18.4 Cache confidentiality

The cache is process-local memory only. It is not included in diagnostics,
crash reports, persistence, pickles, snapshots, or cache introspection APIs.

Cache-clear operations drop all references promptly. Forked worker processes
must not inherit a populated cache.

### 18.5 Existing exposure boundary

This feature preserves the environment-based consumer interface while narrowing
its input. bash and executeScript receive every secret in the authorized
conversation ∩ agent mapping, and receive no denied secret.

The policy is per conversation and agent instance, not per individual command.
Code executed by an authorized agent can read every secret granted to that
agent. A future per-tool project could narrow the boundary further without
changing the external-entry representation or these upper-level allowlists.

## 19. Persistence and migration

### 19.1 No bulk migration

Existing ciphertext strings and sidecar descriptors remain valid. The entry
decoder recognizes them as local entries.

No migration file rewrites existing secrets. The first external reference adds
only the new tagged object for that alias.

### 19.2 Typed raw APIs

Introduce raw entry APIs so mutation code does not decrypt and re-encrypt every
secret merely to change one item:

~~~python
ConfigStore.load_secret_entries_raw(path)
ConfigStore.save_secret_entries_raw(path, entries)
ConfigStore.upsert_local_secret(path, key, value)
ConfigStore.upsert_external_secret(path, key, ref)
ConfigStore.delete_secret_entry(path, key)
~~~

All mutations are atomic under the existing file-locking strategy, preserve
unrelated local ciphertexts and external objects, and invalidate both the raw
configuration cache and relevant external value cache.

Equivalent helpers update conversation extras atomically.

### 19.3 Backups and downgrade

Before writing the first external entry to an existing store, the normal config
backup mechanism must preserve the old file.

An older PawFlow version will treat external objects as invalid/empty secrets.
It must never stringify the object and use it as a credential. Document that
downgrade requires replacing/removing external entries first.

### 19.4 Revision

The external entry has its own schema version. Provider service definition
revision participates in the cache key. Updating provider auth/config therefore
cannot reuse values fetched under the previous definition.

## 20. File-by-file implementation map

### New core modules

- core/secret_entries.py
  - tagged entry types;
  - raw codec;
  - locator encryption/decryption;
  - schema-version validation.
- core/secret_provider.py
  - context/value/protocol;
  - adapter registry;
  - safe provider errors.
- core/secret_resolver.py
  - local/external dispatch;
  - scope and service validation;
  - pre-materialization access filtering;
  - mapping materialization;
  - sync/async facades.
- core/secret_access_policy.py
  - exact SecretIdentity and SecretGrant types;
  - conversation/agent intersection;
  - policy revision and validation;
  - root-conversation and canonical-agent context.
- core/external_secret_cache.py
  - TTL/LRU cache;
  - single-flight;
  - expiration caps;
  - invalidation.
- core/secret_audit.py
  - bounded metadata-only audit events.

### New service modules

- services/secret_provider_service.py
- services/secret_providers/__init__.py
- services/secret_providers/keeper.py
- services/secret_providers/aws_secrets_manager.py
- services/secret_providers/aws_ssm_parameter_store.py
- services/secret_providers/aws_credentials.py
- services/secret_providers/azure_key_vault.py
- services/secret_providers/gcp_secret_manager.py
- services/secret_providers/hashicorp_vault_kv.py
- services/secret_providers/onepassword.py
- later Wave B/C adapter modules listed in section 12.10

### Existing core changes

- core/config_store.py
  - recognize external tagged entries;
  - separate raw-entry caching from remote-value caching;
  - add atomic typed mutations;
  - preserve existing local formats.
- core/expression.py
  - route scope loads through SecretResolver;
  - accept the trusted conversation/agent resolution context;
  - preserve syntax and precedence.
- core/conv_agent_config.py
  - persist the optional per-instance secret allowlist;
  - canonicalize agent identity;
  - preserve missing-policy compatibility.
- core/conversation_store.py and conversation extras helpers
  - persist the optional conversation secret allowlist;
  - compare-and-swap policy revisions.
- core/service_registry.py and registration bootstrap
  - register secretProvider;
  - invalidate provider cache partitions on lifecycle changes.
- core/handlers/help_secrets.py
  - preserve store_secret;
  - add bind/test/refresh handlers;
  - list raw alias metadata without resolving.
- owner/admin secret-access actions
  - set/get conversation policy;
  - set/get per-agent-instance restrictions;
  - keep policy mutation out of the normal agent tool catalog.
- core/tool_approval.py
  - classify external bind/provider enrollment and policy edits as sensitive
    mutations;
  - keep listing metadata read-only.
- core/tool_selection.py
  - describe the external-binding workflow without exposing values.

### Tool relay changes

- services/_tool_relay_base.py
  - replace direct local/conv decryption with resolved secret mappings;
  - pass canonical agent identity;
  - preserve returned env and redaction shapes after filtering.
- services/_tool_relay_cache_req.py
  - include external cache generation/provider revision in fingerprints, or
    delegate freshness entirely to SecretResolver;
  - include conversation and agent policy revisions in materialized mapping
    fingerprints;
  - retain per-request materialized mappings only within their safe lifetime.
- services/_tool_relay_execute.py
  - pass the originating agent context through nested/background execution;
  - ensure the same resolved snapshot feeds execution and redaction.
- services/tool_relay_service.py
  - invalidate caches after external secret/provider mutations.

### Action, CLI, and UI changes

- tasks/ai/actions/secrets_variables.py
  - parse/list/upsert the entry union;
  - add external bind/test/refresh actions;
  - add owner/admin policy read/update actions.
- tasks/ai/actions/command_dispatch.py
  - add explicit slash commands if desired.
- pawflow_cli/commands/secrets.py
  - render local/external metadata;
  - add bind/test/refresh commands.
- mirrored desktop CLI runtime files
  - update through the repository's established sync/generation process rather
    than editing generated copies independently.
- runtime secret editor frontend
  - add Local/External choice and provider-specific locator form;
  - add conversation and per-agent allowlist matrices.
- service editor
  - render secretProvider schema and Keeper enrollment action.

### Documentation changes delivered with implementation

- docs/services.md
- docs/security_model.md
- docs/EXPRESSION_LANGUAGE.md
- docs/deployment.md
- CLI/help text for secrets and provider enrollment
- release notes and upgrade/downgrade notes

## 21. Test plan

### 21.1 Entry codec and persistence

Add tests/test_external_secret_entries.py:

- current ciphertext decodes as local;
- sidecar decodes as local;
- valid external reference round-trips;
- locator is encrypted at rest;
- unsupported version fails closed;
- malformed tagged objects never stringify into credential values;
- local update preserves unrelated external entries byte-for-byte;
- external update preserves unrelated local ciphertexts;
- conversation entries use the same codec;
- file and conversation mutations are atomic under concurrency;
- raw list never resolves providers.

### 21.2 Resolver contract

Add tests/test_secret_resolver.py with a fake provider:

- local and external entries yield identical ConfigValue interfaces;
- global/user/conversation precedence remains unchanged;
- precedence selects a winner before decryption or remote fetch;
- exact-scope expressions remain unchanged;
- a failing higher-scope external alias never falls through;
- provider scope compatibility matrix;
- cross-user provider denial;
- disabled/missing provider;
- provider definition revision invalidation;
- text, bytes, and invalid value handling;
- sanitized errors;
- no plaintext in repr/log records;
- sync facade refuses event-loop misuse.

Add tests/test_secret_access_policy.py:

- absent conversation and agent policies preserve current behavior;
- explicit empty conversation policy resolves no secrets;
- conversation allowlist filters local and external entries identically;
- absent agent policy adds no restriction;
- explicit empty agent policy resolves no secrets;
- effective access is conversation ∩ agent, never a union;
- an agent cannot grant itself or exceed the conversation policy;
- exact name plus source scope matching;
- a precedence change does not retarget an old grant;
- root sub-conversations retain the root policy;
- delegate uses the target canonical agent policy;
- nested and background calls retain origin policy;
- calls without a conversation keep existing server-service behavior;
- unauthorized entries are not decrypted, fetched, cached, or added to the
  redaction set;
- policy revision invalidates materialized environment caches;
- cached provider values never bypass a newly narrowed policy;
- uppercase environment-name collisions fail deterministically.

### 21.3 Cache

Add tests/test_external_secret_cache.py using a fake clock:

- hit within TTL;
- refresh after TTL;
- bounded rotation visibility;
- version-pinned identity;
- single-flight under concurrent misses;
- negative cache;
- LRU bound;
- targeted/provider/global invalidation;
- provider update changes cache partition;
- temporary credential expiration caps TTL;
- no expired value returned;
- cancellation and late-result discard;
- no plaintext in cache keys or metrics.

### 21.4 Keeper

Add tests/test_keeper_secret_provider.py with a stubbed SDK:

- UID lookup;
- standard/custom field extraction;
- index validation;
- missing record/field;
- record-level shared cache identity;
- read-only behavior;
- enrollment success;
- failed enrollment persists nothing;
- one-time token never appears in stored config/logs;
- missing optional dependency message;
- timeout and auth error mapping.

Optional live integration tests run only with explicit environment configuration
and are excluded from the default suite.

### 21.5 AWS Secrets Manager

Add tests/test_aws_secrets_manager_provider.py with Stubber/fakes:

- standard credential chain use;
- SecretString;
- SecretBinary;
- json_key extraction;
- AWSCURRENT default;
- VersionId/VersionStage validation;
- complete ARN;
- access denied, not found, timeout, KMS error mapping;
- returned VersionId;
- optional AssumeRole;
- no request/value data in logs;
- shared record cache identity.

### 21.6 AWS credentials

Add tests/test_aws_credential_provider.py:

- all three fields share one generation;
- refreshable credentials;
- expiration/skew;
- absent session token behavior;
- no static key configuration;
- no use past expiration.

### 21.7 Additional provider contract suites

Add one isolated suite per adapter:

- tests/test_aws_ssm_parameter_store_provider.py;
- tests/test_azure_key_vault_provider.py;
- tests/test_gcp_secret_manager_provider.py;
- tests/test_hashicorp_vault_kv_provider.py;
- tests/test_onepassword_provider.py;
- corresponding Wave B/C files as adapters land.

Every adapter suite runs the same shared contract:

- valid config and locator schemas;
- explicit missing-parameter failures;
- workload identity or encrypted bootstrap handling;
- exact object/version/field selection;
- scalar/binary/map capability claims match behavior;
- stable normalized cache identity;
- provider version/expiry mapping;
- not-found, forbidden, auth, throttling, timeout, and malformed-value errors;
- cancellation and concurrency;
- no list/enumeration side effect;
- no write API in the read-only release;
- no values, locators, or bootstrap credentials in logs/errors/audit;
- dependency-missing behavior;
- opt-in live integration marker.

HashiCorp Vault KV v1 and v2 require separate fixtures. A future dynamic-engine
adapter additionally requires lease renewal, expiration, and revocation tests
and an approved lifecycle-contract extension before it can be enabled.

### 21.8 Existing behavior regression

Update and retain:

- tests/test_secrets_v2.py;
- tests/test_tool_relay_secret_cache.py;
- tests/test_tool_relay_hot_path.py;
- StoreSecretHandler tests in tests/test_new_agent_features.py;
- expression tests;
- service registry scope/encryption tests;
- CLI/action secret tests.

Required regression assertions:

- store_secret still writes current encrypted strings;
- ${name} behaves identically for local and external values;
- bash receives the same uppercase environment shape, filtered by the effective
  conversation/agent policy;
- executeScript receives the same filtered environment shape;
- redaction uses exactly the value injected into the call;
- non-secret tools do not trigger unnecessary provider resolution beyond current
  redaction behavior;
- listing aliases makes zero provider calls;
- provider outage does not expose ciphertext, locator, or lower-scope values.

### 21.9 Security tests

- locator/API values absent from logs and errors;
- SSRF/custom endpoint validation;
- provider bootstrap cycle rejected;
- unauthorized user cannot test another user's reference;
- cache hit still rechecks service visibility/revision;
- cache hit still rechecks conversation and agent policy;
- policy mutation requires owner/admin authority and compare-and-swap revision;
- policy management is absent from the normal LLM tool catalog;
- a forged agent name or source owner cannot widen access;
- provider uninstall reports references without values;
- audit payload rejects sensitive-shaped fields;
- secrets absent from serialized exceptions and snapshots;
- dependency and static security scans.

## 22. Implementation phases

### Phase 0 — Lock contracts and fixtures

Deliver:

- entry JSON schema;
- provider protocol;
- error codes;
- scope matrix;
- conversation/agent policy schema and missing/empty semantics;
- fake provider and fake clock;
- golden local secret files from current releases.

Exit criteria:

- review confirms that expressions and consumers remain provider-blind;
- no unresolved decision about entry compatibility or cache freshness.

### Phase 1 — Typed entry persistence

Deliver:

- SecretEntryCodec;
- typed raw ConfigStore mutations;
- conversation-entry support;
- local format regression tests;
- list operations based on raw metadata.

No external network provider exists yet.

Exit criteria:

- every existing secret test passes;
- external reference objects can round-trip without being resolved or
  stringified;
- local ciphertexts are not rewritten.

### Phase 2 — Resolver and fake provider

Deliver:

- SecretResolver;
- SecretAccessPolicy;
- ExternalSecretCache;
- secretProvider service;
- fake adapter;
- scope/authorization checks;
- conversation ∩ agent filtering before materialization;
- canonical agent propagation through the relay path;
- tool relay integration behind a feature flag.

Exit criteria:

- fake external values behave identically to local values in expressions,
  environment injection, and redaction;
- a denied fake-provider entry produces zero fetches;
- conversation and agent policy tests pass;
- cache single-flight/TTL tests pass;
- no provider I/O runs on an event loop.

### Phase 3 — Management surfaces

Deliver:

- bind_external_secret;
- extended list_secrets;
- test and refresh;
- UI Local/External mode;
- conversation and per-agent allowlist UI;
- CLI/action support;
- approval and audit integration.

Exit criteria:

- an authorized user can bind and consume a fake-provider entry without seeing
  the value in any management response;
- an owner can restrict a conversation and further restrict one agent;
- the agent cannot expand either restriction;
- every mutation path preserves mixed local/external stores.

### Phase 4 — Wave A, first two adapters: Keeper and AWS Secrets Manager

Deliver:

- optional SDK packaging;
- Keeper adapter;
- atomic one-time-token enrollment;
- AWS Secrets Manager adapter;
- AWS default SDK chain and optional AssumeRole;
- provider-specific locator/config UI;
- shared and provider-specific contract tests;
- deployment documentation for both.

Exit criteria:

- a Keeper-backed alias works everywhere a local alias works;
- an AWS-backed alias works everywhere a local alias works;
- repeated resolutions hit the cache;
- OAT and values are absent from persistence/logs except the encrypted resulting
  client configuration;
- AWSCURRENT rotation is observed within TTL;
- SDK credentials are not copied into PawFlow service config.

### Phase 5 — Complete Wave A: Azure, Google Cloud, and HashiCorp Vault KV

Deliver:

- Azure Key Vault adapter using DefaultAzureCredential;
- Google Cloud Secret Manager adapter using ADC;
- HashiCorp Vault KV v1/v2 adapter;
- provider capability declarations;
- composed provider config/locator schemas in the UI;
- contract, fake-SDK, and opt-in live tests;
- authentication and deployment documentation.

Exit criteria:

- one unchanged PawFlow alias resolves through every Wave A adapter;
- string, binary, record-field, map-field, versioned, and unversioned fixtures
  all pass the shared contract;
- managed/workload identity paths require no raw credential in PawFlow config;
- Vault KV v1/v2 path differences remain inside the adapter.

### Phase 6 — Wave B: configuration stores and SaaS managers

Deliver:

- AWS SSM Parameter Store;
- 1Password Secrets Automation;
- Infisical;
- Akeyless;
- Doppler;
- Bitwarden Secrets Manager;
- adapter-specific optional dependency extras;
- shared contract suites and opt-in tenant tests.

Exit criteria:

- every enabled adapter is add-only behind SecretProviderFactory;
- no Wave B adapter requires a resolver, expression, policy, relay, or persistent
  entry format change;
- service/locator schemas render from adapter metadata;
- disabled or missing optional SDKs do not affect other providers.

### Phase 7 — Wave C and operational sources

Deliver according to deployment demand:

- CyberArk Conjur / Secrets Manager;
- Delinea Secret Server;
- OCI Secret Management;
- IBM Cloud Secrets Manager;
- Alibaba Cloud KMS Secrets Manager;
- Kubernetes Secret;
- mounted secret file;
- SOPS encrypted file.

Exit criteria:

- each shipped adapter passes the same provider contract and its additional
  enterprise/filesystem security tests;
- generic HTTP retrieval remains forbidden;
- operational adapters cannot escape approved clusters, namespaces, or file
  roots.

### Phase 8 — Expiring credentials

Deliver:

- credential-chain adapter;
- shared atomic credential generation;
- expiration-aware cache;
- environment compatibility documentation;
- lifecycle foundation for later Vault/Akeyless dynamic-secret adapters.

Exit criteria:

- the three standard AWS environment aliases resolve from one credential
  generation;
- credentials refresh before expiry and are never returned after expiry;
- renewable-lease adapters remain disabled until the lifecycle contract and
  renewal/revocation tests exist.

### Phase 9 — Rollout and hardening

Deliver:

- feature flag removal or default enablement;
- cache metrics and dashboards;
- load/concurrency tests;
- security scan;
- upgrade/downgrade guidance;
- release notes;
- operational runbook.

Exit criteria:

- all acceptance criteria pass;
- no plaintext appears in captured logs, API fixtures, snapshots, or persisted
  test data;
- provider outage behavior is documented and tested.

## 23. Feature flags and rollout

Initial flags:

~~~text
external_secret_providers_enabled = false
external_secret_provider_allowlist = keeper,aws_secrets_manager
~~~

Use one operator allowlist of registered adapter names instead of adding a new
global flag for every vendor. An adapter must be both registered and allowed.
Unknown names fail startup validation in production.

Recommended rollout:

1. enable fake/local contract in CI;
2. enable the core external-entry feature for administrators;
3. enable selected Wave A adapters per deployment after dependencies and
   workload identity are configured;
4. monitor cache hit rate, fetch latency, error codes, and provider throttling;
5. enable for user-scoped provider creation;
6. remove the core flag after one stable release.

Disabling the feature:

- does not delete references;
- prevents remote fetch;
- materializes external entries as unavailable/empty;
- keeps local secrets operational;
- leaves raw external metadata visible to authorized administrators so it can be
  repaired or replaced.

## 24. Acceptance criteria

The feature is complete when all of the following are true:

1. A secret can be stored locally exactly as before.
2. The same name can instead reference any registered and operator-enabled
   provider adapter.
3. Existing ${name} expressions need no change.
4. Existing flows and service configurations need no change.
5. bash and executeScript receive the same environment-variable interface.
6. A conversation allowlist limits which effective secrets enter that
   interface.
7. An agent-instance allowlist can only reduce the conversation set.
8. A denied secret is neither decrypted, fetched, injected, nor collected for
   redaction.
9. Policy changes invalidate materialized mappings and affect the next call.
10. The LLM neither chooses the provider nor grants its own secret access.
11. Result redaction masks the exact remote value used by the call.
12. Provider reads are cached, single-flight, bounded, and memory-only.
13. Remote rotation is observed within the configured TTL.
14. Temporary AWS credentials are never used after expiration.
15. A provider error never falls through to a lower-scope alias.
16. Local secrets continue working during an unrelated provider outage.
17. Listing secrets makes no remote calls and reveals no value or locator.
18. Provider credentials and locators are encrypted or supplied by workload
    identity.
19. No secret appears in logs, audit payloads, metrics, API/UI responses,
    exceptions, or persisted caches.
20. Keeper enrollment is atomic and never persists its one-time token.
21. Provider scope, secret policy, and user ownership are checked on every
    resolution.
22. Existing local secret files require no migration.
23. Provider removal/update invalidates all affected cached values.
24. Adding a new adapter requires no change to SecretEntry, SecretResolver,
    expressions, access policy, or tool relay.
25. Wave A proves Keeper, AWS Secrets Manager, Azure Key Vault, Google Cloud
    Secret Manager, and HashiCorp Vault KV through the shared contract.
26. The full existing secret, expression, service, relay, CLI, and UI regression
    suites pass.

## 25. Deferred follow-up projects

These are intentionally separate so they do not complicate the external-provider
delivery:

1. per-tool or per-call allowlists finer than the conversation/agent layers;
2. credential profiles chosen from tool manifests;
3. a runtime secret broker for dynamically selected aliases;
4. relay-local provider execution;
5. remote provider write/update/delete;
6. provider-driven rotation orchestration;
7. vetted PFP provider adapters;
8. additional regional/vertical providers selected by deployment demand;
9. event-driven cache invalidation where a provider supports notifications.

The external reference format and SecretResolver seam are compatible with those
future projects, but none is required for V1.

## 26. Implementation checklist

- [ ] Approve entry schema and invariants.
- [ ] Add golden compatibility fixtures.
- [ ] Implement typed raw entry persistence.
- [ ] Implement resolver and cache.
- [ ] Implement conversation and agent allowlist intersection.
- [ ] Propagate canonical agent identity through nested/background resolution.
- [ ] Register secretProvider service.
- [ ] Make provider config/locator schemas adapter-declared.
- [ ] Integrate global/user/conversation loads.
- [ ] Preserve expression and relay contracts.
- [ ] Add management handlers/actions/CLI/UI.
- [ ] Add owner/admin allowlist management UI and APIs.
- [ ] Add audit and safe errors.
- [ ] Implement Keeper enrollment and resolver.
- [ ] Implement AWS Secrets Manager resolver.
- [ ] Implement Azure Key Vault resolver.
- [ ] Implement Google Cloud Secret Manager resolver.
- [ ] Implement HashiCorp Vault KV v1/v2 resolver.
- [ ] Implement Wave B adapters through the shared contract.
- [ ] Implement AWS credential-chain resolver.
- [ ] Run regression, concurrency, failure, and security suites.
- [ ] Update operational and security documentation.
- [ ] Complete staged rollout.

## 27. References

Reviewed for this plan on 2026-08-18:

- Keeper Secrets Manager applications:
  https://docs.keeper.io/keeperpam/privileged-access-manager/getting-started/applications
- Keeper Secrets Manager Python SDK:
  https://docs.keeper.io/keeperpam/secrets-manager/developer-sdk-library/python-sdk
- AWS Secrets Manager GetSecretValue:
  https://docs.aws.amazon.com/secretsmanager/latest/apireference/API_GetSecretValue.html
- AWS Secrets Manager retrieval:
  https://docs.aws.amazon.com/secretsmanager/latest/userguide/retrieving-secrets.html
- AWS Secrets Manager rotation:
  https://docs.aws.amazon.com/secretsmanager/latest/userguide/rotating-secrets.html
- AWS standardized credential providers:
  https://docs.aws.amazon.com/sdkref/latest/guide/standardized-credentials.html
- AWS SDK authentication overview:
  https://docs.aws.amazon.com/sdkref/latest/guide/access.html
- AWS Systems Manager Parameter Store:
  https://docs.aws.amazon.com/systems-manager/latest/userguide/systems-manager-parameter-store.html
- Azure Key Vault Python secret client:
  https://learn.microsoft.com/en-us/azure/key-vault/secrets/quick-create-python
- Google Cloud Secret Manager secret-version access:
  https://cloud.google.com/secret-manager/docs/access-secret-version
- HashiCorp Vault KV secrets engine:
  https://developer.hashicorp.com/vault/docs/secrets/kv
- 1Password service accounts:
  https://developer.1password.com/docs/service-accounts/
- Infisical retrieve-secret API:
  https://infisical.com/docs/api-reference/endpoints/secrets/read
- Kubernetes Secrets:
  https://kubernetes.io/docs/concepts/configuration/secret/
