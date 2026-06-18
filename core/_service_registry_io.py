"""Persistence, sensitive-field encryption, and scope-chain resolution for
ServiceRegistry.

Split out of service_registry.py as a leaf mixin so the registry file stays
<= 800 lines. The mixin relies on host state/methods provided by
ServiceRegistry: ``self._definitions``, ``self._data_lock``,
``self._load_failed``, ``self._ensure_loaded``, ``self._resolve_scope_id``,
``self.get_live_instance``, and ``self.get_definition``.
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from core import Service, ServiceFactory

from core._service_defs import (
    CONV_EXTRAS_KEY,
    SCOPE_CONV,
    SCOPE_GLOBAL,
    SCOPE_USER,
    ServiceDef,
    _package_runtime_dedupe_key,
    _parent_conversation_id,
)

logger = logging.getLogger(__name__)


class _ServiceRegistryIOMixin:
    """Persistence + encryption + scope-chain resolution for ServiceRegistry."""

    # ---- Sensitive field encryption ----

    @staticmethod
    def _sensitive_keys(service_type: str) -> set:
        """Return the set of config keys marked sensitive in the service schema."""
        try:
            svc_cls = ServiceFactory.get(service_type)
            schema = svc_cls.get_parameter_schema(svc_cls)
            return {k for k, v in schema.items() if v.get("sensitive")}
        except Exception:
            return set()

    @staticmethod
    def _encrypted_prefix() -> str:
        return "enc" + ":"

    @classmethod
    def _encrypt_config(cls, config: dict, sensitive_keys: set) -> dict:
        """Return a copy of config with sensitive values encrypted."""
        if not sensitive_keys:
            return config
        from core.secrets import get_secrets_manager
        sm = get_secrets_manager()
        out = dict(config)
        prefix = cls._encrypted_prefix()
        for k in sensitive_keys:
            v = out.get(k)
            if isinstance(v, str) and v and not v.startswith(prefix):
                out[k] = sm.encrypt(v)
        return out

    @classmethod
    def _decrypt_config(cls, config: dict, sensitive_keys: set) -> dict:
        """Return a copy of config with sensitive values decrypted."""
        if not sensitive_keys:
            return config
        from core.secrets import get_secrets_manager
        sm = get_secrets_manager()
        out = dict(config)
        prefix = cls._encrypted_prefix()
        for k in sensitive_keys:
            v = out.get(k)
            if isinstance(v, str) and v.startswith(prefix):
                try:
                    out[k] = sm.decrypt(v)
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        return out

    # ---- Persistence ----

    def _load(self, scope: str, scope_id: str) -> None:
        """Load service definitions from the appropriate backend."""
        # [phaseB-diag] Pin WHEN the (lazy) user-scope load is triggered and
        # by WHOM. The 50s post-login gap sits before "Loaded N services";
        # this caller breadcrumb tells us which code path triggers it.
        _diag_t0 = None
        if scope == SCOPE_USER:
            import time as _t
            import traceback as _tb
            _diag_t0 = _t.monotonic()
            _frames = _tb.extract_stack(limit=8)[:-1]
            _caller = " < ".join(
                "%s:%s" % (f.name, f.lineno) for f in reversed(_frames[-5:]))
            logger.debug("[svc-load] START user scope id=%s caller=%s",
                        scope_id[:8] if len(scope_id) > 8 else scope_id, _caller)
        # Resolve the dir getters through the core.service_registry module so
        # tests that monkeypatch service_registry._user_services_dir /
        # _global_services_dir still redirect persistence after the split.
        import core.service_registry as _sr
        try:
            if scope == SCOPE_GLOBAL:
                self._load_dir(scope_id, _sr._global_services_dir(), scope)
            elif scope == SCOPE_USER:
                svc_dir = _sr._user_services_dir() / scope_id
                self._load_dir(scope_id, svc_dir, scope)
            elif scope == SCOPE_CONV:
                self._load_conv(scope_id)
        except Exception as e:
            self._load_failed.add(scope_id)
            logger.error(
                "CRITICAL: Failed to load %s services (id=%s): %s — "
                "registry is READ-ONLY for this scope until restart",
                scope, scope_id[:8] if len(scope_id) > 8 else scope_id, e)
        if _diag_t0 is not None:
            import time as _t
            logger.debug("[svc-load] END user scope id=%s took=%.0fms",
                        scope_id[:8] if len(scope_id) > 8 else scope_id,
                        (_t.monotonic() - _diag_t0) * 1000.0)

    def _load_dir(self, scope_id: str, svc_dir: Path, scope: str) -> None:
        """Load definitions from a directory (1 JSON file per service)."""
        if not svc_dir.exists():
            return
        defs = {}
        for f in svc_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                sid = data.get("service_id", f.stem)
                data["service_id"] = sid
                data["scope"] = scope
                data["scope_id"] = scope_id
                stype = data.get("service_type", "")
                sk = self._sensitive_keys(stype)
                if sk and "config" in data:
                    data["config"] = self._decrypt_config(data["config"], sk)
                defs[sid] = ServiceDef.from_dict(data)
            except Exception as e:
                logger.warning("Failed to load service from %s: %s", f, e)
        self._definitions[scope_id] = defs
        logger.info("Loaded %d %s service(s) (id=%s)", len(defs), scope,
                     scope_id[:8] if len(scope_id) > 8 else scope_id)

    def _load_conv(self, scope_id: str) -> None:
        """Load definitions from ConversationStore extras."""
        from core.conversation_store import ConversationStore
        store = ConversationStore.instance()
        raw = store.get_extra(scope_id, CONV_EXTRAS_KEY) or {}
        defs = {}
        for sid, data in raw.items():
            data = dict(data)
            data["service_id"] = sid
            data["scope"] = SCOPE_CONV
            data["scope_id"] = scope_id
            stype = data.get("service_type", "")
            sk = self._sensitive_keys(stype)
            if sk and "config" in data:
                data["config"] = self._decrypt_config(data["config"], sk)
            defs[sid] = ServiceDef.from_dict(data)
        self._definitions[scope_id] = defs
        if defs:
            logger.info("Loaded %d conv service(s) for conv '%s'", len(defs), scope_id[:8])

    def _save(self, scope: str, scope_id: str) -> None:
        """Save service definitions to the appropriate backend."""
        if scope_id in self._load_failed:
            logger.warning(
                "REFUSING to save %s services (id=%s) — initial load failed.",
                scope, scope_id[:8] if len(scope_id) > 8 else scope_id)
            return
        import core.service_registry as _sr
        if scope == SCOPE_GLOBAL:
            self._save_dir(scope_id, _sr._global_services_dir())
        elif scope == SCOPE_USER:
            svc_dir = _sr._user_services_dir() / scope_id
            self._save_dir(scope_id, svc_dir)
        elif scope == SCOPE_CONV:
            self._save_conv(scope_id)

    def _save_dir(self, scope_id: str, svc_dir: Path) -> None:
        """Save each service as an individual JSON file (atomic writes)."""
        svc_dir.mkdir(parents=True, exist_ok=True)
        with self._data_lock:
            current_defs = dict(self._definitions.get(scope_id, {}))

        # Write/update each service file
        for sid, sdef in current_defs.items():
            d = sdef.to_dict()
            sk = self._sensitive_keys(sdef.service_type)
            if sk:
                d["config"] = self._encrypt_config(d.get("config", {}), sk)
            safe_name = sid.replace("/", "_").replace("\\", "_")
            filepath = svc_dir / f"{safe_name}.json"
            tmp_path = filepath.with_suffix(".tmp")
            try:
                tmp_path.write_text(
                    json.dumps(d, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                tmp_path.replace(filepath)
            except Exception as e:
                logger.error("Failed to save service %s to %s: %s", sid, filepath, e)
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

        # Remove files for services that no longer exist
        for f in svc_dir.glob("*.json"):
            if f.stem not in current_defs and f.stem.replace("_", "/") not in current_defs:
                try:
                    f.unlink()
                    logger.info("Removed stale service file: %s", f)
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

    def _save_conv(self, scope_id: str) -> None:
        """Save to ConversationStore extras."""
        try:
            from core.conversation_store import ConversationStore
            store = ConversationStore.instance()
            with self._data_lock:
                conv_defs = self._definitions.get(scope_id, {})
                data = {}
                for sid, sdef in conv_defs.items():
                    d = sdef.to_dict()
                    sk = self._sensitive_keys(sdef.service_type)
                    if sk:
                        d["config"] = self._encrypt_config(d.get("config", {}), sk)
                    data[sid] = d
            store.set_extra(scope_id, CONV_EXTRAS_KEY, data)
        except Exception as e:
            logger.error("Failed to save conv services for '%s': %s", scope_id[:8], e)

    # ---- Resolution (scope chain: conv > user > global) ----

    def _scope_chain(self, *, user_id: str = "", conv_id: str = ""):
        """Yield (scope, scope_id) in resolution order: conv > user > global."""
        if conv_id:
            yield SCOPE_CONV, conv_id
            parent_id = _parent_conversation_id(conv_id)
            if parent_id and parent_id != conv_id:
                yield SCOPE_CONV, parent_id
        if user_id:
            yield SCOPE_USER, user_id
        yield SCOPE_GLOBAL, ""

    def resolve(self, service_id: str, *,
                user_id: str = "", conv_id: str = "") -> Optional[Service]:
        """Walk conv > user > global and return the first live instance found."""
        for scope, sid in self._scope_chain(user_id=user_id, conv_id=conv_id):
            svc = self.get_live_instance(scope, sid, service_id)
            if svc is not None:
                return svc
        return None

    def resolve_definition(self, service_id: str, *,
                           user_id: str = "", conv_id: str = "") -> Optional[ServiceDef]:
        """Walk conv > user > global and return the first definition found."""
        for scope, sid in self._scope_chain(user_id=user_id, conv_id=conv_id):
            d = self.get_definition(scope, sid, service_id)
            if d is not None:
                return d
        return None

    def resolve_by_type(self, service_type: str, *,
                        user_id: str = "", conv_id: str = "",
                        enabled_only: bool = True) -> List[ServiceDef]:
        """Get all services matching a type, ordered conv > user > global.

        Uses the same canonical scope merge as resolve_all so service pickers
        and typed lookups cannot diverge.
        """
        if service_type == "packageRuntime":
            result = []
            seen = set()
            for scope, sid in self._scope_chain(user_id=user_id, conv_id=conv_id):
                self._ensure_loaded(scope, sid)
                rsid = self._resolve_scope_id(scope, sid)
                with self._data_lock:
                    defs = list(self._definitions.get(rsid, {}).values())
                for sdef in defs:
                    if sdef.service_type != service_type:
                        continue
                    if enabled_only and not sdef.enabled:
                        continue
                    seen_key = _package_runtime_dedupe_key(sdef)
                    if seen_key in seen:
                        continue
                    result.append(sdef)
                    seen.add(seen_key)
            return result
        result: Dict[str, ServiceDef] = {}
        for scope, sid in reversed(list(
                self._scope_chain(user_id=user_id, conv_id=conv_id))):
            self._ensure_loaded(scope, sid)
            rsid = self._resolve_scope_id(scope, sid)
            with self._data_lock:
                for svc_id, sdef in self._definitions.get(rsid, {}).items():
                    if sdef.service_type != service_type:
                        continue
                    if enabled_only and not sdef.enabled:
                        continue
                    result[svc_id] = sdef
        return list(result.values())

    def resolve_all(self, *, user_id: str = "", conv_id: str = "",
                    enabled_only: bool = False) -> Dict[str, ServiceDef]:
        """Get all definitions across all scopes, most specific wins."""
        result: Dict[str, ServiceDef] = {}
        # Walk reverse (global first) so more specific scopes override
        for scope, sid in reversed(list(
                self._scope_chain(user_id=user_id, conv_id=conv_id))):
            self._ensure_loaded(scope, sid)
            rsid = self._resolve_scope_id(scope, sid)
            with self._data_lock:
                for svc_id, sdef in self._definitions.get(rsid, {}).items():
                    if enabled_only and not sdef.enabled:
                        continue
                    result[svc_id] = sdef
        return result
