"""Canonical typed application settings (T-M1-002).

Precedence (explicit):
  1. Process environment variables already set in the OS/process
  2. Values loaded from ``.env`` via ``load_app_dotenv`` with ``override=False``
     (``.env`` never clobbers an existing process env value)

Legacy alias:
  - ``CRON_ENABLED`` → ``SCHEDULER_ENABLED`` (deprecation warning; conflict rejected)

This module does not log raw secret values. Use ``redacted_diagnostics()``.
"""

from __future__ import annotations

import ipaddress
import os
import re
import warnings
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from balvoi.paths import ROOT
from pipeline.errors import ConfigurationError
from pipeline.lib.megaphone_client import PLACEHOLDER_HOSTS, public_base_url_issues

_TRUE = frozenset({"true", "1", "yes", "on"})
_FALSE = frozenset({"false", "0", "no", "off"})
_ENV_NAMES = frozenset({"development", "dev", "test", "testing", "staging", "production", "prod"})
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_SECRET_FIELDS = frozenset(
    {
        "balvoi_api_key",
        "openai_api_key",
        "elevenlabs_api_key",
        "megaphone_api_token",
        "megaphone_api_tokens_by_slug",
        "aws_access_key_id",
        "aws_secret_access_key",
    }
)

DEFAULT_EDITIONS = ("en", "es", "pt", "fr", "de", "ar", "ru", "tr")
MEGAPHONE_API_BASE_DEFAULT = "https://cms.megaphone.fm/api"


def parse_bool(name: str, raw: str | None, *, default: bool | None = None) -> bool:
    """Strict boolean parse: true/false/1/0/yes/no/on/off (case-insensitive)."""
    if raw is None or str(raw).strip() == "":
        if default is None:
            raise ConfigurationError(f"{name} must be true or false")
        return default
    normalized = str(raw).strip().lower()
    if normalized in _TRUE:
        return True
    if normalized in _FALSE:
        return False
    raise ConfigurationError(f"{name} must be true or false (got {raw!r})")


def parse_int(name: str, raw: str | None, *, default: int | None = None, minimum: int | None = None) -> int:
    if raw is None or str(raw).strip() == "":
        if default is None:
            raise ConfigurationError(f"{name} must be an integer")
        value = default
    else:
        try:
            value = int(str(raw).strip())
        except ValueError as err:
            raise ConfigurationError(f"{name} must be an integer") from err
    if minimum is not None and value < minimum:
        raise ConfigurationError(f"{name} must be >= {minimum}")
    return value


def parse_optional_uuid(name: str, raw: str | None, *, strict: bool = True) -> str:
    value = (raw or "").strip()
    if not value:
        return ""
    if _UUID_RE.match(value):
        return value.lower()
    if strict:
        # Redact most of the value in the error (no secret dump).
        shown = value if len(value) <= 12 else f"{value[:8]}…"
        raise ConfigurationError(f"{name} must be a UUID (got {shown})")
    return value

def normalize_url(name: str, raw: str | None, *, require_https: bool = False) -> str:
    """Strip trailing slash; optionally require https. Does not rewrite host/path."""
    value = (raw or "").strip().rstrip("/")
    if not value:
        return ""
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.netloc:
        raise ConfigurationError(f"{name} must be an absolute URL with scheme and host")
    if require_https and parsed.scheme != "https":
        raise ConfigurationError(f"{name} must use https")
    return value


def normalize_runtime_env(raw: str | None) -> str:
    value = (raw or "development").strip().lower()
    if value not in _ENV_NAMES:
        raise ConfigurationError(
            "BALVOI_ENV must be one of: development, test, staging, production "
            f"(got {raw!r})"
        )
    if value in {"dev"}:
        return "development"
    if value in {"testing"}:
        return "test"
    if value in {"prod"}:
        return "production"
    return value


def _env_get(environ: Mapping[str, str], name: str, default: str = "") -> str:
    return str(environ.get(name, default) if name in environ else default)


def _source_map(environ: Mapping[str, str], names: list[str]) -> dict[str, str]:
    return {name: ("process_env" if name in environ and environ.get(name) is not None else "default") for name in names}


