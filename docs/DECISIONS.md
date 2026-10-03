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
