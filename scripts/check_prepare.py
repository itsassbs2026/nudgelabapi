"""Phase 12 check after deploy: can this server store uploads and prepare a training? No DB writes.

    cd /srv/nudgelabapi && venv/bin/python scripts/check_prepare.py

1. S3: put, read, copy and delete one small object under <CONTENT_PREFIX>/check/ (the IAM policy's
   training-content/* permissions: deploy/iam-policy-stage2.json).
2. Bedrock: prepare a tiny made-up walkthrough with Claude (BEDROCK_PREP_MODEL), fact check included. Costs
   about a cent.
Prints PASS/FAIL per step; exits 1 if anything failed.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.studio import prepare, uploads  # noqa: E402
from app.studio.blank import blank_content  # noqa: E402

SOURCE = (
    "Sample policy for a system check. Keep every exit clear at all times. "
    "If a customer is hurt, help them first, then tell your manager the same day."
)


def main() -> int:
    settings = get_settings()
    failed = 0
    s3 = uploads.s3_client(settings)
    key = f"{settings.content_key_prefix}/check/{uuid.uuid4().hex}.txt"
    copy = key.replace("/check/", "/check/copy-")
    try:
        s3.put_object(Bucket=settings.content_bucket, Key=key, Body=b"check", ContentType="text/plain")
        assert s3.get_object(Bucket=settings.content_bucket, Key=key)["Body"].read() == b"check"
        s3.copy_object(
            Bucket=settings.content_bucket,
            Key=copy,
            CopySource={"Bucket": settings.content_bucket, "Key": key},
        )
        for k in (key, copy):
            s3.delete_object(Bucket=settings.content_bucket, Key=k)
        print(f"PASS  S3 put/get/copy/delete under {settings.content_key_prefix}/")
    except Exception as exc:
        failed += 1
        print(f"FAIL  S3 under {settings.content_key_prefix}/: {type(exc).__name__}: {str(exc)[:200]}")

    draft = blank_content(
        title="System check", completion_type="walkthrough", uses_location=False, trainer_name="Anne"
    )
    try:
        result = prepare.prepare(SOURCE, draft, settings)
        usage = result["usage"]["prepare"]
        model = f"{settings.bedrock_prep_model} ({settings.bedrock_region})"
        tokens = f"{usage.get('input_tokens')}+{usage.get('output_tokens')} tokens"
        print(f"PASS  Bedrock {model}: {result['topics']} topic(s), {len(result['flags'])} flag(s), {tokens}")
    except Exception as exc:
        failed += 1
        print(f"FAIL  Bedrock {settings.bedrock_prep_model}: {type(exc).__name__}: {str(exc)[:300]}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
