"""ConfigStore — unified persistence for params/secrets with spill-to-disk.

Small values (< 1 MB) stay inline in JSON.
Large values (>= 1 MB) are written to sidecar files with a $ref pointer in JSON.

Sidecar naming: {json_stem}__{sanitized_key}.dat (params) or .dat.enc (secrets).
"""

import json
import logging
import re
from pathlib import Path
from typing import Dict, Set

from core.config_value import ConfigValue
from core.stream import SPILL_THRESHOLD

logger = logging.getLogger(__name__)

_SIDECAR_MARKER = "$type"
_SIDECAR_TYPE = "spilled"


def _sanitize_key(key: str) -> str:
    """Sanitize a key name for use in sidecar filenames."""
    return re.sub(r'[^a-zA-Z0-9_]', '_', key)


def _sidecar_path(json_path: Path, key: str, encrypted: bool = False) -> Path:
    """Build sidecar file path for a given JSON file and key."""
    stem = json_path.stem
    ext = ".dat.enc" if encrypted else ".dat"
    return json_path.parent / f"{stem}__{_sanitize_key(key)}{ext}"


class ConfigStore:
    """Unified persistence for params/secrets with spill-to-disk."""

    @staticmethod
    def load_params(path: Path) -> Dict[str, ConfigValue]:
        """Load JSON, resolve $ref sidecars into ConfigValue objects."""
        if not path.exists():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"Failed to load params from {path}: {e}")
            return {}

        result = {}
        for key, value in raw.items():
            if isinstance(value, dict) and value.get(_SIDECAR_MARKER) == _SIDECAR_TYPE:
                ref_file = path.parent / value["$ref"]
                if ref_file.exists():
                    data = ref_file.read_bytes()
                    result[key] = ConfigValue(data=data)
                else:
                    logger.warning(f"Sidecar file missing for key '{key}': {ref_file}")
                    result[key] = ConfigValue(value="")
            else:
                result[key] = ConfigValue(value=str(value))
        return result

    @staticmethod
    def save_params(path: Path, data: Dict[str, ConfigValue]) -> None:
        """Save JSON, spill large values to sidecar files, cleanup orphans."""
        path.parent.mkdir(parents=True, exist_ok=True)
        json_data = {}
        valid_sidecars: Set[str] = set()

        for key, cv in data.items():
            if cv.is_large:
                sidecar = _sidecar_path(path, key, encrypted=False)
                sidecar.write_bytes(cv.as_bytes())
                valid_sidecars.add(sidecar.name)
                json_data[key] = {
                    "$ref": sidecar.name,
                    "size": cv.size,
                    _SIDECAR_MARKER: _SIDECAR_TYPE,
                }
            else:
                json_data[key] = str(cv)

        path.write_text(
            json.dumps(json_data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        ConfigStore._cleanup_sidecars(path, valid_sidecars)

    @staticmethod
    def load_secrets(path: Path) -> Dict[str, ConfigValue]:
        """Load JSON, decrypt, resolve $ref sidecars."""
        if not path.exists():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"Failed to load secrets from {path}: {e}")
            return {}

        from core.secrets import get_secrets_manager
        sm = get_secrets_manager()

        result = {}
        for key, value in raw.items():
            if isinstance(value, dict) and value.get(_SIDECAR_MARKER) == _SIDECAR_TYPE:
                ref_file = path.parent / value["$ref"]
                if ref_file.exists():
                    encrypted_bytes = ref_file.read_bytes()
                    try:
                        decrypted = sm.decrypt_bytes(encrypted_bytes)
                        result[key] = ConfigValue(data=decrypted)
                    except Exception as e:
                        logger.warning(f"Failed to decrypt sidecar for key '{key}': {e}")
                        result[key] = ConfigValue(value="")
                else:
                    logger.warning(f"Sidecar file missing for key '{key}': {ref_file}")
                    result[key] = ConfigValue(value="")
            else:
                # Inline encrypted string
                encrypted = value.get("value", "") if isinstance(value, dict) else value
                try:
                    decrypted = sm.decrypt(encrypted)
                except Exception as e:
                    # Fail loud: a corrupted / wrong-key payload must NOT
                    # be returned as ciphertext to the caller — a
                    # provider would then see `enc:v2:...` as if it were
                    # the API key. Map the entry to an empty string so
                    # downstream code visibly fails on missing creds
                    # rather than silently using a useless secret.
                    logger.warning(
                        "Failed to decrypt secret '%s': %s — dropping value",
                        key, e)
                    decrypted = ""
                result[key] = ConfigValue(value=decrypted)
        return result

    @staticmethod
    def save_secrets(path: Path, data: Dict[str, ConfigValue]) -> None:
        """Encrypt, save JSON, spill large encrypted values to .enc sidecars."""
        path.parent.mkdir(parents=True, exist_ok=True)
        from core.secrets import get_secrets_manager
        sm = get_secrets_manager()

        json_data = {}
        valid_sidecars: Set[str] = set()

        for key, cv in data.items():
            if cv.is_large:
                sidecar = _sidecar_path(path, key, encrypted=True)
                encrypted_bytes = sm.encrypt_bytes(cv.as_bytes())
                sidecar.write_bytes(encrypted_bytes)
                valid_sidecars.add(sidecar.name)
                json_data[key] = {
                    "$ref": sidecar.name,
                    "size": cv.size,
                    _SIDECAR_MARKER: _SIDECAR_TYPE,
                }
            else:
                json_data[key] = sm.encrypt(cv.as_str())

        path.write_text(
            json.dumps(json_data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        ConfigStore._cleanup_sidecars(path, valid_sidecars, encrypted=True)

    @staticmethod
    def load_secrets_raw(path: Path) -> Dict[str, str]:
        """Load raw (encrypted) secret values as strings, for GUI display.

        Returns the encrypted strings as-is (no decryption).
        Large values get a special marker dict instead.
        """
        if not path.exists():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return raw

    @staticmethod
    def save_secrets_raw(path: Path, data: Dict[str, str]) -> None:
        """Save raw (encrypted) secret values. For backward compat with GUI."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @staticmethod
    def _cleanup_sidecars(path: Path, valid_names: Set[str],
                          encrypted: bool = False) -> int:
        """Remove orphaned sidecar files for the given JSON file."""
        stem = path.stem
        prefix = f"{stem}__"
        ext = ".dat.enc" if encrypted else ".dat"
        cleaned = 0
        try:
            for f in path.parent.iterdir():
                if (f.name.startswith(prefix) and f.name.endswith(ext)
                        and f.name not in valid_names):
                    f.unlink()
                    cleaned += 1
                    logger.debug(f"Cleaned orphan sidecar: {f.name}")
        except Exception as e:
            logger.warning(f"Error cleaning sidecars: {e}")
        return cleaned