@dataclass(frozen=True)
class AppSettings:
    """Typed snapshot of production-critical configuration."""

    runtime_env: str = "development"
    service_name: str = "balvoi60"
    storage_path: str = "storage"
    pipeline_editions: tuple[str, ...] = DEFAULT_EDITIONS
    scheduler_enabled: bool = False
    megaphone_enabled: bool = False
    megaphone_create_as_draft: bool = True
    megaphone_api_base: str = MEGAPHONE_API_BASE_DEFAULT
    megaphone_api_token: str = ""
    megaphone_network_id: str = ""
    megaphone_podcast_ids: dict[str, str] = field(default_factory=dict)
    megaphone_api_tokens_by_slug: dict[str, str] = field(default_factory=dict)
    megaphone_network_ids_by_slug: dict[str, str] = field(default_factory=dict)
    public_base_url: str = ""
    allow_private_public_base_url: bool = False
    balvoi_site_url: str = "https://staging.balvoi.com"
    balvoi_api_url: str = "https://api.staging.newsgenie.ai"
    balvoi_api_key: str = ""
    balvoi_article_limit: int = 200
    balvoi_article_window_minutes: int = 60
    balvoi_story_cooldown_minutes: int = 360
    balvoi_allow_demo_articles: bool = False
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    elevenlabs_api_key: str = ""
    http_connect_timeout_seconds: float = 10.0
    http_read_timeout_seconds: float = 45.0
    http_retry_limit: int = 3
    ffmpeg_path: str = ""
    log_level: str = "INFO"
    dry_run: bool = False
    preview_mode: bool = False
    min_publish_duration_seconds: int = 600
    language_worker_concurrency: int = 4
    translation_concurrency: int = 4
    tts_request_concurrency: int = 3
    merge_concurrency: int = 2
    port: int = 3001
    sources: dict[str, str] = field(default_factory=dict)

    @property
    def is_staging_or_production(self) -> bool:
        return self.runtime_env in {"staging", "production"}

    def redacted_diagnostics(self) -> dict[str, Any]:
        """Return resolved settings with secrets redacted and source hints."""
        data: dict[str, Any] = {
            "runtime_env": self.runtime_env,
            "service_name": self.service_name,
            "storage_path": self.storage_path,
            "pipeline_editions": list(self.pipeline_editions),
            "scheduler_enabled": self.scheduler_enabled,
            "megaphone_enabled": self.megaphone_enabled,
            "megaphone_create_as_draft": self.megaphone_create_as_draft,
            "megaphone_api_base": self.megaphone_api_base,
            "public_base_url": self.public_base_url,
            "balvoi_site_url": self.balvoi_site_url,
            "balvoi_api_url": self.balvoi_api_url,
            "openai_model": self.openai_model,
            "http_connect_timeout_seconds": self.http_connect_timeout_seconds,
            "http_read_timeout_seconds": self.http_read_timeout_seconds,
            "http_retry_limit": self.http_retry_limit,
            "ffmpeg_path": self.ffmpeg_path or "(which PATH)",
            "log_level": self.log_level,
            "dry_run": self.dry_run,
            "preview_mode": self.preview_mode,
            "min_publish_duration_seconds": self.min_publish_duration_seconds,
            "sources": dict(self.sources),
        }
        data["balvoi_api_key"] = "set" if self.balvoi_api_key else "unset"
        data["openai_api_key"] = "set" if self.openai_api_key else "unset"
        data["elevenlabs_api_key"] = "set" if self.elevenlabs_api_key else "unset"
        data["megaphone_api_token"] = "set" if self.megaphone_api_token else "unset"
        data["megaphone_network_id"] = (
            f"{self.megaphone_network_id[:8]}…" if self.megaphone_network_id else "unset"
        )
        data["megaphone_podcast_ids"] = {
            slug: (f"{pid[:8]}…" if pid else "unset") for slug, pid in self.megaphone_podcast_ids.items()
        }
        data["megaphone_api_tokens_by_slug"] = {
            slug: ("set" if token else "unset") for slug, token in self.megaphone_api_tokens_by_slug.items()
        }
        return data

    def pipeline_worker_settings(self) -> dict[str, int]:
        """Compatibility dict previously returned by ``validate_pipeline_config``."""
        return {
            "language_workers": self.language_worker_concurrency,
            "translation_workers": self.translation_concurrency,
            "tts_workers": self.tts_request_concurrency,
            "merge_workers": self.merge_concurrency,
            "article_window_minutes": self.balvoi_article_window_minutes,
            "story_cooldown_minutes": self.balvoi_story_cooldown_minutes,
            "minimum_publish_seconds": self.min_publish_duration_seconds,
        }


