"""Read-only checks before (and after) a deploy (SPEC §14.1). Changes nothing, prints no secrets.

    cd /srv/nudgelabapi && venv/bin/python scripts/preflight.py

Each line is PASS, WARN (works, but look at it) or FAIL (fix before going live). Exits 1 if anything failed.
Run by deploy/preflight-check.sh, which adds the server-level checks (DNS, certificate, nginx, ports).
"""

from __future__ import annotations

import asyncio
import os
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RESULTS: list[str] = []


def report(level: str, check: str, detail: str = "") -> None:
    RESULTS.append(level)
    print(f"{level:<5} {check}{': ' + detail if detail else ''}")


def check_env_file() -> None:
    env = ROOT / ".env"
    if not env.exists():
        report("FAIL", ".env", "missing (copy .env.example and fill it in)")
        return
    mode = stat.S_IMODE(env.stat().st_mode)
    if os.name != "nt" and mode & 0o077:
        report("FAIL", ".env permissions", f"{oct(mode)}; run: chmod 600 .env (SPEC §12)")
    else:
        report("PASS", ".env present", "mode 600" if os.name != "nt" else "")


def check_settings() -> object | None:
    try:
        from app.config import get_settings

        settings = get_settings()
    except Exception as exc:  # pydantic tells which variable is missing, never its value
        report("FAIL", "settings", f"{type(exc).__name__}: {str(exc).splitlines()[0]}")
        return None
    report("PASS" if settings.is_prod else "WARN", "APP_ENV", settings.app_env.value)
    report(
        "PASS" if len(settings.jwt_secret) >= 32 else "FAIL",
        "JWT_SECRET",
        f"{len(settings.jwt_secret)} characters",
    )
    origins = settings.cors_origin_list
    https_only = all(o.startswith("https://") for o in origins)
    report(
        "PASS" if origins and (https_only or not settings.is_prod) else "FAIL",
        "CORS_ORIGINS",
        ", ".join(origins),
    )
    report(
        "PASS" if settings.dashboard_base_url.startswith("https://") else "WARN",
        "DASHBOARD_BASE_URL",
        settings.dashboard_base_url,
    )
    if settings.database_migration_url and settings.database_migration_url == settings.database_url:
        report(
            "WARN",
            "DATABASE_MIGRATION_URL",
            "same login as DATABASE_URL; use the separate migrate login (SPEC §6.1)",
        )
    return settings


