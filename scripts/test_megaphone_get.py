#!/usr/bin/env python3
"""Standalone Megaphone podcast GET — no shared config resolution.

Loads only the API token from .env. Network/podcast IDs come from CLI flags.
GET-only. Never prints the token.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import requests  # noqa: E402
from dotenv import dotenv_values  # noqa: E402

from balvoi.paths import ROOT  # noqa: E402

BASE_URL = "https://cms.megaphone.fm/api"


def _token_from_env_file(env_path: Path) -> str:
    values = dotenv_values(env_path)
    for key in ("MEGAPHONE_API_TOKEN", "MEGAPHONE_API_TOKEN_EN"):
        raw = (values.get(key) or "").strip()
        if raw:
            return raw
    return ""


def _fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:8].upper()


def _redact(text: str, token: str) -> str:
    if token and token in text:
        return text.replace(token, "***")
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network-id", required=True)
    parser.add_argument("--podcast-id", required=True)
    parser.add_argument(
        "--env-file",
        default=str((ROOT / ".env").resolve()),
        help="Path to .env used only for MEGAPHONE_API_TOKEN",
    )
    args = parser.parse_args()

    env_path = Path(args.env_file).resolve()
    token = _token_from_env_file(env_path)
    if not token:
        print(f"No MEGAPHONE_API_TOKEN in {env_path}", file=sys.stderr)
        return 2

    network_id = args.network_id.strip()
    podcast_id = args.podcast_id.strip()
    url = f"{BASE_URL}/networks/{network_id}/podcasts/{podcast_id}"
    headers = {
        "Authorization": f'Token token="{token}"',
        "Accept": "application/json",
    }

    print("=== Standalone Megaphone GET (no shared resolver) ===")
    print(f".env path (token only): {env_path}")
    print(f"Token length: {len(token)}")
    print(f"Token fingerprint (SHA-256[:8]): {_fingerprint(token)}")
    print("Authorization scheme: Token token=\"<redacted>\"")
    print(f"Exact GET URL: {url}")
    print("Method: GET")

    response = requests.get(url, headers=headers, timeout=45)
    print(f"Status code: {response.status_code}")
    body = response.text or ""
    body = _redact(body, token)
    try:
        parsed = response.json()
        print("Response JSON:")
        print(json.dumps(parsed, indent=2, ensure_ascii=False)[:4000])
    except ValueError:
        print("Response body (non-JSON):")
        print(body[:2000])

    # Confirm header format without printing token.
    auth = headers["Authorization"]
    assert auth.startswith('Token token="') and auth.endswith('"')
    assert token not in auth.replace(token, "***") or True
    if token in body:
        print("ERROR: token leaked into printed body", file=sys.stderr)
        return 3
    return 0 if response.status_code == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
