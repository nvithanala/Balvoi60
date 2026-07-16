from __future__ import annotations

from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

from server.feed import _itunes_duration, build_feed
from tests.helpers import SAMPLE_EDITION, SAMPLE_EPISODE


def test_itunes_duration_formats() -> None:
    assert _itunes_duration(90) == "1:30"
    assert _itunes_duration(3661) == "1:01:01"


def test_build_feed_xml_structure() -> None:
    xml = build_feed(
        SAMPLE_EDITION,
        [SAMPLE_EPISODE],
        "https://example.com",
        lambda _ep: 12345,
    )
    root = ET.fromstring(xml)
    assert root.tag == "rss"
    channel = root.find("channel")
    assert channel is not None
    assert channel.findtext("title") == "BalVoi:30 News"
    assert channel.findtext("language") == "en-US"

    item = channel.find("item")
    assert item is not None
    assert item.findtext("title") == "Headline Alpha"
    assert "Headline Alpha" in (item.findtext("description") or "")
    assert item.findtext("{http://www.itunes.com/dtds/podcast-1.0.dtd}duration") == "32:06"

    enclosure = item.find("enclosure")
    assert enclosure is not None
    assert enclosure.get("url") == (
        "https://example.com/episodes/2026-06-16T23-25-10Z/en.mp3"
    )
    assert enclosure.get("length") == "12345"
    assert enclosure.get("type") == "audio/mpeg"

    pub_date = item.findtext("pubDate")
    assert pub_date
    assert parsedate_to_datetime(pub_date).year == 2026


def test_build_feed_escapes_xml_special_characters() -> None:
    edition = {**SAMPLE_EDITION, "name": "News & Views"}
    episode = {
        **SAMPLE_EPISODE,
        "headlines": ['Break: "Markets" & more'],
    }
    xml = build_feed(edition, [episode], "https://example.com", lambda _ep: 1)
    assert "<title>News &amp; Views</title>" in xml
    assert '<title>Break: "Markets" &amp; more</title>' in xml
    assert "<description>Break: &quot;Markets&quot;" not in xml  # quotes left literal in text nodes


def test_build_feed_strips_trailing_slash_from_base_url() -> None:
    xml = build_feed(SAMPLE_EDITION, [SAMPLE_EPISODE], "https://example.com/", lambda _ep: 1)
    assert 'url="https://example.com/episodes/2026-06-16T23-25-10Z/en.mp3"' in xml
