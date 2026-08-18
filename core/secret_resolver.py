"""Central authorization and materialization path for all PawFlow secrets."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

from core.config_store import ConfigStore
from core.config_value import ConfigValue
from core.external_secret_cache import ExternalSecretCache
from core.secret_access_policy import SecretAccessPolicy, SecretIdentity
from core.secret_entries import (
    decode_external_secret_ref,
    is_external_secret_raw,
)
from core.secret_provider import SecretResolveContext

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResolvedSecret:
    identity: SecretIdentity
    value: ConfigValue
    external: bool = False
    provider_service: str = ""


class SecretResolver:
    """Selects a winning alias, authorizes it, then materializes only that entry."""

    def __init__(self, cache: ExternalSecretCache | None = None):
        self.cache = cache or ExternalSecretCache.instance()

    @staticmethod
    def _root_conversation_id(conversation_id: str) -> str:
        from core.service_registry import _parent_conversation_id
        return _parent_conversation_id(conversation_id) or str(
            conversation_id or "")

    @staticmethod
    def _raw_file(path) -> dict[str, Any]:
        raw = ConfigStore.load_secrets_raw(path)
        return raw if isinstance(raw, dict) else {}

    def _sources(self, owner_user_id: str,
                 conversation_id: str) -> list[tuple[str, str, Any, dict[str, Any]]]:
        from core.paths import GLOBAL_SECRETS_FILE, user_secrets_path

        sources: list[tuple[str, str, Any, dict[str, Any]]] = []
        root = self._root_conversation_id(conversation_id)
        if root:
            try:
                from core.conversation_store import ConversationStore
                store = ConversationStore.instance()
                raw = store.get_extra(root, "conv_secrets") or {}
                if isinstance(raw, dict):
                    sources.append(("conv", root, None, raw))
            except Exception:
                logger.warning("Failed to load conversation secrets", exc_info=True)
        if owner_user_id:
            path = user_secrets_path(owner_user_id)
            sources.append(("user", owner_user_id, path, self._raw_file(path)))
        sources.append(("global", "", GLOBAL_SECRETS_FILE,
                        self._raw_file(GLOBAL_SECRETS_FILE)))
        return sources

    def _select(self, name: str, owner_user_id: str,
                conversation_id: str,
                exact_scope: str | None = None):
        wanted = str(exact_scope or "").strip().lower()
        if wanted == "conversation":
            wanted = "conv"
        for scope, scope_id, path, raw in self._sources(
                owner_user_id, conversation_id):
            if wanted and scope != wanted:
                continue
            if name in raw:
                return scope, scope_id, path, raw[name]
        return None

    @staticmethod
    def _decode_local(path, name: str, raw: Any) -> ConfigValue:
        if path is not None:
            return ConfigStore.decode_secret_entry(path, name, raw)
        from core.secrets import get_secrets_manager
        if not isinstance(raw, str):
            raise TypeError("conversation local secret must be an encrypted string")
        value = get_secrets_manager().decrypt(raw) if raw.startswith("enc:") else raw
        return ConfigValue(value=str(value))

    @staticmethod
    def _provider_candidates(source_scope: str, source_scope_id: str,
                             owner_user_id: str,
                             conversation_id: str):
        root = SecretResolver._root_conversation_id(conversation_id)
        if source_scope == "conv":
            yield "conv", source_scope_id or root
            if owner_user_id:
                yield "user", owner_user_id
            yield "global", ""
        elif source_scope == "user":
            yield "user", source_scope_id or owner_user_id
            yield "global", ""
        else:
            yield "global", ""

    def _provider_service(self, service_id: str, source_scope: str,
                          source_scope_id: str, owner_user_id: str,
                          conversation_id: str):
        # Ensure the type is registered even when the task bootstrap has not run.
        import services.secret_provider_service  # noqa: F401
        from core.service_registry import ServiceRegistry

        registry = ServiceRegistry.get_instance()
        for scope, scope_id in self._provider_candidates(
                source_scope, source_scope_id, owner_user_id, conversation_id):
            definition = registry.get_definition(scope, scope_id, service_id)
            if definition is None:
                continue
            if definition.service_type != "secretProvider":
                raise ValueError(
                    f"Service '{service_id}' is not a secretProvider")
            service = registry.get_live_instance(scope, scope_id, service_id)
            if service is None:
                raise RuntimeError(
                    f"Secret provider service '{service_id}' is disabled")
            return scope, scope_id, service
        raise ValueError(
            f"Secret provider service '{service_id}' is not visible from "
            f"{source_scope} scope")

    def _materialize(self, identity: SecretIdentity, path, raw: Any,
                     owner_user_id: str, conversation_id: str,
                     agent_name: str) -> ResolvedSecret:
        if not is_external_secret_raw(raw):
            return ResolvedSecret(
                identity=identity,
                value=self._decode_local(path, identity.name, raw))

        ref = decode_external_secret_ref(raw)
        provider_scope, provider_scope_id, service = self._provider_service(
            ref.provider_service, identity.source_scope,
            identity.source_scope_id, owner_user_id, conversation_id)
        locator_payload = json.dumps(
            ref.locator, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False).encode("utf-8")
        locator_digest = hashlib.sha256(locator_payload).hexdigest()
        cache_key = (
            provider_scope, provider_scope_id, ref.provider_service,
            locator_digest, ref.version,
        )
        context = SecretResolveContext(
            secret_name=identity.name,
            source_scope=identity.source_scope,
            source_scope_id=identity.source_scope_id,
            owner_user_id=owner_user_id,
            conversation_id=self._root_conversation_id(conversation_id),
            agent_name=agent_name,
        )
        fetched = self.cache.get_or_fetch(
            cache_key, service.cache_ttl_seconds,
            lambda: service.fetch(ref.locator, context))
        return ResolvedSecret(
            identity=identity,
            value=ConfigValue(data=fetched.value),
            external=True,
            provider_service=ref.provider_service,
        )

    def resolve_record(self, name: str, *, owner_user_id: str = "",
                       conversation_id: str = "", agent_name: str = "",
                       exact_scope: str | None = None) -> ResolvedSecret | None:
        if not name:
            raise ValueError("secret name is required")
        selected = self._select(
            name, owner_user_id, conversation_id, exact_scope)
        if selected is None:
            return None
        scope, scope_id, path, raw = selected
        identity = SecretIdentity(name=name, source_scope=scope,
                                  source_scope_id=scope_id)
        policy = SecretAccessPolicy.load(conversation_id, agent_name)
        if not policy.allows(identity):
            return None
        return self._materialize(
            identity, path, raw, owner_user_id, conversation_id, agent_name)

    def resolve_name(self, name: str, **context: Any) -> ConfigValue | None:
        record = self.resolve_record(name, **context)
        return None if record is None else record.value

    def resolve_all(self, *, owner_user_id: str = "",
                    conversation_id: str = "",
                    agent_name: str = "") -> dict[str, ResolvedSecret]:
        """Resolve one winning entry per alias; never fall back after selection."""

        winners: dict[str, tuple] = {}
        for scope, scope_id, path, raw in self._sources(
                owner_user_id, conversation_id):
            for name, value in raw.items():
                winners.setdefault(name, (scope, scope_id, path, value))
        policy = SecretAccessPolicy.load(conversation_id, agent_name)
        result: dict[str, ResolvedSecret] = {}
        for name, (scope, scope_id, path, raw) in winners.items():
            identity = SecretIdentity(name=name, source_scope=scope,
                                      source_scope_id=scope_id)
            if not policy.allows(identity):
                continue
            try:
                result[name] = self._materialize(
                    identity, path, raw, owner_user_id,
                    conversation_id, agent_name)
            except Exception as exc:  # noqa: BLE001 - one provider must not block siblings
                logger.warning(
                    "Failed to materialize secret '%s' from %s: %s",
                    name, scope, exc)
        return result
