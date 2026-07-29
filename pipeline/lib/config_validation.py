"""Centralized, operation-aware configuration validation.

T-M1-002: delegates typed parsing to ``pipeline.lib.settings`` while preserving
the ``validate_pipeline_config(edition_slugs, dry_run=...) -> dict`` contract.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Mapping
from pathlib import Path

from balvoi.paths import storage_root
from pipeline.errors import ConfigurationError
from pipeline.lib import settings as app_settings


def scheduler_enabled(environ: dict[str, str] | None = None) -> bool:
    """Resolve the canonical scheduler flag, warning on the legacy name."""
    env: Mapping[str, str] = environ if environ is not None else os.environ
    return app_settings.resolve_scheduler_enabled(env)


def positive_int(name: str, default: int) -> int:
    return app_settings.parse_int(name, os.environ.get(name), default=default, minimum=1)


def nonnegative_int(name: str, default: int) -> int:
    return app_settings.parse_int(name, os.environ.get(name), default=default, minimum=0)


def _validate_writable_storage(root: Path) -> None:
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as err:
        raise ConfigurationError(f"STORAGE_PATH must be writable for publication: {err}") from err


def validate_pipeline_config(edition_slugs: list[str], *, dry_run: bool) -> dict[str, int]:
    """Validate only the capabilities required by this pipeline invocation.

    Returns the legacy worker-settings dict for call-site compatibility.
    """
    loaded = app_settings.load_settings(os.environ)
    # Feature-flag style: still call scheduler resolution for conflict detection.
    scheduler_enabled()
    app_settings.validate_settings_for_pipeline(loaded, edition_slugs, dry_run=dry_run)

    if not dry_run:
        for executable in ("ffmpeg", "ffprobe"):
            configured = loaded.ffmpeg_path
            if configured:
                # Optional explicit binary directory/file; existence checked loosely.
                exe_path = Path(configured)
                if exe_path.is_dir():
                    candidate = exe_path / executable
                    if not candidate.exists() and shutil.which(executable) is None:
                        raise ConfigurationError(
                            f"{executable} executable is required for audio merge"
                        )
                elif not exe_path.exists() and shutil.which(executable) is None:
                    raise ConfigurationError(
                        f"{executable} executable is required for audio merge"
                    )
            elif shutil.which(executable) is None:
                raise ConfigurationError(f"{executable} executable is required for audio merge")
        _validate_writable_storage(storage_root())

    return loaded.pipeline_worker_settings()
