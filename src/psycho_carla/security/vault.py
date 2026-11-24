"""
PSYCHO-C.A.R.L.A. — Secret vault.

A small, local, encrypted store for the credentials that do not belong in
the configuration file: API keys, OAuth client secrets, webhook URLs.

Design decisions:

  * **One master key.** The vault is encrypted with a single key that
    lives beside it in ``~/.psycho_carla/vault.key``, readable only by
    the owner. It is generated on first use with 32 bytes of OS entropy.
  * **AES-GCM through ``cryptography`` when available.** If that package
    is absent the vault refuses to store anything rather than falling
    back to plaintext — a secret store that quietly degrades to
    unencrypted is worse than no secret store.
  * **No plaintext on disk.** The values are encrypted before they are
    written, and the key file is 0600.

The vault is *not* a credential manager: it does not talk to the OS
keychain, and it does not sync. It is a local, encrypted file.

Layer: Pillar 4 (Tools & Memory).
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import stat
from pathlib import Path
from typing import Any

#: The cipher used for the vault file.
_VAULT_VERSION = 1

#: Environment variable holding the master key, for headless setups.
_KEY_ENV = "PSYCHO_CARLA_VAULT_KEY"


class VaultError(RuntimeError):
    """Raised when the vault cannot be used as asked."""


def _default_home() -> Path:
    override = os.environ.get("PSYCHO_CARLA_HOME")
    if override:
        return Path(override)
    return Path.home() / ".psycho_carla"


def vault_path() -> Path:
    """Where the encrypted vault file lives."""
    return _default_home() / "vault.enc"


def key_path() -> Path:
    """Where the master key lives."""
    return _default_home() / "vault.key"


def _aesgcm():
    """The AES-GCM cipher, or None when ``cryptography`` is absent."""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        return AESGCM
    except ImportError:
        return None


def encryption_available() -> tuple[bool, str]:
    """Is the vault usable here?"""
    aesgcm = _aesgcm()
    if aesgcm is None:
        return (
            False,
            "the 'cryptography' package is not installed — "
            "pip install 'psycho-carla[security]'",
        )
    return (True, "")


def _load_or_create_key() -> bytes:
    """The master key, creating it on first use."""
    env = os.environ.get(_KEY_ENV, "").strip()
    if env:
        try:
            return base64.b64decode(env)
        except Exception as exc:
            raise VaultError(f"{_KEY_ENV} is not valid base64") from exc

    path = key_path()
    if path.exists():
        try:
            return base64.b64decode(path.read_text(encoding="utf-8").strip())
        except Exception as exc:
            raise VaultError(f"The vault key at {path} is unreadable") from exc

    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(32)
    path.write_text(base64.b64encode(key).decode("ascii"), encoding="utf-8")
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return key


def _encrypt(plaintext: bytes, key: bytes) -> bytes:
    aesgcm = _aesgcm()
    if aesgcm is None:
        raise VaultError(
            "the 'cryptography' package is required to use the vault"
        )
    nonce = os.urandom(12)
    ciphertext = aesgcm(key).encrypt(nonce, plaintext, None)
    return nonce + ciphertext


def _decrypt(blob: bytes, key: bytes) -> bytes:
    aesgcm = _aesgcm()
    if aesgcm is None:
        raise VaultError(
            "the 'cryptography' package is required to use the vault"
        )
    if len(blob) < 13:
        raise VaultError("The vault file is corrupt")
    nonce, ciphertext = blob[:12], blob[12:]
    try:
        return aesgcm(key).decrypt(nonce, ciphertext, None)
    except Exception as exc:
        raise VaultError(
            "The vault could not be decrypted — the key does not match"
        ) from exc


class Vault:
    """An encrypted key/value store."""

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path else vault_path()
        self._data: dict[str, Any] | None = None

    # -- persistence ---------------------------------------------------------- #
    def _read(self) -> dict[str, Any]:
        if self._data is not None:
            return self._data
        if not self._path.exists():
            self._data = {}
            return self._data
        key = _load_or_create_key()
        blob = self._path.read_bytes()
        raw = _decrypt(blob, key)
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise VaultError("The vault file is not valid JSON") from exc
        self._data = data if isinstance(data, dict) else {}
        return self._data

    def _write(self) -> None:
        data = self._read()
        key = _load_or_create_key()
        blob = _encrypt(
            json.dumps(data, indent=2, sort_keys=True).encode("utf-8"), key
        )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_bytes(blob)
        try:
            os.chmod(self._path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass

    # -- operations ----------------------------------------------------------- #
    def set(self, key: str, value: str) -> None:
        """Store a secret."""
        if not key.strip():
            raise VaultError("A vault key cannot be empty")
        data = self._read()
        data[key.strip()] = str(value)
        self._write()

    def get(self, key: str, default: str = "") -> str:
        """Read a secret."""
        return str(self._read().get(key.strip(), default))

    def remove(self, key: str) -> bool:
        """Delete a secret. Returns True when one was there."""
        data = self._read()
        if key.strip() in data:
            del data[key.strip()]
            self._write()
            return True
        return False

    def keys(self) -> list[str]:
        """The stored key names — never the values."""
        return sorted(self._read())

    def items(self) -> dict[str, str]:
        """Every secret. Used by ``vault list --values``."""
        return {k: str(v) for k, v in self._read().items()}

    def __contains__(self, key: object) -> bool:
        return str(key) in self._read()

    def __len__(self) -> int:
        return len(self._read())

    @property
    def path(self) -> Path:
        return self._path


__all__ = [
    "Vault",
    "VaultError",
    "encryption_available",
    "key_path",
    "vault_path",
]
