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

# Run the worker (emails, token cleanup, XLSX exports) in a second terminal
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

## Layout

```
app/            FastAPI app: config, db, routers, models (API tables only), utils
alembic/        migrations: 0001 baseline (agent schema), 0002 API tables, …
tests/          pytest; a disposable local database, migrated to head per run
deploy/         IAM policies, database logins, (Phase 9) Nginx, systemd, deploy script
docs/           SPEC.md, ASSIGNMENT_SYNC.md, DECISIONS.md, CHANGELOG.md
```
