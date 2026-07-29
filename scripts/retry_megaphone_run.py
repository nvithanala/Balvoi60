#!/usr/bin/env python3
"""Retry an English Megaphone once-run by run id (English only)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv  # noqa: E402

from balvoi.paths import ROOT  # noqa: E402

load_dotenv(ROOT / ".env", override=True)

from pipeline.lib.megaphone_once import retry_english_run  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Retry one English Megaphone once-run without touching other languages"
    )
    parser.add_argument("--run-id", required=True, help="Existing run id under storage/runs/")
    parser.add_argument("--language", default="en")
    parser.add_argument(
        "--confirm-live-publish",
        action="store_true",
        help="Required to create/finalize a real Megaphone episode",
    )
    args = parser.parse_args()
    if args.language.strip().lower() != "en":
        print("Only --language en is supported", file=sys.stderr)
        return 2
    try:
        code, report = retry_english_run(
            run_id=args.run_id.strip(),
            confirm_live_publish=args.confirm_live_publish,
        )
    except Exception as err:  # noqa: BLE001
        print(f"Retry failed: {type(err).__name__}: {err}", file=sys.stderr)
        return 1
    print(f"Report: storage/runs/{report.get('runId') or args.run_id}/report.json")
    return code


if __name__ == "__main__":
    sys.exit(main())
