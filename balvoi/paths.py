"""Project root and storage paths (honors ``STORAGE_PATH``)."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"


def storage_root() -> Path:
    """Resolve the storage directory from ``STORAGE_PATH`` (default: ``storage``)."""
    configured = os.environ.get("STORAGE_PATH", "storage")
    path = Path(configured)
    if path.is_absolute():
        return path
    return ROOT / path


def pipeline_lock() -> Path:
    return storage_root() / ".pipeline.lock"
