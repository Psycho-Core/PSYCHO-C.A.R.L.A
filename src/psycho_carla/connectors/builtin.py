"""
Built-in concrete connectors.

Each connector implements the :class:`~psycho_carla.connectors.base.BaseConnector`
contract: ``auth_url()``, ``sync()`` returning ``list[Document]``, and
``status()``. OAuth-backed connectors reuse the provider/token machinery in
:mod:`psycho_carla.connectors.oauth`; API-key connectors resolve keys from
the environment (``PSYCHO_CARLA_<ID>_TOKEN`` / ``_API_KEY``) or a token file
in the connectors directory; local connectors (Obsidian vaults, RSS, Hacker
News, weather) need no credentials at all.

``sync()`` never raises for a missing credential — it returns an empty list
and ``status()`` reports ``AUTH_REQUIRED`` — but genuine API/parse failures
propagate as ``RuntimeError`` so callers can surface them.

Layer: Pillar 4 (Tools & Memory).
"""

from __future__ import annotations

import email
import imaplib
import json
import os
import platform
import sqlite3
import time
import xml.etree.ElementTree as ET
from email.header import decode_header
from pathlib import Path
from typing import Any, Iterable

from .base import Attachment, Document, SyncStatus
from .oauth import (
    CONNECTORS_DIR,
    build_auth_url,
    get_client_credentials,
    get_provider_for_connector,
    load_tokens,
    refresh_access_token,
)
from .registry import register_connector

# --------------------------------------------------------------------------- #
# HTTP helpers (module-level so tests can monkeypatch them)
# --------------------------------------------------------------------------- #

