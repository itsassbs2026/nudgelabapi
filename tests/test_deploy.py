"""The deploy files agree with the code: every table the API reads or writes is granted to its login."""

from __future__ import annotations

import re
from pathlib import Path

from app.models import API_TABLES
from app.reference.agent_tables import agent_metadata

DEPLOY = Path(__file__).resolve().parent.parent / "deploy"


def granted(sql_file: str) -> dict[str, set[str]]:
    sql = (DEPLOY / sql_file).read_text(encoding="utf-8")
    out: dict[str, set[str]] = {}
    for privileges, table in re.findall(
        r"^GRANT ([A-Z, ]+?) ON nudgeai\.(\w+)\s+TO 'nudgelab_api'@", sql, re.M
    ):
        out[table] = {p.strip() for p in privileges.split(",")}
    return out


def test_every_agent_table_the_api_reads_is_granted() -> None:
    reads = granted("db-logins.sql")
    missing = sorted(t for t in agent_metadata.tables if t not in reads)
    assert missing == [], f"add SELECT grants for {missing} to deploy/db-logins.sql"
    assert all(privs == {"SELECT"} for privs in reads.values()), "the agent's data is read-only for the API"


def test_every_api_table_is_granted() -> None:
    grants = granted("db-grants-api-tables.sql")
    assert set(grants) == set(API_TABLES), (
        f"deploy/db-grants-api-tables.sql vs models: {set(grants) ^ set(API_TABLES)}"
    )
    assert grants["dash_audit_log"] == {"SELECT", "INSERT"}  # append-only (SPEC §6.3)


def test_no_personal_tables_are_granted() -> None:
    reads = granted("db-logins.sql")
    assert not any(t.startswith("v_") for t in reads), (
        "v_users* / v_stores* stay off limits; use the vw_ views"
    )


def test_studio_writes_are_column_limited() -> None:
    """Phase 11: INSERT on two agent tables, UPDATE only on the columns the code changes, never DELETE."""
    from app.studio.service import TRAINING_UPDATABLE, VERSION_UPDATABLE

    sql = (DEPLOY / "db-grants-0006-studio.sql").read_text(encoding="utf-8")
    grants = re.findall(
        r"^GRANT (INSERT|UPDATE)(?: \(([^)]*)\))? ON nudgeai\.(\w+) TO 'nudgelab_api'@", sql, re.M
    )
    found = {(priv, table): {c.strip() for c in cols.split(",") if c.strip()} for priv, cols, table in grants}
    assert found == {
        ("INSERT", "trainings"): set(),
        ("UPDATE", "trainings"): set(TRAINING_UPDATABLE),
        ("INSERT", "training_versions"): set(),
        ("UPDATE", "training_versions"): set(VERSION_UPDATABLE),
    }
    assert "active_version_id" not in TRAINING_UPDATABLE  # publishing is Phase 16
    assert "status" not in VERSION_UPDATABLE
    assert "DELETE" not in sql.replace("No DELETE", "")
