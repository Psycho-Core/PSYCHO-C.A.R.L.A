"""
PSYCHO-C.A.R.L.A. — Connectors.

A connector brings data from an external service into the agent's reach:
mail, files, calendars, notes. Most need OAuth, so this package provides:

  * :mod:`.oauth` — the OAuth 2.0 authorisation-code flow with a localhost
    callback, provider registry, and secure token storage
  * :mod:`.base` — the `BaseConnector` contract and the `Document` /
    `Attachment` types
  * :mod:`.store` — a local knowledge store for synced documents
  * :mod:`.registry` — the connector registry

Security note: tokens are written with owner-only permissions (0600 on
POSIX). The refresh token is a long-lived credential and is treated as a
secret throughout.

Layer: Pillar 4 (Tools & Memory).
"""

from .base import Attachment, BaseConnector, Document, SyncStatus
from .oauth import (
    OAUTH_PROVIDERS,
    OAuthProvider,
    build_auth_url,
    connector_status,
    delete_tokens,
    get_client_credentials,
    get_provider_for_connector,
    load_tokens,
    refresh_access_token,
    require_access_token,
    run_connector_oauth,
    save_tokens,
)
from .registry import (
    ConnectorRegistry,
    available_connectors,
    get_connector,
    register_connector,
)
from .builtin import register_builtin_connectors, sync_connector
from .store import KnowledgeStore

__all__ = [
    "Attachment",
    "BaseConnector",
    "ConnectorRegistry",
    "Document",
    "OAUTH_PROVIDERS",
    "OAuthProvider",
    "SyncStatus",
    "KnowledgeStore",
    "available_connectors",
    "build_auth_url",
    "connector_status",
    "delete_tokens",
    "get_client_credentials",
    "get_connector",
    "get_provider_for_connector",
    "load_tokens",
    "refresh_access_token",
    "require_access_token",
    "register_connector",
    "register_builtin_connectors",
    "run_connector_oauth",
    "sync_connector",
    "save_tokens",
]