def load_app_dotenv(env_path: Path | None = None, *, environ: Mapping[str, str] | None = None) -> Path:
    """Load ``.env`` without overriding process environment (process wins).

    Returns the path that was considered (may not exist).
    """
    from dotenv import load_dotenv

    path = env_path if env_path is not None else ROOT / ".env"
    # ``override=False``: existing process env values win over .env
    if environ is None:
        load_dotenv(path, override=False)
    return path


def resolve_scheduler_enabled(environ: Mapping[str, str]) -> bool:
    """Canonical scheduler flag with legacy ``CRON_ENABLED`` alias."""
    canonical = environ.get("SCHEDULER_ENABLED")
    legacy = environ.get("CRON_ENABLED")
    if legacy is not None:
        warnings.warn(
            "CRON_ENABLED is deprecated; use SCHEDULER_ENABLED",
            DeprecationWarning,
            stacklevel=3,
        )
    if canonical is not None and legacy is not None:
        if parse_bool("SCHEDULER_ENABLED", canonical) != parse_bool("CRON_ENABLED", legacy):
            raise ConfigurationError("SCHEDULER_ENABLED conflicts with deprecated CRON_ENABLED")
        return parse_bool("SCHEDULER_ENABLED", canonical)
    if canonical is not None:
        return parse_bool("SCHEDULER_ENABLED", canonical)
    if legacy is not None:
        return parse_bool("CRON_ENABLED", legacy)
    return False


def _parse_editions(raw: str | None) -> tuple[str, ...]:
    text = (raw or ",".join(DEFAULT_EDITIONS)).strip()
    slugs = tuple(s.strip().lower() for s in text.split(",") if s.strip())
    if not slugs:
        raise ConfigurationError("PIPELINE_EDITIONS must list at least one slug")
    return slugs


def _parse_float(name: str, raw: str | None, *, default: float, minimum: float = 0.0) -> float:
    if raw is None or str(raw).strip() == "":
        value = default
    else:
        try:
            value = float(str(raw).strip())
        except ValueError as err:
            raise ConfigurationError(f"{name} must be a number") from err
    if value < minimum:
        raise ConfigurationError(f"{name} must be >= {minimum}")
    return value


