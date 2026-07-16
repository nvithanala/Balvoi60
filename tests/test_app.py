from __future__ import annotations

import pytest

from balvoi import config
from server.app import create_app
from tests.helpers import SAMPLE_EPISODE


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("server.data.latest_map", lambda: {"en": SAMPLE_EPISODE})
    monkeypatch.setattr(
        "server.data.latest_for",
        lambda slug: SAMPLE_EPISODE if slug == "en" else None,
    )
    monkeypatch.setattr(
        "server.data.history_for",
        lambda slug: [SAMPLE_EPISODE] if slug == "en" else [],
    )
    monkeypatch.setattr(
        "server.data.episode_by_id",
        lambda episode_id: SAMPLE_EPISODE if episode_id == SAMPLE_EPISODE["id"] else None,
    )
    monkeypatch.setattr(
        "server.data.status",
        lambda: {"lastRunId": "2026-06-16T23-25-10Z", "lastSuccess": "2026-06-16T23:29:48+00:00"},
    )
    monkeypatch.setattr("server.data.audio_size", lambda _ep: 999)

    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def test_health(client) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["lastRunId"] == "2026-06-16T23-25-10Z"


def test_index(client) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert b"BalVoi" in response.data


def test_edition_page(client) -> None:
    response = client.get("/en")
    assert response.status_code == 200
    assert b"Five Eyes" in response.data or b"BalVoi" in response.data


def test_edition_page_unknown_slug(client) -> None:
    response = client.get("/not-a-real-edition")
    assert response.status_code == 404


def test_episode_page(client) -> None:
    response = client.get(f"/episode/{SAMPLE_EPISODE['id']}")
    assert response.status_code == 200
    assert b"Headline Alpha" in response.data


def test_feed(client) -> None:
    response = client.get("/feed/en.xml")
    assert response.status_code == 200
    assert response.mimetype == "application/rss+xml"
    assert b"<rss" in response.data
    assert b"Headline Alpha" in response.data
    assert b"https://" in response.data or b"/episodes/" in response.data


def test_feed_unknown_edition(client) -> None:
    response = client.get("/feed/xx.xml")
    assert response.status_code == 404


def test_public_base_url_used_in_feed(client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://pod.example.com")
    response = client.get("/feed/en.xml")
    assert response.status_code == 200
    assert b"https://pod.example.com/episodes/" in response.data


def test_editions_match_config(client) -> None:
    response = client.get("/")
    assert response.status_code == 200
    slugs = {e["slug"] for e in config.editions()}
    assert slugs == {"en", "es", "pt", "fr", "de", "ar", "ru", "tr"}
