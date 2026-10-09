"""Send the daily summary email (app/summary): yesterday's training calls, to every active recipient.

    cd /srv/nudgelabapi && venv/bin/python scripts/daily_summary.py                 yesterday (Central), once
    venv/bin/python scripts/daily_summary.py --day 2026-10-08                       another day
    venv/bin/python scripts/daily_summary.py --preview /tmp/summary.html            write the email to a file;
                                                                                    sends nothing

Run every morning at 7:00 Chicago time by deploy/nudgelabapi-daily-summary.timer. A day is sent once:
running it again for the same day sends nothing (dash_summary_runs). The worker delivers the emails within
seconds.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.summary import send  # noqa: E402


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--day", type=date.fromisoformat, help="the day to report on (default: yesterday)")
    parser.add_argument("--preview", type=Path, help="write the email's HTML here and send nothing")
    args = parser.parse_args(argv)
    settings = get_settings()
    day = args.day or send.yesterday(settings)
    with SessionLocal() as db:
        if args.preview:
            subject, html, by_claude = send.build(db, settings, day)
            args.preview.write_text(html, encoding="utf-8")
            print(f"{subject} -> {args.preview} (highlights by {'AI' if by_claude else 'rules'})")
            return 0
        sent = send.send_daily(db, settings, day)
    if sent is None:
        print(f"{day}: already sent; nothing to do")
    else:
        print(f"{day}: queued for {sent} recipient(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
