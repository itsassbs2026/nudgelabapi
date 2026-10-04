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

## Phase 7 — for the dashboard reports (2026-10-03)

- Training detail now includes `attempts` (quiz rounds and sessions per trainee).
- `GET /reports/rating-trend` (weekly average rating per training) and `GET /live` (calls in progress, from
  LiveKit; new dependency `livekit-api`).
- CORS exposes `Content-Disposition` (export file names).
- Tests: 231.

## Phase 8 — for the session viewer and quality queue (2026-10-03)

- `GET/POST/DELETE /saved-views` and `GET /team`.
- The e2e seed gives a recorded session a transcript.
- Tests: 247.

## Phase 9 — Deploy files (2026-10-03)

- `deploy/README.md`: the go-live runbook (DNS, deploy keys, database logins, IAM, settings, migrations,
  grants, bootstrap Admin, services, nginx, dashboard, smoke test, redeploy and rollback).
- `deploy/nudgelabapi.service`, `deploy/nudgelabapi-worker.service`, `deploy/nginx-nudgelabapi.conf`,
  `deploy/env.production.example`, `deploy/deploy.sh` (stops on pending migrations), `deploy/preflight-check.sh`
  and `scripts/preflight.py` (read-only checks).
- `deploy/db-grants-api-tables.sql` (split out of `db-logins.sql`), with a test that the grant files match the code.

## v1.0.0 — Stage 1 live (2026-10-03)

- Deployed to the pingitapi server (`nudgelabapi.myprimeportal.com`, port 8002, API + worker) with the dashboard at
  `nudgelab.myprimeportal.com`. Production `nudgeai` stamped at `0001_baseline` (checked first with
  `scripts/check_baseline.py`), then migrated to `0003_jobs` (the API's eight tables only).
- Found during go-live and fixed: database logins use the server's private address (`10.0.1.148`); the preflight
  no longer crashes when the database is unreachable; invalid escape sequences in search (ruff W rules now on).

## Phase 10 — Content model (2026-10-03)

- Migration `0004_training_content`: `status`, `content`, `created_by`, `source_upload_id` on
  `training_versions` (nullable, added in place).
- `app/schemas/training_content.py`: the content document, tested against the agent's exports
  (`tests/fixtures/content`).
- Agent (nudgelab): loads trainings from the database with pinning and file fallback; `content.py export|publish`.
- Live 2026-10-03: migration 0004 applied on production; the agent (nudgelab `99cee78`, `c8d6d6f`) deployed and the
  four trainings published (Big 4 v18, Q4 comp v19, samples v16 and v17). Bot sessions before and after gave
  identical instructions fingerprints.

## Phase A1 — Flutter app endpoints (2026-10-03)

- `/app/v1/trainings`, `/app/v1/trainings/pending-count`, `POST /app/v1/trainings/{training_id}/session`
  (docs/APP_HANDOFF.md §3): the JSON of Wanaka's `usp_ai_trainer_assignments_json` / pending count, plus
  `training_id`, `completion_type`, `status`, `progress` and `due_at`; session starts return a LiveKit token
  with the agent dispatch.
- NudgeLab pass (`app/mobile/passes.py`): ES256, Wanaka's public keys only (`APP_PASS_PUBLIC_KEYS`, rotation by
  key id), uid from `sub` only, active employees only. Per-employee rate limits.
- Migration `0005_app_handoff`: catalog columns on `trainings`, rule columns on `training_assignments` (NULL for
  now), the `vw_app_profile` view and the `app_session_starts` table. Grants: `deploy/db-grants-0005-app.sql`.
- Owner scripts: `deploy/assign-testers.sql`, `deploy/app-catalog.sql`. Wanaka brief:
  `docs/WANAKA_NUDGE_TOKEN.md`. Preflight checks the pass keys.
- Tests: 300.
- Live 2026-10-03: migration 0005 applied on production, `83815e8` deployed, preflight clean; the app endpoints
  answer 401 until Wanaka's public key is set (`APP_PASS_PUBLIC_KEYS`). Preflight now also reads
  `vw_app_profile` and `app_session_starts` (the 0005 grants).

## Phase 11 — Training CRUD & versions (2026-10-04)

- `GET/POST /trainings`, `GET/PATCH /trainings/{id}`, `GET/POST /trainings/{id}/versions`,
  `GET/PUT /versions/{id}/content` (optimistic locking on `revision`), `GET /versions/{a}/diff/{b}` (structured:
  settings, lines, preamble, topics, quiz, vocabulary). Trainers and Admins; archiving and hiding from the app
  are Admin-only.
- New trainings start as `draft` with a blank first version: the content structure and the line keys the agent
  needs for the completion type, all empty (nothing invented).