def check_database(settings: object) -> None:
    from sqlalchemy import create_engine, text

    url = settings.database_url  # type: ignore[attr-defined]
    engine = create_engine(url, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            report("PASS", "database (app login)", f"connected as {engine.url.username}@{engine.url.host}")
            for table in ("training_sessions", "session_transcripts", "vw_trainees", "vw_training_stores"):
                try:
                    conn.execute(text(f"SELECT 1 FROM `{table}` LIMIT 1"))  # noqa: S608 - fixed names
                    report("PASS", f"read {table}")
                except Exception as exc:
                    report("FAIL", f"read {table}", _short(exc))
            for table in ("dash_users", "dash_audit_log", "review_queue", "jobs", "saved_views"):
                try:
                    conn.execute(text(f"SELECT 1 FROM `{table}` LIMIT 1"))  # noqa: S608 - fixed names
                    report("PASS", f"API table {table}")
                except Exception as exc:
                    report(
                        "WARN",
                        f"API table {table}",
                        f"{_short(exc)} (normal before `alembic upgrade head` + grants)",
                    )
    except Exception as exc:
        report("FAIL", "database (app login)", _short(exc))
    finally:
        engine.dispose()


def check_migrations(settings: object) -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine, text

    url = settings.database_migration_url or settings.database_url  # type: ignore[attr-defined]
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    head = ScriptDirectory.from_config(cfg).get_current_head()
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            current = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except Exception as exc:
        report(
            "WARN",
            "migrations",
            f"can't read alembic_version ({_short(exc)}). If the database was never stamped, follow the "
            "baseline step in deploy/README.md; never run `alembic upgrade` from nothing on production.",
        )
        return
    finally:
        engine.dispose()
    if current == head:
        report("PASS", "migrations", f"at head ({head})")
    else:
        report(
            "WARN",
            "migrations",
            f"database at {current}, code at {head}: review, then `alembic upgrade head`",
        )


def check_recordings(settings: object) -> None:
    """HEAD on the newest recording: the instance role's s3:GetObject, without listing the bucket."""
    try:
        import boto3

        arn = boto3.client("sts").get_caller_identity()["Arn"]
        if ":assumed-role/" in arn:
            report("PASS", "AWS credentials", f"instance role {arn.split('/')[1]}")
        else:
            report(
                "WARN", "AWS credentials", f"{arn.split(':')[-1]}: not an instance role (SPEC §12: role only)"
            )
    except Exception as exc:
        report("FAIL", "AWS credentials", f"{_short(exc)} (attach Prime-nudgeapi-ec2-role / its policy)")
        return
    from app.services import recordings
    from sqlalchemy import create_engine, text

    engine = create_engine(settings.database_url)  # type: ignore[attr-defined]
    try:
        with engine.connect() as conn:
            key = conn.execute(
                text(
                    "SELECT recording_s3_key FROM training_sessions WHERE recording_s3_key IS NOT NULL "
                    "ORDER BY started_at DESC LIMIT 1"
                )
            ).scalar()
    finally:
        engine.dispose()
    if not key:
        report("WARN", "recordings", "no recorded session to test with yet")
        return
    try:
        recordings._client(settings).head_object(Bucket=settings.recordings_bucket, Key=key)  # type: ignore[attr-defined]
        report("PASS", "recordings", f"can read the newest recording in {settings.recordings_bucket}")  # type: ignore[attr-defined]
    except Exception as exc:
        report(
            "FAIL",
            "recordings",
            f"{_short(exc)}: the role can't read {key} (IAM policy deploy/iam-policy-stage1.json; a bucket "
            "policy if the bucket is in another account), or the file is missing",
        )


def check_livekit(settings: object) -> None:
    if not settings.livekit_configured:  # type: ignore[attr-defined]
        report("WARN", "LiveKit", "not set: the Overview's 'Live now' stays off")
        return
    from app.services import live

    try:
        rooms = asyncio.run(live.fetch_rooms(settings))  # type: ignore[arg-type]
        report("PASS", "LiveKit", f"{len(rooms)} room(s) live")
    except Exception as exc:
        report("FAIL", "LiveKit", _short(exc))


def check_email(settings: object) -> None:
    if not settings.graph_configured:  # type: ignore[attr-defined]
        report("WARN", "email (Graph)", "off: invitations and reset emails wait in the outbox")
        return
    from app.notifications import graph

    try:
        token = graph._access_token(settings)  # type: ignore[arg-type]
        report("PASS" if token else "FAIL", "email (Graph)", "token acquired" if token else "no token")
    except Exception as exc:
        report("FAIL", "email (Graph)", _short(exc))


def check_exports(settings: object) -> None:
    path = Path(settings.export_dir)  # type: ignore[attr-defined]
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".preflight"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        report("PASS", "EXPORT_DIR", f"{path.resolve()} is writable")
    except Exception as exc:
        report("FAIL", "EXPORT_DIR", f"{path}: {_short(exc)}")


def _short(exc: Exception) -> str:
    return f"{type(exc).__name__}: {str(exc).splitlines()[0][:160]}" if str(exc) else type(exc).__name__


def main() -> int:
    os.chdir(ROOT)
    check_env_file()
    settings = check_settings()
    if settings is not None:
        check_database(settings)
        check_migrations(settings)
        check_recordings(settings)
        check_livekit(settings)
        check_email(settings)
        check_exports(settings)
    failed = RESULTS.count("FAIL")
    print(f"\n{RESULTS.count('PASS')} passed, {RESULTS.count('WARN')} warnings, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