def load_settings(
    environ: Mapping[str, str] | None = None,
    *,
    validate_public_base_for_env: bool = True,
) -> AppSettings:
    """Parse and validate settings from ``environ`` (default: ``os.environ``)."""
    env: Mapping[str, str] = os.environ if environ is None else environ
    sources: dict[str, str] = {}

    def take(name: str, default: str = "") -> str:
        if name in env and env.get(name) is not None and str(env.get(name)) != "":
            sources[name] = "process_env"
            return str(env.get(name))
        sources[name] = "default"
        return default

    runtime_env = normalize_runtime_env(take("BALVOI_ENV", "development") or "development")
    sources["BALVOI_ENV"] = "process_env" if "BALVOI_ENV" in env else "default"

    editions = _parse_editions(take("PIPELINE_EDITIONS", ",".join(DEFAULT_EDITIONS)))
    scheduler = resolve_scheduler_enabled(env)
    sources["SCHEDULER_ENABLED"] = (
        "process_env"
        if "SCHEDULER_ENABLED" in env
        else ("legacy:CRON_ENABLED" if "CRON_ENABLED" in env else "default")
    )

    megaphone_enabled = parse_bool("MEGAPHONE_ENABLED", env.get("MEGAPHONE_ENABLED"), default=False)
    create_draft = parse_bool(
        "MEGAPHONE_CREATE_AS_DRAFT", env.get("MEGAPHONE_CREATE_AS_DRAFT"), default=True
    )
    allow_private = parse_bool(
        "BALVOI_ALLOW_PRIVATE_PUBLIC_BASE_URL",
        env.get("BALVOI_ALLOW_PRIVATE_PUBLIC_BASE_URL"),
        default=False,
    )
    allow_demo = parse_bool(
        "BALVOI_ALLOW_DEMO_ARTICLES", env.get("BALVOI_ALLOW_DEMO_ARTICLES"), default=False
    )
    dry_run = parse_bool("DRY_RUN", env.get("DRY_RUN"), default=False)
    preview = parse_bool("PREVIEW_MODE", env.get("PREVIEW_MODE"), default=False)

    public_base = normalize_url(
        "PUBLIC_BASE_URL",
        take("PUBLIC_BASE_URL", ""),
        require_https=False,
    )
    if public_base:
        # Re-normalize with scheme check only when non-empty
        public_base = normalize_url("PUBLIC_BASE_URL", public_base, require_https=False)
        parsed = urlparse(public_base)
        if parsed.scheme not in {"http", "https"}:
            raise ConfigurationError("PUBLIC_BASE_URL scheme must be http or https")

    uuid_strict = runtime_env in {"staging", "production"}
    megaphone_token = (env.get("MEGAPHONE_API_TOKEN") or "").strip()
    megaphone_network = (env.get("MEGAPHONE_NETWORK_ID") or "").strip()
    if megaphone_network:
        megaphone_network = parse_optional_uuid(
            "MEGAPHONE_NETWORK_ID", megaphone_network, strict=uuid_strict
        )

    podcast_ids: dict[str, str] = {}
    tokens_by_slug: dict[str, str] = {}
    networks_by_slug: dict[str, str] = {}
    for slug in DEFAULT_EDITIONS:
        suffix = slug.upper()
        pod = (env.get(f"MEGAPHONE_PODCAST_ID_{suffix}") or "").strip()
        if pod:
            podcast_ids[slug] = parse_optional_uuid(
                f"MEGAPHONE_PODCAST_ID_{suffix}", pod, strict=uuid_strict
            )
        tok = (env.get(f"MEGAPHONE_API_TOKEN_{suffix}") or "").strip()
        if tok:
            tokens_by_slug[slug] = tok
        net = (env.get(f"MEGAPHONE_NETWORK_ID_{suffix}") or "").strip()
        if net:
            networks_by_slug[slug] = parse_optional_uuid(
                f"MEGAPHONE_NETWORK_ID_{suffix}", net, strict=uuid_strict
            )

    api_base = normalize_url(
        "MEGAPHONE_API_BASE",
        take("MEGAPHONE_API_BASE", MEGAPHONE_API_BASE_DEFAULT),
        require_https=True,
    ) or MEGAPHONE_API_BASE_DEFAULT

    window = parse_int(
        "BALVOI_ARTICLE_WINDOW_MINUTES",
        env.get("BALVOI_ARTICLE_WINDOW_MINUTES"),
        default=60,
        minimum=1,
    )
    if window != 60:
        raise ConfigurationError(
            "BALVOI_ARTICLE_WINDOW_MINUTES must be 60 for gap-free hourly ownership"
        )

    settings = AppSettings(
        runtime_env=runtime_env,
        service_name=(env.get("BALVOI_SERVICE_NAME") or "balvoi60").strip() or "balvoi60",
        storage_path=(env.get("STORAGE_PATH") or "storage").strip() or "storage",
        pipeline_editions=editions,
        scheduler_enabled=scheduler,
        megaphone_enabled=megaphone_enabled,
        megaphone_create_as_draft=create_draft,
        megaphone_api_base=api_base,
        megaphone_api_token=megaphone_token,
        megaphone_network_id=megaphone_network,
        megaphone_podcast_ids=podcast_ids,
        megaphone_api_tokens_by_slug=tokens_by_slug,
        megaphone_network_ids_by_slug=networks_by_slug,
        public_base_url=public_base,
        allow_private_public_base_url=allow_private,
        balvoi_site_url=normalize_url(
            "BALVOI_SITE_URL",
            take("BALVOI_SITE_URL", "https://staging.balvoi.com"),
        )
        or "https://staging.balvoi.com",
        balvoi_api_url=normalize_url(
            "BALVOI_API_URL",
            take("BALVOI_API_URL", "https://api.staging.newsgenie.ai"),
        )
        or "https://api.staging.newsgenie.ai",
        balvoi_api_key=(env.get("BALVOI_API_KEY") or "").strip(),
        balvoi_article_limit=parse_int(
            "BALVOI_ARTICLE_LIMIT", env.get("BALVOI_ARTICLE_LIMIT"), default=200, minimum=1
        ),
        balvoi_article_window_minutes=window,
        balvoi_story_cooldown_minutes=parse_int(
            "BALVOI_STORY_COOLDOWN_MINUTES",
            env.get("BALVOI_STORY_COOLDOWN_MINUTES"),
            default=360,
            minimum=0,
        ),
        balvoi_allow_demo_articles=allow_demo,
        openai_api_key=(env.get("OPENAI_API_KEY") or "").strip(),
        openai_model=(env.get("OPENAI_MODEL") or "gpt-4o-mini").strip() or "gpt-4o-mini",
        elevenlabs_api_key=(env.get("ELEVENLABS_API_KEY") or "").strip(),
        http_connect_timeout_seconds=_parse_float(
            "HTTP_CONNECT_TIMEOUT_SECONDS",
            env.get("HTTP_CONNECT_TIMEOUT_SECONDS"),
            default=10.0,
            minimum=0.1,
        ),
        http_read_timeout_seconds=_parse_float(
            "HTTP_READ_TIMEOUT_SECONDS",
            env.get("HTTP_READ_TIMEOUT_SECONDS"),
            default=45.0,
            minimum=0.1,
        ),
        http_retry_limit=parse_int(
            "HTTP_RETRY_LIMIT", env.get("HTTP_RETRY_LIMIT"), default=3, minimum=0
        ),
        ffmpeg_path=(env.get("FFMPEG_PATH") or "").strip(),
        log_level=(env.get("LOG_LEVEL") or "INFO").strip().upper() or "INFO",
        dry_run=dry_run,
        preview_mode=preview,
        min_publish_duration_seconds=parse_int(
            "MIN_PUBLISH_DURATION_SECONDS",
            env.get("MIN_PUBLISH_DURATION_SECONDS"),
            default=600,
            minimum=1,
        ),
        language_worker_concurrency=parse_int(
            "LANGUAGE_WORKER_CONCURRENCY",
            env.get("LANGUAGE_WORKER_CONCURRENCY"),
            default=4,
            minimum=1,
        ),
        translation_concurrency=parse_int(
            "TRANSLATION_CONCURRENCY", env.get("TRANSLATION_CONCURRENCY"), default=4, minimum=1
        ),
        tts_request_concurrency=parse_int(
            "TTS_REQUEST_CONCURRENCY", env.get("TTS_REQUEST_CONCURRENCY"), default=3, minimum=1
        ),
        merge_concurrency=parse_int(
            "MERGE_CONCURRENCY", env.get("MERGE_CONCURRENCY"), default=2, minimum=1
        ),
        port=parse_int("PORT", env.get("PORT"), default=3001, minimum=1),
        sources=sources,
    )

    if validate_public_base_for_env:
        _validate_public_base_policy(settings)
    if settings.megaphone_enabled and sources.get("BALVOI_ENV") == "default":
        raise ConfigurationError(
            "BALVOI_ENV must be set explicitly when MEGAPHONE_ENABLED=true. "
            "Unset BALVOI_ENV defaults to development and disables the demo-article block."
        )
    if settings.is_staging_or_production and settings.balvoi_allow_demo_articles:
        raise ConfigurationError(
            "BALVOI_ALLOW_DEMO_ARTICLES must be false in staging/production"
        )
    return settings


