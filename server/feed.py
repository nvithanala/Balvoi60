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
    *,
    owner_name: str = "BalVoi:60",
    owner_email: str = "",
    artwork_url: str = "",
) -> str:
    base = base_url.rstrip("/")
    slug = edition.get("slug", "")
    title = edition.get("name", "BalVoi:60")
    edition_name = edition.get("editionName", "")
    description = f"{edition_name} — world news every sixty minutes from BalVoi:60.".strip(" —")
    link = f"{base}/{slug}"
    language = edition.get("locale", "en")

    items: list[str] = []
    for ep in episodes:
        audio_size = size_for(ep)
        if audio_size <= 0 or not ep.get("audioUrl"):
            continue
        audio_url = f"{base}{ep.get('audioUrl', '')}"
        headlines = ep.get("headlines") or []
        item_title = headlines[0] if headlines else f"{edition_name} — {ep.get('timestamp', '')}"
        item_desc = " • ".join(headlines) if headlines else description
        items.append(
            f"""    <item>
      <title>{escape(item_title)}</title>
      <description>{escape(item_desc)}</description>
      <pubDate>{format_rfc2822(ep.get("timestamp"))}</pubDate>
      <guid isPermaLink="false">{escape(str(ep.get("id", "")))}</guid>
      <enclosure url={quoteattr(audio_url)} length="{audio_size}" type="audio/mpeg"/>
      <itunes:duration>{_itunes_duration(ep.get("durationSeconds"))}</itunes:duration>
      <itunes:author>BalVoi:60</itunes:author>
      <itunes:explicit>false</itunes:explicit>
    </item>"""
        )

    items_xml = "\n".join(items)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" xmlns:content="http://purl.org/rss/1.0/modules/content/" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>{escape(title)}</title>
    <link>{escape(link)}</link>
    <atom:link href={quoteattr(f"{base}/feed/{slug}.xml")} rel="self" type="application/rss+xml"/>
    <description>{escape(description)}</description>
    <language>{escape(language)}</language>
    <itunes:author>BalVoi:60</itunes:author>
    <itunes:summary>{escape(description)}</itunes:summary>
    <itunes:type>episodic</itunes:type>
    <itunes:category text="News"/>
    <itunes:explicit>false</itunes:explicit>
    <itunes:owner>
      <itunes:name>{escape(owner_name)}</itunes:name>
      <itunes:email>{escape(owner_email)}</itunes:email>
    </itunes:owner>
    {f"<itunes:image href={quoteattr(artwork_url)}/>" if artwork_url else ""}
{items_xml}
  </channel>
</rss>
"""
