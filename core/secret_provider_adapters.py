"""Built-in adapters for common external secret providers.

Provider SDKs are imported lazily so PawFlow can run without installing every
vendor dependency. Adapters are read-only and accept provider-specific locators.
"""

from __future__ import annotations

import base64
import json
import ssl
import urllib.parse
import urllib.request
from collections.abc import Mapping
from typing import Any

from core.secret_provider import (
    ProviderValue,
    SecretProviderAdapter,
    SecretProviderError,
    SecretProviderFactory,
)


def _required(mapping: Mapping[str, Any], key: str) -> str:
    value = str(mapping.get(key) or "").strip()
    if not value:
        raise ValueError(f"secret locator requires {key}")
    return value


def _json_field(value: Any, locator: Mapping[str, Any]) -> Any:
    field = str(locator.get("json_key") or "").strip()
    if not field:
        return value
    try:
        current = json.loads(
            value.decode("utf-8") if isinstance(value, bytes) else str(value))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SecretProviderError(
            "provider value is not valid JSON for json_key extraction") from exc
    for segment in field.split("."):
        if not isinstance(current, dict) or segment not in current:
            raise SecretProviderError(f"json_key '{field}' was not found")
        current = current[segment]
    if isinstance(current, (dict, list)):
        return json.dumps(current, ensure_ascii=False, separators=(",", ":"))
    return current


class _AwsBase(SecretProviderAdapter):
    service_name = ""

    def __init__(self, config=None):
        super().__init__(config)
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover - optional deployment dep
            raise SecretProviderError("boto3 is required for AWS secret providers") from exc
        session_args = {}
        for key in ("aws_access_key_id", "aws_secret_access_key",
                    "aws_session_token", "region_name", "profile_name"):
            value = self.config.get(key)
            if value:
                session_args[key] = value
        session = boto3.Session(**session_args)
        client_args = {}
        if self.config.get("endpoint_url"):
            client_args["endpoint_url"] = str(self.config["endpoint_url"])
        self._client = session.client(self.service_name, **client_args)


class AwsSecretsManagerAdapter(_AwsBase):
    """AWS Secrets Manager adapter.

    Locator: secret_id; optional version_id, version_stage and json_key.
    """

    service_name = "secretsmanager"

    def fetch(self, locator, context):
        request = {"SecretId": _required(locator, "secret_id")}
        if locator.get("version_id"):
            request["VersionId"] = str(locator["version_id"])
        if locator.get("version_stage"):
            request["VersionStage"] = str(locator["version_stage"])
        response = self._client.get_secret_value(**request)
        if response.get("SecretString") is not None:
            value = response["SecretString"]
        elif response.get("SecretBinary") is not None:
            value = response["SecretBinary"]
            if isinstance(value, str):
                value = base64.b64decode(value)
        else:
            raise SecretProviderError("AWS returned no secret value")
        value = _json_field(value, locator)
        return ProviderValue.from_value(
            value, version=str(response.get("VersionId") or ""))


class AwsSsmParameterStoreAdapter(_AwsBase):
    """AWS SSM Parameter Store adapter. Locator: name."""

    service_name = "ssm"

    def fetch(self, locator, context):
        response = self._client.get_parameter(
            Name=_required(locator, "name"), WithDecryption=True)
        parameter = response.get("Parameter") or {}
        if "Value" not in parameter:
            raise SecretProviderError("AWS SSM returned no parameter value")
        value = _json_field(parameter["Value"], locator)
        return ProviderValue.from_value(
            value, version=str(parameter.get("Version") or ""))


class HashicorpVaultKvAdapter(SecretProviderAdapter):
    """HashiCorp Vault KV v1/v2 adapter using the HTTP API.

    Config requires address and token. Locator requires path and may specify
    mount, key, version, and kv_version (1 or 2).
    """

    def fetch(self, locator, context):
        address = _required(self.config, "address").rstrip("/")
        token = _required(self.config, "token")
        path = _required(locator, "path").strip("/")
        mount = str(locator.get("mount") or self.config.get("mount") or "secret").strip("/")
        kv_version = int(locator.get(
            "kv_version", self.config.get("kv_version", 2)))
        if kv_version not in (1, 2):
            raise ValueError("Vault kv_version must be 1 or 2")
        api_path = f"{mount}/data/{path}" if kv_version == 2 else f"{mount}/{path}"
        url = f"{address}/v1/{api_path}"
        if locator.get("version") and kv_version == 2:
            url += "?" + urllib.parse.urlencode({
                "version": str(locator["version"])})
        headers = {"X-Vault-Token": token}
        namespace = str(self.config.get("namespace") or "").strip()
        if namespace:
            headers["X-Vault-Namespace"] = namespace
        request = urllib.request.Request(url, headers=headers, method="GET")
        timeout = float(self.config.get("timeout_seconds", 15))
        try:
            with urllib.request.urlopen(
                    request, timeout=timeout,
                    context=ssl.create_default_context()) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise SecretProviderError(f"Vault secret fetch failed: {exc}") from exc
        data = payload.get("data") or {}
        metadata = {}
        if kv_version == 2:
            metadata = data.get("metadata") or {}
            data = data.get("data") or {}
        key = str(locator.get("key") or "value")
        if not isinstance(data, dict) or key not in data:
            raise SecretProviderError(f"Vault field '{key}' was not found")
        return ProviderValue.from_value(
            data[key], version=str(metadata.get("version") or ""))


