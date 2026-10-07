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

## Phase 7 — for the dashboard reports (2026-10-03)

46. **Retries per trainee** (SPEC §7.2 Training detail) are part of `GET /reports/trainings/{id}` as `attempts`:
    for the same cohort as the funnel, how many quiz rounds each trainee has started, and how many sessions
    they've had for the training so far (all time, test sessions left out). Counted from `quiz_answers` and
    `training_sessions`, not from `training_progress.sessions_count`, which the agent may not have set.
47. **`GET /reports/rating-trend`** (Feedback page, "rating trend per training"): the average rating per training
    per week, weeks starting Monday in the user's time zone, over the same sessions as the feedback list.
48. **`GET /live`** (Overview, "live sessions now"): LiveKit's room list, read-only, with a 5-second timeout (503
    `live_unavailable` on failure). Rooms named `nl-<training>-<uid>-<tag>` are training calls; tags starting with
    `bot` are tests, shown to Admins only on request. Names, trainings and start times only. Off
    (`configured: false`) until `LIVEKIT_URL`, `LIVEKIT_API_KEY` and `LIVEKIT_API_SECRET` are set; the owner adds
    them to the server's `.env` (the same values as the agent's).
49. **CORS exposes `Content-Disposition`** so the dashboard (another origin) can read export file names.

## Phase 8 — for the session viewer and quality queue (2026-10-03)

50. **Saved views** (`/saved-views`) are private to each user: a name and the page's query string (validated:
    URL-query characters only, 2,000 at most, 30 per page). The dashboard re-applies one by navigating to the page
    with that query. Pages: overview, trainings, drilldown, sessions, quality, feedback, cost, compliance.
51. **`GET /team`** lists active dashboard users (id, name, role only) so Trainers can assign quality-queue items;
    the full user list stays Admin-only.
