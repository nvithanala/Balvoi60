"""T-M1-002 typed settings and startup validation tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.errors import ConfigurationError
from pipeline.lib import settings as app_settings
from pipeline.lib.config_validation import validate_pipeline_config
from pipeline.lib.settings import load_app_dotenv, load_settings, parse_bool, parse_int


def _base_dev(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BALVOI_ENV", "development")
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    monkeypatch.setenv("BALVOI_ARTICLE_WINDOW_MINUTES", "60")
    monkeypatch.delenv("CRON_ENABLED", raising=False)
    monkeypatch.delenv("SCHEDULER_ENABLED", raising=False)
    monkeypatch.delenv("MEGAPHONE_ENABLED", raising=False)
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("BALVOI_ALLOW_PRIVATE_PUBLIC_BASE_URL", raising=False)


def test_parse_bool_strict() -> None:
    assert parse_bool("X", "true") is True
    assert parse_bool("X", "FALSE") is False
    assert parse_bool("X", "1") is True
    assert parse_bool("X", "0") is False
    with pytest.raises(ConfigurationError):
        parse_bool("X", "maybe")


def test_parse_int_and_timeout_rejects_invalid() -> None:
    assert parse_int("N", "4", default=1, minimum=1) == 4
    with pytest.raises(ConfigurationError):
        parse_int("N", "nope", default=1, minimum=1)
    with pytest.raises(ConfigurationError):
        parse_int("N", "0", default=1, minimum=1)
    with pytest.raises(ConfigurationError):
        app_settings._parse_float("T", "x", default=1.0)


def test_valid_development_configuration(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _base_dev(monkeypatch, tmp_path)
    monkeypatch.setenv("MEGAPHONE_ENABLED", "false")
    cfg = load_settings()
    assert cfg.runtime_env == "development"
    assert cfg.megaphone_enabled is False
    assert cfg.pipeline_editions[0] == "en"


def test_valid_test_configuration(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _base_dev(monkeypatch, tmp_path)
    monkeypatch.setenv("BALVOI_ENV", "test")
    cfg = load_settings()
    assert cfg.runtime_env == "test"


def test_valid_staging_configuration(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _base_dev(monkeypatch, tmp_path)
    monkeypatch.setenv("BALVOI_ENV", "staging")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://cdn.staging.balvoi.com")
    monkeypatch.setenv("MEGAPHONE_ENABLED", "false")
    monkeypatch.setenv("BALVOI_ALLOW_DEMO_ARTICLES", "false")
    cfg = load_settings()
    assert cfg.runtime_env == "staging"
    assert cfg.public_base_url == "https://cdn.staging.balvoi.com"


def test_valid_production_configuration(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _base_dev(monkeypatch, tmp_path)
    monkeypatch.setenv("BALVOI_ENV", "production")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://cdn.balvoi.com")
    monkeypatch.setenv("MEGAPHONE_ENABLED", "false")
    monkeypatch.setenv("BALVOI_ALLOW_DEMO_ARTICLES", "false")
    monkeypatch.setenv(
        "MEGAPHONE_NETWORK_ID", "99184232-8214-11f1-9caf-57ae77772f7c"
    )
    cfg = load_settings()
    assert cfg.runtime_env == "production"
    assert cfg.megaphone_network_id == "99184232-8214-11f1-9caf-57ae77772f7c"


def test_publishing_disabled_allows_missing_megaphone_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _base_dev(monkeypatch, tmp_path)
    monkeypatch.setenv("MEGAPHONE_ENABLED", "false")
    monkeypatch.delenv("MEGAPHONE_API_TOKEN", raising=False)
    monkeypatch.delenv("MEGAPHONE_NETWORK_ID", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "x")
    monkeypatch.setenv("BALVOI_ALLOW_DEMO_ARTICLES", "true")
    monkeypatch.setattr("pipeline.lib.config_validation.shutil.which", lambda _n: "/bin/x")
    settings = validate_pipeline_config(["en"], dry_run=False)
    assert settings["language_workers"] >= 1


def test_publishing_enabled_missing_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _base_dev(monkeypatch, tmp_path)
    monkeypatch.setenv("MEGAPHONE_ENABLED", "true")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://cdn.staging.balvoi.com")
    monkeypatch.delenv("MEGAPHONE_API_TOKEN", raising=False)
    monkeypatch.setenv("MEGAPHONE_NETWORK_ID", "shared-network")
    monkeypatch.setenv("MEGAPHONE_PODCAST_ID_EN", "en-podcast")
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "x")
    monkeypatch.setenv("BALVOI_ALLOW_DEMO_ARTICLES", "true")
    monkeypatch.setattr("pipeline.lib.config_validation.shutil.which", lambda _n: "/bin/x")
    with pytest.raises(ConfigurationError, match="MEGAPHONE_API_TOKEN"):
        validate_pipeline_config(["en"], dry_run=False)


def test_publishing_enabled_missing_network_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _base_dev(monkeypatch, tmp_path)
    monkeypatch.setenv("MEGAPHONE_ENABLED", "true")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://cdn.staging.balvoi.com")
    monkeypatch.setenv("MEGAPHONE_API_TOKEN", "tok")
    monkeypatch.delenv("MEGAPHONE_NETWORK_ID", raising=False)
    monkeypatch.setenv("MEGAPHONE_PODCAST_ID_EN", "en-podcast")
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "x")
    monkeypatch.setenv("BALVOI_ALLOW_DEMO_ARTICLES", "true")
    monkeypatch.setattr("pipeline.lib.config_validation.shutil.which", lambda _n: "/bin/x")
    with pytest.raises(ConfigurationError, match="MEGAPHONE_NETWORK_ID"):
        validate_pipeline_config(["en"], dry_run=False)


def test_missing_per_language_podcast_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _base_dev(monkeypatch, tmp_path)
    monkeypatch.setenv("MEGAPHONE_ENABLED", "true")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://cdn.staging.balvoi.com")
    monkeypatch.setenv("MEGAPHONE_API_TOKEN", "tok")
    monkeypatch.setenv("MEGAPHONE_NETWORK_ID", "net")
    monkeypatch.delenv("MEGAPHONE_PODCAST_ID_EN", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "x")
    monkeypatch.setenv("BALVOI_ALLOW_DEMO_ARTICLES", "true")
    monkeypatch.setattr("pipeline.lib.config_validation.shutil.which", lambda _n: "/bin/x")
    with pytest.raises(ConfigurationError, match="MEGAPHONE_PODCAST_ID_EN"):
        validate_pipeline_config(["en"], dry_run=False)


def test_malformed_uuid_rejected_in_production(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _base_dev(monkeypatch, tmp_path)
    monkeypatch.setenv("BALVOI_ENV", "production")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://cdn.balvoi.com")
    monkeypatch.setenv("MEGAPHONE_NETWORK_ID", "not-a-uuid")
    with pytest.raises(ConfigurationError, match="UUID"):
        load_settings()


def test_malformed_url_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _base_dev(monkeypatch, tmp_path)
    monkeypatch.setenv("PUBLIC_BASE_URL", "not-a-url")
    with pytest.raises(ConfigurationError, match="absolute URL"):
        load_settings()


def test_placeholder_public_base_url_rejected_when_megaphone_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _base_dev(monkeypatch, tmp_path)
    monkeypatch.setenv("MEGAPHONE_ENABLED", "true")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://podcast.example.com")
    with pytest.raises(ConfigurationError, match="placeholder|example"):
        load_settings()


def test_localhost_public_base_rejected_in_production(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _base_dev(monkeypatch, tmp_path)
    monkeypatch.setenv("BALVOI_ENV", "production")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://127.0.0.1")
    monkeypatch.setenv("BALVOI_ALLOW_DEMO_ARTICLES", "false")
    with pytest.raises(ConfigurationError):
        load_settings()


def test_megaphone_rejects_trycloudflare_public_base(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _base_dev(monkeypatch, tmp_path)
    monkeypatch.setenv("MEGAPHONE_ENABLED", "true")
    monkeypatch.setenv(
        "PUBLIC_BASE_URL", "https://random-name.trycloudflare.com"
    )
    with pytest.raises(ConfigurationError, match=r"trycloudflare\.com.*permanent"):
        load_settings()


def test_megaphone_rejects_localhost_public_base(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _base_dev(monkeypatch, tmp_path)
    monkeypatch.setenv("MEGAPHONE_ENABLED", "true")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://localhost:8443")
    with pytest.raises(ConfigurationError, match=r"localhost.*permanent"):
        load_settings()


def test_megaphone_rejects_loopback_ip_public_base(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _base_dev(monkeypatch, tmp_path)
    monkeypatch.setenv("MEGAPHONE_ENABLED", "true")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://127.0.0.1/media")
    with pytest.raises(ConfigurationError, match=r"127\.0\.0\.1.*permanent"):
        load_settings()


def test_megaphone_rejects_rfc1918_public_base(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _base_dev(monkeypatch, tmp_path)
    monkeypatch.setenv("MEGAPHONE_ENABLED", "true")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://10.0.0.5")
    with pytest.raises(ConfigurationError, match=r"10\.0\.0\.5.*permanent"):
        load_settings()


def test_megaphone_accepts_public_https_base(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _base_dev(monkeypatch, tmp_path)
    monkeypatch.setenv("MEGAPHONE_ENABLED", "true")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://cdn.balvoi.com")
    cfg = load_settings()
    assert cfg.public_base_url == "https://cdn.balvoi.com"
    assert cfg.megaphone_enabled is True


def test_megaphone_requires_explicit_balvoi_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _base_dev(monkeypatch, tmp_path)
    monkeypatch.delenv("BALVOI_ENV", raising=False)
    monkeypatch.setenv("MEGAPHONE_ENABLED", "true")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://cdn.balvoi.com")
    with pytest.raises(ConfigurationError, match=r"BALVOI_ENV must be set explicitly.*demo-article"):
        load_settings()


def test_megaphone_allows_explicit_development_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _base_dev(monkeypatch, tmp_path)
    monkeypatch.setenv("BALVOI_ENV", "development")
    monkeypatch.setenv("MEGAPHONE_ENABLED", "true")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://cdn.balvoi.com")
    cfg = load_settings()
    assert cfg.runtime_env == "development"
    assert cfg.megaphone_enabled is True


def test_english_only_non_dry_does_not_require_openai(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _base_dev(monkeypatch, tmp_path)
    monkeypatch.setenv("BALVOI_ALLOW_DEMO_ARTICLES", "true")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "x")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("pipeline.lib.config_validation.shutil.which", lambda _n: "/bin/x")
    validate_pipeline_config(["en"], dry_run=False)


def test_non_english_non_dry_uses_balvoi_api_key_not_openai(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _base_dev(monkeypatch, tmp_path)
    monkeypatch.setenv("BALVOI_ALLOW_DEMO_ARTICLES", "true")
    monkeypatch.setenv("BALVOI_API_KEY", "ng-key")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "x")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("pipeline.lib.config_validation.shutil.which", lambda _n: "/bin/x")
    validate_pipeline_config(["es"], dry_run=False)


def test_conflicting_legacy_and_canonical_scheduler_rejected() -> None:
    with pytest.warns(DeprecationWarning), pytest.raises(ConfigurationError, match="conflicts"):
        app_settings.resolve_scheduler_enabled(
            {"SCHEDULER_ENABLED": "true", "CRON_ENABLED": "false"}
        )


def test_legacy_cron_alias_warns() -> None:
    with pytest.warns(DeprecationWarning):
        assert app_settings.resolve_scheduler_enabled({"CRON_ENABLED": "true"}) is True


def test_redacted_diagnostics_hide_secrets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _base_dev(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-value-should-not-appear")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "el-secret")
    monkeypatch.setenv("MEGAPHONE_API_TOKEN", "meg-secret")
    cfg = load_settings()
    diag = cfg.redacted_diagnostics()
    blob = str(diag)
    assert "sk-secret-value-should-not-appear" not in blob
    assert "el-secret" not in blob
    assert "meg-secret" not in blob
    assert diag["openai_api_key"] == "set"
    assert diag["elevenlabs_api_key"] == "set"
    assert diag["megaphone_api_token"] == "set"
    assert "sources" in diag
    assert isinstance(diag["sources"], dict)


def test_configuration_error_messages_do_not_embed_secret_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _base_dev(monkeypatch, tmp_path)
    monkeypatch.setenv("BALVOI_ENV", "production")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://cdn.balvoi.com")
    secret = "super-secret-token-value-xyz"
    monkeypatch.setenv("MEGAPHONE_API_TOKEN", secret)
    monkeypatch.setenv("MEGAPHONE_NETWORK_ID", "not-a-uuid")
    with pytest.raises(ConfigurationError) as err:
        load_settings()
    assert secret not in str(err.value)


def test_load_from_temporary_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _base_dev(monkeypatch, tmp_path)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "BALVOI_ENV=development\nLOG_LEVEL=DEBUG\nBALVOI_ARTICLE_WINDOW_MINUTES=60\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    monkeypatch.delenv("BALVOI_ENV", raising=False)
    monkeypatch.setenv("BALVOI_ARTICLE_WINDOW_MINUTES", "60")
    monkeypatch.setenv("MEGAPHONE_ENABLED", "false")
    load_app_dotenv(env_file)
    # process env still empty for LOG_LEVEL → .env fills it because override=False
    # and key was unset
    assert Path(env_file).is_file()
    # After load_dotenv, os.environ should have LOG_LEVEL from file
    import os

    assert os.environ.get("LOG_LEVEL") == "DEBUG"
    cfg = load_settings()
    assert cfg.log_level == "DEBUG"


def test_process_environment_overrides_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _base_dev(monkeypatch, tmp_path)
    env_file = tmp_path / ".env"
    env_file.write_text("LOG_LEVEL=DEBUG\nBALVOI_ARTICLE_WINDOW_MINUTES=60\n", encoding="utf-8")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    monkeypatch.setenv("BALVOI_ARTICLE_WINDOW_MINUTES", "60")
    monkeypatch.setenv("BALVOI_ENV", "development")
    monkeypatch.setenv("MEGAPHONE_ENABLED", "false")
    load_app_dotenv(env_file)
    import os

    assert os.environ.get("LOG_LEVEL") == "WARNING"
    cfg = load_settings()
    assert cfg.log_level == "WARNING"


def test_demo_articles_blocked_in_staging(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _base_dev(monkeypatch, tmp_path)
    monkeypatch.setenv("BALVOI_ENV", "staging")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://cdn.staging.balvoi.com")
    monkeypatch.setenv("BALVOI_ALLOW_DEMO_ARTICLES", "true")
    with pytest.raises(ConfigurationError, match="DEMO"):
        load_settings()


def test_dry_run_still_skips_secret_requirements(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _base_dev(monkeypatch, tmp_path)
    for name in ("BALVOI_API_KEY", "OPENAI_API_KEY", "ELEVENLABS_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    settings = validate_pipeline_config(["en", "es"], dry_run=True)
    assert settings["article_window_minutes"] == 60
