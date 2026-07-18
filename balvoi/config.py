"""Shared configuration access."""

from __future__ import annotations

import json
from pathlib import Path

from balvoi.paths import CONFIG_DIR

_ENGLISH_ALIASES = frozenset({"english", "en", "en-us"})


def is_english(language: str) -> bool:
    return language.lower() in _ENGLISH_ALIASES


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def load_editions_doc() -> dict:
    """Load ``config/editions.json`` with safe fallbacks when missing or invalid."""
    return _read_json(
        CONFIG_DIR / "editions.json",
        {"editions": [], "masterBrand": {}},
    )


def editions() -> list[dict]:
    return load_editions_doc().get("editions", [])


def edition_by_slug(slug: str) -> dict | None:
    if not slug:
        return None
    return next((e for e in editions() if e.get("slug") == slug), None)


def master_brand() -> dict:
    brand = load_editions_doc().get("masterBrand", {})
    return {
        "name": brand.get("name", "BalVoi:60"),
        "tagline": brand.get("tagline", "The Global Podcast Network"),
        "subtitle": brand.get("subtitle", "World News Every 60 Minutes"),
    }
