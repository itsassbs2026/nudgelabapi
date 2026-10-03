"""Background worker (SPEC §3.1): `python -m app.worker`, systemd unit `nudgelabapi-worker`, one instance.
The API's Uvicorn processes never run scheduled jobs, so nothing is sent twice."""

from __future__ import annotations

from collections.abc import Callable

import structlog
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import SessionLocal
from app.exports import service as exports
from app.services import email_sender, token_cleanup
from app.utils.logging import configure_logging

logger = structlog.get_logger(__name__)


def run_job(name: str, fn: Callable[[Session, Settings], object]) -> None:
    db = SessionLocal()
    try:
        result = fn(db, get_settings())
        logger.info("worker_job_ran", job=name, result=result)
    except Exception:
        db.rollback()
        logger.exception("worker_job_failed", job=name)
    finally:
        db.close()


def build_scheduler() -> BlockingScheduler:
    scheduler = BlockingScheduler(timezone="UTC")
    jobs: list[tuple[str, Callable[[Session, Settings], object], IntervalTrigger]] = [
        ("email_sender", email_sender.drain_email_outbox, IntervalTrigger(seconds=15)),
        ("token_cleanup", token_cleanup.purge_expired_tokens, IntervalTrigger(hours=6)),
        ("exports", exports.run_export_jobs, IntervalTrigger(seconds=5)),
        ("export_cleanup", exports.purge_old_exports, IntervalTrigger(hours=1)),
    ]
    for name, fn, trigger in jobs:
        scheduler.add_job(run_job, trigger, args=[name, fn], id=name, max_instances=1, coalesce=True)
    return scheduler


def main() -> None:
    configure_logging(get_settings().log_level)
    logger.info("worker_starting")
    try:
        build_scheduler().start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("worker_stopping")


if __name__ == "__main__":
    main()
