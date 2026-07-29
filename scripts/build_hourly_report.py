#!/usr/bin/env python3
"""Rebuild hourly review report JSON from existing status/manifest files."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv  # noqa: E402

from balvoi.dates import parse_iso_datetime  # noqa: E402
from balvoi.paths import ROOT  # noqa: E402
from pipeline.lib.hourly_report import write_hourly_report  # noqa: E402
from pipeline.lib.megaphone_client import ALL_SLUGS  # noqa: E402
from pipeline.lib.publication_identity import canonical_run_id  # noqa: E402


def main() -> int:
    load_dotenv(ROOT / ".env", override=False)
    parser = argparse.ArgumentParser(description="Build hourly publish review report.json")
    parser.add_argument(
        "--boundary",
        required=True,
        help="Publication boundary ISO-8601 UTC, e.g. 2026-07-28T21:00:00Z",
    )
    parser.add_argument(
        "--editions",
        default=",".join(ALL_SLUGS),
        help="Comma-separated edition slugs (default: all eight)",
    )
    args = parser.parse_args()
    boundary = parse_iso_datetime(args.boundary)
    if boundary is None:
        print("Invalid --boundary", file=sys.stderr)
        return 2
    slugs = [s.strip() for s in args.editions.split(",") if s.strip()]
    run_id = canonical_run_id(boundary)
    path = write_hourly_report(run_id=run_id, boundary=boundary, edition_slugs=slugs)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
