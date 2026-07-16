"""Random variant selection with run-seeded reproducibility."""

from __future__ import annotations

import hashlib
import random
from typing import TypeVar

T = TypeVar("T")


def pick_variant(items: list[T], run_id: str, key: str) -> tuple[int, T]:
    if not items:
        raise ValueError(f"No items to pick for {key}")
    seed = int(hashlib.sha256(f"{run_id}:{key}".encode()).hexdigest(), 16) % (2**32)
    rng = random.Random(seed)
    idx = rng.randrange(len(items))
    return idx, items[idx]