def _megaphone_rejects_public_host(host: str) -> bool:
    """Hosts that must never back Megaphone mediaFileUrl (ephemeral / non-public)."""
    hostname = (host or "").strip().lower().rstrip(".")
    if not hostname:
        return True
    if hostname in {"localhost", "127.0.0.1", "::1"}:
        return True
    if hostname == "trycloudflare.com" or hostname.endswith(".trycloudflare.com"):
        return True
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    # RFC1918 and other non-global unicast (literal IP hosts only; no DNS).
    return bool(ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified)


def _validate_public_base_policy(settings: AppSettings) -> None:
    """Reject placeholders / private hosts for Megaphone and staging/production."""
    raw = settings.public_base_url
    if not raw:
        if settings.megaphone_enabled or settings.is_staging_or_production:
            # staging/prod may still serve feeds without Megaphone; only hard-require
            # non-empty base when Megaphone is on. Placeholder checks apply when set.
            if settings.megaphone_enabled:
                raise ConfigurationError(
                    "PUBLIC_BASE_URL is required when MEGAPHONE_ENABLED=true"
                )
        return

    host = (urlparse(raw).hostname or "").lower()
    if settings.megaphone_enabled and _megaphone_rejects_public_host(host):
        raise ConfigurationError(
            f"PUBLIC_BASE_URL host '{host}' is not allowed when MEGAPHONE_ENABLED=true. "
            "Megaphone re-fetches this URL on reconcile and media-replace, so it must be permanent."
        )

    issues = public_base_url_issues(raw)
    if not issues:
        return

    # Allow documented break-glass for private hosts only (not placeholders).
    # Never applies when Megaphone is enabled (checked above).
    is_placeholder = (
        host in PLACEHOLDER_HOSTS
        or host.endswith(".example.com")
        or host.endswith(".example.org")
    )
    if (
        settings.allow_private_public_base_url
        and not is_placeholder
        and any("private" in i.lower() or "localhost" in i.lower() or "loopback" in i.lower() for i in issues)
        and not settings.is_staging_or_production
    ):
        return

    if settings.megaphone_enabled or settings.is_staging_or_production:
        raise ConfigurationError("; ".join(issues))
    # development/test with Megaphone off: warn via issues only when Megaphone later enabled


