"""Images for a topic (2026-10-24): a trainer attaches images to a topic, and the app (and the web tester
page) shows them in the session while the trainer teaches it. Images only; video is a later step.

What the API does with an upload, so nothing risky or oversized reaches a trainee's phone:
- checks it is really a JPEG, PNG, WebP or GIF by its content (never by its file name), and not absurdly large
  in pixels;
- turns a phone photo the right way up, then **strips every piece of metadata** (location, camera, dates);
- shrinks it to at most `media_max_edge` pixels on its long side and re-encodes it to about
  `media_target_bytes` (PNG stays PNG when it has transparency and is small enough, the rest becomes JPEG);
- stores it in S3 under `<prefix>/media/<training id>/<image id>.<ext>`. Published versions keep pointing at
  the same key, so an image never changes under a version, and a new draft just keeps the same images.

The agent signs short-lived links to these objects when it shows them (agent repo media.py); the Studio signs
its own for the thumbnails (`urls`).
"""

from __future__ import annotations

import io
import uuid
from dataclasses import dataclass
from typing import Any

import structlog
from botocore.exceptions import BotoCoreError, ClientError
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy.orm import Session

from app.auth.deps import CurrentUser
from app.config import Settings
from app.studio.uploads import s3_client
from app.utils.errors import ApiError

logger = structlog.get_logger(__name__)

MAX_PIXELS = 40_000_000  # width x height: a 40-megapixel photo is fine, a decompression bomb is not
_FORMATS = {"JPEG", "PNG", "WEBP", "GIF"}
JPEG_QUALITIES = (85, 75, 65, 55, 45)


@dataclass(frozen=True)
class Processed:
    data: bytes
    ext: str
    content_type: str
    width: int
    height: int


def _bad(message: str) -> ApiError:
    return ApiError(422, "bad_image", message)


def _jpeg(img: Image.Image, quality: int) -> bytes:
    out = io.BytesIO()
    img.save(out, "JPEG", quality=quality, optimize=True, progressive=True)
    return out.getvalue()


def process(data: bytes, max_edge: int, max_bytes: int) -> Processed:
    """The image as it will be stored. Raises ApiError 422 `bad_image` with a message the trainer can use."""
    try:
        img: Image.Image = Image.open(io.BytesIO(data))
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise _bad("That file isn't an image we can use. Use a JPEG, PNG, WebP or GIF.") from exc
    if img.format not in _FORMATS:
        raise _bad("That image type isn't supported. Use a JPEG, PNG, WebP or GIF.")
    if img.width * img.height > MAX_PIXELS:
        raise _bad("That image is too large (over 40 megapixels). Shrink it and try again.")
    try:
        img.load()  # an animated GIF or WebP: its first frame
        img = ImageOps.exif_transpose(
            img
        )  # the phone's rotation, applied for real (the metadata is dropped below)
    except (OSError, ValueError, SyntaxError) as exc:
        raise _bad("That image file is damaged.") from exc
    if max(img.size) > max_edge:
        img.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)

    has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
    if has_alpha:
        rgba = img.convert("RGBA")
        out = io.BytesIO()
        rgba.save(out, "PNG", optimize=True)  # no metadata is copied
        if out.tell() <= max_bytes:
            return Processed(out.getvalue(), "png", "image/png", rgba.width, rgba.height)
        flat = Image.new("RGB", rgba.size, (255, 255, 255))
        flat.paste(rgba, mask=rgba.getchannel("A"))
        rgb = flat
    else:
        rgb = img.convert("RGB")

    for _ in range(6):  # still too big after the lowest quality: make it smaller and try again
        for quality in JPEG_QUALITIES:
            encoded = _jpeg(rgb, quality)
            if len(encoded) <= max_bytes:
                return Processed(encoded, "jpg", "image/jpeg", rgb.width, rgb.height)
        rgb = rgb.resize(
            (max(1, int(rgb.width * 0.8)), max(1, int(rgb.height * 0.8))), Image.Resampling.LANCZOS
        )
    raise _bad("That image can't be made small enough. Try a simpler or smaller picture.")


def _key(settings: Settings, training_id: str, media_id: str, ext: str) -> str:
    return f"{settings.content_key_prefix}/media/{training_id}/{media_id}.{ext}"


def upload(
    db: Session, settings: Settings, current: CurrentUser, version_id: int, data: bytes
) -> dict[str, Any]:
    """Stores one image for a draft. Returns what the editor puts on the topic (id, key, size) and a link to
    show it. The trainer adds the caption and the description (alt text) in the editor."""
    from app.studio import service  # circular at import time

    row = service._version(db, version_id)
    if row.status != service.DRAFT:
        raise ApiError(409, "not_a_draft", "Only a draft can be edited. Make a new draft from this version.")
    if not data:
        raise _bad("No image was sent.")
    if len(data) > settings.media_max_upload_bytes:
        raise ApiError(
            413, "image_too_large", f"Images can be up to {settings.media_max_upload_bytes // 1_000_000} MB."
        )
    image = process(data, settings.media_max_edge, settings.media_target_bytes)
    media_id = f"img_{uuid.uuid4().hex[:12]}"
    key = _key(settings, row.training_id, media_id, image.ext)
    try:
        s3_client(settings).put_object(
            Bucket=settings.content_bucket,
            Key=key,
            Body=image.data,
            ContentType=image.content_type,
            CacheControl="private, max-age=31536000, immutable",
        )
    except (BotoCoreError, ClientError) as exc:
        logger.warning("media_put_failed", error=type(exc).__name__)
        raise ApiError(
            503, "media_unavailable", "Images can't be saved right now. Try again in a minute."
        ) from exc
    logger.info(
        "media_uploaded",
        version_id=version_id,
        media_id=media_id,
        bytes=len(image.data),
        user_id=current.user.id,
    )
    return {
        "id": media_id,
        "key": key,
        "width": image.width,
        "height": image.height,
        "bytes": len(image.data),
        "url": sign(settings, key),
    }


def sign(settings: Settings, key: str) -> str:
    return str(
        s3_client(settings).generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.content_bucket, "Key": key},
            ExpiresIn=settings.media_url_seconds,
        )
    )


def keys_in(content: dict[str, Any]) -> dict[str, str]:
    """{image id: S3 key} for every image on every topic of a content document."""
    out: dict[str, str] = {}
    for topic in (content.get("knowledge_base") or {}).get("topics") or []:
        for item in topic.get("media") or []:
            if item.get("id") and item.get("key"):
                out[str(item["id"])] = str(item["key"])
    return out


def urls(db: Session, settings: Settings, version_id: int) -> dict[str, str]:
    """Short-lived links to show a version's images in the editor: {image id: url}."""
    import json

    from app.studio import service

    row = service._version(db, version_id)
    content = row.content
    if isinstance(content, str | bytes):
        content = json.loads(content)
    prefix = f"{settings.content_key_prefix}/media/{row.training_id}/"
    return {mid: sign(settings, key) for mid, key in keys_in(content or {}).items() if key.startswith(prefix)}
