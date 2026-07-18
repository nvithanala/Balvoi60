#!/usr/bin/env python3
"""Read-only audit of published episode metadata and audio files."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from balvoi.paths import storage_root


def audit_history(history_path: Path | None = None) -> dict:
    root = storage_root()
    path = history_path or root / "manifests" / "history.json"
    history = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    id_counts = Counter(str(row.get("id") or "") for row in history)
    boundary_counts = Counter(
        (str(row.get("publicationBoundary") or ""), str(row.get("slug") or ""))
        for row in history
        if row.get("publicationBoundary") and row.get("slug")
    )
    entries = []
    for row in history:
        rel = str(row.get("audioUrl") or "").lstrip("/")
        audio = root / rel if rel else None
        exists = bool(audio and audio.exists())
        size = audio.stat().st_size if exists and audio else 0
        entries.append(
            {
                "id": row.get("id"),
                "slug": row.get("slug"),
                "publicationBoundary": row.get("publicationBoundary"),
                "audioUrl": row.get("audioUrl"),
                "exists": exists,
                "size": size,
                "issue": "missing" if not exists else ("zero_byte" if size == 0 else None),
            }
        )
    return {
        "historyPath": str(path),
        "totalEntries": len(entries),
        "missingAudio": sum(entry["issue"] == "missing" for entry in entries),
        "zeroByteAudio": sum(entry["issue"] == "zero_byte" for entry in entries),
        "duplicateIds": sorted(key for key, count in id_counts.items() if key and count > 1),
        "duplicateBoundaries": [
            {"publicationBoundary": boundary, "slug": slug}
            for (boundary, slug), count in sorted(boundary_counts.items())
            if count > 1
        ],
        "entries": entries,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", type=Path, help="Optional history.json path")
    parser.add_argument("--json", action="store_true", help="Print full JSON report")
    args = parser.parse_args()
    report = audit_history(args.history)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return
    print(f"History: {report['historyPath']}")
    print(f"Entries: {report['totalEntries']}")
    print(f"Missing audio: {report['missingAudio']}")
    print(f"Zero-byte audio: {report['zeroByteAudio']}")
    print(f"Duplicate IDs: {len(report['duplicateIds'])}")
    print(f"Duplicate boundary/language pairs: {len(report['duplicateBoundaries'])}")
    for entry in report["entries"]:
        if entry["issue"]:
            print(f"  {entry['issue']}: {entry['id']} ({entry['audioUrl']})")


if __name__ == "__main__":
    main()
