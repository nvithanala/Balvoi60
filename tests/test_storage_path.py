from __future__ import annotations

import os
from pathlib import Path

from balvoi import paths


def test_storage_root_default() -> None:
    assert paths.storage_root() == paths.ROOT / "storage"


def test_storage_root_relative_env() -> None:
    os.environ["STORAGE_PATH"] = "custom-storage"
    assert paths.storage_root() == paths.ROOT / "custom-storage"


def test_storage_root_absolute_env(tmp_path: Path) -> None:
    os.environ["STORAGE_PATH"] = str(tmp_path)
    assert paths.storage_root() == tmp_path


def test_pipeline_lock_under_storage_root() -> None:
    os.environ["STORAGE_PATH"] = "alt"
    assert paths.pipeline_lock() == paths.ROOT / "alt" / ".pipeline.lock"
