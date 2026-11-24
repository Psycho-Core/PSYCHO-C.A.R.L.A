"""
Artifact signing — Ed25519 signatures for files and payloads.

Used to sign (and verify) things C.A.R.L.A. produces or loads: skill
packages, operator manifests, exported traces. The key pair lives at
``security.signing_key_path`` (default ``<home>/signing.key``); the
private key file is written owner-only and never logged.

Layer: security.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .vault import _default_home


@dataclass
class KeyPair:
    private_path: Path
    public_path: Path

    def public_key_pem(self) -> str:
        return self.public_path.read_text(encoding="utf-8")


def signing_key_path(config: Any = None) -> Path:
    security = getattr(config, "security", None) if config is not None else None
    configured = getattr(security, "signing_key_path", "") if security else ""
    if configured:
        return Path(configured).expanduser()
    return _default_home() / "signing.key"


def _load_or_create_private(key_path: Path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
        load_pem_private_key,
    )

    if key_path.exists():
        return load_pem_private_key(key_path.read_bytes(), password=None)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    private = Ed25519PrivateKey.generate()
    pem = private.private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    key_path.write_bytes(pem)
    try:
        os.chmod(key_path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return private


def ensure_key_pair(config: Any = None) -> KeyPair:
    """Create (once) and return the signing key pair paths."""
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        PublicFormat,
    )

    key_path = signing_key_path(config)
    private = _load_or_create_private(key_path)
    public_path = key_path.with_suffix(".pub")
    if not public_path.exists():
        public_pem = private.public_key().public_bytes(
            Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
        public_path.write_bytes(public_pem)
    return KeyPair(private_path=key_path, public_path=public_path)


def sign_bytes(data: bytes, config: Any = None) -> bytes:
    pair = ensure_key_pair(config)
    private = _load_or_create_private(pair.private_path)
    return private.sign(data)


def sign_file(path: str | Path, config: Any = None) -> Path:
    """Sign a file; writes ``<file>.sig`` beside it and returns that path."""
    target = Path(path)
    signature = sign_bytes(target.read_bytes(), config)
    sig_path = target.with_suffix(target.suffix + ".sig")
    sig_path.write_bytes(signature)
    return sig_path


def verify_bytes(data: bytes, signature: bytes, config: Any = None) -> bool:
    from cryptography.exceptions import InvalidSignature

    pair = ensure_key_pair(config)
    private = _load_or_create_private(pair.private_path)
    try:
        private.public_key().verify(signature, data)
        return True
    except InvalidSignature:
        return False


def verify_file(path: str | Path, config: Any = None) -> bool:
    target = Path(path)
    sig_path = target.with_suffix(target.suffix + ".sig")
    if not sig_path.exists():
        return False
    return verify_bytes(target.read_bytes(), sig_path.read_bytes(), config)


__all__ = [
    "KeyPair", "ensure_key_pair", "sign_bytes", "sign_file",
    "signing_key_path", "verify_bytes", "verify_file",
]
