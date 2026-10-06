"""The PortalLive reference-table sync (app/reference/sync.py): guards, atomic replace, logging, results.

PortalLive is faked: each test hands the sync a fetch function. The four target tables are the real local
ones (from the baseline migration); the sync commits for real, so every test empties them again afterwards.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest
from app.reference import sync
from sqlalchemy import Engine, text

TABLES = list(sync.SOURCES)


@pytest.fixture
def target(engine: Engine) -> Generator[Engine, None, None]:
    yield engine
    with engine.begin() as conn:
        for table in [*TABLES, "sync_run_log"]:
            conn.execute(text(f"DELETE FROM `{table}`"))


def columns_of(engine: Engine, table: str) -> list[tuple[str, str, int | None, str]]:
    with engine.connect() as conn:
        return [tuple(r) for r in conn.execute(text(
            "SELECT column_name, data_type, character_maximum_length, column_type"
            " FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = :t"
            " ORDER BY ordinal_position"), {"t": table})]  # fmt: skip


def fake_rows(engine: Engine, table: str, n: int, start: int = 1) -> tuple[list[str], list[tuple[Any, ...]]]:
    """n rows with a value in every column (unique ids, so the primary keys hold)."""
    cols = columns_of(engine, table)
    rows = []
    for i in range(start, start + n):
        row: list[Any] = []
        for _name, kind, length, column_type in cols:
            if kind in ("int", "tinyint", "smallint", "bigint", "decimal", "bit"):
                row.append(i % 2 if kind in ("tinyint", "bit") else i)
            elif kind == "enum":
                row.append(column_type.split("'")[1])
            elif kind in ("date", "datetime"):
                row.append("2026-10-06")
            else:
                row.append(f"{table[:3]}{i}"[: length or 10])
        rows.append(tuple(row))
    return [c[0] for c in cols], rows


def count(engine: Engine, table: str) -> int:
    with engine.connect() as conn:
        return int(conn.execute(text(f"SELECT COUNT(*) FROM `{table}`")).scalar_one())


def log(engine: Engine) -> list[Any]:
    with engine.connect() as conn:
        return list(conn.execute(text(
            "SELECT table_name, status, row_count, error_message"
            " FROM sync_run_log ORDER BY id")))  # fmt: skip


def fetcher(engine: Engine, sizes: dict[str, int], start: int = 1) -> sync.Fetch:
    by_sql = {sql: table for table, sql in sync.SOURCES.items()}

    def fetch(sql: str) -> tuple[list[str], list[tuple[Any, ...]]]:
        return fake_rows(engine, by_sql[sql], sizes[by_sql[sql]], start)

    return fetch


def test_refreshes_every_table_in_batches(target: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sync, "BATCH_ROWS", 2)  # 5 rows: three INSERT batches in one transaction
    results = sync.sync_reference_tables(target, fetcher(target, dict.fromkeys(TABLES, 5)))
    assert [(r.table, r.status, r.on_file, r.fetched) for r in results] == [
        (t, "success", 0, 5) for t in TABLES
    ]
    assert all(count(target, t) == 5 for t in TABLES)
    assert [(r.table_name, r.status, r.row_count) for r in log(target)] == [(t, "success", 5) for t in TABLES]

    # The next run replaces the rows (different ids), it doesn't add to them.
    sync.sync_reference_tables(target, fetcher(target, dict.fromkeys(TABLES, 6), start=100))
    assert all(count(target, t) == 6 for t in TABLES)


@pytest.mark.parametrize(
    ("fetched", "message"), [(0, "returned 0 rows"), (4, "under half of the 10 on file")]
)
def test_an_empty_or_shrunken_fetch_changes_nothing(target: Engine, fetched: int, message: str) -> None:
    sync.sync_reference_tables(target, fetcher(target, dict.fromkeys(TABLES, 10)), tables=["v_stores"])
    (result,) = sync.sync_reference_tables(
        target, fetcher(target, {"v_stores": fetched}), tables=["v_stores"]
    )
    assert (
        result.status == "failed" and message in (result.error or "") and "v_stores" in (result.error or "")
    )
    assert count(target, "v_stores") == 10
    assert log(target)[-1][:3] == ("v_stores", "failed", None)
    # Half exactly is fine, and growth always is.
    (half,) = sync.sync_reference_tables(target, fetcher(target, {"v_stores": 5}), tables=["v_stores"])
    assert half.status == "success" and count(target, "v_stores") == 5


def test_different_columns_change_nothing(target: Engine) -> None:
    sync.sync_reference_tables(target, fetcher(target, {"v_stores": 3}), tables=["v_stores"])

    def fetch(sql: str) -> tuple[list[str], list[tuple[Any, ...]]]:
        names, rows = fake_rows(target, "v_stores", 3)
        return [*names, "new_column"], [(*r, "x") for r in rows]

    (result,) = sync.sync_reference_tables(target, fetch, tables=["v_stores"])
    assert result.status == "failed" and "Only on PortalLive: ['new_column']" in (result.error or "")
    assert count(target, "v_stores") == 3


def test_a_failed_insert_rolls_back_to_the_old_rows(target: Engine) -> None:
    sync.sync_reference_tables(target, fetcher(target, {"v_users": 4}), tables=["v_users"])

    def duplicate_ids(sql: str) -> tuple[list[str], list[tuple[Any, ...]]]:
        names, rows = fake_rows(target, "v_users", 4, start=50)
        return names, [
            *rows,
            rows[0],
        ]  # the last row repeats a primary key: the INSERT fails after the DELETE

    (result,) = sync.sync_reference_tables(target, duplicate_ids, tables=["v_users"])
    assert result.status == "failed" and "IntegrityError" in (result.error or "")
    assert count(target, "v_users") == 4  # the DELETE was rolled back with it


def test_one_failing_table_never_stops_the_others(target: Engine) -> None:
    good = fetcher(target, dict.fromkeys(TABLES, 3))

    def fetch(sql: str) -> tuple[list[str], list[tuple[Any, ...]]]:
        if sql == sync.SOURCES["v_users"]:
            raise ConnectionError("PortalLive unreachable")
        return good(sql)

    results = sync.sync_reference_tables(target, fetch)
    assert {r.table: r.status for r in results} == {
        "v_users_all": "success",
        "v_users": "failed",
        "v_stores": "success",
        "v_stores_all": "success",
    }
    assert "ConnectionError: PortalLive unreachable" in (results[1].error or "")
    assert len(log(target)) == 4


def test_dry_run_writes_nothing(target: Engine) -> None:
    sync.sync_reference_tables(target, fetcher(target, {"v_stores": 2}), tables=["v_stores"])
    before = log(target)
    results = sync.sync_reference_tables(target, fetcher(target, dict.fromkeys(TABLES, 7)), dry_run=True)
    assert [(r.status, r.fetched) for r in results] == [("dry_run", 7)] * 4
    assert next(r for r in results if r.table == "v_stores").on_file == 2
    assert count(target, "v_stores") == 2 and log(target) == before


def test_a_view_is_never_replaced(target: Engine) -> None:
    with pytest.raises(sync.SyncRefused, match="vw_trainees is view"):
        sync._target_columns(target, "vw_trainees")


def test_the_command_fails_loudly_without_its_settings(capsys: pytest.CaptureFixture[str]) -> None:
    import importlib

    cli = importlib.import_module("scripts.sync_reference_tables")
    assert cli.main([]) == 1  # PORTALLIVE_DATABASE_URL and REFERENCE_SYNC_DATABASE_URL aren't set in tests
    assert "must both be set" in capsys.readouterr().out