52. **The e2e seed gives `rec-new` the s1 transcript**, so the dashboard's test has a recorded session with lines
    to click. The browser test serves its own audio file for the recording link: S3 signing is covered here by
    the moto tests, and running a local S3 server (moto's server mode) would add dozens of packages, some
    Windows-only, to the lock file.

## Phase 9 — Deploy (prepared 2026-10-03)

53. **The runbook is `deploy/README.md`**: one ordered list for both servers, the database, IAM and DNS, each step
    with its check. The owner runs the server and database steps (deploys are `git pull`, never file copies).
54. **Migrations never run from `deploy.sh`.** It stops when a release has migrations; they're reviewed with
    `alembic upgrade <current>:head --sql` (prints SQL, executes nothing) and applied by hand with the migrate
    login (SPEC §14.1). For go-live that's eight `CREATE TABLE`s for the API's own tables, nothing else. The
    runbook starts with `alembic current` because the production state couldn't be read from here (the agent's
    login can't see `alembic_version`); if it was never stamped, it's stamped at `0001_baseline`, never upgraded.
55. **Grants on the API's tables are their own file** (`deploy/db-grants-api-tables.sql`), run after the
    migration creates the tables and before the bootstrap Admin (who is inserted with the app login). A test
    checks both grant files against the code: every table the API reads or writes is granted, the agent's
    data is SELECT-only, the audit log is append-only, and no `v_*` table is granted.
56. **The worker runs from day one** (SPEC §14.1 said Stage 2): emails, XLSX exports and cleanup need it.
57. **The wildcard certificate pingit already uses** (`/etc/ssl/myprimeportal/…`) instead of Let's Encrypt
    (SPEC §14.1): same server, same domain, no renewals to run.
58. **`scripts/preflight.py`** checks what breaks deploys, read-only and without printing secrets: settings,
    `.env` permissions, the app login's reads, the API tables, the migration state, the instance role and a HEAD
    on the newest recording (S3 access without listing the bucket), LiveKit, Graph, and the export folder.
    `deploy/preflight-check.sh` adds DNS, certificate, Python, port, nginx and systemd checks.

## Phase 10 — Content model and agent loading (2026-10-03)

59. **Content format 1 mirrors the files losslessly** (SPEC §6.5): `training` is training.json as written, the
    knowledge base is split into topics and typed lines (kind, the exact marker, the text, location tags),
    `quiz` is quiz.json, `vocabulary` is vocabulary.txt. The agent's `content.py` writes and reads it;
    `app/schemas/training_content.py` is the API's copy, tested against real exports of all four trainings
    (each validates and dumps back unchanged).
60. **The database is the source of truth once a training has content** (owner's decision): editing the files
    changes nothing live until `uv run content.py publish <training>`; from Phase 11 the dashboard publishes.
    Trainings without database content still run from their files, exactly as before.
61. **Trainees stay on the version they started until they pass** (owner's decision), quiz retries included;
    "start over" and anything after passing use the active version. `training_progress.version_id` is no
    longer overwritten after a pass, so the reports' per-version figures keep the version a trainee passed on.
    Trainees mid-training on a version without content (published from files before Phase 10) move to the
    active version, which is what they had before.
62. **Migration 0004 only adds four nullable columns** to the agent's `training_versions` (in place on MySQL 8,
    no table copy). No check constraint or foreign keys: either would rebuild the table under the live agent, and
    the agent's tables stay independent of the API's. The agent's login gets `UPDATE (content, status)` on that
    table, nothing more, for `content.py publish`.
63. **Preview-call isolation (SPEC 10.6.3) and the testers table (10.6.4)** are built in Phases 15 and 14, as the
    phase list assigns them; nothing issues preview tokens or reads testers from the database before then.

## Phase A1 — Flutter app hand-off (2026-10-03)

64. **The app keeps Wanaka's JSON** (owner's decision): the list and the badge return the procedures' keys, so
    the app's screens need no change; new keys are only added. `trainer_key` is the NudgeLab training id,
    `trainer_id` the old Wanaka id (`trainings.wanaka_trainer_id`), `elevenlabs_agent_id` the completion key.
65. **A NudgeLab pass, signed by Wanaka with its own ES256 key** (owner's decision), instead of sharing Wanaka's
    HS256 `SECRET_KEY`: this API can check passes but never make one. Required claims, `iss`/`aud`, 30 s leeway,
    at most an hour's lifetime, uid as a digits-only `sub`. A dashboard token is never a pass and vice versa
    (different algorithms and keys), with a test each way.
66. **Assignments come from `training_assignments` only**, added by hand for now (`deploy/assign-testers.sql`);
    rules later (APP_HANDOFF.md 4.2). A training is listed when assigned (not cancelled), `app_status = 'active'`,
    not retired and published. The badge counts the same rows (required, not completed), so the two can't
    disagree, unlike the old procedures.
67. **Profile through a view** (`vw_app_profile`, definer's rights): the API still has no grant on `v_users`.
    The district manager is `v_users.district_id` read as a uid, as in Wanaka's procedure. Checked by the owner on
    2026-10-04 (counts of missing, inactive, non-DM titles and other-district DMs, plus spot checks): correct.
68. **`assignment_month`** is the current month in `DEFAULT_TIMEZONE`; Wanaka used the month of its latest sales
    data, which assignments here don't depend on.
69. **Rate limits per uid, not per IP** (60 reads and 6 session starts a minute by default): many employees share a
    store's address. Counted per Uvicorn worker, like the login limit.
70. **Session tokens last 30 minutes** (as the tester page), so a dropped call can rejoin; each start is logged in
    `app_session_starts` (uid, training, room, pass id, IP). The session itself is still the agent's row.
71. **Errors use this API's shape** (`{"error": {"code", "message", "details"}}`), never database messages.

## Phase 11 — Training CRUD & versions (2026-10-04)

72. **Nothing in Phase 11 can change what the voice agent runs.** The agent runs a training's active version or
    a trainee's pinned version, never "the latest", so drafts are invisible to it. The API is granted UPDATE on
    named columns only: not `trainings.active_version_id`, not `training_versions.status` (Phase 16 adds them
    with publishing). A test asserts the active versions and their content are unchanged by studio work.
73. **Versions made in the studio get a random content hash**, fixed at creation: `training_versions` is unique on
    (training, hash), and the agent finds file-published versions by their file hash, so neither can collide.
74. **Optimistic locking:** every save sends the revision it started from; the update matches on it and bumps it.
    A stale save gets 409 `edit_conflict` with the current revision and who saved it, and changes nothing. No
    locks to release, nothing lost silently (SPEC §15 Phase 11 acceptance, tested with two editors).
75. **Only drafts are editable.** A published or retired version is changed by copying it into a new draft.
76. **A new training starts as `draft`** (`trainings.status`) with no active version: not in reports, not in the
    app, not runnable. Blank content has the line keys the agent reads for the completion type, all empty; the
    trainer writes them and validation (Phase 13/16) blocks publishing empty required lines.
77. **Completion type and location setting lock once a version is published**: changing them would change what
    the content must contain under trainees already on it.
78. **Archive = `trainings.status = 'retired'`** (Admin): out of the app's list; the published version and history
    stay. Restoring makes it `active` again, or `draft` if it was never published.
79. **Content is stored exactly as sent** (`exclude_unset`), the same round-trip the agent's export is tested with.
    Saves are limited to 2 MB. Content saves aren't audit-logged (they're frequent); the version row keeps who
    saved last and when. Creating trainings and versions and changing settings are audit-logged.
80. **Per-training default voice** (SPEC 10.2) isn't a column the agent reads: the voice comes from the training's
    setup (`profile_id` → `training_profiles.voice_id`). Choosing a voice per training waits for Phase 14.

## Trainer persona, step 1: API (2026-10-04)

81. **The trainer's name and voice are decided in one place** (`app/mobile/persona.py`, owner's decision, option
    B: the trainer uses the name). The list offers them; the app sends them back on session start; the server
    accepts only what it offered (that persona's name or the training's default name) and active voices. So the
    rule can change server-side without any app change, and nobody can make the trainer say an arbitrary name.
82. **Today's persona:** the district manager's name (`vw_app_profile`), and the voice the agent would pick
    anyway: the training's setup voice if set and active, else the `default_marker` voice. No change in voice
    until someone changes those.
83. **First name only, plain letters only:** the trainer says the first word of the name; anything that isn't
    letters with single spaces, hyphens or apostrophes (≤ 40) isn't spoken, and the training's default is used.
    Keeps names natural and keeps anything else out of the agent's instructions.
84. **The default name comes from the version the session will run** (a trainee mid-training stays on theirs;
    starting over runs the active version), read from the content's `training.trainer_name`, else "Anne".
85. **Recorded:** `app_session_starts` keeps the name and voice put in each token; `training_sessions.trainer_name`
    (migration 0007) is for the agent to fill in step 2.

## Phase 12 — Uploads & prepare for voice (2026-10-04)

86. **Files go straight to S3 by presigned POST**, under a random key, with the content type and a 1 byte–10 MB
    range in S3's own policy; the API confirms the object and the worker does everything with the bytes. The
    file name is stored only to show people.
87. **Files are checked by content, not name**, in the worker: a Word file must be a ZIP holding
    `word/document.xml` (parsed with defusedxml; ≤ 2,000 entries, ≤ 100 MB unpacked), a PDF must start `%PDF-`
    (pypdf; not encrypted; ≤ 300 pages), text must be UTF-8 without NUL bytes. Scanned PDFs have no text and are
    rejected with that message.
88. **A document over 200,000 characters is rejected, not cut**: a training made from part of a document would
    be missing things without anyone noticing.
89. **Claude's answer comes back as a `submit` tool call, not structured output.** Tested on Bedrock with Sonnet
    5.5: `output_config.format` and `strict` tools are rejected there, and a forced tool choice is rejected by
    the model. So the tool is offered with the default choice, and everything that reads its input checks it
    (the converter ignores anything off-schema, the content schema validates the result).
90. **The prompt's examples are the sample trainings, never Big 4**, so the acceptance test on the Big 4
    document proves the method instead of copying the answer.
91. **Fact check in two layers:** every number written in digits must appear in the source (deterministic), and
    a second Claude pass must quote, per statement, a passage that supports it; the API checks the quote is
    really in the source (normalized). Either failing flags the statement. Structural flags too: no question,
    a missing line, `{trainer_name}` missing from the welcome, a topic over 30 seconds (SPEC 10.4's warning).
92. **Preparing never overwrites a trainer's edits:** the job saves only if the draft's revision is still the
    one it started from; otherwise it fails and keeps its result in the job.
93. **Bedrock from the API server goes to us-east-1** (`BEDROCK_REGION`, the `us.` inference profile, as the
    agent): the pingitapi server is in us-west-1, where the model isn't served directly.

## Phase 13 — Training studio (API part, 2026-10-04)

94. **Validation is the API's, not the dashboard's**: Phase 16's publish enforces the same function. Errors and
    warnings are SPEC 10.4's, with two refinements from the live trainings: a quiz question may have **two or
    three** options (Q4 comp has true/false questions), and the welcome line should use `{trainer_name}`.
95. **Saves drop explicit nulls in training settings and lines**: to the agent a missing `completion_type` means
    "quiz" but a null one is an error, so a client sending nulls must not be able to break a training. Only the
    top-level `quiz` and `vocabulary` keep null ("none").

## Voice samples (2026-10-04)

96. **Samples are made on demand, not pre-recorded** (SPEC 10.3 planned stored clips): Polly reads whatever the
    trainer types, so they can hear a line from their own training in each voice. A sample costs a fraction of a
    cent; length (600 characters) and rate (20 a minute per user) are capped, and nothing is stored. Managing
    voices (switching on and off, the default) stays in Phase 14.

## Phase 14 — Voices, setups & testers (2026-10-04)

97. **Testers move from testers.json to a table the API owns** (`testers`); the agent only reads it. Codes stay
    web.py's format and hash (trimmed, uppercased, SHA-256), so imported testers keep their codes and a code made
    on the dashboard works on the tester page unchanged. A code is shown once; only "new code" replaces it.
98. **A tester's trainings must exist and have an active version**: the tester page runs the active version, so
    a draft-only training would fail at start. Imports drop unknown trainings and report them instead of failing.
99. **Setups pick models from an allowlist** (`SETUP_MODELS`: Haiku 4.5, Sonnet 5.5, as Bedrock inference
    profiles) rather than free text, and `effort` is refused for a model that doesn't take it: a typo in either
    would break every session on that setup. Adding a model is a code change with a test.
100. **The default voice or setup can't be switched off, and an off one can't become the default** (the tables'
    CHECK would refuse it anyway); the API says why instead of a database error.

## Phase 15 — Preview calls (2026-10-04)

101. **A preview is proven by a signed pass, not by the token alone** (SPEC 10.6.3): HMAC-SHA256 with a secret
     only the API and the agent hold, over version, training, uid, start and expiry. Without a valid pass the
     agent refuses the room outright; it never falls back to a normal session, which would write progress under
     the preview uid.
102. **Previews write the session, transcript, issues and usage, and nothing else**: no progress, completions,
     acknowledgments, feedback, quiz answers or topic events (reports can't pick them up), and no audio recording
     or recording notice. The session row is what Phase 16's "at least one completed preview" will check.
103. **A preview can start after the topics** (straight to the quiz, or the end of a walkthrough) so testing the
     quiz doesn't take ten minutes; the start is part of the signed pass.
104. **Previews run only versions without check errors** (warnings are fine): a broken draft could leave Anne
     silent. Any active setup can be chosen, not only requestable ones; one open preview per trainer.

## Phase 16 — Publish workflow (2026-10-04)

105. **A light review step**: submitting locks the content; anyone (Trainer or Admin, the author included) can
     then publish it or send it back with a note. The lock means what was previewed and reviewed is what goes live.
106. **"Completed preview" means** a preview call on that exact version, started after its last edit, in which
     the trainee said something; reaching the end isn't required. A rollback (a retired version published again)
     needs no new preview: it was live before.
107. **One Transcribe vocabulary per published version** (`nudgelab-<training>-v<id>`), created and READY before
     the switch, instead of updating one per training: updating makes a vocabulary unusable for minutes, which
     would fail sessions starting then. Old ones stay for pinned trainees; cleanup later (account limit 100).
108. **Publishing is a worker job and switches in one transaction at the end**: topic rows and the vocabulary
     come first, so a failure leaves the live version untouched; a version sent back while queued isn't published.
109. **No Wanaka completion-key picker**: the API has no Wanaka access by design; the key stays a typed setting,
     and publishing without one asks for confirmation.

## Phase 17 — Hardening (2026-10-04)

110. **Sign-in answers a locked account exactly like a wrong password** (the message mentions the lockout), so
     nobody can learn which emails have accounts; the audit log still records the lockout.
111. **Load-tested at full rollout, locally, and checked on production**: a local database at production's real
     population (50,500 trainees, 2,542 stores) with 300,000 sessions, plus a read-only one-minute run on
     production (5 users, 5,400 requests, 0 errors, p95 ≤ 102 ms). Production's own data is still small, so its
     numbers say nothing about volume; the local run does.
112. **Never join `store_id_at_session` to the store views row by row**: the synced `v_stores_all.store_id` is
     utf8mb3 and the agent's column utf8mb4, so MySQL can't use the store index for that join (the sessions list
     took minutes at full rollout). Count and page on the sessions table, then look up stores for the page.
113. **No new index on the agent's training_sessions yet**: a covering index halved some report queries locally,
     but changing the agent's live table isn't worth it on laptop numbers. Requests over 2 s are logged
     (`slow_request`, path only) so the reports get tuned against real volume as it arrives.

## Completions to Prime Portal only (2026-10-04)

114. **The agent no longer writes to Wanaka** (owner's decision): passes are copied to Prime Portal only, and the
     completion key comes from `nudgeai.trainings` (the dashboard's training settings), not Wanaka's old class
     catalog. The app already reads NudgeLab's own record of passes, so NudgeLab now uses no Wanaka table at all;
     the agent login's two Wanaka grants, and its unused one on `nudge.prime_nudge_ai_completions`, are revoked
     (`deploy/db-revoke-agent-wanaka.sql`). Prime Portal is `primetwok` on its own server, so it's unaffected. Checked first: every live training that copies passes
     (Big 4, Q4 comp) already had its key in `nudgeai.trainings`.

## PortalLive reference-table sync (2026-10-06)

115. **NudgeLab fills its own copies of `v_users_all`, `v_users`, `v_stores`, `v_stores_all`** (owner's decision;
     there was no sync before). A oneshot script on the API server, twice a day (08:00 and 23:00 Chicago), modelled
     on joynapi's proven one: fetch first, refuse an empty fetch, a drop under half, or different columns, then
     DELETE + INSERT by column name in one transaction, one `sync_run_log` row per table per run, failures emailed.
     Every column is copied (owner's choice), so the personal-data columns stay out of the API's reach the way they
     were: the sync uses its own logins (read-only on PortalLive; `nudgelab_sync` on nudgeai, which can replace only
     these four tables), and the API's login still has no grant on them. The four tables become utf8mb4
     (`deploy/reference-tables-utf8mb4.sql`), ending the utf8mb3 join problem of #112. The sync's INSERT is built
     from identifiers (SPEC rule 7's one exception): the table names come from a fixed list and the column names
     must equal the target's own columns before anything runs; every value is a bound parameter.

116. **An on-demand Portal sync, confirmed by an emailed code** (owner's request, 2026-10-06). The lists hold
     employees' details, so an Admin session alone isn't enough: *Sync user/store list from Portal* emails a 6-digit
     code to the Admin's own address (HMAC-stored with the server secret, 10 minutes, single use, 5 tries, 3 per 15
     minutes, a new code cancels the old), and only the right code queues the run. The run is a worker job, never
     the API process: the worker uses the sync's own logins, so the API's login still has no access to the four
     tables. One run at a time; each table's result lands in `sync_run_log` like the scheduled runs.
