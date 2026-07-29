#!/usr/bin/env python3
"""One-shot English BalVoi:60 production run with optional Megaphone publish."""

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

load_dotenv(ROOT / ".env", override=True)

from pipeline.lib.megaphone_once import run_english_once  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run exactly one English BalVoi:60 production pipeline execution. "
            "Does not start the scheduler and does not process other languages."
        )
    )
    parser.add_argument(
        "--language",
        default="en",
        help="Must be 'en' for this once-run helper",
    )
    parser.add_argument(
        "--boundary-utc",
        default="",
        help="Publication boundary ISO-8601 UTC (e.g. 2026-07-22T19:00:00Z)",
    )
    parser.add_argument(
        "--run-id",
        default="",
        help="Optional run id (default: boundary timestamp)",
    )
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="Validate configuration and Megaphone English show; do not generate or publish",
    )
    parser.add_argument(
        "--confirm-live-publish",
        action="store_true",
        help="Required to create a real Megaphone episode after public URL verification",
    )
    args = parser.parse_args()

    if args.language.strip().lower() != "en":
        print("Only --language en is supported for scripts/run_megaphone_once.py", file=sys.stderr)
        return 2
    if args.preflight_only and args.confirm_live_publish:
        print("Use either --preflight-only or --confirm-live-publish, not both", file=sys.stderr)
        return 2

    boundary = parse_iso_datetime(args.boundary_utc) if args.boundary_utc.strip() else None
    if args.boundary_utc.strip() and boundary is None:
        print(f"Invalid --boundary-utc: {args.boundary_utc}", file=sys.stderr)
        return 2

    code, report = run_english_once(
        new_run=True,
        boundary=boundary,
        run_id=args.run_id.strip() or None,
        confirm_live_publish=args.confirm_live_publish,
        preflight_only=args.preflight_only,
    )
    report_path = ROOT / "storage" / "runs" / str(report.get("runId")) / "report.json"
    # Prefer configured STORAGE_PATH via report writer path already used inside run.
    print(f"Report written under storage/runs/{report.get('runId')}/report.json")
    if report_path.exists():
        print(f"Local path: {report_path}")
    return code


if __name__ == "__main__":
    sys.exit(main())
