"""Presigned playback links for session recordings (SPEC §12).

Credentials come from boto3's default chain: the EC2 instance role on the server (`s3:GetObject` on
`nudgeailab/recordings/*`, deploy/iam-policy-stage1.json), never an access key in config. Links last 5 minutes
and play inline; nothing here lists the bucket.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
import structlog
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.config import Settings
from app.utils.errors import ApiError

CONTENT_TYPE = "audio/ogg"
logger = structlog.get_logger()


def _client(settings: Settings) -> Any:
    # Not cached: moto patches botocore per test, and a client is cheap to make.
    # SigV4 against the bucket's regional endpoint: a link signed for the wrong region fails on playback.
    return boto3.client(
        "s3",
        region_name=settings.recordings_region,
        config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
    )


def is_expired(settings: Settings, started_at: datetime, now: datetime | None = None) -> bool:
    """The bucket's lifecycle rule deletes recordings after `recording_retention_days`."""
    now = now or datetime.now(UTC).replace(tzinfo=None)
    return started_at < now - timedelta(days=settings.recording_retention_days)


def playback_url(settings: Settings, session_id: str, key: str) -> tuple[str, datetime]:
    """A short-lived inline link to the recording. 404 if the file isn't there, 503 if S3 can't be reached."""
    client = _client(settings)
    try:
        client.head_object(Bucket=settings.recordings_bucket, Key=key)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code in ("404", "NoSuchKey", "NotFound"):
            raise ApiError(404, "recording_missing", "This session's recording isn't available.") from exc
        if code in ("403", "AccessDenied"):
            # The role has s3:GetObject but not s3:ListBucket (by design): S3 answers 403 for a missing file.
            # It can also mean the role lost access: the warning makes that visible in the logs.
            logger.warning(
                "recording_head_forbidden", key=key, hint="file missing, or no s3:GetObject access"
            )
            raise ApiError(404, "recording_missing", "This session's recording isn't available.") from exc
        raise ApiError(503, "recording_unavailable", "Recordings can't be reached right now.") from exc
    except BotoCoreError as exc:
        raise ApiError(503, "recording_unavailable", "Recordings can't be reached right now.") from exc
    url: str = client.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": settings.recordings_bucket,
            "Key": key,
            "ResponseContentType": CONTENT_TYPE,
            "ResponseContentDisposition": f'inline; filename="{session_id}.ogg"',
        },
        ExpiresIn=settings.recording_url_seconds,
    )
    expires_at = datetime.now(UTC) + timedelta(seconds=settings.recording_url_seconds)
    return url, expires_at
