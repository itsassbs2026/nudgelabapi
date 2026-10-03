# Decisions and assumptions

Recorded as they're made (SPEC §0.2). Newest last.

## Phase 1 — API foundation (2026-10-03)

1. **Baseline as captured DDL.** `0001_baseline` holds the production definitions of the agent's 24 tables and
   5 views, captured from `nudgeai` (MySQL 8.4.8) with `SHOW CREATE TABLE/VIEW` on 2026-10-03, rather than
   replaying the agent's `sql/001–007` scripts. This guarantees local, CI and future staging databases have
   exactly production's structure. Views drop their `DEFINER`/`ALGORITHM` clauses (environment-specific);
   `AUTO_INCREMENT` counters are dropped.
2. **Baseline guard.** The baseline raises if `trainings` or `training_sessions` already exists, so it can only
   build an empty database. Production is `alembic stamp 0001_baseline`. Its downgrade always raises.
3. **External tables in the baseline.** `v_users_all`, `v_stores_all`, `v_users`, `v_stores` (filled by
   Prime's daily sync in real environments) are created as empty structural copies so the views compile
   locally. They're hidden from autogenerate and never migrated.
4. **MariaDB locally, MySQL 8.4 in CI.** The local test server is XAMPP's MariaDB 10.6 (no Docker on the dev
   laptop). MariaDB lacks the `utf8mb4_0900_ai_ci` collation, so the baseline swaps it for
   `utf8mb4_unicode_ci` on MariaDB only. CI uses a `mysql:8.4` service, matching production exactly.
5. **Autogenerate allowlist.** `alembic/env.py` includes only tables on the API's metadata
   (`app.models.API_TABLES`). Agent-table changes are hand-written, additive, and reviewed (SPEC §0.5).
6. **No foreign key from `review_queue` to `session_reviews`.** `session_reviews` is an agent table; the API's
   tables don't constrain the agent's (the agent may rewrite a review). The link is by `session_id`.
7. **`dash_email_outbox`.** Added to SPEC §6.3 for password-reset emails through Graph (the owner chose both
   Graph email and Admin reset), mirroring pingit's `email_outbox`.
8. **Migration login without DROP.** `deploy/db-logins.sql` gives `nudgelab_api_migrate` no `DROP` privilege,
   so no migration can drop a table in production. Downgrades that drop API tables work locally only.
9. **Test database guard.** `tests/conftest.py` refuses any host other than 127.0.0.1/localhost/`mysql` and any
   database not ending in `_test`, before importing the app. Each run drops, recreates and migrates the test
   database to head.
10. **Dependencies locked with uv.** `requirements.lock` is produced by `uv pip compile` (same format pingit
    uses). `cryptography` is included because PyMySQL needs it for MySQL 8's `caching_sha2_password`.

## Phase 2 — Auth & users (2026-10-03)

11. **Same design as pingit** for passwords (Argon2id, 12+ characters, common-password list), access tokens
    (HS256, 15 minutes, in memory) and refresh tokens (rotating, reuse revokes the family, 12 h sliding, 7 days
    absolute), lockout (5 failures → 15 minutes) and the login rate limit (10/minute/IP). Forgot- and
    reset-password share the login rate limit.
12. **Host-only refresh cookie.** SPEC §9 named `Domain=nudgelabapi.myprimeportal.com`; the cookie is set with no
    `Domain` instead, which is stricter: it goes to the API host only, never to its subdomains (same as pingit).
13. **Forced password change is enforced by the API.** While `must_change_password` is set, every endpoint
    except `GET /me`, `POST /auth/change-password` and `POST /auth/logout` answers 403
    `password_change_required`. The bootstrap Admin, users created with a temporary password, and Admin resets
    to a temporary password all start in this state.
14. **Admin password reset, two ways** (the owner chose both): set a temporary password (works without email),
    or send a reset link through Graph. Both unlock the account and end the user's sessions. Creating a user
    works the same way: a temporary password, or an invitation email.