def validate_settings_for_pipeline(
    settings: AppSettings,
    edition_slugs: list[str],
    *,
    dry_run: bool,
) -> None:
    """Fail-closed checks for a pipeline invocation (used by validate_pipeline_config)."""
    if settings.balvoi_article_window_minutes != 60:
        raise ConfigurationError(
            "BALVOI_ARTICLE_WINDOW_MINUTES must be 60 for gap-free hourly ownership"
        )

    if dry_run:
        return

    if not settings.balvoi_allow_demo_articles and not settings.balvoi_api_key:
        raise ConfigurationError("BALVOI_API_KEY is required for article fetch")

    # LLM calls (translation / headline rewrite) use NewsGenie POST /bedrock/prompt
    # authenticated with BALVOI_API_KEY (same credential as article fetch).

    if not settings.elevenlabs_api_key:
        raise ConfigurationError("ELEVENLABS_API_KEY is required for audio synthesis")

    if settings.megaphone_enabled:
        if not settings.public_base_url:
            raise ConfigurationError("PUBLIC_BASE_URL is required for Megaphone media import")
        _validate_public_base_policy(settings)
        for slug in edition_slugs:
            token = settings.megaphone_api_tokens_by_slug.get(slug) or settings.megaphone_api_token
            network = settings.megaphone_network_ids_by_slug.get(slug) or settings.megaphone_network_id
            podcast = settings.megaphone_podcast_ids.get(slug, "")
            suffix = slug.upper()
            if not token:
                raise ConfigurationError(
                    f"MEGAPHONE_API_TOKEN_{suffix} or MEGAPHONE_API_TOKEN is required "
                    f"for Megaphone {slug} publication"
                )
            if not network:
                raise ConfigurationError(
                    f"MEGAPHONE_NETWORK_ID_{suffix} or MEGAPHONE_NETWORK_ID is required "
                    f"for Megaphone {slug} publication"
                )
            if not podcast:
                raise ConfigurationError(
                    f"MEGAPHONE_PODCAST_ID_{suffix} is required for Megaphone {slug} publication"
                )


# Module-level cache for optional single-load use (tests should call load_settings directly).
_cached: AppSettings | None = None


def get_settings(*, refresh: bool = False) -> AppSettings:
    global _cached
    if _cached is None or refresh:
        _cached = load_settings()
    return _cached


def clear_settings_cache() -> None:
    global _cached
    _cached = None
