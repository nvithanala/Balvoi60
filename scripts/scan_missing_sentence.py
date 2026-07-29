"""Scan local storage for NewsGenie 'missing sentence' rewrite-junk signatures."""

from __future__ import annotations

import json
import re

from balvoi.paths import storage_root
from pipeline.lib.balvoi_api import _is_junk_chunk, _split_body_chunks

SIGNATURES = re.compile(
    r"sentence to rewrite was not provided|"
    r"please provide the (?:input )?sentence|"
    r"i['']?m ready to rewrite|"
    r"please provide the sentence",
    re.I,
)


def main() -> None:
    root = storage_root()
    hits: list[tuple[str, str]] = []
    for path in list(root.rglob("*.json")) + list(root.rglob("*.txt")):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        match = SIGNATURES.search(text)
        if not match:
            continue
        start = max(0, match.start() - 40)
        snippet = " ".join(text[start : match.end() + 80].split())
        hits.append((str(path.relative_to(root)), snippet[:220]))

    print(f"files with missing-sentence signatures: {len(hits)}")
    for rel, snippet in hits[:40]:
        print(f"- {rel}")
        print(f"  {snippet}")
        print()

    empty = 0
    total = 0
    junk_chunks = 0
    sel_dir = root / "manifests" / "selection"
    if sel_dir.is_dir():
        for path in sel_dir.glob("*.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            items = list(data.get("selectedArticles") or [])
            for edition in (data.get("editions") or {}).values():
                if isinstance(edition, dict):
                    items.extend(edition.get("stories") or [])
            for article in items:
                total += 1
                body = str(
                    article.get("fullText")
                    or article.get("body")
                    or article.get("summary")
                    or article.get("broadcastScript")
                    or ""
                )
                if not body.strip():
                    empty += 1
                junk = [c for c in _split_body_chunks(body) if _is_junk_chunk(c)]
                junk_chunks += len(junk)

    print(f"selection story bodies scanned: {total}")
    print(f"empty bodies: {empty}")
    print(f"junk rewrite chunks in selections: {junk_chunks}")


if __name__ == "__main__":
    main()
