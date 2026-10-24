"""Images for a topic (2026-10-24): checking and cleaning an upload, storing it, and the publish checks."""

from __future__ import annotations

import io
import json
import random
from collections.abc import Iterator
from typing import Any

import boto3
import pytest
from app.schemas.training_content import TrainingContent
from app.studio import media
from app.studio.validate import validate
from fastapi.testclient import TestClient
from moto import mock_aws
from PIL import Image
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.test_studio import big4_content

BUCKET = "nudgeailab"
MAX_EDGE = 1600
MAX_BYTES = 1_500_000


def photo(width: int, height: int, fmt: str = "JPEG", **save: Any) -> bytes:
    """A picture with noise, so it has real size."""
    rng = random.Random(1)
    img = Image.new("RGB", (width, height))
    img.putdata([(rng.randrange(256), rng.randrange(256), rng.randrange(256)) for _ in range(width * height)])
    out = io.BytesIO()
    img.save(out, fmt, **save)
    return out.getvalue()


def flat(width: int, height: int, mode: str = "RGB", color: Any = (10, 120, 200), fmt: str = "PNG") -> bytes:
    out = io.BytesIO()
    Image.new(mode, (width, height), color).save(out, fmt)
    return out.getvalue()


# -- processing ----------------------------------------------------------------------------------------


def test_a_big_photo_is_shrunk_and_made_small() -> None:
    result = media.process(photo(2400, 1800, quality=95), MAX_EDGE, MAX_BYTES)
    assert result.ext == "jpg" and result.content_type == "image/jpeg"
    assert max(result.width, result.height) <= MAX_EDGE and (result.width, result.height) == (1600, 1200)
    assert len(result.data) <= MAX_BYTES
    assert Image.open(io.BytesIO(result.data)).size == (result.width, result.height)


def test_a_small_image_is_not_enlarged() -> None:
    result = media.process(flat(400, 300), MAX_EDGE, MAX_BYTES)
    assert (result.width, result.height) == (400, 300)


def test_metadata_is_stripped_and_the_phone_rotation_applied() -> None:
    img = Image.new("RGB", (300, 200), (200, 30, 30))
    exif = Image.Exif()
    exif[0x0112] = 6  # rotated 90 degrees
    exif[0x010F] = "Acme Phone"  # camera make
    out = io.BytesIO()
    img.save(out, "JPEG", exif=exif.tobytes())
    result = media.process(out.getvalue(), MAX_EDGE, MAX_BYTES)
    assert (result.width, result.height) == (200, 300)  # turned the right way up
    assert dict(Image.open(io.BytesIO(result.data)).getexif()) == {}  # nothing left to leak


def test_transparency_keeps_a_png_when_it_is_small() -> None:
    result = media.process(flat(200, 200, "RGBA", (0, 0, 0, 0)), MAX_EDGE, MAX_BYTES)
    assert result.ext == "png" and Image.open(io.BytesIO(result.data)).mode == "RGBA"


def test_a_png_too_big_becomes_a_jpeg_on_white() -> None:
    rng = random.Random(2)
    img = Image.new("RGBA", (1200, 1200))
    img.putdata(
        [
            (rng.randrange(256), rng.randrange(256), rng.randrange(256), 255 if rng.random() > 0.01 else 0)
            for _ in range(1200 * 1200)
        ]
    )
    out = io.BytesIO()
    img.save(out, "PNG")
    result = media.process(out.getvalue(), MAX_EDGE, 400_000)
    assert result.ext == "jpg" and len(result.data) <= 400_000


def test_a_gif_and_a_webp_are_accepted() -> None:
    assert media.process(flat(100, 80, "P", 3, "GIF"), MAX_EDGE, MAX_BYTES).ext in ("png", "jpg")
    assert media.process(flat(100, 80, "RGB", (1, 2, 3), "WEBP"), MAX_EDGE, MAX_BYTES).ext in ("png", "jpg")


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"not an image at all",
        b"GIF89a" + b"\x00" * 20,
        b"%PDF-1.4 hello",
        b"<svg xmlns='http://www.w3.org/2000/svg'/>",
    ],
)
def test_things_that_are_not_images_are_refused(data: bytes) -> None:
    with pytest.raises(media.ApiError) as e:
        media.process(data, MAX_EDGE, MAX_BYTES)
    assert e.value.code == "bad_image"


def test_an_image_with_too_many_pixels_is_refused_without_loading_it() -> None:
    huge = flat(7000, 7000)  # a flat colour: tiny file, 49 million pixels
    with pytest.raises(media.ApiError) as e:
        media.process(huge, MAX_EDGE, MAX_BYTES)
    assert e.value.code == "bad_image" and "too large" in e.value.message


# -- the endpoints -------------------------------------------------------------------------------------


@pytest.fixture
def s3(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.setenv(name, "testing")
    with mock_aws():
        client = boto3.client("s3", region_name="us-west-1")
        client.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "us-west-1"})
        yield client


@pytest.fixture
def draft(client: TestClient, trainer_headers: dict[str, str]) -> int:
    r = client.post(
        "/api/v1/trainings",
        headers=trainer_headers,
        json={"training_id": "store_safety", "title": "Store Safety", "completion_type": "walkthrough"},
    )
    assert r.status_code == 201, r.text
    versions = client.get("/api/v1/trainings/store_safety", headers=trainer_headers).json()["versions"]
    return int(versions[0]["version_id"])


