"""Copy v_users_all, v_users, v_stores and v_stores_all from PortalLive into nudgeai (app/reference/sync.py).

    cd /srv/nudgelabapi && venv/bin/python scripts/sync_reference_tables.py     refresh all four
    venv/bin/python scripts/sync_reference_tables.py --dry-run                  counts only, writes nothing
    venv/bin/python scripts/sync_reference_tables.py --table v_stores           one table

Run twice a day by deploy/nudgelabapi-reference-sync.timer (08:00 and 23:00 Chicago). Exits 1 if any table
failed, and emails the failures to REFERENCE_SYNC_ALERT_EMAIL. Never prints a connection string or password.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.reference.sync import SOURCES, TableResult, source_fetcher, sync_reference_tables  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402


def alert(settings: object, failed: list[TableResult]) -> str:
    """Email the failures; returns what happened, for the log."""
    to = getattr(settings, "reference_sync_alert_email", None)
    if not to or not getattr(settings, "graph_configured", False):
        return "no alert email sent (REFERENCE_SYNC_ALERT_EMAIL or Graph not set)"
    from app.notifications.graph import send_email

    rows = "".join(f"<li><b>{r.table}</b>: {r.error}</li>" for r in failed)
    try:
        send_email(
            settings,
            to,
            f"NudgeLab: reference-table sync failed ({len(failed)} table(s))",  # type: ignore[arg-type]
            f"<p>The PortalLive reference-table sync failed for:</p><ul>{rows}</ul>"
            "<p>The tables keep their previous data. See sync_run_log and deploy/README.md.</p>",
        )
        return f"alert emailed to {to}"
    except Exception as exc:  # noqa: BLE001
        return f"alert email failed: {type(exc).__name__}"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="show counts on file and on PortalLive; write nothing"
    )
    parser.add_argument(
        "--table", action="append", choices=sorted(SOURCES), help="only this table (repeatable)"
    )
    args = parser.parse_args(argv)
    settings = get_settings()
    if not settings.portallive_database_url or not settings.reference_sync_database_url:
        print("FAILED: PORTALLIVE_DATABASE_URL and REFERENCE_SYNC_DATABASE_URL must both be set in .env")
        return 1
    source = create_engine(settings.portallive_database_url, pool_pre_ping=True, future=True)
    target = create_engine(settings.reference_sync_database_url, pool_pre_ping=True, future=True)
    try:
        results = sync_reference_tables(
            target, source_fetcher(source), dry_run=args.dry_run, tables=args.table
        )
    finally:
        source.dispose()
        target.dispose()
    for r in results:
        on_file = "?" if r.on_file is None else r.on_file
        counts = f"on file {on_file}, PortalLive {'?' if r.fetched is None else r.fetched}"
        print(f"{r.status.upper():<8} {r.table:<13} {counts}" + (f"  {r.error}" if r.error else ""))
    failed = [r for r in results if r.status == "failed"]
    if failed and not args.dry_run:
        print(alert(settings, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
