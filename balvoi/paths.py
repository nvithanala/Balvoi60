"""Project root and storage paths (honors ``STORAGE_PATH``).

T-M1-004: ``storage_root`` remains the compatibility entry point; canonical
typed builders live in ``pipeline.lib.storage_paths``.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"


def storage_root() -> Path:
    """Resolve the storage directory from ``STORAGE_PATH`` (default: ``storage``)."""
    from pipeline.lib.storage_paths import resolve_storage_root

    return resolve_storage_root()


def pipeline_lock() -> Path:
    from pipeline.lib.storage_paths import get_storage_paths

    return get_storage_paths().pipeline_lock_path
