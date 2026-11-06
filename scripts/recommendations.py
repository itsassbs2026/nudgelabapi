"""Content recommendations (app/recommendations): each training's last 7 days of calls, reviewed by Claude.

    cd /srv/nudgelabapi && venv/bin/python scripts/recommendations.py               tonight's run, once
    venv/bin/python scripts/recommendations.py --training benefits_know_it_choose_it_use_it --preview
                                        one training: print the evidence size and what Claude recommends;
                                        saves nothing
    venv/bin/python scripts/recommendations.py --email-preview /tmp/ours.html      tonight's email to a file

Run every night at 2:00 Chicago time by deploy/nudgelabapi-recommendations.timer. A training is looked at once
per night; a rerun only does the ones not done yet, and the email to the NudgeLab team goes out once.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.recommendations import evidence, run, writer  # noqa: E402
from app.reports.filters import build_filters  # noqa: E402


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--date", type=date.fromisoformat, help="the run date (default: today, Central)")
    parser.add_argument("--training", help="one training only (with --preview)")
    parser.add_argument("--preview", action="store_true", help="print what Claude recommends; save nothing")
    parser.add_argument(
        "--email-preview", type=Path, help="write the night's email to this file; send nothing"
    )
    args = parser.parse_args(argv)
    settings = get_settings()
    run_date = args.date or run.today(settings)
    with SessionLocal() as db:
        if args.email_preview:
            subject, html = run.build_email(db, settings, run_date)
            args.email_preview.write_text(html, encoding="utf-8")
            print(f"{subject} -> {args.email_preview}")
            return 0
        if args.preview:
            if not args.training:
                parser.error("--preview needs --training")
            days = run.window(settings, run_date)
            if days is None:
                print(f"No days to look at before {run_date} (recommendations_not_before).")
                return 0
            f = build_filters(
                timezone=settings.summary_timezone,
                date_from=days[0],
                date_to=days[1],
                training_id=args.training,
            )
            ev = evidence.gather(db, f)
            print(
                f"{args.training}: {ev['calls']} calls; "
                f"evidence {len(json.dumps(ev, default=str)):,} characters"
            )
            got, usage = writer.writer(settings, ev, [])
            recs, ours = writer.clean(got, ev)
            print(
                json.dumps(
                    {"usage": usage, "recommendations": recs, "ours": ours}, indent=2, ensure_ascii=False
                )
            )
            return 0
        print(json.dumps(run.run_all(db, settings, run_date)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
