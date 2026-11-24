"""Concrete connectors: registration, status, and sync with mocked HTTP."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from psycho_carla.connectors import (  # noqa: E402
    KnowledgeStore,
    available_connectors,
    get_connector,
    sync_connector,
)
from psycho_carla.connectors import builtin  # noqa: E402
from psycho_carla.connectors.base import SyncStatus  # noqa: E402

UPSTREAM_27 = {
    "apple_calendar", "apple_contacts", "apple_health", "apple_music",
    "apple_notes", "dropbox", "gcalendar", "gcontacts", "gdrive",
    "github_notifications", "gmail", "gmail_imap", "google_tasks",
    "granola", "hackernews", "imap", "imessage", "news_rss", "notion",
    "obsidian", "oura", "outlook", "slack", "spotify", "strava",
    "weather", "whatsapp",
}


def test_all_upstream_connectors_registered():
    assert UPSTREAM_27 <= set(available_connectors())


def test_every_connector_instantiates_with_status():
    for connector_id in available_connectors():
        connector = get_connector(connector_id)
        assert connector is not None, connector_id
        assert isinstance(connector.status(), SyncStatus), connector_id


def test_gmail_requires_credentials():
    connector = get_connector("gmail")
    assert connector.status() == SyncStatus.AUTH_REQUIRED
    with pytest.raises(RuntimeError, match="not connected"):
        connector.sync()


def test_obsidian_syncs_a_vault(tmp_path):
    (tmp_path / "note.md").write_text("# Shopping\n\nEggs and flour.")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "idea.txt").write_text("Build a thing.")
    connector = builtin.ObsidianConnector(vault_path=str(tmp_path))
    docs = connector.sync()
    assert len(docs) == 2
    assert {d.source for d in docs} == {"obsidian"}


def test_hackernews_sync_with_mocked_http(monkeypatch):
    def fake_get(url, **kwargs):
        if url.endswith("topstories.json"):
            return [101, 102]
        if "/101" in url:
            return {"type": "story", "title": "Big launch", "url": "https://x",
                    "by": "pg", "score": 42, "time": 1700000000}
        return {"type": "comment", "text": "skip me"}

    monkeypatch.setattr(builtin, "_get_json", fake_get)
    docs = builtin.HackerNewsConnector().sync(limit=5)
    assert len(docs) == 1
    assert docs[0].title == "Big launch"
    assert docs[0].metadata["author"] == "pg"


def test_rss_sync_with_mocked_fetch(monkeypatch):
    feed = """<?xml version="1.0"?>
    <rss><channel>
      <item><title>First</title><link>https://a/1</link>
      <description>alpha</description></item>
      <item><title>Second</title><link>https://a/2</link>
      <description>beta</description></item>
    </channel></rss>"""
    monkeypatch.setattr(builtin, "_fetch_text", lambda url, **kw: feed)
    connector = builtin.NewsRSSConnector(feeds=["https://feed.example/rss"])
    docs = connector.sync()
    assert [d.title for d in docs] == ["First", "Second"]


def test_sync_connector_indexes_into_knowledge_store(tmp_path, monkeypatch):
    def fake_get(url, **kwargs):
        if url.endswith("topstories.json"):
            return [7]
        return {"type": "story", "title": "Indexed story", "text": "body text",
                "time": 1700000000}

    monkeypatch.setattr(builtin, "_get_json", fake_get)
    store = KnowledgeStore(tmp_path / "knowledge.db")
    count = sync_connector("hackernews", store)
    assert count == 1
    results = store.search("Indexed")
    assert results and results[0].title == "Indexed story"
    assert store.count("hackernews") == 1


def test_weather_sync_with_mocked_http(monkeypatch):
    monkeypatch.setattr(builtin, "_get_json", lambda url, **kw: {
        "daily": {"time": ["2026-11-14"],
                  "temperature_2m_min": [12.0], "temperature_2m_max": [24.0],
                  "precipitation_probability_max": [10], "weathercode": [0]},
    })
    docs = builtin.WeatherConnector().sync()
    assert len(docs) == 1
    assert "24.0" in docs[0].body


def test_apple_calendar_parses_ics(tmp_path):
    ics = tmp_path / "cal.ics"
    ics.write_text("BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:1\nSUMMARY:Dentist\n"
                   "DTSTART:20261120T090000\nEND:VEVENT\nEND:VCALENDAR\n")
    connector = builtin.AppleCalendarConnector(data_path=str(ics))
    docs = connector.sync()
    assert len(docs) == 1
    assert docs[0].title == "Dentist"


def test_imessage_honest_on_non_mac(tmp_path):
    connector = get_connector("imessage")
    assert connector.status() == SyncStatus.NOT_CONNECTED
    with pytest.raises(RuntimeError, match="macOS"):
        connector.sync()
