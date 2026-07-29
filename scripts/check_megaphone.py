#!/usr/bin/env python3
"""Read-only Megaphone verification and network/podcast discovery.

Default checks use GET-only. ``--show-payload`` builds the production Create
Episode JSON locally and never sends any HTTP request.
Never creates, updates, publishes, or deletes.
Never prints API tokens.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import requests  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

from balvoi.paths import ROOT  # noqa: E402
from pipeline.config_loader import edition_by_slug  # noqa: E402
from pipeline.lib.megaphone_client import (  # noqa: E402
    ALL_SLUGS,
    create_as_draft,
    evaluate_run_ready_for_megaphone,
    fetch_podcast,
    resolve_megaphone_config,
)
from pipeline.lib.megaphone_discover import (  # noqa: E402
    authorization_header,
    discover_megaphone,
    format_discovery_report,
    redact_secrets,
)
from pipeline.lib.megaphone_episode_payload import (  # noqa: E402
    debug_endpoint_report,
    first_process_token,
    format_debug_endpoint_report,
)


def _check_slug(slug: str) -> tuple[bool, str]:
    token, network, podcast = resolve_megaphone_config(slug)
    if not token:
        return False, "missing MEGAPHONE_API_TOKEN (shared or per-slug)"
    if not network:
        return False, "missing MEGAPHONE_NETWORK_ID (shared or per-slug)"
    if not podcast:
        return False, f"missing MEGAPHONE_PODCAST_ID_{slug.upper()}"
    # Confirm header format without printing the token.
    header = authorization_header(token)
    if not header.startswith('Token token="') or not header.endswith('"'):
        return False, "Authorization header format is incorrect"
    try:
        payload = fetch_podcast(slug)
    except requests.HTTPError as err:
        status = err.response.status_code if err.response is not None else "?"
        detail = ""
        if err.response is not None:
            detail = redact_secrets((err.response.text or "")[:200], token)
        return False, f"HTTP {status} — {detail or type(err).__name__}"
    except Exception as err:  # noqa: BLE001
        return False, redact_secrets(f"{type(err).__name__}: {err}", token)
    title = str(payload.get("title") or payload.get("id") or "")
    return True, f"ok id={payload.get('id')} title={title!r}"


def _run_discover(*, language: str = "en") -> int:
    token, network, podcast = resolve_megaphone_config(language)
    if not token:
        print("Missing MEGAPHONE_API_TOKEN (or MEGAPHONE_API_TOKEN_EN)", file=sys.stderr)
        return 2
    print("Megaphone GET-only discovery")
    print('Authorization: Token token="***"')
    report = discover_megaphone(
        token=token,
        configured_network_id=network,
        configured_podcast_id=podcast,
    )
    output = format_discovery_report(report)
    if token in output:
        output = redact_secrets(output, token)
    print(output)
    # Non-zero when English mapping is not healthy.
    healthy = report.direct_podcast.get("ok") is True or (
        "configured English podcast is accessible" in report.diagnoses
    )
    return 0 if healthy else 1


def _mark(ok: bool) -> str:
    return "PASS" if ok else "FAIL"


def _run_show_payload(
    *,
    language: str,
    run_id: str,
    check_media_url: bool,
) -> int:
    """Build production Create Episode JSON from an existing run — no Megaphone HTTP."""
    slug = language.strip().lower() or "en"
    if slug not in ALL_SLUGS:
        print(f"Unknown language slug: {slug!r}", file=sys.stderr)
        return 2

    resolved_run_id = run_id.strip()
    if not resolved_run_id:
        print(
            "--show-payload requires --run-id <existing-run-id>. "
            "It will not invent a boundary or use an empty run.",
            file=sys.stderr,
        )
        return 2

    edition = edition_by_slug(slug)
    if not edition:
        print(f"Edition not found for slug={slug!r}", file=sys.stderr)
        return 2

    # Force-disabled for this diagnostic path (never publish).
    os.environ["MEGAPHONE_ENABLED"] = "false"

    report = evaluate_run_ready_for_megaphone(
        run_id=resolved_run_id,
        slug=slug,
        edition=edition,
        check_media_url=check_media_url,
    )
    payload = report.get("payload")

    print("=== Megaphone Create Episode payload preview (no Megaphone HTTP) ===")
    print(f"MEGAPHONE_ENABLED={os.environ.get('MEGAPHONE_ENABLED')}")
    print(f"MEGAPHONE_CREATE_AS_DRAFT → draft={create_as_draft()}")
    print(f"edition slug: {slug}")
    print(f"edition name: {edition.get('name')}")
    print(f"exact run used: {report.get('runId')}")
    print(f"publication boundary: {report.get('boundary') or '(unresolved)'}")
    print(f"stories source: {report.get('storiesSource')}")
    print(f"story count: {report.get('storyCount')}")
    print(f"local MP3 path: {report.get('mp3Path')}")
    print(f"local MP3 size: {report.get('mp3Size')}")
    print(f"PUBLIC_BASE_URL: {report.get('publicBaseUrl') or '(empty)'}")
    print(f"final public media URL: {report.get('mediaFileUrl') or '(empty)'}")
    if report.get("publicBaseIssues"):
        print("PUBLIC_BASE_URL errors:")
        for issue in report["publicBaseIssues"]:
            print(f"  - {issue}")
    print("")
    print("Validation:")
    for name, ok, detail in report.get("checks") or []:
        shown = detail if len(str(detail)) <= 160 else str(detail)[:157] + "..."
        print(f"  {name}: [{_mark(ok)}] {shown}")

    print("")
    print("Fields:")
    if payload:
        for key in (
            "title",
            "summary",
            "mediaFileUrl",
            "externalId",
            "pubdate",
            "episodeType",
            "explicit",
            "draft",
            "cleanTitle",
        ):
            if key in payload:
                print(f"  {key}: {payload.get(key)!r}")
        print("")
        print("Complete JSON payload:")
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print("  (payload not built — fix FAIL checks above)")

    media_probe = report.get("mediaProbe")
    if check_media_url and media_probe:
        print("")
        print("Media URL reachability (GET/HEAD only — not Megaphone):")
        print(f"  method: {media_probe.get('method')}")
        print(f"  HTTP status: {media_probe.get('status')}")
        print(f"  Content-Type: {media_probe.get('contentType')}")
        print(f"  Content-Length: {media_probe.get('contentLength')}")
        print(f"  redirects occurred: {media_probe.get('redirected')}")
        print(f"  final URL: {media_probe.get('finalUrl')}")
        if media_probe.get("error"):
            print(f"  error: {media_probe.get('error')}")

    ready = bool(report.get("ready"))
    print("")
    print(f"Payload ready for draft publish: {'YES' if ready else 'NO'}")
    print("Megaphone POST called: NO")
    print(f"megaphonePostCalled={report.get('megaphonePostCalled')}")
    return 0 if ready else 1


def main() -> int:
    env_path = (ROOT / ".env").resolve()
    _process_key, process_token_before_dotenv = first_process_token()
    load_dotenv(env_path, override=True)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--editions",
        default="en",
        help="Comma-separated edition slugs for direct show checks (default: en)",
    )
    parser.add_argument(
        "--discover",
        action="store_true",
        help="Enumerate accessible networks/podcasts (GET-only) and diagnose EN mapping",
    )
    parser.add_argument(
        "--debug-endpoints",
        action="store_true",
        help="Print GET/POST URLs, auth scheme, IDs, and payload keys only (no POST)",
    )
    parser.add_argument(
        "--language",
        default="en",
        help="Edition slug for --discover / --debug-endpoints / --show-payload",
    )
    parser.add_argument(
        "--show-payload",
        action="store_true",
        help=(
            "Build the exact Create Episode JSON publish_episode() would POST "
            "from an existing run (no Megaphone POST; optional media HEAD/GET only)"
        ),
    )
    parser.add_argument(
        "--run-id",
        default="",
        help="Required with --show-payload: existing completed run id",
    )
    parser.add_argument(
        "--check-media-url",
        action="store_true",
        help="With --show-payload: HEAD/GET the public mediaFileUrl (never Megaphone)",
    )
    args = parser.parse_args()

    if args.show_payload:
        return _run_show_payload(
            language=args.language.strip().lower() or "en",
            run_id=args.run_id,
            check_media_url=bool(args.check_media_url),
        )

    if args.debug_endpoints:
        language = args.language.strip().lower() or "en"
        report = debug_endpoint_report(
            slug=language,
            env_path=env_path,
            process_token_before_dotenv=process_token_before_dotenv,
            dotenv_override=True,
        )
        text = format_debug_endpoint_report(report)
        token, _network, _podcast = resolve_megaphone_config(language)
        if token and token in text:
            text = redact_secrets(text, token)
        print(text)
        print("\nProbing GET only (no POST)...")
        if not token or not _network or not _podcast:
            print("  GET podcast: skipped (incomplete Megaphone config)")
            print("  POST episodes: not called")
            return 2
        ok, message = _check_slug(language)
        print(f"  GET podcast: [{'OK' if ok else 'FAIL'}] {message}")
        print("  POST episodes: not called")
        return 0 if ok else 1

    if args.discover:
        return _run_discover(language=args.language.strip().lower() or "en")

    slugs = [s.strip() for s in args.editions.split(",") if s.strip()]
    if not slugs:
        print("No edition slugs to check", file=sys.stderr)
        return 2

    print(f"Megaphone read-only check ({len(slugs)} edition(s))")
    print(f"MEGAPHONE_ENABLED={os.environ.get('MEGAPHONE_ENABLED', '').strip() or 'unset'}")
    print('Authorization: Token token="***"')
    failures = 0
    for slug in slugs:
        ok, message = _check_slug(slug)
        status = "OK" if ok else "FAIL"
        print(f"  [{status}] {slug}: {message}")
        if not ok:
            failures += 1

    if failures:
        print(
            f"\n{failures} edition(s) failed. "
            "Run: python scripts/check_megaphone.py --discover"
        )
        return 1
    print("\nAll configured shows reachable.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