- Migration `0006_version_editing`: `revision`, `created_at`, `updated_at`, `updated_by` on `training_versions`.
- Grants `deploy/db-grants-0006-studio.sql`: INSERT and **column-level** UPDATE on `trainings` and
  `training_versions`; `active_version_id` and version `status` are not writable until Phase 16. Checked
  against the code (test) and against a local user holding exactly these grants; the preflight reads them.
- Reports leave out trainings never published and versions never live (drafts, in review).
- Tests: 363.
- Live 2026-10-04: migration 0006 and `db-grants-0006-studio.sql` on production, `dcecdd8` deployed, preflight
  25/25; the studio API lists the four trainings with their live versions (Big 4 v18, Q4 v19, samples v16/v17).

## Trainer persona, step 1: API (2026-10-04)

- The app's list adds `trainer_voice` and `default_trainer_name` per card; session start accepts `trainer_name` and
  `trainer_voice` (only values the list offered, or the training's default name; active voices) and puts the
  spoken first name and the voice in the token (`app/mobile/persona.py`).
- Migration `0007_trainer_persona`: `trainer_name`, `voice_id` on `app_session_starts`; `trainer_name` on
  `training_sessions` (for the agent, step 2).
- Flutter guide and APP_HANDOFF updated.

## Trainer persona, steps 2–3: agent and content (2026-10-04, live)

- Agent (nudgelab `011f12f`, `f605e02`, deployed 03:10 UTC): `trainer_name` from the token fills the rules, lines
  and opening; says it's an AI voice trainer if asked; records `training_sessions.trainer_name`. A test now imports
  agent.py and checks for undefined names (a missing import was caught before deploy).
- Content: the four trainings republished with `{trainer_name}` in `first_message` (Big 4 v20, Q4 v21, samples
  v22/v23).
- Bot checks on production: no name → "I'm Anne", recorded Anne/Matthew; `Dana` + `Ruth` → "I'm Dana", in
  Ruth's voice, answers "are you really Dana?" with "I'm an AI voice trainer for Prime Communications…", recorded
  Dana/Ruth. uid 3784's sample progress restored after; 0 agent errors since the deploy.

## Phase 12 — Uploads & prepare for voice (2026-10-04)

- Uploads: `POST /trainings/{id}/uploads/presign` (presigned POST to `training-content/<env>/pending/<random>`,
  type and size fixed by S3's policy), `POST /uploads/{id}/complete`, `GET /uploads/{id}`, `/uploads/{id}/text`,
  `GET /trainings/{id}/uploads`. The worker checks each file by its content (Word = ZIP with document.xml via
  defusedxml and unzip limits; PDF via pypdf; UTF-8 text), extracts the text (≤ 200,000 characters, else
  rejected), and keeps the original under `uploads/`.
- Prepare for voice: a draft made from a document (`POST /trainings/{id}/versions` with `source: "upload"`) is
  prepared by a worker job with Claude Sonnet 5.5 on Bedrock (`prompts/prepare_for_voice.md`), converted into the
  agent's content format, validated, fact-checked (numbers must be in the source; a second pass must quote a
  supporting passage that really is in the source), and saved only if nobody edited the draft meanwhile.
  `POST/GET /versions/{id}/prepare`, `GET /jobs/{id}`.
- Migration `0008_content_uploads`; grant `deploy/db-grants-0008-uploads.sql`; `scripts/check_prepare.py`.
- Checked for real (Bedrock, before deploy): the original Big 4 instructions became 12 topics and a 5-question
  quiz in about 80 s for about $0.17, matching the hand-made version's structure and much of its wording; long
  topics flagged; two planted false statements both flagged. Tests: 449.
- Live 2026-10-04: migration 0008 and the uploads grant on production, `f30c702` deployed; IAM stage 2 policy,
  bucket lifecycle (`training-content/prod/pending/`, 1 day) and CORS (POST from the dashboard) set by the
  owner; preflight 26/26 with the uploads table; `check_prepare.py`: S3 and Bedrock pass from the server's role.

## Phase 13 — Training studio (API part, 2026-10-04)

- `POST /versions/{id}/validate`: the publish checks (SPEC 10.4) as errors and warnings, each pointing at a topic,
  line, question or section. All four live trainings pass with no errors.
- Content saves drop explicit nulls the agent reads differently from "missing" (`completion_type`, other
  settings, a line's `locations`, question fields beside `variants`).
- The studio itself is in nudgelabdashboard (`d373bbc`). Tests: 464.

## Voice samples (SPEC 10.3, 2026-10-04)

- `GET /voices` (the voices Anne can use) and `POST /voices/{voice_id}/sample` (any text up to 600 characters read
  by that voice, as MP3, made by Amazon Polly with the voice's own engine; 20 a minute per user; nothing stored).
  Polly in `POLLY_REGION` (us-east-1, as the agent) through the instance role (stage 2 policy). Tests: 479.
