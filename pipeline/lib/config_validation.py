"""Centralized, operation-aware configuration validation."""

from __future__ import annotations

import os
import shutil
import warnings
from pathlib import Path

from balvoi.paths import storage_root
from pipeline.errors import ConfigurationError


def _bool(name: str, value: str) -> bool:
    normalized = value.strip().lower()
    if normalized not in {"true", "false", "1", "0", "yes", "no"}:
        raise ConfigurationError(f"{name} must be true or false")
    return normalized in {"true", "1", "yes"}


def scheduler_enabled(environ: dict[str, str] | None = None) -> bool:
    """Resolve the canonical scheduler flag, warning on the legacy name."""
    env = environ if environ is not None else os.environ
    canonical = env.get("SCHEDULER_ENABLED")
    legacy = env.get("CRON_ENABLED")
    if legacy is not None:
        warnings.warn(
            "CRON_ENABLED is deprecated; use SCHEDULER_ENABLED",
            DeprecationWarning,
            stacklevel=2,
        )
    if canonical is not None and legacy is not None:
        canonical_value = _bool("SCHEDULER_ENABLED", canonical)
        legacy_value = _bool("CRON_ENABLED", legacy)
        if canonical_value != legacy_value:
            raise ConfigurationError("SCHEDULER_ENABLED conflicts with deprecated CRON_ENABLED")
        return canonical_value
    if canonical is not None:
        return _bool("SCHEDULER_ENABLED", canonical)
    if legacy is not None:
        return _bool("CRON_ENABLED", legacy)
    return False


def positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as err:
        raise ConfigurationError(f"{name} must be a positive integer") from err
    if value <= 0:
        raise ConfigurationError(f"{name} must be a positive integer")
    return value


def nonnegative_int(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as err:
        raise ConfigurationError(f"{name} must be a non-negative integer") from err
    if value < 0:
        raise ConfigurationError(f"{name} must be a non-negative integer")
    return value


def _require(name: str, stage: str) -> None:
    if not os.environ.get(name, "").strip():
        raise ConfigurationError(f"{name} is required for {stage}")


def _validate_writable_storage(root: Path) -> None:
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as err:
        raise ConfigurationError(f"STORAGE_PATH must be writable for publication: {err}") from err


def validate_pipeline_config(edition_slugs: list[str], *, dry_run: bool) -> dict[str, int]:
    """Validate only the capabilities required by this pipeline invocation."""
    settings = {
        "language_workers": positive_int("LANGUAGE_WORKER_CONCURRENCY", 4),
        "translation_workers": positive_int("TRANSLATION_CONCURRENCY", 4),
        "tts_workers": positive_int("TTS_REQUEST_CONCURRENCY", 3),
        "merge_workers": positive_int("MERGE_CONCURRENCY", 2),
        "article_window_minutes": positive_int("BALVOI_ARTICLE_WINDOW_MINUTES", 60),
        "story_cooldown_minutes": nonnegative_int("BALVOI_STORY_COOLDOWN_MINUTES", 360),
        "minimum_publish_seconds": positive_int("MIN_PUBLISH_DURATION_SECONDS", 600),
    }
    if settings["article_window_minutes"] != 60:
        raise ConfigurationError(
            "BALVOI_ARTICLE_WINDOW_MINUTES must be 60 for gap-free hourly ownership"
        )
    if not dry_run and os.environ.get("BALVOI_ALLOW_DEMO_ARTICLES", "").lower() != "true":
        _require("BALVOI_API_KEY", "article fetch")
    if not dry_run and any(slug != "en" for slug in edition_slugs):
        _require("OPENAI_API_KEY", "non-English localization")
    if not dry_run:
        _require("ELEVENLABS_API_KEY", "audio synthesis")
        for executable in ("ffmpeg", "ffprobe"):
            if shutil.which(executable) is None:
                raise ConfigurationError(f"{executable} executable is required for audio merge")
        _validate_writable_storage(storage_root())
        if os.environ.get("MEGAPHONE_ENABLED", "").strip().lower() == "true":
            _require("PUBLIC_BASE_URL", "Megaphone media import")
            for slug in edition_slugs:
                suffix = slug.upper()
                _require(f"MEGAPHONE_API_TOKEN_{suffix}", f"Megaphone {slug} publication")
                _require(f"MEGAPHONE_NETWORK_ID_{suffix}", f"Megaphone {slug} publication")
                _require(f"MEGAPHONE_PODCAST_ID_{suffix}", f"Megaphone {slug} publication")
    scheduler_enabled()
    return settings
