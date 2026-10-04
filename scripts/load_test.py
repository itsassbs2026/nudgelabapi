"""Load test of the report endpoints (SPEC Phase 17): GET requests only, nothing is written.

    venv/bin/python scripts/load_test.py --email bgupta@primecomms.com [--base http://127.0.0.1:8002]
        [--users 5] [--seconds 60] [--days 90]

Run on the API server (it reads the server's .env) or locally against a load database (scripts/load_seed.py).
It signs a 15-minute access token for an existing dashboard user with the server's own JWT secret, so no
password is needed or typed; the token never leaves this process. Each simulated user calls the report pages
a trainer opens (overview, trainings, a training's detail and questions, drill-down, cost, sessions, quality,
feedback, compliance, filters) in turn, as fast as the API answers, for `--seconds`. Prints per-endpoint
counts, errors and response times (p50 / p95 / max).
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def token_for(email: str) -> str:
    from app.auth.jwt import create_access_token
    from app.config import get_settings
    from app.db import SessionLocal
    from app.models.dashboard import DashUser
    from sqlalchemy import select

    with SessionLocal() as db:
        active = DashUser.is_active.is_(True)
        user = db.scalars(select(DashUser).where(DashUser.email == email, active)).first()
        if user is None:
            raise SystemExit(f"No active dashboard user {email}.")
        token, _ = create_access_token(user, get_settings())
    return token


def endpoints(days: int, training: str) -> list[tuple[str, str]]:
    end = date.today()
    q = f"date_from={end - timedelta(days=days)}&date_to={end}"
    return [
        ("overview", f"/api/v1/reports/overview?{q}"),
        ("trainings", f"/api/v1/reports/trainings?{q}"),
        ("training detail", f"/api/v1/reports/trainings/{training}?{q}"),
        ("questions", f"/api/v1/reports/trainings/{training}/questions?{q}"),
        ("drill-down", f"/api/v1/reports/drilldown?{q}&level=region"),
        ("cost", f"/api/v1/reports/cost?{q}"),
        ("rating trend", f"/api/v1/reports/rating-trend?{q}"),
        ("sessions", f"/api/v1/sessions?{q}&page_size=50"),
        ("quality", f"/api/v1/quality?{q}&page_size=50"),
        ("feedback", f"/api/v1/feedback?{q}&page_size=50"),
        ("compliance", f"/api/v1/assignments?{q}&page_size=50"),
        ("filter options", "/api/v1/reports/filter-options"),
    ]


async def user_loop(client, token: str, plan: list[tuple[str, str]], until: float, times, errors) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    i = 0
    while time.monotonic() < until:
        name, path = plan[i % len(plan)]
        i += 1
        started = time.monotonic()
        try:
            r = await client.get(path, headers=headers)
            ok = r.status_code == 200
        except Exception:  # noqa: BLE001 - a timeout or refused connection counts as an error
            ok = False
        times[name].append(time.monotonic() - started)
        if not ok:
            errors[name] += 1


async def run(base: str, token: str, users: int, seconds: int, days: int, training: str) -> None:
    import httpx

    plan = endpoints(days, training)
    times: dict[str, list[float]] = defaultdict(list)
    errors: dict[str, int] = defaultdict(int)
    async with httpx.AsyncClient(base_url=base, timeout=30) as client:
        # One warm-up pass, not counted.
        warm: dict[str, list[float]] = defaultdict(list)
        await user_loop(client, token, plan, time.monotonic() + 0.01, warm, defaultdict(int))
        until = time.monotonic() + seconds
        # Users start a little apart and at different pages, like people opening the dashboard.
        tasks = []
        for u in range(users):
            rotated = plan[u % len(plan) :] + plan[: u % len(plan)]
            tasks.append(user_loop(client, token, rotated, until, times, errors))
        await asyncio.gather(*tasks)

    def ms(v: float) -> str:
        return f"{v * 1000:7.0f}"

    total = sum(len(v) for v in times.values())
    print(f"\n{users} users, {seconds} s, last {days} days: {total} requests ({total / seconds:.1f}/s)\n")
    print(f"{'endpoint':16} {'count':>6} {'errors':>6} {'p50 ms':>7} {'p95 ms':>7} {'max ms':>7}")
    for name, _ in plan:
        v = sorted(times[name])
        if not v:
            continue
        p95 = v[min(len(v) - 1, int(len(v) * 0.95))]
        print(f"{name:16} {len(v):6} {errors[name]:6} {ms(statistics.median(v))} {ms(p95)} {ms(v[-1])}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--email", required=True, help="an active dashboard user (Admins see every report)")
    parser.add_argument("--base", default="http://127.0.0.1:8002")
    parser.add_argument("--users", type=int, default=5)
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--training", default="big4", help="the training whose detail page is loaded")
    args = parser.parse_args()
    if args.users > 20:
        raise SystemExit("At most 20 users: this is a check, not a stress test.")
    asyncio.run(run(args.base, token_for(args.email), args.users, args.seconds, args.days, args.training))


if __name__ == "__main__":
    main()