class AzureKeyVaultAdapter(SecretProviderAdapter):
    """Azure Key Vault adapter. Locator: name; optional version."""

    def __init__(self, config=None):
        super().__init__(config)
        try:
            from azure.identity import ClientSecretCredential, DefaultAzureCredential
            from azure.keyvault.secrets import SecretClient
        except ImportError as exc:  # pragma: no cover - optional deployment dep
            raise SecretProviderError(
                "azure-identity and azure-keyvault-secrets are required") from exc
        tenant = str(self.config.get("tenant_id") or "")
        client = str(self.config.get("client_id") or "")
        secret = str(self.config.get("client_secret") or "")
        if any((tenant, client, secret)):
            if not all((tenant, client, secret)):
                raise ValueError(
                    "tenant_id, client_id and client_secret must be supplied together")
            credential = ClientSecretCredential(tenant, client, secret)
        else:
            credential = DefaultAzureCredential()
        self._client = SecretClient(
            vault_url=_required(self.config, "vault_url"),
            credential=credential)

    def fetch(self, locator, context):
        result = self._client.get_secret(
            _required(locator, "name"),
            str(locator.get("version") or "") or None)
        return ProviderValue.from_value(
            result.value, version=str(getattr(result.properties, "version", "") or ""))

    def close(self):
        close = getattr(self._client, "close", None)
        if callable(close):
            close()


class GoogleCloudSecretManagerAdapter(SecretProviderAdapter):
    """Google Cloud Secret Manager adapter.

    Locator: secret and optional project/version. Project may be configured.
    """

    def __init__(self, config=None):
        super().__init__(config)
        try:
            from google.cloud import secretmanager
        except ImportError as exc:  # pragma: no cover - optional deployment dep
            raise SecretProviderError(
                "google-cloud-secret-manager is required") from exc
        client_options = None
        endpoint = str(self.config.get("api_endpoint") or "").strip()
        if endpoint:
            client_options = {"api_endpoint": endpoint}
        self._client = secretmanager.SecretManagerServiceClient(
            client_options=client_options)

    def fetch(self, locator, context):
        secret = _required(locator, "secret")
        project = str(locator.get("project") or self.config.get("project") or "").strip()
        if not project:
            raise ValueError("Google secret locator requires project")
        version = str(locator.get("version") or "latest")
        name = f"projects/{project}/secrets/{secret}/versions/{version}"
        response = self._client.access_secret_version(request={"name": name})
        return ProviderValue(
            value=bytes(response.payload.data),
            version=version,
            content_type="application/octet-stream",
        )


class KeeperSecretsManagerAdapter(SecretProviderAdapter):
    """Keeper Secrets Manager adapter.

    Config accepts config_json (exported KSM configuration). Locator requires
    record_uid and either field or custom_field.
    """

    def __init__(self, config=None):
        super().__init__(config)
        try:
            from keeper_secrets_manager_core import SecretsManager
            from keeper_secrets_manager_core.storage import InMemoryKeyValueStorage
        except ImportError as exc:  # pragma: no cover - optional deployment dep
            raise SecretProviderError(
                "keeper-secrets-manager-core is required") from exc
        raw = self.config.get("config_json")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError("Keeper config_json must be valid JSON") from exc
        if not isinstance(raw, dict):
            raise TypeError("Keeper provider requires config_json")
        self._client = SecretsManager(
            config=InMemoryKeyValueStorage(raw))

    def fetch(self, locator, context):
        uid = _required(locator, "record_uid")
        records = self._client.get_secrets([uid])
        if not records:
            raise SecretProviderError(f"Keeper record '{uid}' was not found")
        record = records[0]
        custom = str(locator.get("custom_field") or "").strip()
        field = str(locator.get("field") or "").strip()
        if custom:
            value = record.get_custom_field(custom)
        elif field:
            value = record.get_field(field)
        else:
            raise ValueError("Keeper locator requires field or custom_field")
        if isinstance(value, list):
            if not value:
                raise SecretProviderError("Keeper field is empty")
            index = int(locator.get("index", 0))
            value = value[index]
        if value is None:
            raise SecretProviderError("Keeper field was not found")
        return ProviderValue.from_value(value)


SecretProviderFactory.register("aws_secrets_manager", AwsSecretsManagerAdapter)
SecretProviderFactory.register("aws_ssm_parameter_store", AwsSsmParameterStoreAdapter)
SecretProviderFactory.register("hashicorp_vault_kv", HashicorpVaultKvAdapter)
SecretProviderFactory.register("vault", HashicorpVaultKvAdapter)
SecretProviderFactory.register("azure_key_vault", AzureKeyVaultAdapter)
SecretProviderFactory.register("gcp_secret_manager", GoogleCloudSecretManagerAdapter)
SecretProviderFactory.register("google_secret_manager", GoogleCloudSecretManagerAdapter)
SecretProviderFactory.register("keeper", KeeperSecretsManagerAdapter)
