"""Creates the first Admin from BOOTSTRAP_ADMIN_EMAIL / BOOTSTRAP_ADMIN_PASSWORD (SPEC §9).

Refuses if any Admin already exists. The password is temporary: it must be changed at first sign-in.
Remove BOOTSTRAP_ADMIN_PASSWORD from .env afterwards.

Usage: python -m scripts.bootstrap_admin
"""

from __future__ import annotations

import sys

from app.auth.passwords import hash_password, password_policy_violation
from app.config import Settings, get_settings
from app.models.dashboard import DashRole, DashUser
from sqlalchemy import select
from sqlalchemy.orm import Session


def bootstrap(db: Session, settings: Settings) -> DashUser:
    if not settings.bootstrap_admin_email or not settings.bootstrap_admin_password:
        raise SystemExit("BOOTSTRAP_ADMIN_EMAIL and BOOTSTRAP_ADMIN_PASSWORD must be set.")
    violation = password_policy_violation(settings.bootstrap_admin_password)
    if violation:
        raise SystemExit(f"BOOTSTRAP_ADMIN_PASSWORD: {violation}")
    existing = db.execute(select(DashUser).filter_by(role=DashRole.ADMIN.value)).scalars().first()
    if existing is not None:
        raise SystemExit(f"Refusing to run: an Admin already exists ({existing.email}).")
    admin = DashUser(
        email=settings.bootstrap_admin_email.strip().lower(),
        full_name=settings.bootstrap_admin_full_name,
        password_hash=hash_password(settings.bootstrap_admin_password),
        role=DashRole.ADMIN.value,
        is_active=True,
        timezone=settings.default_timezone,
        must_change_password=True,
        preferences={},
    )
    db.add(admin)
    db.commit()
    return admin


def main() -> None:
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        admin = bootstrap(db, get_settings())
        print(f"First Admin created: {admin.email} (must change the password at first sign-in).")
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1) from None
    finally:
        db.close()


if __name__ == "__main__":
    main()
