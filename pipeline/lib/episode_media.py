"""Prepare episode MP3 for public HTTPS delivery before Megaphone create.

Remote upload is optional: when ``BALVOI_EPISODE_S3_BUCKET`` is set, the file is
uploaded to ``episodes/{run_id}/{slug}.mp3``. Otherwise the canonical local
episode path is treated as the origin behind ``PUBLIC_BASE_URL`` (Flask or an
external sync). Megaphone still requires a reachable public HTTPS URL.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from pipeline.errors import PublishRejectedError
from pipeline.lib.logging_utils import log_event
from pipeline.lib.megaphone_client import (
    enabled as megaphone_enabled,
)
from pipeline.lib.megaphone_client import (
    production_public_audio_url,
    require_public_base_url,
    verify_media_file_url,
)
from pipeline.lib.storage_paths import get_storage_paths


def _optional_s3_upload(local_path: Path, *, object_key: str) -> str | None:
    """Upload to S3 when ``BALVOI_EPISODE_S3_BUCKET`` is set; else return None."""
    bucket = os.environ.get("BALVOI_EPISODE_S3_BUCKET", "").strip()
    if not bucket:
        return None
    try:
        import boto3  # type: ignore
    except ImportError as err:
        raise PublishRejectedError(
            "BALVOI_EPISODE_S3_BUCKET is set but boto3 is not installable"
        ) from err
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or None
    client = boto3.client("s3", region_name=region) if region else boto3.client("s3")
    extra: dict[str, str] = {"ContentType": "audio/mpeg"}
    client.upload_file(str(local_path), bucket, object_key, ExtraArgs=extra)
    return f"s3://{bucket}/{object_key}"


def prepare_public_episode_media(
    *,
    audio_path: Path,
    run_id: str,
    slug: str,
    require_reachable: bool | None = None,
) -> dict[str, Any]:
    """Ensure canonical MP3 location, build public URL, optionally upload + probe.

    Returns ``{localPath, publicUrl, objectKey, uploadedToS3, probe}``.
    Does **not** call Megaphone.
    """
    path = Path(audio_path)
    if not path.is_file() or path.stat().st_size <= 0:
        raise PublishRejectedError(f"Episode audio missing or empty: {path}")

    canonical = get_storage_paths().episode_mp3(run_id, slug)
    canonical.parent.mkdir(parents=True, exist_ok=True)
    if path.resolve() != canonical.resolve():
        shutil.copy2(path, canonical)

    object_key = f"episodes/{run_id}/{slug}.mp3"
    log_event(
        "Episode Media Prepare Started",
        stage="media_prepare",
        runId=run_id,
        slug=slug,
        path=str(canonical),
    )
    s3_uri = _optional_s3_upload(canonical, object_key=object_key)

    must_probe = (
        require_reachable if require_reachable is not None else megaphone_enabled()
    )
    if must_probe or megaphone_enabled():
        public_base = require_public_base_url()
        public_url = production_public_audio_url(
            public_base=public_base,
            run_id=run_id,
            slug=slug,
        )
    else:
        public_base = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
        public_url = (
            production_public_audio_url(
                public_base=public_base,
                run_id=run_id,
                slug=slug,
            )
            if public_base
            else f"/episodes/{run_id}/{slug}.mp3"
        )

    probe: dict[str, Any] | None = None
    if must_probe:
        if not public_url.startswith("https://"):
            raise PublishRejectedError(
                "Megaphone requires a public HTTPS media URL before create"
            )
        probe = verify_media_file_url(public_url)

    log_event(
        "Episode Media Prepare Completed",
        stage="media_prepare",
        runId=run_id,
        slug=slug,
        publicUrl=public_url,
        uploadedToS3=bool(s3_uri),
        s3Uri=s3_uri,
    )
    return {
        "localPath": str(canonical),
        "publicUrl": public_url,
        "objectKey": object_key,
        "uploadedToS3": bool(s3_uri),
        "s3Uri": s3_uri,
        "probe": probe,
    }
