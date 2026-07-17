"""BalVoi / NewsGenie API client — fetch podcast articles via API engine."""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from html import unescape

import requests

from balvoi.dates import (
    format_iso_utc,
    parse_any_datetime,
    parse_iso_datetime,
    previous_podcast_boundary,
)

DEFAULT_API_BASE = "https://api.staging.newsgenie.ai"
DEFAULT_ARTICLE_LIMIT = 200

# Model-leakage signatures anchored at paragraph/sentence start (case-insensitive).
# Only first-person meta-text about rewriting/debiasing — not incidental news vocabulary.
_LEADING = r'^(?:["\'\(\[]?\s*)?'

_JUNK_LEAD_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(_LEADING + r"i['']?m\s+ready\s+to\s+help\s+rewrite", re.I),
    re.compile(_LEADING + r"i['']?m\s+ready\s+to\s+rewrite", re.I),
    re.compile(_LEADING + r"the\s+sentence\s+to\s+rewrite\s+was\s+not\s+provided", re.I),
    re.compile(_LEADING + r"please\s+provide\s+the\s+sentence", re.I),
    re.compile(_LEADING + r"bias\s+detection\s+and\s+humani[sz]ation\s+guidelines", re.I),
    re.compile(_LEADING + r"debiasing\s+and\s+humani[sz]ation\s+process", re.I),
    re.compile(_LEADING + r"integrated\s+prompt\s+details", re.I),
)


def _is_junk_chunk(chunk: str) -> bool:
    normalized = re.sub(r"\s+", " ", chunk).strip()
    if not normalized:
        return False
    return any(pattern.match(normalized) for pattern in _JUNK_LEAD_PATTERNS)


def _split_body_chunks(raw: str) -> list[str]:
    if "<" in raw and ">" in raw:
        parts = re.split(r"</p>\s*|<p[^>]*>", raw, flags=re.I)
        chunks: list[str] = []
        for part in parts:
            chunk = unescape(re.sub(r"<[^>]+>", " ", part))
            chunk = re.sub(r"\s+", " ", chunk).strip()
            if chunk:
                chunks.append(chunk)
        return chunks

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", raw) if p.strip()]
    if len(paragraphs) > 1:
        return [unescape(re.sub(r"\s+", " ", p)) for p in paragraphs]

    text = unescape(re.sub(r"\s+", " ", raw)).strip()
    if not text:
        return []
    sentences = re.split(r"(?<=[.!?])\s+", text)
    return [s.strip() for s in sentences if s.strip()]


def clean_article_body(text: str, *, article_id: str | None = None) -> str:
    """Strip HTML and drop NewsGenie pipeline junk paragraphs from article bodies."""
    raw = str(text or "")
    if not raw.strip():
        return ""

    kept: list[str] = []
    chunks = _split_body_chunks(raw)
    total = len(chunks)
    dropped = 0
    for chunk in chunks:
        if _is_junk_chunk(chunk):
            dropped += 1
            continue
        kept.append(chunk)

    if article_id and dropped:
        print(f"  [clean] {article_id}: dropped {dropped}/{total} paragraphs")

    return re.sub(r"\s+", " ", " ".join(kept)).strip()


def _is_breaking(raw: dict) -> bool:
    if raw.get("breaking") is True:
        return True
    tags = [str(t).lower() for t in (raw.get("tags") or [])]
    if "breaking" in tags or "breaking news" in tags:
        return True
    cat = str(raw.get("category") or "").lower()
    if cat == "breaking":
        return True
    importance = str(raw.get("importance") or raw.get("tier") or "").lower()
    return importance in ("breaking", "hero", "urgent")


def normalize_podcast_article(raw: dict, site_url: str) -> dict:
    article_id = str(raw.get("_id") or raw.get("id") or raw.get("slug") or "")
    slug = str(raw.get("slug") or article_id)
    title = str(raw.get("title") or "Untitled")
    body = raw.get("body") or ""

    categories = raw.get("categories") or []
    if isinstance(categories, str):
        categories = [categories]
    tags = [str(c) for c in categories if c]
    category = tags[0] if tags else ""

    publish = raw.get("createdAt")
    dt = parse_any_datetime(publish) or datetime.now(UTC)

    mapped = {
        "id": article_id or slug,
        "slug": slug,
        "title": title,
        "summary": str(raw.get("summary") or "")[:500],
        "country": str(raw.get("countryName") or ""),
        "category": category,
        "tags": tags,
    }
    breaking_source = {**raw, "category": category, "tags": tags}

    return {
        "id": article_id or slug,
        "slug": slug,
        "title": title,
        "summary": mapped["summary"],
        "fullText": clean_article_body(str(body), article_id=article_id or slug),
        "url": f"{site_url.rstrip('/')}/story/{slug}",
        "publishDate": dt.isoformat(),
        "publishTimestamp": dt.timestamp(),
        "breaking": _is_breaking(breaking_source),
        "category": category,
        "tags": tags,
        "country": mapped["country"],
        "source": str(raw.get("source") or "BalVoi"),
    }


def _resolve_since() -> str:
    override = os.environ.get("BALVOI_SINCE_OVERRIDE", "").strip()
    if override:
        dt = parse_iso_datetime(override)
        if dt:
            return format_iso_utc(dt)
        print(f"  [warn] invalid BALVOI_SINCE_OVERRIDE ({override!r}) — using clock boundary")
    return format_iso_utc(previous_podcast_boundary())


def fetch_podcast_articles() -> list[dict]:
    """Fetch articles from NewsGenie ``GET /podcast_articles`` for the current cycle window."""
    key = os.environ.get("BALVOI_API_KEY", "").strip()
    if not key:
        return []

    api_base = (os.environ.get("BALVOI_API_URL") or DEFAULT_API_BASE).rstrip("/")
    site_url = (os.environ.get("BALVOI_SITE_URL") or "https://staging.balvoi.com").rstrip("/")
    limit = int(os.environ.get("BALVOI_ARTICLE_LIMIT", str(DEFAULT_ARTICLE_LIMIT)))
    since = _resolve_since()

    url = f"{api_base}/podcast_articles"
    headers = {"X-Api-Token": key, "Accept": "application/json"}
    params = {"limit": limit, "since": since}

    try:
        res = requests.get(url, headers=headers, params=params, timeout=45)
    except requests.RequestException as err:
        print(f"  [warn] podcast_articles request failed: {err}")
        return []

    if not res.ok:
        print(f"  [warn] podcast_articles HTTP {res.status_code}")
        return []

    try:
        payload = res.json()
    except ValueError:
        print("  [warn] podcast_articles returned non-JSON response")
        return []

    if payload.get("status") is False:
        message = payload.get("message") or "unknown error"
        print(f"  [warn] podcast_articles status=false ({message})")
        return []

    data = payload.get("data") or {}
    items = data.get("articles") or []
    if not isinstance(items, list) or not items:
        print(f"  [warn] podcast_articles empty (since={since})")
        return []

    print(f"  [ok] podcast_articles: {len(items)} articles (since={since})")
    return [normalize_podcast_article(a, site_url) for a in items if isinstance(a, dict)]
