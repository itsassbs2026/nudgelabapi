# Changelog

## Phase 1 — API foundation (2026-10-03)

- Project scaffold in pingit's layout: settings (`pydantic-settings`), SQLAlchemy session, request-id
  middleware, structured JSON logging with redaction, SPEC §8 error shape, `GET /api/v1/health`.
- Alembic: `0001_baseline` (production's agent schema: 24 tables, 5 views, with a guard so it only builds empty
  databases) and `0002_api_tables` (`dash_users`, `dash_refresh_tokens`, `dash_password_reset_tokens`,
  `dash_audit_log`, `dash_email_outbox`, `review_queue`, `saved_views`).
- Tests (12): health, error shape, full schema present, views compile, models match the database, baseline
  refuses an existing schema, role CHECK constraint, test-database guard.
- CI: GitHub Actions with MySQL 8.4 (lint, format, mypy strict, tests).
- Deploy prep: IAM policies (stage 1 and 2), database logins script, assignment-sync guide.

## Phase 2 — Auth & users (2026-10-03)

- Sign-in (`/auth/login`, `/auth/refresh`, `/auth/logout`), change / forgot / reset password, `GET/PATCH /me`.
- Admin: list, create (temporary password or invitation email), edit, deactivate / reactivate users, reset a
  user's password (temporary or by email); audit log with filters.
- Forced password change enforced by the API; lockout; login rate limit; refresh-token rotation with reuse
  detection.
- Email outbox + Microsoft Graph sender; worker (`python -m app.worker`): email every 15 s, token cleanup every
  6 h.
- `scripts/bootstrap_admin.py` (first Admin; refuses if one exists).
- Tests: 77 (65 new), including an authorization matrix over every route.

## Phase 3 — Metrics & report API (2026-10-03)

- Shared metric definitions (`app/reports/metrics.py`) and global filters (dates in the user's time zone,
  training, completion type, region → store, setup, voice; bot sessions excluded, Admin-only toggle).
- `GET /reports/overview`, `/reports/trainings`, `/reports/trainings/{id}` (funnel, drop-off, time per topic,
  ratings, review stats, versions), `/reports/trainings/{id}/questions` (first-try accuracy, wrong options,
  "heard as"), `/reports/drilldown`, `/reports/cost`, `/reports/filter-options`.
- `GET /employees/{uid}`, `/feedback`, `/acknowledgments`, `/assignments` (state counts, overdue).
- Tests: 132 (55 new). Every figure is checked against a hand-built dataset (`tests/agent_data.py`); the
  authorization matrix covers the new routes.

## Phase 4 — Sessions & recordings (2026-10-03)

- `GET /sessions`: the session list with the global filters plus trainee, outcome, end reason, flagged, rating
  and search (name, uid or session id); each row shows rating, review score, cost and recording state.
- `GET /sessions/{session_id}`: the session viewer's data (metadata, transcript with seconds for seeking, timeline
  events, AI review with issues linked to transcript lines, feedback, usage and cost). Audit logged.
- `POST /sessions/{session_id}/recording-url`: a 5-minute inline playback link (404 never recorded or missing,
  410 past the 90-day retention, 503 if S3 can't be reached). Audit logged.
- New dependencies: boto3 (and boto3-stubs, moto for tests).
- Tests: 163 (31 new), with S3 mocked by moto.

## Phase 5 — Quality queue & exports (2026-10-03)

- `GET /quality` (flagged sessions with status counts; filters: status, issue type, assignee and the global
  filters) and `PATCH /quality/{session_id}` (status, resolution, note, assignee; audit logged). The session
  viewer now includes the queue state.
- `POST /exports` for ten reports: CSV streamed, XLSX queued for the worker; `GET /exports/{id}` and
  `GET /exports/{id}/download`. Row limit, local times, formula escaping, audit log.
- Worker: `exports` (every 5 s) and `export_cleanup` (hourly).
- Migration `0003_jobs`. New dependency: openpyxl.
- The session timeline labels dropped connections, reconnects and timeouts (logged by the agent since
  nudgelab 4b86529).
- Tests: 214 (51 new), including a check that every export matches its screen and a collation guard.

## Phase 6 — for the dashboard shell (2026-10-03)

- `GET /search?q=` for the dashboard's ⌘K palette: employees (name or uid), sessions (id prefix), trainings and
  stores. LIKE wildcards in the search text are escaped.
- `scripts/export_openapi.py` writes `openapi.json` (committed) for the dashboard's generated types.
- `scripts/e2e_seed.py` builds the throwaway local database the dashboard's Playwright tests use (refuses
  anything that isn't a local `*_e2e` database).
- Tests: 218.