def post_image(client: TestClient, headers: dict[str, str], version_id: int, data: bytes) -> Any:
    return client.post(
        f"/api/v1/versions/{version_id}/media",
        headers={**headers, "content-type": "application/octet-stream"},
        content=data,
    )


def test_a_trainer_uploads_an_image_to_a_draft(
    client: TestClient, trainer_headers: dict[str, str], draft: int, s3: Any
) -> None:
    r = post_image(client, trainer_headers, draft, photo(2000, 1000, quality=90))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["id"].startswith("img_") and (body["width"], body["height"]) == (1600, 800)
    assert (
        body["key"].startswith("training-content/")
        and "/media/store_safety/" in body["key"]
        and body["key"].endswith(".jpg")
    )
    stored = s3.get_object(Bucket=BUCKET, Key=body["key"])
    assert stored["ContentType"] == "image/jpeg" and stored["ContentLength"] == body["bytes"] <= MAX_BYTES
    assert (
        "X-Amz-Signature" in body["url"] and body["key"] in body["url"]
    )  # a signed link to show it in the editor


def test_it_needs_a_real_image_a_draft_and_a_sign_in(
    client: TestClient, trainer_headers: dict[str, str], draft: int, s3: Any, db_session: Session
) -> None:
    assert post_image(client, trainer_headers, draft, b"hello").json()["error"]["code"] == "bad_image"
    assert post_image(client, trainer_headers, draft, b"").status_code == 422
    assert client.post(f"/api/v1/versions/{draft}/media", content=photo(50, 50)).status_code == 401
    assert post_image(client, trainer_headers, 999999, photo(50, 50)).status_code == 404
    db_session.execute(
        text("UPDATE training_versions SET status = 'published' WHERE version_id = :v"), {"v": draft}
    )
    r = post_image(client, trainer_headers, draft, photo(50, 50))
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_a_draft"
    assert s3.list_objects_v2(Bucket=BUCKET).get("KeyCount", 0) == 0  # nothing was stored for any of those


def test_a_file_over_the_upload_limit_is_refused(
    client: TestClient, trainer_headers: dict[str, str], draft: int, s3: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "media_max_upload_bytes", 1000)
    r = post_image(client, trainer_headers, draft, photo(200, 200, quality=95))
    assert r.status_code == 413 and r.json()["error"]["code"] == "image_too_large"


def test_media_urls_sign_the_images_of_a_version(
    client: TestClient, trainer_headers: dict[str, str], draft: int, s3: Any, db_session: Session
) -> None:
    up = post_image(client, trainer_headers, draft, photo(300, 200)).json()
    content = big4_content()
    content["knowledge_base"]["topics"][0]["media"] = [
        {"id": up["id"], "key": up["key"], "width": up["width"], "height": up["height"], "alt": "A schedule"},
        {
            "id": "img_000000000000",
            "key": "training-content/prod/media/OTHER_TRAINING/x.jpg",
            "width": 1,
            "height": 1,
            "alt": "x",
        },
    ]
    db_session.execute(
        text("UPDATE training_versions SET content = :c WHERE version_id = :v"),
        {"c": json.dumps(content), "v": draft},
    )
    urls = client.get(f"/api/v1/versions/{draft}/media-urls", headers=trainer_headers).json()
    assert set(urls) == {up["id"]}  # only this training's own images are signed, never another's key
    assert "X-Amz-Signature" in urls[up["id"]]
    assert client.get(f"/api/v1/versions/{draft}/media-urls").status_code == 401


# -- the content format and the publish checks ---------------------------------------------------------


def topic_media(**over: Any) -> list[dict[str, Any]]:
    item = {
        "id": "img_0123456789ab",
        "key": "training-content/prod/media/t/a.jpg",
        "width": 800,
        "height": 600,
        "alt": "The schedule with the Fiber column highlighted",
    }
    return [{**item, **over}]


def test_content_accepts_images_on_a_topic_and_nothing_unknown() -> None:
    content = big4_content()
    content["knowledge_base"]["topics"][0]["media"] = topic_media(caption="Where to look")
    parsed = TrainingContent.model_validate(content)
    assert parsed.knowledge_base.topics[0].media[0].caption == "Where to look"  # type: ignore[index]
    content["knowledge_base"]["topics"][0]["media"] = topic_media(colour="red")
    with pytest.raises(ValidationError):
        TrainingContent.model_validate(content)
    content["knowledge_base"]["topics"][0]["media"] = topic_media(id="nope")
    with pytest.raises(ValidationError):
        TrainingContent.model_validate(content)


def checks(media_items: list[dict[str, Any]], topic: int = 0) -> list[str]:
    content = big4_content()
    content["knowledge_base"]["topics"][topic]["media"] = media_items
    return [
        f"{e['where']}: {e['message']}"
        for e in validate(content, completion_key="agent_x")["errors"]
        if "image" in e["where"] or "mage" in e["message"]
    ]


def test_publish_checks_for_images() -> None:
    assert checks(topic_media()) == []
    assert any("Describe what the image shows" in m for m in checks(topic_media(alt="  ")))
    assert any("caption is over 140" in m for m in checks(topic_media(caption="x" * 141)))
    seven = [{**topic_media()[0], "id": f"img_{n:012x}"} for n in range(7)]
    assert any("At most 6 images" in m for m in checks(seven))
    twice = topic_media() + topic_media()
    assert any("attached twice" in m for m in checks(twice))
