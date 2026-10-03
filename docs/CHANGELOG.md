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
