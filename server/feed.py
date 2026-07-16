"""Build a standard podcast RSS 2.0 feed (with iTunes tags) per edition."""

from __future__ import annotations

from collections.abc import Callable
from xml.sax.saxutils import escape, quoteattr

from balvoi.dates import format_rfc2822


def _itunes_duration(seconds) -> str:
    sec = int(seconds or 0)
    hours, rem = divmod(sec, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def build_feed(
    edition: dict,
    episodes: list[dict],
    base_url: str,
    size_for: Callable[[dict], int],
) -> str:
    base = base_url.rstrip("/")
    slug = edition.get("slug", "")
    title = edition.get("name", "BalVoi:30")
    edition_name = edition.get("editionName", "")
    description = f"{edition_name} — world news every 30 minutes from BalVoi:30.".strip(" —")
    link = f"{base}/{slug}"
    language = edition.get("locale", "en")

    items: list[str] = []
    for ep in episodes:
        audio_url = f"{base}{ep.get('audioUrl', '')}"
        headlines = ep.get("headlines") or []
        item_title = headlines[0] if headlines else f"{edition_name} — {ep.get('timestamp', '')}"
        item_desc = " • ".join(headlines) if headlines else description
        items.append(
            f"""    <item>
      <title>{escape(item_title)}</title>
      <description>{escape(item_desc)}</description>
      <pubDate>{format_rfc2822(ep.get('timestamp'))}</pubDate>
      <guid isPermaLink="false">{escape(str(ep.get('id', '')))}</guid>
      <enclosure url={quoteattr(audio_url)} length="{size_for(ep)}" type="audio/mpeg"/>
      <itunes:duration>{_itunes_duration(ep.get('durationSeconds'))}</itunes:duration>
      <itunes:author>BalVoi:30</itunes:author>
      <itunes:explicit>false</itunes:explicit>
    </item>"""
        )

    items_xml = "\n".join(items)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>{escape(title)}</title>
    <link>{escape(link)}</link>
    <description>{escape(description)}</description>
    <language>{escape(language)}</language>
    <itunes:author>BalVoi:30</itunes:author>
    <itunes:summary>{escape(description)}</itunes:summary>
    <itunes:type>episodic</itunes:type>
    <itunes:category text="News"/>
    <itunes:explicit>false</itunes:explicit>
{items_xml}
  </channel>
</rss>
"""
