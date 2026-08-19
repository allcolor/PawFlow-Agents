"""One-shot migration from native media providers to bundled PFP providers."""

from __future__ import annotations

import threading
from typing import Any, Iterable


PROVIDER_PACKAGES = {
    "pawflow.pixazo-provider": frozenset({
        "pixazoImageGeneration", "pixazoVideoGeneration",
        "pixazoAudioGeneration", "pixazo3DGeneration", "pixazoUpscale",
        "pixazoTryOn", "pixazoLipsync", "pixazoTrainer",
    }),
    "pawflow.wavespeed-provider": frozenset({
        "wavespeedImageGeneration", "wavespeedVideoGeneration",
        "wavespeedAudioGeneration", "wavespeedVoiceClone",
        "wavespeed3DGeneration", "wavespeedUpscale", "wavespeedTryOn",
        "wavespeedLipsync", "wavespeedTrainer",
    }),
    "pawflow.kling-provider": frozenset({"klingVideoGeneration"}),
}

_state = threading.local()


class ProviderPfpMigrationError(RuntimeError):
    """Raised when a legacy provider instance cannot be migrated safely."""


def migrate_scope(scope: str, scope_id: str,
                  definitions: Iterable[Any]) -> list[str]:
    """Install verified provider PFPs required by definitions in one scope."""
    if scope not in {"user", "conv"} or not scope_id:
        return []
    service_types = {
        str(getattr(item, "service_type", "") or "") for item in definitions
    }
    required = [
        package for package, types in PROVIDER_PACKAGES.items()
        if service_types & types
    ]
    if not required or getattr(_state, "active", False):
        return []

    if scope == "user":
        user_id = scope_id
        conversation_id = ""
        package_scope = "user"
    else:
        from core.conversation_store import ConversationStore
        user_id = str(ConversationStore.instance().resolve_owner(scope_id) or "")
        if not user_id:
            raise ProviderPfpMigrationError(
                f"Cannot resolve owner for provider migration in conversation {scope_id}")
        conversation_id = scope_id
        package_scope = "conversation"

    from core import pfp_package, pfp_registry

    try:
        installed = {
            str(item.get("package") or "")
            for item in pfp_package.list_installed_packages(
                user_id=user_id, conversation_id=conversation_id,
                scope=package_scope).get("packages", [])
        }
        migrated = []
        _state.active = True
        for package in required:
            if package in installed:
                continue
            resolved = pfp_registry.resolve_package_path(
                package, user_id=user_id)
            path = str(resolved.get("path") or "")
            if not path:
                raise ProviderPfpMigrationError(
                    f"Bundled provider package is unavailable: {package}")
            result = pfp_package.install_pfp(
                path, user_id=user_id, conversation_id=conversation_id,
                scope=package_scope, force=True)
            if not result.get("ok"):
                raise ProviderPfpMigrationError(
                    f"Provider package migration failed: {package}")
            installed_types = {
                str(item.get("service_type") or "")
                for item in result.get("installed", [])
            }
            missing_types = PROVIDER_PACKAGES[package] - installed_types
            if missing_types:
                raise ProviderPfpMigrationError(
                    f"Provider package migration incomplete: {package} "
                    f"is missing {', '.join(sorted(missing_types))}")
            migrated.append(package)
        return migrated
    except ProviderPfpMigrationError:
        raise
    except Exception as exc:
        raise ProviderPfpMigrationError(
            f"Provider package migration failed for {scope}:{scope_id}") from exc
    finally:
        _state.active = False
