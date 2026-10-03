"""Before `alembic stamp 0001_baseline` on production: does the database match the baseline? Read-only.

    cd /srv/nudgelabapi && venv/bin/python scripts/check_baseline.py

Stamping records "the agent's tables are already here, as in 0001_baseline" without checking, so this compares
production's tables, views and columns (information_schema, with the migrate login) against the baseline's
DDL, and confirms none of the API's own tables exist yet. Prints names only, never data. Exits 1 if stamping
would be wrong.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_COLUMN = re.compile(r"^\s+`(\w+)`", re.M)


def baseline() -> tuple[dict[str, set[str]], set[str]]:
    path = next((ROOT / "alembic" / "versions").glob("0001_*.py"))
    spec = importlib.util.spec_from_file_location("baseline", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    tables = {name: set(_COLUMN.findall(ddl.split(") ENGINE")[0])) for name, ddl in module.TABLES}
    views = {name for name, _ in module.VIEWS}
    return tables, views


def main() -> int:
    from app.config import get_settings
    from app.models import API_TABLES
    from sqlalchemy import create_engine, text

    settings = get_settings()
    url = settings.database_migration_url or settings.database_url
    tables, views = baseline()
    engine = create_engine(url)
    with engine.connect() as conn:
        schema = conn.execute(text("SELECT DATABASE()")).scalar()
        rows = conn.execute(
            text(
                "SELECT TABLE_NAME, TABLE_TYPE FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE()"
            )
        ).all()
        present = {name: kind for name, kind in rows}
        columns: dict[str, set[str]] = {}
        for table, column in conn.execute(
            text(
                "SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.COLUMNS"
                " WHERE TABLE_SCHEMA = DATABASE()"
            )
        ):
            columns.setdefault(table, set()).add(column)
    engine.dispose()

    problems = 0
    print(f"Database: {schema}  ({len(present)} tables and views visible to this login)\n")

    missing_tables = sorted(t for t in tables if present.get(t) != "BASE TABLE")
    missing_views = sorted(v for v in views if present.get(v) != "VIEW")
    for name in missing_tables:
        print(f"MISSING table  {name}")
    for name in missing_views:
        print(f"MISSING view   {name}")
    problems += len(missing_tables) + len(missing_views)

    for name, expected in sorted(tables.items()):
        if name in missing_tables:
            continue
        lacking = sorted(expected - columns.get(name, set()))
        extra = sorted(columns.get(name, set()) - expected)
        if lacking:
            print(f"MISSING column {name}: {', '.join(lacking)}")
            problems += 1
        if extra:
            print(f"extra column   {name}: {', '.join(extra)}  (fine: added after the baseline was captured)")

    already = sorted(t for t in API_TABLES if t in present)
    for name in already:
        print(f"ALREADY EXISTS {name}  (an API table: the migration would fail; ask before going on)")
    problems += len(already)

    if "alembic_version" in present:
        print("NOTE           alembic_version exists: run `venv/bin/alembic current` instead of stamping")
        problems += 1

    print()
    if problems:
        print(f"Don't stamp yet: {problems} problem(s) above. Send this output.")
        return 1
    print(
        f"OK to stamp: all {len(tables)} baseline tables and {len(views)} views are here with their columns, "
        "and none of the API's tables exist yet."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