def _get_json(url: str, *, headers: dict[str, str] | None = None,
              params: dict[str, Any] | None = None, timeout: float = 30.0) -> Any:
    import httpx

    try:
        resp = httpx.get(url, headers=headers or {}, params=params or {},
                         timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        raise RuntimeError(f"GET {url} failed: {exc}") from exc


def _post_json(url: str, *, headers: dict[str, str] | None = None,
               json_body: dict[str, Any] | None = None,
               timeout: float = 30.0) -> Any:
    import httpx

    try:
        resp = httpx.post(url, headers=headers or {}, json=json_body or {},
                          timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        raise RuntimeError(f"POST {url} failed: {exc}") from exc


def _fetch_text(url: str, *, timeout: float = 30.0) -> str:
    import httpx

    try:
        resp = httpx.get(url, timeout=timeout, follow_redirects=True)
        resp.raise_for_status()
        return resp.text
    except Exception as exc:
        raise RuntimeError(f"GET {url} failed: {exc}") from exc


# --------------------------------------------------------------------------- #
# Credential helpers
# --------------------------------------------------------------------------- #

def stored_tokens(connector_id: str) -> dict[str, Any]:
    """Tokens stored for a connector (OAuth file first, then its own file)."""
    provider = get_provider_for_connector(connector_id)
    if provider is not None:
        for filename in provider.credential_files:
            tokens = load_tokens(CONNECTORS_DIR / filename)
            if tokens:
                return tokens
    return load_tokens(CONNECTORS_DIR / f"{connector_id}.json")


def access_token_for(connector_id: str) -> str:
    """A usable access token for an OAuth connector, refreshing if expired."""
    tokens = stored_tokens(connector_id)
    token = str(tokens.get("access_token") or "")
    if not token:
        return ""
    expires_in = tokens.get("expires_in") or 0
    obtained_at = tokens.get("obtained_at") or 0
    if expires_in and obtained_at and (time.time() - float(obtained_at)) > float(expires_in):
        if tokens.get("refresh_token"):
            try:
                fresh = refresh_access_token(connector_id)
                return str(fresh.get("access_token") or token)
            except Exception:
                return token
    return token


def api_key_for(connector_id: str) -> str:
    """An API key/token for non-OAuth connectors (env, then token file)."""
    upper = connector_id.upper().replace("-", "_")
    for env_name in (f"PSYCHO_CARLA_{upper}_TOKEN", f"PSYCHO_CARLA_{upper}_API_KEY",
                     f"{upper}_API_KEY", f"{upper}_TOKEN"):
        value = os.environ.get(env_name, "")
        if value:
            return value
    tokens = stored_tokens(connector_id)
    return str(tokens.get("access_token") or tokens.get("api_key") or "")


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


# --------------------------------------------------------------------------- #
# Base class
# --------------------------------------------------------------------------- #

class SyncConnector:
    """Shared plumbing for the built-in connectors."""

    connector_id: str = ""
    display_name: str = ""
    #: OAuth provider name, or "" for key/local connectors.
    provider_name: str = ""
    #: True when no credential of any kind is needed.
    open_access: bool = False

    def __init__(self, **options: Any) -> None:
        self.options = dict(options)
        self._last_sync_count = 0
        self._last_error = ""

    # -- contract ---------------------------------------------------------- #
    def auth_url(self) -> str:
        provider = get_provider_for_connector(self.connector_id)
        if provider is None:
            return ""
        creds = get_client_credentials(provider)
        if not creds:
            return ""
        return build_auth_url(provider, creds[0])

    def is_connected(self) -> bool:
        if self.open_access:
            return True
        if self.provider_name:
            return bool(access_token_for(self.connector_id))
        return bool(api_key_for(self.connector_id))

    def status(self) -> SyncStatus:
        if self._last_error:
            return SyncStatus.ERROR
        return SyncStatus.OK if self.is_connected() else SyncStatus.AUTH_REQUIRED

    def sync(self, **kwargs: Any) -> list[Document]:  # pragma: no cover
        raise NotImplementedError

    # -- helpers ------------------------------------------------------------- #
    def _require_token(self) -> str:
        token = access_token_for(self.connector_id) if self.provider_name \
            else api_key_for(self.connector_id)
        if not token:
            raise RuntimeError(
                f"{self.display_name} is not connected — no credentials stored. "
                f"Connect it with `carla connect login {self.connector_id}` "
                "or set its API key in the environment."
            )
        return token

    def _done(self, docs: list[Document]) -> list[Document]:
        self._last_sync_count = len(docs)
        self._last_error = ""
        return docs


def _doc(doc_id: str, source: str, title: str, body: str, url: str = "",
         created: float = 0.0, **metadata: Any) -> Document:
    return Document(doc_id=doc_id, title=title, body=body, source=source,
                    url=url, created_at=created, updated_at=created or time.time(),
                    metadata=metadata)


# --------------------------------------------------------------------------- #
# No-credential connectors
# --------------------------------------------------------------------------- #

class HackerNewsConnector(SyncConnector):
    connector_id = "hackernews"
    display_name = "Hacker News"
    open_access = True

    def sync(self, *, limit: int = 10, **kwargs: Any) -> list[Document]:
        ids = _get_json("https://hacker-news.firebaseio.com/v0/topstories.json") or []
        docs: list[Document] = []
        for story_id in list(ids)[: int(limit)]:
            item = _get_json(
                f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json") or {}
            if not item or item.get("type") != "story":
                continue
            docs.append(_doc(
                f"hackernews:{story_id}", "hackernews",
                str(item.get("title") or f"HN story {story_id}"),
                str(item.get("text") or item.get("url") or ""),
                str(item.get("url") or f"https://news.ycombinator.com/item?id={story_id}"),
                float(item.get("time") or 0),
                author=str(item.get("by") or ""), score=item.get("score", 0),
            ))
        return self._done(docs)


class NewsRSSConnector(SyncConnector):
    connector_id = "news_rss"
    display_name = "News RSS"
    open_access = True

    DEFAULT_FEEDS: tuple[str, ...] = ()

    def __init__(self, feeds: Iterable[str] | None = None, **options: Any) -> None:
        super().__init__(**options)
        self.feeds = [str(f) for f in (feeds or options.get("feeds") or self.DEFAULT_FEEDS)]

    def is_connected(self) -> bool:
        return bool(self.feeds)

    def sync(self, *, feeds: Iterable[str] | None = None, limit: int = 20,
             **kwargs: Any) -> list[Document]:
        urls = [str(f) for f in (feeds or self.feeds)]
        if not urls:
            raise RuntimeError(
                "News RSS is not configured — pass feed URLs to sync() or "
                "construct the connector with feeds=[...]."
            )
        docs: list[Document] = []
        for feed_url in urls:
            root = ET.fromstring(_fetch_text(feed_url))
            for item in root.iter("item"):
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                desc = (item.findtext("description") or "").strip()
                if not title and not link:
                    continue
                docs.append(_doc(f"news_rss:{link or title}", "news_rss",
                                 title or link, desc, link, feed=feed_url))
                if len(docs) >= int(limit):
                    return self._done(docs)
            for entry in root.iter("{http://www.w3.org/2005/Atom}entry"):
                ns = "{http://www.w3.org/2005/Atom}"
                title = (entry.findtext(f"{ns}title") or "").strip()
                link_el = entry.find(f"{ns}link")
                link = link_el.get("href", "") if link_el is not None else ""
                summary = (entry.findtext(f"{ns}summary") or "").strip()
                if title or link:
                    docs.append(_doc(f"news_rss:{link or title}", "news_rss",
                                     title or link, summary, link, feed=feed_url))
        return self._done(docs[: int(limit)])


class WeatherConnector(SyncConnector):
    connector_id = "weather"
    display_name = "Weather (Open-Meteo)"
    open_access = True

    def __init__(self, location: str = "Adelaide", latitude: float = -34.9285,
                 longitude: float = 138.6007, **options: Any) -> None:
        super().__init__(**options)
        self.location = str(options.get("location", location))
        self.latitude = float(options.get("latitude", latitude))
        self.longitude = float(options.get("longitude", longitude))

    def sync(self, *, days: int = 3, **kwargs: Any) -> list[Document]:
        data = _get_json(
            "https://api.open-meteo.com/v1/forecast",
            params={"latitude": self.latitude, "longitude": self.longitude,
                    "daily": "temperature_2m_max,temperature_2m_min,"
                             "precipitation_probability_max,weathercode",
                    "forecast_days": int(days), "timezone": "auto"},
        ) or {}
        daily = data.get("daily") or {}
        lines: list[str] = []
        for i, date in enumerate(daily.get("time") or []):
            lines.append(
                f"{date}: {daily['temperature_2m_min'][i]}–"
                f"{daily['temperature_2m_max'][i]} °C, precipitation chance "
                f"{(daily.get('precipitation_probability_max') or [0] * 9)[i]}%"
            )
        return self._done([_doc(
            f"weather:{self.location}", "weather",
            f"Weather forecast — {self.location}", "\n".join(lines),
            "https://open-meteo.com/", location=self.location,
        )])


class ObsidianConnector(SyncConnector):
    connector_id = "obsidian"
    display_name = "Obsidian / Markdown vault"

    def __init__(self, vault_path: str = "", **options: Any) -> None:
        super().__init__(**options)
        self.vault_path = str(options.get("vault_path", vault_path) or "")

    def is_connected(self) -> bool:
        return bool(self.vault_path) and Path(self.vault_path).is_dir()

    def status(self) -> SyncStatus:
        if not self.vault_path:
            return SyncStatus.NOT_CONNECTED
        return SyncStatus.OK if self.is_connected() else SyncStatus.ERROR

    def sync(self, *, vault_path: str = "", limit: int = 500,
             **kwargs: Any) -> list[Document]:
        root = Path(vault_path or self.vault_path)
        if not root.is_dir():
            raise RuntimeError(f"Obsidian vault not found: {root}")
        docs: list[Document] = []
        for path in sorted(root.rglob("*")):
            if path.suffix.lower() not in (".md", ".markdown", ".txt"):
                continue
            if any(part.startswith(".") for part in path.parts):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            rel = str(path.relative_to(root))
            docs.append(_doc(f"obsidian:{rel}", "obsidian",
                             path.stem, text, str(path),
                             path.stat().st_mtime, path=rel))
            if len(docs) >= int(limit):
                break
        return self._done(docs)


# --------------------------------------------------------------------------- #
# Google (one OAuth provider, five connectors)
# --------------------------------------------------------------------------- #

class _GoogleConnector(SyncConnector):
    provider_name = "google"
    _api_base = "https://www.googleapis.com"

    def _get(self, url: str, **params: Any) -> Any:
        return _get_json(url, headers=_bearer(self._require_token()), params=params)


class GmailConnector(_GoogleConnector):
    connector_id = "gmail"
    display_name = "Gmail"

    def sync(self, *, limit: int = 20, query: str = "", **kwargs: Any) -> list[Document]:
        listing = self._get(f"{self._api_base}/gmail/v1/users/me/messages",
                            maxResults=int(limit), q=query) or {}
        docs: list[Document] = []
        for entry in listing.get("messages") or []:
            msg = self._get(
                f"{self._api_base}/gmail/v1/users/me/messages/{entry['id']}",
                format="metadata") or {}
            headers = {h.get("name", "").lower(): h.get("value", "")
                       for h in (msg.get("payload") or {}).get("headers") or []}
            docs.append(_doc(
                f"gmail:{entry['id']}", "gmail",
                headers.get("subject", "(no subject)"),
                str(msg.get("snippet") or ""),
                f"https://mail.google.com/mail/u/0/#inbox/{entry['id']}",
                metadata={"from": headers.get("from", ""),
                          "date": headers.get("date", "")},
            ))
        return self._done(docs)


class GDriveConnector(_GoogleConnector):
    connector_id = "gdrive"
    display_name = "Google Drive"

    def sync(self, *, limit: int = 50, **kwargs: Any) -> list[Document]:
        data = self._get(f"{self._api_base}/drive/v3/files",
                         pageSize=int(limit),
                         fields="files(id,name,mimeType,modifiedTime,webViewLink)") or {}
        docs = [_doc(
            f"gdrive:{f.get('id')}", "gdrive", str(f.get("name") or ""),
            f"{f.get('mimeType', '')} file in Google Drive.",
            str(f.get("webViewLink") or ""),
            metadata={"mime_type": f.get("mimeType", ""),
                      "modified": f.get("modifiedTime", "")},
        ) for f in data.get("files") or []]
        return self._done(docs)


class GCalendarConnector(_GoogleConnector):
    connector_id = "gcalendar"
    display_name = "Google Calendar"

    def sync(self, *, limit: int = 25, days: int = 14, **kwargs: Any) -> list[Document]:
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        data = self._get(
            f"{self._api_base}/calendar/v3/calendars/primary/events",
            maxResults=int(limit), timeMin=now, singleEvents="true",
            orderBy="startTime") or {}
        docs: list[Document] = []
        for ev in data.get("items") or []:
            start = (ev.get("start") or {}).get("dateTime") or \
                (ev.get("start") or {}).get("date") or ""
            docs.append(_doc(
                f"gcalendar:{ev.get('id')}", "gcalendar",
                str(ev.get("summary") or "(untitled event)"),
                str(ev.get("description") or ""),
                str(ev.get("htmlLink") or ""), metadata={"start": start,
                "location": ev.get("location", "")},
            ))
        return self._done(docs)


class GContactsConnector(_GoogleConnector):
    connector_id = "gcontacts"
    display_name = "Google Contacts"

    def sync(self, *, limit: int = 50, **kwargs: Any) -> list[Document]:
        data = self._get("https://people.googleapis.com/v1/people/me/connections",
                         pageSize=int(limit),
                         personFields="names,emailAddresses,phoneNumbers") or {}
        docs: list[Document] = []
        for person in data.get("connections") or []:
            names = person.get("names") or [{}]
            emails = [e.get("value", "") for e in person.get("emailAddresses") or []]
            phones = [p.get("value", "") for p in person.get("phoneNumbers") or []]
            name = names[0].get("displayName", "") if names else ""
            docs.append(_doc(
                f"gcontacts:{person.get('resourceName', name)}", "gcontacts",
                name, f"Emails: {', '.join(emails)}\nPhones: {', '.join(phones)}",
                metadata={"emails": emails, "phones": phones},
            ))
        return self._done(docs)


class GoogleTasksConnector(_GoogleConnector):
    connector_id = "google_tasks"
    display_name = "Google Tasks"

    def sync(self, *, limit: int = 50, **kwargs: Any) -> list[Document]:
        lists = self._get(f"{self._api_base}/tasks/v1/users/@me/lists") or {}
        docs: list[Document] = []
        for task_list in lists.get("items") or []:
            tasks = self._get(
                f"{self._api_base}/tasks/v1/lists/{task_list['id']}/tasks",
                maxResults=int(limit)) or {}
            for task in tasks.get("items") or []:
                docs.append(_doc(
                    f"google_tasks:{task.get('id')}", "google_tasks",
                    str(task.get("title") or "(untitled task)"),
                    str(task.get("notes") or ""),
                    metadata={"list": task_list.get("title", ""),
                              "status": task.get("status", ""),
                              "due": task.get("due", "")},
                ))
        return self._done(docs)


# --------------------------------------------------------------------------- #
# Other OAuth / API-key services
# --------------------------------------------------------------------------- #

class GitHubNotificationsConnector(SyncConnector):
    connector_id = "github_notifications"
    display_name = "GitHub Notifications"
    provider_name = "github"

    def sync(self, *, limit: int = 30, **kwargs: Any) -> list[Document]:
        notes = _get_json("https://api.github.com/notifications",
                          headers=_bearer(self._require_token()),
                          params={"per_page": int(limit)}) or []
        docs: list[Document] = []
        for note in notes if isinstance(notes, list) else []:
            subject = note.get("subject") or {}
            repo = (note.get("repository") or {}).get("full_name", "")
            docs.append(_doc(
                f"github_notifications:{note.get('id')}", "github_notifications",
                f"{repo}: {subject.get('title', '')}",
                f"{note.get('reason', '')} notification on {repo}.",
                str(subject.get("url") or note.get("url") or ""),
                metadata={"repo": repo, "reason": note.get("reason", "")},
            ))
        return self._done(docs)


class GitHubConnector(GitHubNotificationsConnector):
    connector_id = "github"
    display_name = "GitHub"

    def sync(self, *, limit: int = 30, **kwargs: Any) -> list[Document]:
        docs = super().sync(limit=limit)
        repos = _get_json("https://api.github.com/user/repos",
                          headers=_bearer(self._require_token()),
                          params={"per_page": int(limit),
                                  "sort": "updated"}) or []
        for repo in repos if isinstance(repos, list) else []:
            docs.append(_doc(
                f"github:{repo.get('full_name')}", "github",
                str(repo.get("full_name") or ""),
                str(repo.get("description") or ""),
                str(repo.get("html_url") or ""),
                metadata={"language": repo.get("language") or "",
                          "stars": repo.get("stargazers_count", 0)},
            ))
        return self._done(docs)


class SpotifyConnector(SyncConnector):
    connector_id = "spotify"
    display_name = "Spotify"
    provider_name = "spotify"

    def sync(self, *, limit: int = 20, **kwargs: Any) -> list[Document]:
        data = _get_json(
            "https://api.spotify.com/v1/me/player/recently-played",
            headers=_bearer(self._require_token()), params={"limit": int(limit)}) or {}
        docs: list[Document] = []
        for item in data.get("items") or []:
            track = item.get("track") or {}
            artists = ", ".join(a.get("name", "") for a in track.get("artists") or [])
            docs.append(_doc(
                f"spotify:{track.get('id')}:{item.get('played_at', '')}",
                "spotify", f"{track.get('name', '')} — {artists}",
                f"Played at {item.get('played_at', '')}.",
                (track.get("external_urls") or {}).get("spotify", ""),
                metadata={"artists": artists},
            ))
        return self._done(docs)


class StravaConnector(SyncConnector):
    connector_id = "strava"
    display_name = "Strava"
    provider_name = "strava"

    def sync(self, *, limit: int = 20, **kwargs: Any) -> list[Document]:
        acts = _get_json("https://www.strava.com/api/v3/athlete/activities",
                         headers=_bearer(self._require_token()),
                         params={"per_page": int(limit)}) or []
        docs: list[Document] = []
        for act in acts if isinstance(acts, list) else []:
            docs.append(_doc(
                f"strava:{act.get('id')}", "strava",
                str(act.get("name") or "Activity"),
                f"{act.get('type', '')} — {act.get('distance', 0)} m in "
                f"{act.get('moving_time', 0)} s.",
                f"https://www.strava.com/activities/{act.get('id')}",
                metadata={"type": act.get("type", "")},
            ))
        return self._done(docs)


class NotionConnector(SyncConnector):
    connector_id = "notion"
    display_name = "Notion"

    def sync(self, *, limit: int = 25, **kwargs: Any) -> list[Document]:
        data = _post_json(
            "https://api.notion.com/v1/search",
            headers={**_bearer(self._require_token()),
                     "Notion-Version": "2022-06-28"},
            json_body={"page_size": int(limit)}) or {}
        docs: list[Document] = []
        for page in data.get("results") or []:
            props = page.get("properties") or {}
            title = ""
            for value in props.values():
                if isinstance(value, dict) and value.get("type") == "title":
                    title = "".join(t.get("plain_text", "")
                                    for t in value.get("title") or [])
                    break
            docs.append(_doc(
                f"notion:{page.get('id')}", "notion",
                title or "Notion page", f"Notion {page.get('object', 'page')}.",
                str(page.get("url") or ""),
                metadata={"last_edited": page.get("last_edited_time", "")},
            ))
        return self._done(docs)


class DropboxConnector(SyncConnector):
    connector_id = "dropbox"
    display_name = "Dropbox"

    def sync(self, *, limit: int = 50, **kwargs: Any) -> list[Document]:
        data = _post_json(
            "https://api.dropboxapi.com/2/files/list_folder",
            headers=_bearer(self._require_token()),
            json_body={"path": "", "recursive": True, "limit": int(limit)}) or {}
        docs = [_doc(
            f"dropbox:{entry.get('id') or entry.get('path_lower')}", "dropbox",
            str(entry.get("name") or ""), f"dropbox {entry.get('.tag', 'item')}.",
            metadata={"path": entry.get("path_display", ""),
                      "tag": entry.get(".tag", "")},
        ) for entry in data.get("entries") or []]
        return self._done(docs)


class SlackConnector(SyncConnector):
    connector_id = "slack"
    display_name = "Slack (data connector)"

    def sync(self, *, limit: int = 20, **kwargs: Any) -> list[Document]:
        token = self._require_token()
        convs = _get_json("https://slack.com/api/conversations.list",
                          headers=_bearer(token)) or {}
        docs: list[Document] = []
        for channel in (convs.get("channels") or [])[:5]:
            history = _get_json(
                "https://slack.com/api/conversations.history",
                headers=_bearer(token),
                params={"channel": channel["id"], "limit": int(limit)}) or {}
            for message in history.get("messages") or []:
                docs.append(_doc(
                    f"slack:{channel['id']}:{message.get('ts')}", "slack",
                    f"#{channel.get('name', '')}",
                    str(message.get("text") or ""),
                    metadata={"channel": channel.get("name", ""),
                              "user": message.get("user", "")},
                ))
        return self._done(docs)


class OuraConnector(SyncConnector):
    connector_id = "oura"
    display_name = "Oura"

    def sync(self, *, days: int = 7, **kwargs: Any) -> list[Document]:
        end = time.strftime("%Y-%m-%d")
        start = time.strftime("%Y-%m-%d", time.gmtime(time.time() - days * 86400))
        data = _get_json(
            "https://api.ouraring.com/v2/usercollection/daily_readiness",
            headers=_bearer(self._require_token()),
            params={"start_date": start, "end_date": end}) or {}
        docs = [_doc(
            f"oura:readiness:{day.get('day')}", "oura",
            f"Oura readiness {day.get('day')}",
            f"Score: {day.get('score')}.", metadata={"score": day.get("score")},
        ) for day in data.get("data") or []]
        return self._done(docs)


# --------------------------------------------------------------------------- #
# Mail (IMAP)
# --------------------------------------------------------------------------- #

def _decode_header_value(raw: str) -> str:
    parts: list[str] = []
    for chunk, charset in decode_header(raw or ""):
        if isinstance(chunk, bytes):
            parts.append(chunk.decode(charset or "utf-8", "replace"))
        else:
            parts.append(chunk)
    return "".join(parts)


class IMAPConnector(SyncConnector):
    connector_id = "imap"
    display_name = "IMAP Mail"
    imap_host = "imap.gmail.com"
    imap_port = 993

    def __init__(self, host: str = "", username: str = "", password: str = "",
                 **options: Any) -> None:
        super().__init__(**options)
        self.host = str(options.get("host", host) or self.imap_host)
        self.username = str(options.get("username", username) or "")
        self.password = str(options.get("password", password) or "")

    def _credentials(self) -> tuple[str, str]:
        user = self.username or os.environ.get(
            f"PSYCHO_CARLA_{self.connector_id.upper()}_USERNAME", "")
        password = self.password or api_key_for(self.connector_id)
        return user, password

    def is_connected(self) -> bool:
        user, password = self._credentials()
        return bool(user and password)

    def sync(self, *, limit: int = 15, mailbox: str = "INBOX",
             **kwargs: Any) -> list[Document]:
        user, password = self._credentials()
        if not (user and password):
            raise RuntimeError(
                f"{self.display_name} needs a username and app password "
                f"(PSYCHO_CARLA_{self.connector_id.upper()}_USERNAME / _TOKEN)."
            )
        docs: list[Document] = []
        client = imaplib.IMAP4_SSL(self.host, self.imap_port, timeout=30)
        try:
            client.login(user, password)
            client.select(mailbox, readonly=True)
            _, data = client.search(None, "ALL")
            ids = (data[0].split() if data and data[0] else [])[-int(limit):]
            for msg_id in reversed(ids):
                _, fetched = client.fetch(msg_id, "(RFC822.HEADER)")
                if not fetched or not fetched[0]:
                    continue
                message = email.message_from_bytes(fetched[0][1])
                docs.append(_doc(
                    f"{self.connector_id}:{msg_id.decode()}", self.connector_id,
                    _decode_header_value(message.get("Subject", "(no subject)")),
                    f"From: {_decode_header_value(message.get('From', ''))}\n"
                    f"Date: {message.get('Date', '')}",
                    metadata={"from": _decode_header_value(message.get("From", "")),
                              "date": message.get("Date", "")},
                ))
        finally:
            try:
                client.logout()
            except Exception:
                pass
        return self._done(docs)


class GmailIMAPConnector(IMAPConnector):
    connector_id = "gmail_imap"
    display_name = "Gmail (IMAP)"
    imap_host = "imap.gmail.com"


class OutlookConnector(IMAPConnector):
    connector_id = "outlook"
    display_name = "Outlook (IMAP)"
    imap_host = "outlook.office365.com"


# --------------------------------------------------------------------------- #
# Local-data connectors (Apple services, iMessage, WhatsApp, Granola)
# --------------------------------------------------------------------------- #

class _LocalDataConnector(SyncConnector):
    """Reads an exported/local data file; honest status everywhere else."""

    data_hint = "an exported data file"

    def __init__(self, data_path: str = "", **options: Any) -> None:
        super().__init__(**options)
        self.data_path = str(options.get("data_path", data_path) or "")

    def is_connected(self) -> bool:
        return bool(self.data_path) and Path(self.data_path).exists()

    def status(self) -> SyncStatus:
        return SyncStatus.OK if self.is_connected() else SyncStatus.NOT_CONNECTED

    def _require_path(self) -> Path:
        if not self.data_path:
            raise RuntimeError(
                f"{self.display_name} needs {self.data_hint}. Pass "
                "data_path=... pointing at the exported file."
            )
        path = Path(self.data_path)
        if not path.exists():
            raise RuntimeError(
                f"{self.display_name} needs {self.data_hint}. Pass "
                "data_path=... pointing at the exported file."
            )
        return path


class AppleHealthConnector(_LocalDataConnector):
    connector_id = "apple_health"
    display_name = "Apple Health"
    data_hint = "an Apple Health export.xml"

    def sync(self, *, limit: int = 100, **kwargs: Any) -> list[Document]:
        root = ET.parse(str(self._require_path())).getroot()
        docs: list[Document] = []
        for record in root.iter("Record"):
            docs.append(_doc(
                f"apple_health:{record.get('type')}:{record.get('startDate')}",
                "apple_health", str(record.get("type") or "Health record"),
                f"{record.get('value')} {record.get('unit', '')} "
                f"({record.get('startDate')})",
                metadata={"type": record.get("type", "")},
            ))
            if len(docs) >= int(limit):
                break
        return self._done(docs)


class AppleMusicConnector(_LocalDataConnector):
    connector_id = "apple_music"
    display_name = "Apple Music"
    data_hint = "an Apple Music library export (JSON)"

    def sync(self, **kwargs: Any) -> list[Document]:
        data = json.loads(self._require_path().read_text(encoding="utf-8"))
        tracks = data if isinstance(data, list) else data.get("tracks", [])
        docs = [_doc(
            f"apple_music:{t.get('id') or t.get('name')}", "apple_music",
            str(t.get("name") or "Track"),
            f"{t.get('artist', '')} — {t.get('album', '')}",
            metadata={"artist": t.get("artist", "")},
        ) for t in tracks if isinstance(t, dict)]
        return self._done(docs)


class AppleNotesConnector(_LocalDataConnector):
    connector_id = "apple_notes"
    display_name = "Apple Notes"
    data_hint = "a folder of exported notes (.txt/.md)"

    def sync(self, **kwargs: Any) -> list[Document]:
        root = self._require_path()
        files = [root] if root.is_file() else sorted(root.rglob("*.txt")) + \
            sorted(root.rglob("*.md"))
        docs = [_doc(f"apple_notes:{f.name}", "apple_notes", f.stem,
                     f.read_text(encoding="utf-8", errors="replace"), str(f))
                for f in files]
        return self._done(docs)


class AppleCalendarConnector(_LocalDataConnector):
    connector_id = "apple_calendar"
    display_name = "Apple Calendar"
    data_hint = "an exported .ics calendar file"

    def sync(self, **kwargs: Any) -> list[Document]:
        text = self._require_path().read_text(encoding="utf-8", errors="replace")
        docs: list[Document] = []
        for block in text.split("BEGIN:VEVENT")[1:]:
            fields = {}
            for line in block.splitlines():
                if ":" in line:
                    key, value = line.split(":", 1)
                    fields[key.strip()] = value.strip()
            docs.append(_doc(
                f"apple_calendar:{fields.get('UID', len(docs))}", "apple_calendar",
                fields.get("SUMMARY", "Event"), fields.get("DESCRIPTION", ""),
                metadata={"start": fields.get("DTSTART", "")},
            ))
        return self._done(docs)


class AppleContactsConnector(_LocalDataConnector):
    connector_id = "apple_contacts"
    display_name = "Apple Contacts"
    data_hint = "an exported contacts .vcf file"

    def sync(self, **kwargs: Any) -> list[Document]:
        text = self._require_path().read_text(encoding="utf-8", errors="replace")
        docs: list[Document] = []
        for block in text.split("BEGIN:VCARD")[1:]:
            fields = {}
            for line in block.splitlines():
                if ":" in line:
                    key, value = line.split(":", 1)
                    fields[key.strip()] = value.strip()
            docs.append(_doc(
                f"apple_contacts:{fields.get('FN', len(docs))}", "apple_contacts",
                fields.get("FN", "Contact"),
                f"Email: {fields.get('EMAIL', '')}\nPhone: {fields.get('TEL', '')}",
                metadata={"email": fields.get("EMAIL", "")},
            ))
        return self._done(docs)


class IMessageConnector(SyncConnector):
    connector_id = "imessage"
    display_name = "iMessage"
    db_path = Path.home() / "Library" / "Messages" / "chat.db"

    def is_connected(self) -> bool:
        return self.db_path.exists()

    def status(self) -> SyncStatus:
        return SyncStatus.OK if self.is_connected() else SyncStatus.NOT_CONNECTED

    def sync(self, *, limit: int = 25, **kwargs: Any) -> list[Document]:
        if platform.system() != "Darwin" or not self.db_path.exists():
            raise RuntimeError(
                "iMessage sync needs the macOS Messages database "
                "(~/Library/Messages/chat.db) on a Mac signed into Messages."
            )
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT m.ROWID, m.text, m.date, h.id FROM message m "
                "LEFT JOIN handle h ON m.handle_id = h.ROWID "
                "WHERE m.text IS NOT NULL ORDER BY m.date DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        finally:
            conn.close()
        docs = [_doc(f"imessage:{row[0]}", "imessage",
                     f"Message from {row[3] or 'unknown'}", str(row[1] or ""),
                     metadata={"contact": row[3] or ""}) for row in rows]
        return self._done(docs)


class WhatsAppConnector(_LocalDataConnector):
    connector_id = "whatsapp"
    display_name = "WhatsApp"
    data_hint = "a folder of exported WhatsApp chats (.txt)"

    def sync(self, **kwargs: Any) -> list[Document]:
        root = self._require_path()
        files = [root] if root.is_file() else sorted(root.rglob("*.txt"))
        docs = [_doc(f"whatsapp:{f.name}", "whatsapp", f.stem,
                     f.read_text(encoding="utf-8", errors="replace")[:20_000],
                     str(f)) for f in files]
        return self._done(docs)


class GranolaConnector(_LocalDataConnector):
    connector_id = "granola"
    display_name = "Granola"
    data_hint = "the Granola local cache (JSON meeting notes)"

    def sync(self, **kwargs: Any) -> list[Document]:
        path = self._require_path()
        data = json.loads(path.read_text(encoding="utf-8"))
        meetings = data if isinstance(data, list) else \
            data.get("meetings", data.get("notes", []))
        docs = [_doc(
            f"granola:{m.get('id') or m.get('title')}", "granola",
            str(m.get("title") or "Meeting"),
            str(m.get("notes") or m.get("summary") or ""),
            metadata={"date": m.get("date", "")},
        ) for m in meetings if isinstance(m, dict)]
        return self._done(docs)


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #

_BUILTIN_CLASSES: tuple[type[SyncConnector], ...] = (
    HackerNewsConnector, NewsRSSConnector, WeatherConnector, ObsidianConnector,
    GmailConnector, GDriveConnector, GCalendarConnector, GContactsConnector,
    GoogleTasksConnector, GitHubNotificationsConnector, GitHubConnector,
    SpotifyConnector, StravaConnector, NotionConnector, DropboxConnector,
    SlackConnector, OuraConnector, IMAPConnector, GmailIMAPConnector,
    OutlookConnector, AppleHealthConnector, AppleMusicConnector,
    AppleNotesConnector, AppleCalendarConnector, AppleContactsConnector,
    IMessageConnector, WhatsAppConnector, GranolaConnector,
)


def register_builtin_connectors() -> list[str]:
    """Register every built-in connector factory. Idempotent."""
    registered: list[str] = []
    for cls in _BUILTIN_CLASSES:
        register_connector(cls.connector_id, factory=cls)
        registered.append(cls.connector_id)
    return registered


def sync_connector(connector_id: str, store: Any, **kwargs: Any) -> int:
    """Sync one connector into a KnowledgeStore. Returns documents indexed."""
    from .registry import get_connector

    connector = get_connector(connector_id)
    if connector is None:
        raise ValueError(f"Unknown connector '{connector_id}'")
    documents = connector.sync(**kwargs)
    return int(store.add_many(documents))


__all__ = [
    "SyncConnector",
    "HackerNewsConnector", "NewsRSSConnector", "WeatherConnector",
    "ObsidianConnector", "GmailConnector", "GDriveConnector",
    "GCalendarConnector", "GContactsConnector", "GoogleTasksConnector",
    "GitHubNotificationsConnector", "GitHubConnector", "SpotifyConnector",
    "StravaConnector", "NotionConnector", "DropboxConnector",
    "SlackConnector", "OuraConnector", "IMAPConnector", "GmailIMAPConnector",
    "OutlookConnector", "AppleHealthConnector", "AppleMusicConnector",
    "AppleNotesConnector", "AppleCalendarConnector", "AppleContactsConnector",
    "IMessageConnector", "WhatsAppConnector", "GranolaConnector",
    "access_token_for", "api_key_for", "register_builtin_connectors",
    "stored_tokens", "sync_connector",
]
