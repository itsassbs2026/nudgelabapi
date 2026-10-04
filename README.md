# nudgelabapi

The API behind the NudgeLab dashboard (`nudgelab.myprimeportal.com`): reporting on the NudgeLab voice trainer
(Stage 1) and training management (Stage 2). **Read `docs/SPEC.md` first**; `CLAUDE.md` has the database rules.

## Safety first

`nudgeai` on the RDS host is **production**, and the voice agent writes to it all day.

- Tests only ever run against a local database whose name ends in `_test` (`tests/conftest.py` refuses
  anything else, before connecting).
- Production's existing schema is **stamped** (`alembic stamp 0001_baseline`), never upgraded through the
  baseline. The baseline refuses to run on a database that already has the agent's tables.
- Autogenerate only sees the API's own tables (`alembic/env.py`), so it can never propose changes to the
  agent's tables or the synced `v_*` tables.

## Local setup

Needs Python 3.12 and a local MySQL 8 or MariaDB (XAMPP's MariaDB works: `127.0.0.1:3306`, user `root`).

```bash
uv venv --python 3.12 .venv            # or: python -m venv .venv
uv pip install -r requirements.lock    # or: .venv/Scripts/pip install -r requirements.lock
cp .env.example .env                   # points at a local nudgeai_dev database

# Build a local copy of the schema (baseline + API tables)
# (same collation as the agent's tables: utf8mb4_unicode_ci on MariaDB, utf8mb4_0900_ai_ci on MySQL 8)
mysql -u root -h 127.0.0.1 -e "CREATE DATABASE nudgeai_dev CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
.venv/Scripts/alembic upgrade head

# Run the API
.venv/Scripts/uvicorn app.main:app --reload --port 8002   # http://localhost:8002/api/docs

# Run the worker (emails, token cleanup, XLSX exports, uploads, prepare, publish) in a second terminal
.venv/Scripts/python -m app.worker
```

## Checks (the same ones CI runs)

```bash
.venv/Scripts/ruff check . && .venv/Scripts/ruff format --check .
.venv/Scripts/mypy app
TEST_DATABASE_URL="mysql+pymysql://root:@127.0.0.1:3306/nudgeai_test?charset=utf8mb4" .venv/Scripts/pytest -q
```

## Dependencies

`requirements.in` lists direct dependencies; `requirements.lock` pins everything
(`uv pip compile requirements.in -o requirements.lock --python-version 3.12`). Servers install from the lock.

## Running it in production

Everything operational is in **`deploy/README.md`**: first install, every phase's deploy steps (migrations,
grants files, AWS changes), rollback, and where logs and settings live. In short:

| Task | How |
|---|---|
| Deploy | `bash /srv/nudgelabapi/deploy/deploy.sh` on the API server; it stops at a pending migration |
| Migration | review `venv/bin/alembic upgrade <current>:head --sql`, then `venv/bin/alembic upgrade head` |
| Grants | `deploy/db-grants-*.sql`, run by an admin; `tests/test_deploy.py` checks them against the code |
| Health | `bash deploy/preflight-check.sh` (read-only; any time) |
| Logs | `journalctl -u nudgelabapi -f`, `journalctl -u nudgelabapi-worker -f` |
| Slow pages | `journalctl -u nudgelabapi \| grep slow_request` (any request over 2 s, path only) |
| Load check | `venv/bin/python scripts/load_test.py --email <admin> --users 5 --seconds 60` (GETs only) |

The worker (`python -m app.worker`, unit `nudgelabapi-worker`) runs emails, token cleanup, exports, upload
extraction, AI preparation and publishing; one instance only.

### Performance at full rollout (Phase 17)

`scripts/load_seed.py` builds a local `*_load` database at full-rollout size (50,500 trainees, 2,542 stores,
300,000 sessions); `scripts/load_test.py` measures the report pages against it or against a server. Two things
learned: the synced `v_stores_all.store_id` is utf8mb3 while `training_sessions.store_id_at_session` is
utf8mb4, so never join those columns row by row (look stores up for a page of rows instead); and give a local
MariaDB a realistic buffer pool (XAMPP's default 16 MB makes every report look slow).

## Layout

```
app/            FastAPI app: config, db, routers, models (API tables only), utils
alembic/        migrations: 0001 baseline (agent schema), 0002 API tables, …
tests/          pytest; a disposable local database, migrated to head per run
app/reports/    the report queries (metrics.py has the shared definitions); app/studio/ the training studio
app/mobile/     the Flutter app's API (passes, sessions, persona)
scripts/        bootstrap_admin, preflight, export_openapi, e2e_seed, check_prepare, load_seed, load_test
deploy/         deploy.sh, preflight, Nginx, systemd, IAM policies, database logins and grants
docs/           SPEC.md (kept identical in both repos), DECISIONS.md, CHANGELOG.md, APP_HANDOFF.md,
                FLUTTER_APP_GUIDE.md, WANAKA_NUDGE_TOKEN.md
```
