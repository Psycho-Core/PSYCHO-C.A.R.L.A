"""
PSYCHO-C.A.R.L.A. — Security.

The local secret store. Secrets that do not belong in a configuration
file — API keys, OAuth client secrets, webhook URLs — live here, in an
AES-GCM encrypted file whose master key is readable only by the owner.

The vault refuses to store anything when the ``cryptography`` package is
absent. A secret store that quietly falls back to plaintext is worse than
no secret store.

Layer: Pillar 4 (Tools & Memory).
"""

from .audit import AuditLogger
from .capabilities import CapabilityPolicy, load_policy
from .rate_limit import RateLimiter, TokenBucket
from .scanners import (
    InjectionScanner,
    PIIScanner,
    ScanFinding,
    ScanResult,
    SecretScanner,
    redact_text,
    scan_text,
)
from .signing import (
    KeyPair,
    ensure_key_pair,
    sign_bytes,
    sign_file,
    signing_key_path,
    verify_bytes,
    verify_file,
)
from .ssrf import SSRFError, is_safe_url, validate_url
from .vault import (
    Vault,
    VaultError,
    encryption_available,
    key_path,
    vault_path,
)

__all__ = [
    "AuditLogger",
    "CapabilityPolicy",
    "InjectionScanner",
    "KeyPair",
    "PIIScanner",
    "RateLimiter",
    "SSRFError",
    "ScanFinding",
    "ScanResult",
    "SecretScanner",
    "TokenBucket",
    "Vault",
    "VaultError",
    "encryption_available",
    "ensure_key_pair",
    "is_safe_url",
    "key_path",
    "load_policy",
    "redact_text",
    "scan_text",
    "sign_bytes",
    "sign_file",
    "signing_key_path",
    "validate_url",
    "vault_path",
    "verify_bytes",
    "verify_file",
]
