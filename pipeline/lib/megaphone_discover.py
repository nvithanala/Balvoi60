"""GET-only Megaphone network/podcast discovery and diagnosis helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import requests

from pipeline.lib.megaphone_client import BASE_URL

NETWORKS_COLLECTION = f"{BASE_URL}/networks"


def authorization_header(token: str) -> str:
    """Exact Megaphone Authorization header value (never log the token)."""
    return f'Token token="{token}"'


def auth_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": authorization_header(token),
        "Accept": "application/json",
    }


def redact_secrets(text: str, token: str) -> str:
    if not text:
        return ""
    if token and token in text:
        text = text.replace(token, "***")
    # Also redact Authorization header forms if echoed.
    text = text.replace(f'Token token="{token}"', 'Token token="***"')
    return text


def _as_rows(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("networks", "podcasts", "data", "items", "results"):
            rows = payload.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
        # Single object with id.
        if payload.get("id"):
            return [payload]
    return []


@dataclass
class GetResult:
    endpoint: str
    status_code: int | None
    ok: bool
    rows: list[dict] = field(default_factory=list)
    error: str = ""
    raw_preview: str = ""


def get_collection(
    endpoint: str,
    *,
    token: str,
    timeout: float = 45,
    session: requests.Session | None = None,
    params: dict | None = None,
) -> GetResult:
    """GET-only helper. Never mutates Megaphone resources."""
    headers = auth_headers(token)
    # Guard: header format must remain Token token="...".
    assert headers["Authorization"].startswith('Token token="')
    assert headers["Authorization"].endswith('"')
    http = session or requests
    try:
        response = http.get(endpoint, headers=headers, timeout=timeout, params=params)
    except requests.RequestException as err:
        return GetResult(
            endpoint=endpoint,
            status_code=None,
            ok=False,
            error=redact_secrets(f"{type(err).__name__}: {err}", token),
        )
    preview = redact_secrets((response.text or "")[:300], token)
    if response.status_code >= 400:
        return GetResult(
            endpoint=endpoint,
            status_code=response.status_code,
            ok=False,
            error=preview or f"HTTP {response.status_code}",
            raw_preview=preview,
        )
    try:
        payload = response.json()
    except ValueError:
        return GetResult(
            endpoint=endpoint,
            status_code=response.status_code,
            ok=False,
            error="Response was not JSON",
            raw_preview=preview,
        )
    return GetResult(
        endpoint=endpoint,
        status_code=response.status_code,
        ok=True,
        rows=_as_rows(payload),
        raw_preview=preview,
    )


@dataclass
class DiscoveredNetwork:
    id: str
    name: str


@dataclass
class DiscoveredPodcast:
    id: str
    title: str
    network_id: str


@dataclass
class DiscoveryReport:
    networks_endpoint: str
    networks_status: int | None
    networks_enumeration_ok: bool
    networks: list[DiscoveredNetwork] = field(default_factory=list)
    podcasts: list[DiscoveredPodcast] = field(default_factory=list)
    podcast_list_attempts: list[dict[str, Any]] = field(default_factory=list)
    direct_podcast: dict[str, Any] = field(default_factory=dict)
    configured_network_id: str = ""
    configured_podcast_id: str = ""
    diagnoses: list[str] = field(default_factory=list)
    log_lines: list[str] = field(default_factory=list)


def discover_megaphone(
    *,
    token: str,
    configured_network_id: str,
    configured_podcast_id: str,
    session: requests.Session | None = None,
    timeout: float = 45,
) -> DiscoveryReport:
    """Enumerate networks/podcasts with GET-only calls and diagnose EN mapping."""
    report = DiscoveryReport(
        networks_endpoint=NETWORKS_COLLECTION,
        networks_status=None,
        networks_enumeration_ok=False,
        configured_network_id=configured_network_id.strip(),
        configured_podcast_id=configured_podcast_id.strip(),
    )

    def log(line: str) -> None:
        report.log_lines.append(redact_secrets(line, token))

    log("Authorization header format: Token token=\"***\"")
    log(f"GET {NETWORKS_COLLECTION}")
    networks_result = get_collection(
        NETWORKS_COLLECTION, token=token, timeout=timeout, session=session
    )
    report.networks_status = networks_result.status_code
    if not networks_result.ok:
        log(
            f"Networks collection failed: endpoint={networks_result.endpoint} "
            f"status={networks_result.status_code} body={networks_result.error}"
        )
        report.diagnoses.append(
            "no networks accessible to this token"
            if networks_result.status_code in {401, 403}
            else (
                f"network enumeration not permitted: GET {networks_result.endpoint} "
                f"returned status {networks_result.status_code}"
            )
        )
    else:
        report.networks_enumeration_ok = True
        for row in networks_result.rows:
            nid = str(row.get("id") or "").strip()
            if not nid:
                continue
            name = str(row.get("name") or row.get("title") or nid)
            report.networks.append(DiscoveredNetwork(id=nid, name=name))
            log(f"network name={name!r} id={nid}")

        if not report.networks:
            report.diagnoses.append("no networks accessible to this token")
            log("Networks collection returned HTTP success but zero networks")

        for network in report.networks:
            podcasts_endpoint = f"{BASE_URL}/networks/{network.id}/podcasts"
            log(f"GET {podcasts_endpoint}")
            podcasts_result = get_collection(
                podcasts_endpoint,
                token=token,
                timeout=timeout,
                session=session,
                params={"per_page": 100},
            )
            report.podcast_list_attempts.append(
                {
                    "endpoint": podcasts_endpoint,
                    "networkId": network.id,
                    "status": podcasts_result.status_code,
                    "ok": podcasts_result.ok,
                    "error": podcasts_result.error,
                }
            )
            if not podcasts_result.ok:
                log(
                    f"Podcasts collection failed for network={network.id}: "
                    f"status={podcasts_result.status_code} body={podcasts_result.error}"
                )
                continue
            for row in podcasts_result.rows:
                pid = str(row.get("id") or "").strip()
                if not pid:
                    continue
                title = str(row.get("title") or row.get("name") or pid)
                parent = str(row.get("networkId") or network.id)
                report.podcasts.append(
                    DiscoveredPodcast(id=pid, title=title, network_id=parent)
                )
                log(
                    f"podcast title={title!r} id={pid} parent_network_id={parent}"
                )

    # Direct configured podcast fetch (same path used by production).
    if report.configured_network_id and report.configured_podcast_id:
        direct_endpoint = (
            f"{BASE_URL}/networks/{report.configured_network_id}"
            f"/podcasts/{report.configured_podcast_id}"
        )
        log(f"GET {direct_endpoint}")
        direct = get_collection(
            direct_endpoint, token=token, timeout=timeout, session=session
        )
        report.direct_podcast = {
            "endpoint": direct_endpoint,
            "status": direct.status_code,
            "ok": direct.ok,
            "error": direct.error,
            "title": (direct.rows[0].get("title") if direct.rows else None),
        }
        if not direct.ok:
            log(
                f"Direct English podcast fetch failed: status={direct.status_code} "
                f"body={direct.error}"
            )

    # Compare configured IDs to discovery results.
    configured_network = report.configured_network_id
    configured_podcast = report.configured_podcast_id
    accessible_network_ids = {n.id for n in report.networks}
    podcast_by_id = {p.id: p for p in report.podcasts}

    if report.networks_enumeration_ok:
        if configured_network and configured_network not in accessible_network_ids:
            report.diagnoses.append("configured network not accessible")
        if configured_podcast:
            found = podcast_by_id.get(configured_podcast)
            if found is None:
                if not any(
                    attempt.get("ok") for attempt in report.podcast_list_attempts
                ):
                    # Enumeration of podcasts failed; rely on direct fetch.
                    pass
                else:
                    report.diagnoses.append("configured podcast not found")
            elif configured_network and found.network_id != configured_network:
                report.diagnoses.append(
                    "podcast exists under a different accessible network"
                )
                log(
                    f"Configured podcast {configured_podcast} is under network "
                    f"{found.network_id}, not configured {configured_network}"
                )

    direct = report.direct_podcast
    if direct and direct.get("status") == 403:
        if configured_podcast in podcast_by_id:
            report.diagnoses.append("podcast exists but direct fetch is forbidden")
        elif report.networks_enumeration_ok:
            # We could list networks/podcasts, but this direct path is forbidden.
            if configured_network and configured_network not in accessible_network_ids:
                if "configured network not accessible" not in report.diagnoses:
                    report.diagnoses.append("configured network not accessible")
            report.diagnoses.append("podcast exists but direct fetch is forbidden")
        else:
            report.diagnoses.append(
                "direct English podcast fetch forbidden (HTTP 403) on configured network path"
            )

    if not report.diagnoses and direct.get("ok"):
        report.diagnoses.append("configured English podcast is accessible")
    elif not report.diagnoses and report.networks_enumeration_ok and not report.networks:
        report.diagnoses.append("no networks accessible to this token")

    # Deduplicate diagnoses while preserving order.
    seen: set[str] = set()
    unique: list[str] = []
    for item in report.diagnoses:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    report.diagnoses = unique
    return report


def format_discovery_report(report: DiscoveryReport) -> str:
    lines = list(report.log_lines)
    lines.append("")
    lines.append("=== Diagnosis ===")
    if not report.diagnoses:
        lines.append("- unable to form a conclusive diagnosis from GET responses")
    for item in report.diagnoses:
        lines.append(f"- {item}")
    lines.append("")
    lines.append(
        f"Configured network ID: {report.configured_network_id or '(empty)'}"
    )
    lines.append(
        f"Configured English podcast ID: {report.configured_podcast_id or '(empty)'}"
    )
    return "\n".join(lines)
