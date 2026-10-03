# NudgeLab Dashboard & API — Build Specification

> **Audience:** Claude (VS Code / Claude Code) and the Prime Business Systems team.
> **Purpose:** single source of truth for the NudgeLab trainer dashboard (`nudgelabdashboard`) and its API (`nudgelabapi`).
> **Scope:** Stage 1 (reporting) and Stage 2 (training management). Stage 3 (agent CI, staging, deploys) is out of scope here.
> **Status:** v0.2 (owner's answers to v0.1 open items applied, 2026-10-03). Items marked `⚠ CONFIRM` still need a decision or data from the project owner.

---

## 0. Instructions for Claude (read first)

1. **Build in phases** (Section 15). Finish one phase, run its tests, summarize what changed, and **stop for review** before the next.
2. **Don't invent scope.** If something isn't in this spec, ask. If the spec is ambiguous, state the assumption in the commit summary and in `docs/DECISIONS.md`.
3. **The security rules in Section 12 are non-negotiable.** Every endpoint gets an authorization test, not just an authentication test.
4. **`nudgeai` is a live production database** (on the `primetwok8-testing…` RDS host, which despite its name is production). **Never run tests, `alembic downgrade`, `alembic stamp base`, or anything that creates or drops tables against it.** Tests use a disposable local MySQL/MariaDB (as in pingit's `CLAUDE.md`).
5. **The voice agent writes to `nudgeai` all day.** Never change, rename or drop a table or column the agent uses (Section 6.2) without a matching, tested agent change. Schema changes are additive by default.
6. **Read-only sources, never written:** `v_users_all`, `v_stores_all`, `v_users`, `v_stores` and `training_assignments` (external syncs). The API has no access to `wanaka` or Prime Portal (`primetwok`).
7. **All SQL through SQLAlchemy with bound parameters.** No string-built SQL, including `ORDER BY` and filter fields (use allowlists). Report queries may use SQLAlchemy Core or `text()` with bound parameters, never f-strings.
8. **Keep the two repos independent.** The dashboard talks to the API only over HTTPS JSON. No shared code. The agent (`nudgelab` repo) is a third, independent codebase.
9. Prefer boring, well-maintained libraries, the same ones pingit uses where possible. Pin versions in lockfiles.
10. When a phase is done, update `docs/CHANGELOG.md` and tick its checklist here.

Repo layout (local):

```
C:\python\nudgelabapp\
├── nudgelabdashboard\   → github.com/itsassbs2026/nudgelabdashboard   (React SPA)
├── nudgelabapi\         → github.com/itsassbs2026/nudgelabapi         (FastAPI)
└── nudgelab\            → github.com/itsassbs2026/nudgelab            (voice agent; moves here in Stage 3)
```

Put this file at `docs/SPEC.md` in both repos, and a `CLAUDE.md` at each root saying: *"Read docs/SPEC.md before any task. Follow Section 0 rules."*

---

## 1. Product Overview

### 1.1 What it is
NudgeLab is Prime Communications' AI voice trainer: an AI coach ("Anne") teaches a training out loud and, for quiz trainings, grades a short quiz. Trainees use it through the Flutter app (and today, a tester web page). The voice agent already records every session, transcript, quiz answer, rating, cost, review and recording.

This project adds:
- **Stage 1: Reporting dashboard.** Trainers and admins see how trainings perform, drill from region down to a single session, play recordings, review flagged sessions, and export data.
- **Stage 2: Training management.** Trainers create and publish trainings in the dashboard (upload content, AI-assisted voice preparation, editing, voice and type selection, preview calls, versions) instead of a developer editing files on the agent server.

### 1.2 Users
Only **Admins** and **Trainers** log in (Section 4). Trainees never use the dashboard.

### 1.3 Goals
- A trainer can answer "how is this training doing, and where do people struggle?" in under a minute.
- Any figure can be drilled down: region → market → district → store → employee → session → transcript and recording.
- A trainer can turn a source document into a reviewed, voice-ready, published training without developer help (Stage 2).

### 1.4 Non-goals (v1)
- No trainee or leadership logins (leaders get exported or scheduled reports instead, later).
- No editing of the voice agent's behavior rules (`trainer_rules.md`) from the dashboard.
- No live listening to in-progress sessions.
- No phone layout (desktop and tablet only, like pingit).

---

## 2. Architecture

```
 Trainers / Admins (browser)
        │ HTTPS
        ▼
 PINGIT SERVER 1 (existing)                 PINGIT SERVER 2 (existing)
┌──────────────────────────┐   HTTPS+JWT   ┌──────────────────────────────────────────┐
│ Nginx                    │ ────────────▶ │ Nginx (TLS) → Uvicorn → FastAPI (systemd)│
│  └ nudgelabdashboard SPA │               │   nudgelabapi on 127.0.0.1:8002           │
│    nudgelab.myprimeportal│               │   + worker process (systemd), Stage 2    │
└──────────────────────────┘               └───────┬───────────┬────────────┬─────────┘
                                                   │           │            │
                       MySQL RDS (us-west-1)       │           │            │ AWS
          ┌────────────────────────────────────────┴──┐        │            │
          │ nudgeai  (agent tables + new API tables)  │   S3 recordings     Bedrock (Claude, Stage 2)
          │ (training_assignments: synced from Wanaka)│   S3 training content  Transcribe (vocabulary, Stage 2)
          └───────────────────────▲───────────────────┘        │
                                  │ reads trainings (Stage 2)   │ LiveKit (preview-call tokens, Stage 2)
                     VOICE AGENT SERVER (unchanged location, us-east-1, 35.173.142.66)
```

### 2.1 Domains
| Purpose | URL |
|---|---|
| Dashboard | `https://nudgelab.myprimeportal.com` |
| API | `https://nudgelabapi.myprimeportal.com` |

Both are subdomains of `myprimeportal.com`, so the refresh-token cookie is same-site (Section 9).

### 2.2 Environments
`prod` first. `alpha` (staging) comes later, with the agent's staging work (Stage 3): separate `.env`, database (`nudgeai_staging`), S3 prefix, and LiveKit agent name. The environment is config (`APP_ENV`), not part of the URL.

### 2.3 Hosting (decided)
- API: on the existing pingitapi server (`204.236.179.185`), as its own systemd service (`nudgelabapi`, port **8002**) and Nginx site. Code arrives by **`git pull`** from GitHub (Section 14), not by copying files over SSH.
- Dashboard: static files on the existing pingit frontend server, its own Nginx site.
- The voice agent stays on its current EC2 server. Nothing in this project moves it.

---

## 3. Tech Stack

Same as pingit unless noted.

### 3.1 API
| Concern | Choice |
|---|---|
| Language | Python 3.12 |
| Framework | FastAPI, Pydantic v2, `pydantic-settings` |
| Server | Uvicorn (`--workers 2`), systemd |
| DB | MySQL 8 (RDS), SQLAlchemy 2.x sync + PyMySQL, Alembic |
| Passwords / JWT | Argon2id (`argon2-cffi`), PyJWT |
| AWS | boto3 via the server's **EC2 instance role** only (no keys in `.env`) |
| Rate limiting | `slowapi` |
| Background jobs (Stage 2) | separate worker process (APScheduler + a `jobs` table) |
| Documents (Stage 2) | `python-docx`, `pypdf` (text extraction); `filetype` for magic-byte checks |
| LiveKit (Stage 2) | `livekit-api` (preview-call tokens) |
| Exports | `openpyxl` (XLSX), stdlib `csv` |
| Logging | `structlog` → JSON → journald |
| Testing | pytest, httpx TestClient, disposable local MySQL/MariaDB |
| Lint | ruff, mypy (strict on `app/`) |

### 3.2 Dashboard
| Concern | Choice |
|---|---|
| Build | Vite + React + TypeScript (strict) |
| UI | Tailwind CSS, shadcn/ui (Radix), lucide-react |
| Data | TanStack Query; TanStack Table |
| Routing / forms | React Router; React Hook Form + Zod |
| Charts | Recharts |
| Audio | native `<audio>` with a custom transcript-synced player |
| Editor (Stage 2) | structured forms per topic and question (not free text); diff view via `diff` |
| Voice preview (Stage 2) | `livekit-client` (same as the tester page) |
| Testing | Vitest + RTL; Playwright smoke tests |
| API types | `openapi-typescript` generated from the API's OpenAPI |

---

## 4. Roles & Permissions

| Capability | Trainer | Admin |
|---|:-:|:-:|
| View all reports, drill-downs, sessions, transcripts | ✅ | ✅ |
| Play session recordings | ✅ | ✅ |
| Quality queue: mark reviewed, add notes | ✅ | ✅ |
| Export CSV / XLSX | ✅ | ✅ |
| Create and edit draft trainings (Stage 2) | ✅ | ✅ |
| Publish a training version (Stage 2), after a preview call | ✅ | ✅ |
| Archive a training | ❌ | ✅ |
| Manage voices, setups (profiles), recording notice | ❌ | ✅ |
| Manage testers and access codes | ❌ | ✅ |
| Manage dashboard users | ❌ | ✅ |
| View audit log | ❌ | ✅ |

Every playback of a recording and every export is written to the audit log (Section 12).

---

## 5. Concepts & Metric Definitions

These definitions are used everywhere (API, charts, exports). Implement each **once** in `app/services/metrics.py`.

| Term | Definition |
|---|---|
| **Session** | One row in `training_sessions` (one voice call). Bot test sessions (`client = 'bot_test'`) are **excluded by default** from every report (toggle for Admins). |
| **Trainee** | A distinct `uid` with at least one session. |
| **Assigned** | A `training_assignments` row with `status = 'assigned'` (synced from Wanaka, Section 6.4). |
| **Started** | `training_progress` row exists with ≥ 1 session. |
| **Walkthrough done** | `training_progress.walkthrough_finished_at` is set. |
| **Completed** | `training_progress.passed_at` is set (quiz passed, walkthrough completed, or acknowledged, per `trainings.completion_type`). |
| **Completion rate** | Completed ÷ Assigned (when assignment data exists), else Completed ÷ Started. Always show which. |
| **First-try accuracy** (per question) | Share of trainees whose **first** answer (`quiz_answers.round = 1`) to that question was correct. `vw_question_stats` is the reference. |
| **Most-missed questions** | Questions ranked by first-try accuracy, ascending. |
| **Drop-off topic** | For trainees who started but haven't completed: the highest topic in `topics_covered`, plus 1 (the topic they never finished). Shown as a funnel by topic. |
| **Session length** | `training_sessions.duration_sec`. |
| **Rating** | `training_feedback.rating` (1–10). |
| **Review score** | `session_reviews.score` (1–5); **flagged** = `session_reviews.flagged`. |
| **Cost** | `session_usage.est_total_cost` (list prices; estimate). Cost per completion = cost of all sessions ÷ completions. |
| **Org hierarchy** | Store → district → market → region, from `vw_training_stores`. Session-level reports use the store **at session time** (`training_sessions.store_id_at_session`). Trainee-level reports use the trainee's **current** store (`vw_trainees`). |

All times are stored in UTC and displayed in the user's time zone (default `America/Chicago`).

---

## 6. Data Model

### 6.1 Ownership after this project
- **Schema owner:** the `nudgelabapi` repo, through **Alembic**, from now on. The agent's `sql/001–007` scripts are frozen history. The first Alembic migration is a **baseline** that represents 001–007 and is applied with `alembic stamp` on prod (never `upgrade` against existing tables).
- **Writers:** the agent writes session data (6.2). The API writes only its own tables (6.3), plus the training-content tables in Stage 2 (6.5).
- **Database logins:**
  - `nudgelab_api` (new, host `10.0.1.148`, the pingitapi server's private address): SELECT on all `nudgeai` agent tables and views (including `training_assignments`); INSERT/UPDATE/DELETE on API tables; in Stage 2, write on `trainings`, `training_versions`, `training_topics`, `training_questions`, `training_voices`, `training_profiles`. No Wanaka or Portal access. The owner runs `deploy/db-logins.sql`.
  - `nudgelab_api_migrate` (new, DDL on `nudgeai` only): used only by Alembic, from a deploy step, never by the running app.

### 6.2 Existing agent tables (read-only for the API, unless noted)
| Table / view | Used for |
|---|---|
| `trainings` | catalog: title, `completion_type`, `completion_key`, `profile_id`, `active_version_id` (**API writes in Stage 2**) |
| `training_versions`, `training_topics`, `training_questions` | versions and their topics and questions (**API writes in Stage 2**) |
| `training_progress` | per-trainee status, topics covered, walkthrough done, correct questions, `passed_at` |
| `training_sessions` | every session: start/end, outcome, end reason, store at session, profile, voice, recording key, client |
| `session_transcripts` | transcript lines (`seq`, `role`, `message`, `seconds_into_session`, `interrupted`) |
| `session_topic_events` | when each topic was reached in a session |
| `quiz_answers` | every answer: question, round, option, correct, what was heard |
| `training_feedback` | rating and comment |
| `session_usage` | tokens, characters, audio seconds, estimated costs |
| `session_reviews`, `daily_review_reports` | daily AI reviews (score, flagged, issues JSON) |
| `session_issues` | safety corrections, refused hang-ups, etc. |
| `training_acknowledgments` | acknowledgment statement and the trainee's exact words |
| `completion_writes` | log of completion copies to Wanaka and Portal |
| `training_assignments`, `vw_assignment_status` | assignments, **filled by the owner's Wanaka → nudgeai sync** (6.4); read-only for the API |
| `training_voices`, `training_profiles` | voices and setups (**API writes in Stage 2, Admin only**) |
| `vw_trainees`, `vw_training_stores`, `vw_session_report`, `vw_question_stats` | joins with the org hierarchy |
| `v_users_all`, `v_stores_all`, `v_users`, `v_stores` | external sync, **read-only**, never in Alembic autogenerate |

### 6.3 New API tables (Stage 1)
Conventions as in pingit: `BIGINT UNSIGNED` ids, `DATETIME(6)` UTC, enums as `VARCHAR(32)` + CHECK.

- **`dash_users`**: id, email (unique, lowercased), full_name, password_hash (argon2id), role (`trainer`|`admin`), is_active, timezone, failed_login_count, locked_until, must_change_password, last_login_at, preferences JSON (theme, saved filters, table columns), created_at, updated_at.
- **`dash_refresh_tokens`**: id, user_id, token_hash, family_id, expires_at, revoked_at, replaced_by_id, user_agent, ip, created_at.
- **`dash_password_reset_tokens`**: id, user_id, token_hash, expires_at, used_at.
- **`dash_audit_log`** (append-only): id, actor_user_id, action (`login`, `recording_played`, `transcript_viewed`, `export`, `training_published`, `settings_changed`, …), target_type, target_id, details JSON, ip, created_at.
- **`review_queue`**: session_id (PK, FK `session_reviews`), status (`open`|`reviewed`|`dismissed`), assignee_user_id NULL, note TEXT NULL, resolution (`script_changed`|`agent_issue`|`no_action`|`other`) NULL, updated_by, updated_at. Rows are created lazily the first time someone acts on a flagged review.
- **`dash_email_outbox`**: id, to_email, subject, body_html, template, status (`pending`|`sent`|`failed`|`dead`), attempts, next_attempt_at, last_error, created_at, sent_at (password-reset emails, sent by the worker through Graph, as pingit's `email_outbox`).
- **`saved_views`** (optional, phase 8): id, user_id, page, name, filters JSON, created_at.

### 6.4 Assignments (synced from Wanaka)
Wanaka builds assignments dynamically, so the owner runs a **sync from Wanaka into `nudgeai.training_assignments`** (an existing, empty table). The API only reads it. Contract for the sync:

| Column | Value |
|---|---|
| `uid` | employee uid |
| `training_id` | the nudgeai training id (e.g. `big4`), found by matching the Wanaka class key (`prime_ai_training_agents.elevenlabs_agent_id`) to `trainings.completion_key`. Classes with no nudgeai training are skipped (foreign key). |
| `assigned_at` | when assigned in Wanaka |
| `due_at` | due date, or NULL |
| `status` | `assigned`, or `cancelled` when unassigned (never delete rows; reports keep history). `completed` is not set by the sync: completion comes from `training_progress`. |
| `assigned_by` | assigning uid if known, else NULL |

Upsert on the unique key (`uid`, `training_id`). The sync's own login needs INSERT and UPDATE on this table only. An additive `synced_at` column may be added in Phase 1 for freshness reporting ("assignments as of …").

### 6.5 Training content (Stage 2)
The agent currently reads each training from files. In Stage 2 the content lives in the database, versioned:

- **`training_versions`** gains (additive columns): `status` (`draft`|`in_review`|`published`|`retired`), `content` JSON (the full training package: settings, lines, opening, knowledge base topics, quiz, acknowledgment, vocabulary), `created_by`, `source_upload_id` NULL, `published_at` (exists), `published_by` (exists), `notes` (exists).
- **`training_drafts`** is not needed: a draft is a `training_versions` row with `status = 'draft'`.
- **`content_uploads`**: id, training_id, s3_key, original_filename, content_type, size_bytes, status (`pending`|`ready`|`rejected`), extracted_text MEDIUMTEXT, uploaded_by, created_at.
- **`jobs`**: id, type (`prepare_for_voice`, `create_vocabulary`, `export`), training_id NULL, version_id NULL, status (`queued`|`running`|`done`|`failed`), input JSON, result JSON, error, attempts, created_by, created_at, started_at, finished_at.
- **`testers`** (moves from the agent server's `testers.json`): uid, name, code_hash, trainings JSON, is_active, created_by, created_at. The tester page (`web.py`) switches to this table in the agent's Stage 2 change.

The `content` JSON mirrors today's files exactly (`training.json` + parsed `knowledge_base.md` topics + `quiz.json` + `vocabulary.txt`), so the agent's loader needs only a new source, not new logic. JSON schema: `app/schemas/training_content.py` (Pydantic), shared contract with the agent.

---

## 7. Stage 1 — Reporting Dashboard

### 7.1 Global filters (every report page)
Date range (presets: 7 / 30 / 90 days, this month, custom), training (one or all), region / market / district / store (cascading), completion type, setup (profile), voice, include bot tests (Admin only). Filters live in the URL query string, so any view can be bookmarked or shared.

### 7.2 Pages
| Page | Content |
|---|---|
| **Overview** | KPI tiles: sessions, trainees, completions, completion rate, average rating, average review score, average session length, cost and cost per completion; trend lines (daily); completions by training; flagged sessions (count + link); live sessions now (from LiveKit `list_rooms`, read-only) |
| **Trainings** | Table of trainings: type, active version, assigned / started / completed / rate, average rating, average review score, sessions, cost; click → Training detail |
| **Training detail** | Funnel (assigned → started → walkthrough done → [quiz attempted] → completed); **drop-off by topic** (bar chart); time per topic (from `session_topic_events`); **most-missed questions** with first-try accuracy, overall accuracy, the most common wrong option and sample "heard as" text; retries per trainee; rating distribution and comments; review score distribution and top issue types; version history with per-version metrics |
| **Drill-down** | Hierarchical table region → market → district → store → employee, with assigned, started, completed, rate, average rating, sessions, last activity at each level. Expand inline or click to scope all pages to that node (breadcrumb). |
| **Employee** | Header (name, job title, store, hierarchy); per-training status, attempts, sessions, quiz answers by round, acknowledgments, ratings; session list |
| **Session viewer** | Metadata (trainee, training/version, setup, voice, store, start/end, outcome, end reason, client); **recording player** with the **transcript synced** to playback (click a line to seek, using `seconds_into_session`); timeline markers for quiz grades, safety corrections, dropped connections, refused hang-ups, topic changes; the AI review (score, summary, issues with quotes, linked to transcript lines); usage and cost breakdown |
| **Quality queue** | Flagged sessions (from `session_reviews`), filter by issue type, training and status; open → session viewer; set status, resolution and note (writes `review_queue`) |
| **Feedback** | All ratings and comments, searchable, filterable; rating trend per training |
| **Cost** | Cost by day, training, setup; Polly vs Transcribe vs Claude split; cost per session and per completion; cache hit rate (cached ÷ input tokens) |
| **Compliance** | Acknowledgments: trainee, training, statement, exact words, time, link to the session and recording |
| **Exports** | Every table has "Export CSV / XLSX" (current filters applied; streamed; max 100k rows; audit logged) |
| **Admin: Users** | Create, deactivate, reset password, change role |
| **Admin: Audit log** | Filterable list |

### 7.3 Performance
Report endpoints must respond in < 1.5 s at p95 for 90-day ranges at current volume. Use indexed queries and pre-aggregation where needed (a nightly `report_daily` rollup table is allowed if a query can't meet the target; document it in `DECISIONS.md`). Add indexes only through Alembic, additive, and only after checking the agent's write paths aren't affected.

---

## 8. API Specification

Base path `/api/v1`. JSON. Errors: `{ "error": { "code", "message", "details" } }`. Pagination: `?page=&page_size=` (max 200) with `total`. Sorting: `?sort=field,-field` against a per-endpoint allowlist. All endpoints require a dashboard JWT except `/auth/*` and `/health`.

### 8.1 Auth (as pingit)
`POST /auth/login`, `POST /auth/refresh`, `POST /auth/logout`, `POST /auth/change-password`, `POST /auth/forgot-password`, `POST /auth/reset-password`, `GET /me`, `PATCH /me` (preferences, timezone).

### 8.2 Reports (Stage 1)
| Method & path | Returns |
|---|---|
| `GET /reports/overview` | KPIs + daily series |
| `GET /reports/trainings` | trainings table |
| `GET /reports/trainings/{training_id}` | funnel, drop-off, time per topic, ratings, review stats, versions |
| `GET /reports/trainings/{training_id}/questions` | per-question stats, wrong-option breakdown, sample "heard as" |
| `GET /reports/drilldown?level=region|market|district|store|employee&parent=` | rows for one level under a parent |
| `GET /employees/{uid}` | employee header + per-training status |
| `GET /sessions` | session list (filters) |
| `GET /sessions/{session_id}` | metadata, transcript, events, review, usage |
| `POST /sessions/{session_id}/recording-url` | presigned GET (5 min, inline audio); **audit logged** |
| `GET /quality` / `PATCH /quality/{session_id}` | flagged sessions; update status/note |
| `GET /feedback` | ratings and comments |
| `GET /reports/cost` | cost series and splits |
| `GET /acknowledgments` | compliance list |
| `GET /assignments` | assigned trainees and their state (not started, in progress, completed, overdue) |
| `POST /exports` → `GET /exports/{id}` | export job (streamed CSV for small results; XLSX via job) |
| `GET /live` | current live sessions (LiveKit rooms; names only) |
| `GET /admin/users` … `POST/PATCH` | user management (Admin) |
| `GET /admin/audit` | audit log (Admin) |

Every report endpoint accepts the global filters (7.1) through one shared Pydantic model.

### 8.3 Training management (Stage 2)
| Method & path | Purpose |
|---|---|
| `GET /trainings`, `POST /trainings` | list; create (title, completion type, persona, default voice, setup, completion key) |
| `GET /trainings/{id}`, `PATCH /trainings/{id}` | details and settings |
| `GET /trainings/{id}/versions`, `POST /trainings/{id}/versions` | versions; new draft (blank, from upload, or copy of a version) |
| `GET/PUT /versions/{version_id}/content` | the full content JSON (drafts only for PUT; optimistic locking with `revision`) |
| `GET /versions/{a}/diff/{b}` | structured diff |
| `POST /trainings/{id}/uploads/presign` → `POST /uploads/{id}/complete` | upload a source document (presigned POST, magic-byte check, text extraction) |
| `POST /versions/{version_id}/prepare` | job: AI "prepare for voice" from the upload into the draft |
| `POST /versions/{version_id}/validate` | checks (Section 10.4) |
| `POST /versions/{version_id}/preview-call` | LiveKit token for a browser preview session with this version |
| `POST /versions/{version_id}/submit`, `/publish`, `/retire` | workflow |
| `GET/POST/PATCH /admin/voices`, `/admin/profiles`, `/admin/testers` | settings (Admin) |
| `GET /jobs/{id}` | job status |

---

## 9. Authentication Details

Exactly pingit's design (pingit SPEC §8.1), with these values:
- Access token: JWT HS256, 15 min, claims `sub`, `role`, `iat`, `exp`, `jti`, held **in memory only** in the SPA.
- Refresh token: opaque 256-bit, 12 h sliding, 7 days absolute, stored hashed. Cookie: `HttpOnly; Secure; SameSite=Strict; Path=/api/v1/auth`, host-only (no `Domain`, so it goes to the API host alone; DECISIONS #12).
- Rotation with reuse detection; lockout after 5 failures (15 min); `/auth/login` limited to 10/min/IP; minimum 12-character passwords checked against a common-password list.
- Bootstrap: `scripts/bootstrap_admin.py` creates the first Admin (`bgupta@primecomms.com`) from env, with `must_change_password = true`, and refuses if an Admin exists.
- Password reset, both ways: (a) self-service "forgot password" email through **Microsoft Graph** (as pingit §10: client credentials, `Mail.Send` restricted to one sender mailbox, sent by the worker from an outbox table); (b) an Admin resets a user's password from the Users page (forces a change at next login). It reuses pingit's Azure app registration and sender mailbox.
- CORS: exactly `https://nudgelab.myprimeportal.com` (plus `http://localhost:5173` outside prod), credentials allowed.

---

## 10. Stage 2 — Training Management

### 10.1 Workflow
```
draft ──submit──▶ in_review ──publish──▶ published ──(newer version published)──▶ retired
  ▲                    │
  └─────reject─────────┘
```
- One `published` version per training at a time (`trainings.active_version_id`).
- **Trainees who started a training finish on the version they started** (`training_progress.version_id`), so topic numbers never shift under them. New trainees get the active version. This needs the agent change in 10.6.
- Publishing requires passing validation (10.4) **and at least one completed preview call on that version** (logged). Trainers and Admins can both publish.

### 10.2 Creating a training
1. **Settings:** title, completion type (quiz / walkthrough / acknowledgment), trainer persona name (default "Anne"), **default voice** (from `training_voices`, with a play-sample button using pre-generated Polly samples), setup (profile: Standard / Enhanced), Wanaka completion key (picked from Wanaka's catalog), uses-location flag.
2. **Content:** upload a source document (Word `.docx`, PDF, `.txt`, `.md`; max 10 MB) **or** start blank **or** copy an existing version.
3. **Prepare for voice (AI):** a worker job sends the extracted text to Claude (Bedrock, Sonnet 5.5) with the same preparation rules used for the Big 4 voice version: short beats of 2–3 sentences, one check-in per topic, Expected and Accept answers, key points that don't repeat the expected answer, "Say exactly" for compliance lines, a draft quiz (quiz type), an acknowledgment statement (acknowledgment type), and a vocabulary list of acronyms and product names. Output is the content JSON (6.5), written into the draft. **Every generated fact is checked against the source text**; anything not found is flagged in the editor.
4. **Review and edit:** structured editor, never free text:
   - **Topics:** reorderable cards; per topic: title, Say lines, Say-exactly lines, Ask, Expected, Accept, Key point / Then add, notes; estimated speaking time per topic (characters ÷ 15 per second) with a warning above 30 s.
   - **Quiz** (quiz type): sections (with topic mapping for retries), questions, three options, correct letter, explanation.
   - **Lines:** welcome message, welcome-back lines, quiz intro, closing, feedback question, completed lines, acknowledgment intro (shown per completion type).
   - **Vocabulary** terms.
   - **Side by side:** the source document's text next to the topic being edited.
5. **Validate** (10.4) → **preview call** → **submit** → **publish**.

### 10.3 Voices, setups, testers (Admin)
- Voices: list from `training_voices`; activate/deactivate; set default (one statement, as today); generate a sample clip (Polly) stored in S3.
- Setups (profiles): view and edit `training_profiles` (model, effort, engine, prices); `allow_request`.
- Testers: create (shows the access code once), assign trainings, deactivate. Replaces `web.py add-tester`.

### 10.4 Validation rules (block publish)
- At least one topic; every topic has an Ask with ≥ 4 words; Ask lines are distinct (the agent's topic tracker matches on them).
- Quiz type: ≥ 1 question; every question has 3 options, a correct letter and an explanation; every section maps to existing topics.
- Acknowledgment type: statement present (10–300 chars).
- Required lines present for the completion type (as in the agent README's "Completion types" table).
- Warnings (don't block): topics over 30 s of speech, unverified facts, missing Accept lines, a missing completion key.

### 10.5 Preview calls
The API issues a LiveKit token (named dispatch to the agent, metadata `{training_id, version_id, preview: true, uid: 900000 + dashboard user id}`), and the dashboard opens a browser voice session, like the tester page. Each dashboard user has a **reserved preview uid** (900000 + their id) that exists only for previews. Preview sessions are labelled `client = 'preview'`, never write progress or completions, and are excluded from reports.

### 10.6 Required agent changes (in the `nudgelab` repo, Stage 2, phase 10)
1. Load a training from `training_versions.content` (by `active_version_id`, or by the trainee's pinned `training_progress.version_id`), falling back to files if the database has no content yet.
2. Pin new trainees to the active version; keep in-progress trainees on theirs.
3. Accept `version_id` + `preview: true` in dispatch metadata **only** from preview tokens (signed by the API; checked against a shared secret or a dedicated agent name) and never write completions for previews.
4. Read testers from the `testers` table (tester page).
5. Bot-tested before deploy, with the deploy rules in the agent's README (check live rooms first).

---

## 11. AWS & Integrations

| Need | Detail |
|---|---|
| S3 recordings (existing bucket `nudgeailab`, us-west-1) | `s3:GetObject` on `recordings/*` for presigned playback (5 min, inline, `audio/ogg`) |
| S3 training content (Stage 2) | prefix `training-content/{env}/` in the existing `nudgeailab` bucket: Put/Get; private; lifecycle delete of `training-content/{env}/pending/` after 1 day |
| Bedrock (Stage 2) | `bedrock:InvokeModel` on the Claude Sonnet 5.5 inference profile (prepare for voice) |
| Transcribe (Stage 2) | `CreateVocabulary` / `UpdateVocabulary` / `GetVocabulary` on `vocabulary/nudgelab-*` (as the agent's policy) |
| Polly (Stage 2) | `SynthesizeSpeech` for voice samples |
| LiveKit | API key/secret in `.env` (preview tokens, `list_rooms` for Live) |
| RDS | the pingitapi server reaches RDS from its private address `10.0.1.148`: the new logins use that host (the RDS security group already allows it, for pingit) |

All AWS access is through the pingitapi server's **instance role, `Prime-nudgeapi-ec2-role`**: attach `deploy/iam-policy-stage1.json` now and replace it with `deploy/iam-policy-stage2.json` for Stage 2. No keys.

---

## 12. Security Requirements (non-negotiable)

1. Every endpoint has an **authorization test** (Trainer vs Admin vs anonymous).
2. Transcripts and recordings are **personal data** (employees' voices and words): only logged-in users; recording URLs are presigned for 5 minutes, inline, never listed in bulk; every playback, transcript view and export is **audit logged** with user, target and IP.
3. Bound parameters everywhere; sort and filter fields from allowlists.
4. Uploads: presigned POST with size limits, server-side magic-byte check, S3 keys never derived from filenames, text extraction in the worker (never in the request), and a cap on extracted text size.
5. AI output is untrusted: validate it against the content schema; never execute or render it as HTML.
6. No secrets in the repo; `.env` on the server with permissions 600; AWS through the instance role only.
7. Rate limits on auth routes; generic error messages on login failure.
8. Strict security headers on both Nginx sites (HSTS, CSP for the SPA, `X-Content-Type-Options`, `Referrer-Policy`).
9. The production database rule in Section 0.4 applies to every script, test and migration.

---

## 13. Dashboard — Design & Structure

- **Look:** **the same branding and design language as pingit** (same accent color, Geist font, components and theme tokens), light and dark themes, tabular numbers, skeleton loading.
- **Layout:** left sidebar (Overview, Trainings, Drill-down, Sessions, Quality, Feedback, Cost, Compliance; Stage 2: Training studio; Admin), top bar with global filters, a ⌘K command palette (jump to an employee, a session id or a training), theme toggle and user menu.
- **Drill-down UX:** breadcrumb scope (e.g. `All › West › Texas Market › District 12`) applies to every page until cleared.
- **Charts:** Recharts, colorblind-safe palette, every chart has a "view as table" toggle and its own export.
- **Session viewer:** a split view, with the player and timeline on top and the synced transcript below; the review panel on the right.
- **Responsive:** 1280–1920 px; usable on tablet.
- **Structure:** `src/pages/*`, `src/components/*`, `src/api/*` (generated types + TanStack Query hooks), `src/lib/filters.ts` (URL-synced filters), `src/features/studio/*` (Stage 2).

---

## 14. Deployment

### 14.0 Getting code onto the servers: `git pull`
Both servers deploy by **`git pull`** from GitHub, never by copying files over SSH. Each server gets a **read-only deploy key** for its repo (GitHub → repo → Settings → Deploy keys; the private key in `~/.ssh/` of the deploying user, with a `Host github-nudgelabapi` alias in `~/.ssh/config`). A deploy is: `git pull` on `main` → install dependencies from the lockfile → (API) `alembic upgrade head` if the release has migrations → restart → smoke test. A `deploy/deploy.sh` per repo runs these steps; a failed step stops the deploy. Rollback = `git checkout <previous tag>` and the same steps. Releases are tagged (`v1.0.0`, …).

### 14.1 API (pingitapi server, 204.236.179.185)
- Code at `/srv/nudgelabapi` (a git clone), venv at `/srv/nudgelabapi/venv`, `.env` at `/srv/nudgelabapi/.env` (pydantic-settings, mode 600), systemd `nudgelabapi.service` (Uvicorn `127.0.0.1:8002 --workers 2 --proxy-headers`), `nudgelabapi-worker.service` (Stage 2).
- Nginx site `nudgelabapi.myprimeportal.com` → `127.0.0.1:8002`, Let's Encrypt.
- Migrations: `alembic upgrade head` with the migrate login, run by hand during a deploy, after a review of the migration file. **The baseline is `alembic stamp`, never `upgrade`.**
- `deploy/preflight-check.sh` as in pingit (DNS, certificate, `.env` keys, DB reachability with the app login, S3 access).

### 14.2 Dashboard (pingit frontend server)
- `git pull` in `/srv/nudgelabdashboard` → `npm ci && npm run build` → `dist/` published to `/var/www/nudgelabdashboard` (atomic swap of a release folder); Nginx site `nudgelab.myprimeportal.com` with SPA fallback, long cache for hashed assets, no cache for `index.html`.

### 14.3 Environment variables (`/srv/nudgelabapi/.env`)
`APP_ENV`, `DATABASE_URL` (nudgelab_api), `DATABASE_MIGRATION_URL` (migrate login), `JWT_SECRET`, `GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID`, `GRAPH_CLIENT_SECRET`, `GRAPH_SENDER_MAILBOX`, `CORS_ORIGINS`, `REFRESH_COOKIE_DOMAIN`, `AWS_REGION`, `RECORDINGS_BUCKET`, `CONTENT_BUCKET`, `CONTENT_PREFIX`, `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `AGENT_NAME`, `BEDROCK_PREP_MODEL`, `DEFAULT_TIMEZONE`, `BOOTSTRAP_ADMIN_EMAIL`, `BOOTSTRAP_ADMIN_PASSWORD`.

---

## 15. Build Phases & Acceptance Criteria

### Stage 1 — Reporting
- [x] **Phase 1 — API foundation:** repo scaffold (pingit layout), settings, DB session, `/health`, structured logging, ruff/mypy/pytest in CI (GitHub Actions: lint + tests against a MySQL service container), Alembic configured with the **baseline** (001–007) and API tables (6.3). *Accept:* tests pass locally and in CI; `alembic upgrade head` on an empty local DB builds the full schema; no connection to prod from tests.
- [x] **Phase 2 — Auth & users:** login/refresh/logout, lockout, password reset (Admin reset, plus forgot-password email through Graph via an outbox and the worker), bootstrap admin, user admin, audit log. *Accept:* authorization tests for every route; refresh-token reuse revokes the family.
- [x] **Phase 3 — Metrics & report API:** `metrics.py` (Section 5), global filters, overview, trainings, training detail, questions, drill-down, employee, cost, feedback, acknowledgments, assignments (from `training_assignments`). *Accept:* every metric has a unit test against fixture data; p95 < 1.5 s on a prod-sized copy; bot sessions excluded by default. *(Done 2026-10-03; the p95 check waits for a production-sized copy, DECISIONS #28.)*
- [x] **Phase 4 — Sessions & recordings:** session list and detail (transcript, events, review, usage), presigned recording URL, audit logging. *Accept:* a Trainer can play a recording; the audit log shows it; URLs expire. *(Done 2026-10-03 against mocked S3; the first real playback is checked after deploy, DECISIONS #32.)*
- [x] **Phase 5 — Quality queue & exports:** `review_queue`, CSV/XLSX exports (streamed / job), audit. *Accept:* exports match on-screen figures; large exports don't time out. *(Done 2026-10-03.)*
- [x] **Phase 6 — Dashboard shell & auth:** Vite app, layout, theming, login, refresh on load, route guards, global filters in the URL, ⌘K. *Accept:* Playwright: log in, navigate, log out. *(Done 2026-10-03; the smoke tests run locally against the real API, dashboard DECISIONS #8. The Admin Users and Audit log pages have no phase yet: see Open Items.)*
- [x] **Phase 7 — Dashboard reports:** Overview, Trainings, Training detail, Drill-down, Employee, Feedback, Cost, Compliance. *Accept:* figures match the API; drill-down scope persists across pages. *(Done 2026-10-03: Playwright compares each page with the API and follows a scope across pages.)*
- [x] **Phase 8 — Session viewer & quality queue UI:** synced player and transcript, timeline markers, review panel, queue actions, saved views. *Accept:* clicking a transcript line seeks the audio; flagged sessions can be worked end to end. *(Done 2026-10-03, with the Sessions list and the Admin Users and Audit log pages; Playwright covers both acceptance points.)*
- [x] **Phase 9 — Deploy Stage 1:** deploy keys and `git pull` deploy scripts, Nginx sites, certificates, systemd, preflight script, new DB logins and grants, IAM policy, DNS (Route 53, by the owner), `alembic stamp` baseline on prod, smoke test. *Accept:* the owner logs in at `nudgelab.myprimeportal.com` and sees real data. *(Done 2026-10-03: live at `nudgelab.myprimeportal.com`; the owner signed in and checked real data. Release `v1.0.0`.)*

### Stage 2 — Training management
- [x] **Phase 10 — Content model + agent loading:** content JSON schema; migrate existing trainings (Big 4, Q4, samples) from files into `training_versions.content`; **agent change 10.6** in the `nudgelab` repo, bot-tested, deployed with the live-room check. *Accept:* the agent runs Big 4 from the database with identical behavior (same bot scenarios pass); file fallback works; in-progress trainees stay pinned. *(Done 2026-10-03: live; Big 4, Q4 comp and both samples run from the database. Bot sessions before and after gave identical instructions fingerprints (Big 4 `6cf101b00e78`, samples `8c5a60d6f204` / `6ce6bf085dde`). Preview isolation (10.6.3) → Phase 15, testers table (10.6.4) → Phase 14.)*
- [ ] **Phases A1–A5 — Flutter app hand-off (before Phase 11, decided 2026-10-03):** the app's Nudge menu moves from Wanaka to `nudgelabapi` (training list, badge, LiveKit session start, authenticated by a Wanaka-signed NudgeLab pass); `training_assignments` is the one source of assignments, filled by hand for now (rules later), replacing 6.4's sync; catalog fields move onto `trainings`; the owner moves ElevenLabs trainings to NudgeLab before go-live. Wanaka's only change: `docs/WANAKA_NUDGE_TOKEN.md`. Design, decisions and phase split: `docs/APP_HANDOFF.md`. *Accept:* per phase, in that document.
- [ ] **Phase 11 — Training CRUD & versions:** trainings list/create/settings; versions; content GET/PUT with optimistic locking; diff. *Accept:* two editors can't overwrite each other silently.
- [ ] **Phase 12 — Uploads & prepare for voice:** presigned upload, magic bytes, extraction, worker job with Claude, fact check. *Accept:* uploading the original Big 4 instruction document produces a draft close to the hand-prepared version, with unverified facts flagged.
- [ ] **Phase 13 — Training studio UI:** settings, topic editor, quiz editor, lines, vocabulary, side-by-side source, speaking-time estimates, validation panel, diff view. *Accept:* a trainer builds a 3-topic walkthrough training without help.
- [ ] **Phase 14 — Voices, setups, testers:** admin pages; voice samples; testers table; agent's tester page reads it. *Accept:* `testers.json` retired.
- [ ] **Phase 15 — Preview calls:** token issuing, browser call, preview isolation in the agent. *Accept:* a preview never writes progress or completions and never appears in reports.
- [ ] **Phase 16 — Publish workflow & vocabulary:** submit / publish / retire, publish rules, Transcribe vocabulary created on publish, Wanaka completion-key picker. *Accept:* publishing a new Big 4 version doesn't disturb in-progress trainees.
- [ ] **Phase 17 — Hardening & handover:** security review against Section 12, load test of report endpoints, docs (`README`, a trainer user guide, `DECISIONS.md`). *Accept:* checklist complete; the owner signs off.

---

## 16. Open Items

Resolved 2026-10-03: assignments come from the owner's Wanaka → `training_assignments` sync (6.4); pingitapi server `204.236.179.185`, deploys by `git pull` (14.0); DNS by the owner in Route 53 when ready; Trainers and Admins both publish, after a required preview call (10.1); password reset by Graph email and by Admin (9); time zone `America/Chicago`; pingit's branding; training content in the existing `nudgeailab` bucket; reserved preview uid per dashboard user (10.5); first Admin `bgupta@primecomms.com`.

Also resolved: `Prime-nudgeapi-ec2-role`, the agent's role and the recordings uploader (`nudgeailab-iam-recordings`) are all in AWS account 825245835842, so no bucket policy is needed (checked 2026-10-03); the role has the read-only inline policy `nudgelab-dashboard-play-recordings` (= `deploy/iam-policy-stage1.json`); the shared `nudgeailab-recordings` policy is the uploader's (`s3:PutObject` only) and must not be on this role. The Admin Users and Audit log pages were built in Phase 8 (2026-10-03). The pingitapi server's role is `Prime-nudgeapi-ec2-role` (policies in `deploy/iam-policy-stage1.json` and `deploy/iam-policy-stage2.json`); Graph reuses pingit's Azure app registration and sender mailbox; the owner runs `deploy/db-logins.sql` and builds the assignment sync from `docs/ASSIGNMENT_SYNC.md`.

Still open: none for Stage 1. (Settled 2026-10-03: the new logins' host is the pingitapi server's private address, `10.0.1.148`, as the preflight showed.)

---

## 17. Glossary
- **Agent:** the NudgeLab voice trainer (LiveKit + Transcribe + Claude + Polly), repo `nudgelab`.
- **Completion type:** quiz, walkthrough or acknowledgment (how credit is earned).
- **Completion key:** the Wanaka class id (`prime_ai_training_agents.elevenlabs_agent_id`) used for completion rows in Wanaka and Portal.
- **Setup / profile:** a named model + voice-engine combination (`training_profiles`), e.g. Standard (Haiku) or Enhanced (Sonnet).
- **Bot test session:** an automated test conversation (`client = 'bot_test'`), excluded from reports.
- **Preview call:** a trainer's test session against a draft version (`client = 'preview'`).
