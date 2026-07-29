"""Report articles whose bodies contain NewsGenie missing-sentence rewrite junk."""

from __future__ import annotations

import json
import re

from balvoi.paths import storage_root
from pipeline.lib.balvoi_api import _is_junk_chunk, _split_body_chunks, clean_article_body

SIGNATURES = re.compile(
    r"sentence to rewrite was not provided|"
    r"please provide the (?:input )?sentence|"
    r"i['']?m ready to rewrite|"
    r"please provide the sentence|"
    r"i['']?m ready to help rewrite",
    re.I,
)


def main() -> None:
    path = storage_root() / "articles" / "latest.json"
    print(f"storage_root={storage_root()}")
    print(f"articles={path} size={path.stat().st_size if path.exists() else 0}")
    if not path.is_file():
        print("No articles cache found.")
        return

    articles = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(articles, list):
        print(f"Unexpected articles payload type: {type(articles)}")
        return

    hits = []
    empty_after_clean = []
    for article in articles:
        if not isinstance(article, dict):
            continue
        raw = str(article.get("fullText") or article.get("body") or article.get("summary") or "")
        junk = [c for c in _split_body_chunks(raw) if _is_junk_chunk(c) or SIGNATURES.search(c)]
        cleaned = clean_article_body(raw)
        if junk or SIGNATURES.search(raw):
            m = SIGNATURES.search(raw)
            snippet = ""
            if m:
                snippet = " ".join(raw[max(0, m.start() - 30) : m.end() + 90].split())
            hits.append(
                {
                    "id": article.get("id"),
                    "title": article.get("title"),
                    "junk_chunks": len(junk),
                    "raw_len": len(raw),
                    "cleaned_len": len(cleaned),
                    "snippet": snippet[:220],
                }
            )
        if raw.strip() and not cleaned.strip():
            empty_after_clean.append(
                {"id": article.get("id"), "title": article.get("title")}
            )

    print(f"total articles: {len(articles)}")
    print(f"missing-sentence / rewrite-junk hits: {len(hits)}")
    for row in hits:
        print("---")
        print(f"id: {row['id']}")
        print(f"title: {row['title']}")
        print(f"junk_chunks: {row['junk_chunks']} raw_len={row['raw_len']} cleaned_len={row['cleaned_len']}")
        print(f"snippet: {row['snippet']}")
    print()
    print(f"empty after clean_article_body: {len(empty_after_clean)}")
    for row in empty_after_clean[:20]:
        print(f"  {row['id']}: {row['title']}")


if __name__ == "__main__":
    main()