15. **Wrong current password is 400, not 401** (pingit uses 401). A 401 means "your session is gone" to the SPA,
    which would sign the user out for a typo.
16. **Safeguards:** at least one active Admin always remains; an Admin can't demote or deactivate themselves.
17. **Audit log entries for auth events:** login, login_failed (with reason, never the typed email), account
    locked, logout, password changed / reset / reset requested, refresh-token reuse, user created / updated /
    deactivated / reactivated, admin password reset.
18. **Emails wait in the outbox** until Graph is configured (`EMAIL_ENABLED` plus the four `GRAPH_*` values from
    pingit's app registration). The worker retries after 1, 5 and 30 minutes, then marks the email dead.
19. **Authorization matrix test.** `tests/test_permissions.py` calls every route as anonymous, Trainer and Admin,
    and fails if a route is missing from the matrix, so new routes can't skip authorization tests (SPEC §0.3).

## Phase 3 — Metrics & report API (2026-10-03)

20. **Reports live in `app/reports/`** (SPEC §5 named `app/services/metrics.py`). `metrics.py` holds the shared
    definitions; one module per page uses them. The agent's tables are described in `app/reference/agent_tables.py`
    on their own MetaData, so Alembic never sees them and the API can't create or alter them.
21. **Two kinds of figures.** Activity (sessions, trainees, completions, ratings, reviews, cost) counts events whose
    own timestamp is in the date range. Cohort progress (funnel, completion rate, drill-down cohort) follows a
    cohort: trainees **assigned** in the period when assignment rows exist for the scope, otherwise trainees who
    **started** in the period. Every response carries `basis` so the dashboard can label it (SPEC §5).
22. **"Assigned" means `status <> 'cancelled'`**, the same rule as `vw_assignment_status`. The sync never writes
    `completed` (SPEC §6.4), so this equals `status = 'assigned'` today and stays right if it ever does.
23. **Org scoping:** session figures use the store at session time; trainee figures (cohort, completions,
    assignments, acknowledgments) use the trainee's current store. The drill-down groups everything by the
    trainee's **current** place, so each level adds up to its parent.
24. **Days are the user's local days.** Date filters convert local midnights to UTC; the daily series is grouped
    in Python with `zoneinfo`, so it doesn't depend on MySQL's time-zone tables.
25. **Bot and preview sessions (`client` in `bot_test`, `preview`) are excluded** from every report.
    `include_bots=true` is Admin-only (403 for Trainers).
26. **Assignment states** in `GET /assignments`: completed (passed) → overdue (past due, not passed) →
    in progress (started) → not started. The list is the "assigned" cohort for the period, so its counts match the
    funnel; widen the date range to see older overdue assignments.
27. **Path parameter `training_key`** in `/reports/trainings/{training_key}`: FastAPI can't have a path parameter
    and a query filter both named `training_id`. The URL is unchanged.
28. **Speed not yet measured on production-sized data** (SPEC Phase 3 acceptance: p95 < 1.5 s). There's no copy
    of production to test on, and tests never touch production. The queries use the existing indexes
    (`ix_sessions_training_started`, `ix_sessions_store_started`, primary keys); date-only scans of
    `training_sessions` and `training_progress` have no index, which is fine at thousands of rows. Check again
    when staging exists; if needed, ask the owner to add `started_at` / `passed_at` indexes on the agent tables.

## Phase 4 — Sessions & recordings (2026-10-03)

29. **Opening a session is audit logged as `transcript_viewed`**, and every recording link as `recording_played`
    (with the S3 key and the client IP). A failed request (unknown session, missing or expired recording) logs
    nothing.
30. **Recording links:** SigV4, signed for the bucket's region (us-west-1), 5 minutes, `audio/ogg`, played inline.
    Credentials come from the instance role (boto3's default chain); no keys in config. The API never lists the
    bucket.
31. **Recording state** is `available`, `none` (never recorded) or `expired`. Expired means the session is older
    than the bucket's 90-day lifecycle rule (`RECORDING_RETENTION_DAYS`); the link request then answers 410. Before
    signing, the API checks that the file exists (HEAD), so a recording that never reached S3 is a clear 404, not
    a broken player.
32. **403 from S3 counts as "missing".** The role has `s3:GetObject` but not `s3:ListBucket` (by design), and
    without ListBucket S3 answers 403 for a file that doesn't exist. The API logs a `recording_head_forbidden`
    warning each time, so a role that has lost access shows up in the logs. After deploying, play one known
    recording to confirm access.
33. **Timeline events** come from the database: topics reached, quiz answers, safety corrections (`guardrail`),
    refused hang-ups, errors, the acknowledgment and the rating, each with `seconds` into the session for the
    player. Dropped connections were only in the agent's log file at first; since the agent change of 2026-10-03
    (nudgelab 4b86529) they're in `session_issues` too, as `dropped`, `reconnected` and `not_reconnected`.
34. **Review issues are linked to transcript lines** by matching the issue's quote. If no line matches, `seconds`
    comes from the issue's clock time (the agent server's UTC clock) when it falls inside the session.
35. **Session ids in URLs** must match `^[A-Za-z0-9-]+$` (max 36), so odd input never reaches a query or an S3 key.

## Phase 5 — Quality queue & exports (2026-10-03)

36. **The queue is every session the AI review flagged** (`session_reviews.flagged`), filtered like the other
    reports (by the session's date, store, training…). A session with no `review_queue` row is "open"; the row is
    created the first time someone changes it. Only the fields sent in `PATCH` change, `null` clears resolution,
    note or assignee, and every change is audit logged (`quality_updated`, with before and after). Trainers and
    Admins can both work the queue (SPEC §4). The session viewer shows the queue state of a flagged session.
37. **Exports reuse the on-screen functions**, so a file always matches its table (tested report by report). Ten
    reports can be exported: sessions, trainings, questions, drill-down, feedback, acknowledgments, assignments,
    quality queue, daily activity and cost by training.
38. **CSV comes straight back; XLSX is a job.** `POST /exports` answers 200 with the CSV, or 202 with a job for
    XLSX; the worker builds the file into `EXPORT_DIR` on the API server (shared by the API and the worker), and
    only the person who asked can download it. Files are deleted after 24 hours. A job stuck "running" for 30
    minutes (worker restarted) is marked failed. The `jobs` table is the one SPEC §6.5 defines for Stage 2, created
    now (migration 0003).
39. **At most 100,000 rows per export** (SPEC §7.2); a larger request answers 422 `export_too_large` and asks the
    user to narrow the filters, rather than cutting the file short.
40. **Times in exports are the user's local time**, with the time zone in the column header; numbers stay numbers
    (rates are 0–1, shown as percentages in XLSX). CSV has a UTF-8 byte-order mark so Excel opens it correctly.
41. **Spreadsheet formulas never run:** text starting with `=`, `+`, `-` or `@` gets a leading apostrophe.
    Comments, quotes and transcripts are things people said.
42. **Every export is audit logged** (`export`) with the report, format, filters, parameters and row count (XLSX:
    the job id; the row count is on the job).
43. **Collations:** the API's tables take the database's default collation, so it must equal the agent tables'
    (`utf8mb4_0900_ai_ci` on production, checked 2026-10-03) or joins like `review_queue` → `training_sessions`
    fail. The test database is created the same way, and a schema test fails if they ever differ.

## Phase 6 — for the dashboard shell (2026-10-03)

44. **`GET /search` for ⌘K** (SPEC §13 asks to "jump to an employee, a session id or a training"): up to 8
    employees (active first), 5 sessions, 5 trainings and 5 active stores. Session search only runs for text that
    looks like part of a session id. Trainers and Admins; not audit logged (it returns names, not transcripts or
    recordings).
45. **`openapi.json` is committed** so the dashboard can regenerate its types without running the API. Re-export it
    with `scripts/export_openapi.py` after any API change.
