"""Copies of four PortalLive (`primetwok`) tables in nudgeai, refreshed twice a day (2026-10-06).

`v_users_all`, `v_users`, `v_stores` and `v_stores_all` feed the views the agent, the reports and the app
read (`vw_trainees`, `vw_training_stores`, `vw_app_profile`). Nothing else writes them. Each run, per
table and each table on its own (one failing never stops the others):

1. Fetch the whole source into memory, before touching anything.
2. Refuse when the fetch is empty, or under half of what's on file (a broken source must never wipe good
   data); growth is always fine. Refuse when the source and target columns differ (by name; nothing is ever
   dropped or reordered silently).
3. Replace the rows in one transaction (DELETE, then INSERT by column name in batches): readers see the old
   rows or the new ones, never an empty table.
4. Write one `sync_run_log` row, success or failure, in its own commit.

Never raises: returns one result per table. Connections: a read-only PortalLive login
(PORTALLIVE_DATABASE_URL) and the `nudgelab_sync` login on nudgeai (REFERENCE_SYNC_DATABASE_URL), which can
only read and replace these four tables and write `sync_run_log`. The API's own login can't read them
(personal data).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine, text

SYNC_NAME = "portallive_reference"
# Target table -> the query that reads it on PortalLive. Adding a table is one line here (plus its DDL
# and grants).
SOURCES: dict[str, str] = {
    "v_users_all": "SELECT * FROM v_users_all",
    "v_users": "SELECT * FROM v_users",
    "v_stores": "SELECT * FROM v_stores",
    "v_stores_all": "SELECT * FROM v_stores_all",
}
MIN_KEPT = 0.5  # a fetch under half the rows on file is refused
BATCH_ROWS = 2000

Fetch = Callable[[str], tuple[list[str], list[tuple[Any, ...]]]]


@dataclass
class TableResult:
    table: str
    status: str  # success, failed, or dry_run
    on_file: int | None = None
    fetched: int | None = None
    error: str | None = None


class SyncRefused(Exception):
    """A refresh that would lose data or columns: nothing was changed."""


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def source_fetcher(source: Engine) -> Fetch:
    """Run a source query and return (column names, all rows)."""

    def fetch(sql: str) -> tuple[list[str], list[tuple[Any, ...]]]:
        with source.connect() as conn:
            result = conn.exec_driver_sql(sql)
            return list(result.keys()), [tuple(row) for row in result]

    return fetch


def _target_columns(target: Engine, table: str) -> list[str]:
    with target.connect() as conn:
        kind = conn.execute(
            text(
                "SELECT table_type FROM information_schema.tables"
                " WHERE table_schema = DATABASE() AND table_name = :t"
            ),
            {"t": table},
        ).scalar()
        if kind != "BASE TABLE":
            raise SyncRefused(
                f"{table} is {'missing' if kind is None else kind.lower()} in nudgeai, not a table."
            )
        rows = conn.execute(
            text("SELECT column_name FROM information_schema.columns WHERE table_schema = DATABASE() "
                 "AND table_name = :t ORDER BY ordinal_position"),
            {"t": table},
        )  # fmt: skip
        return [str(r[0]) for r in rows]


def _on_file(target: Engine, table: str) -> int:
    with target.connect() as conn:
        return int(conn.execute(text(f"SELECT COUNT(*) FROM `{table}`")).scalar_one())


def check_refresh(
    table: str, columns: Sequence[str], target_columns: Sequence[str], fetched: int, on_file: int
) -> None:
    """The guards: raise SyncRefused, naming the table and the numbers, if the refresh would lose anything."""
    missing = [c for c in columns if c not in target_columns]
    extra = [c for c in target_columns if c not in columns]
    if missing or extra:
        raise SyncRefused(
            f"{table}: columns differ. Only on PortalLive: {missing or 'none'}; "
            f"only in nudgeai: {extra or 'none'}."
        )
    if fetched == 0:
        raise SyncRefused(f"{table}: PortalLive returned 0 rows ({on_file} on file); nothing changed.")
    if on_file and fetched < on_file * MIN_KEPT:
        raise SyncRefused(
            f"{table}: PortalLive returned {fetched} rows, under half of the {on_file} on file; "
            "nothing changed."
        )


def _replace(target: Engine, table: str, columns: Sequence[str], rows: list[tuple[Any, ...]]) -> None:
    names = ", ".join(f"`{c}`" for c in columns)
    insert = f"INSERT INTO `{table}` ({names}) VALUES ({', '.join(['%s'] * len(columns))})"
    with target.begin() as conn:  # one transaction: commit at the end, roll back on any error
        conn.exec_driver_sql(f"DELETE FROM `{table}`")
        for start in range(0, len(rows), BATCH_ROWS):
            conn.exec_driver_sql(insert, rows[start : start + BATCH_ROWS])


def _log(target: Engine, result: TableResult, started: datetime) -> None:
    with target.begin() as conn:
        conn.execute(
            text("INSERT INTO sync_run_log (sync_name, table_name, started_at, finished_at, status, "
                 "row_count, "
                 "error_message) VALUES (:s, :t, :a, :b, :st, :n, :e)"),
            {"s": SYNC_NAME, "t": result.table, "a": started, "b": _now(), "st": result.status,
             "n": result.fetched if result.status == "success" else None, "e": result.error},
        )  # fmt: skip


def refresh_table(target: Engine, fetch: Fetch, table: str, *, dry_run: bool = False) -> TableResult:
    started = _now()
    result = TableResult(table, "failed")
    try:
        result.on_file = _on_file(target, table)
        columns, rows = fetch(SOURCES[table])
        result.fetched = len(rows)
        check_refresh(table, columns, _target_columns(target, table), len(rows), result.on_file)
        if dry_run:
            result.status = "dry_run"
            return result
        _replace(target, table, columns, rows)
        result.status = "success"
    except Exception as exc:  # noqa: BLE001 - one table's failure is reported, never raised
        result.error = (
            str(exc)[:2000] if isinstance(exc, SyncRefused) else f"{type(exc).__name__}: {str(exc)[:1800]}"
        )
    if not dry_run:
        try:
            _log(target, result, started)
        except Exception as exc:  # noqa: BLE001
            result.error = (
                result.error or ""
            ) + f" (and sync_run_log couldn't be written: {type(exc).__name__})"
            result.status = "failed"
    return result


def sync_reference_tables(
    target: Engine, fetch: Fetch, *, dry_run: bool = False, tables: Sequence[str] | None = None
) -> list[TableResult]:
    """Refresh every table (or `tables`), each on its own. Never raises."""
    return [refresh_table(target, fetch, table, dry_run=dry_run) for table in (tables or SOURCES)]
