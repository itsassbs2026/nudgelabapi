# Flutter app → nudgelabapi hand-off

> **Status:** decided 2026-10-03 (Section 6). A1 (the API side) built and deployed 2026-10-03. Runs **before Phase 11** (owner's decision).
> Assignment *rules* are later (Section 4.2): until then the owner assigns trainings by hand.
> **Readers:** the owner (Sections 2, 4, 6), whoever changes the Flutter app (Section 5), the DB team when rules come (4.2, Appendix A).

## 1. Goal

Wanaka keeps doing what it does: sign-in and everything that isn't training. **Everything about AI trainings moves
to `nudgeai` and `nudgelabapi`.** The app signs in with Wanaka as today. For anything training-related (the Nudge
badge on the bottom bar, the training list, starting a voice session) it talks only to `nudgelabapi`.

What it replaces:

| Today (Wanaka) | After |
|---|---|
| `GET /v1/trainer-assignments/{uid}` → `usp_ai_trainer_assignments_json` | `GET /app/v1/trainings` |
| `GET /v1/trainer-assignments/{uid}/pending-count` → `usp_ai_trainer_assignments_pendingcount_json` | `GET /app/v1/trainings/pending-count` |
| ElevenLabs session started by the app | `POST /app/v1/trainings/{training_id}/session` → LiveKit token |
| `prime_ai_training_agents` (wanaka catalog) | `trainings` (new columns) |
| `prime_ai_training_assignment_rules`, `prime_ai_training_rule_uids` (wanaka) | rows in `training_assignments`, by hand now; rules in nudgeai later (4.2) |
| `prime_nudge_ai_completions` as the app's "done" flag | `training_progress.passed_at` (the agent's own record) |

**Unchanged:** the agent still writes completions to Wanaka and Portal, so anything else that reads
`prime_nudge_ai_completions` keeps working. The planned Wanaka → nudgeai assignment sync (`ASSIGNMENT_SYNC.md`) is
**replaced** by Section 4.

## 2. Sign-in hand-off: the NudgeLab pass

```
App ──login──▶ Wanaka /v1/login-portal            (as today; Wanaka token, 15 min)
App ──────────▶ Wanaka POST /v1/nudge/token        (new; Wanaka token in, NudgeLab pass out)
App ──────────▶ nudgelabapi /app/v1/...            (Authorization: Bearer <NudgeLab pass>)
```

**When the app gets a pass:** right after login, and again whenever it needs one and the one it has is expired
or about to be: the badge on the bottom bar needs a pass before the user ever opens Nudge. Not only when Nudge
is opened.

**Wanaka (owner):** one new endpoint, `POST /v1/nudge/token`.
- Protected by `verify_jwt_primetwok` (the full check, including the one-active-token rule).
- Returns `{"token": "...", "expires_in": 900}`.
- The pass is a JWT signed with a **dedicated private key** (ES256), kept only on the Wanaka server and separate
  from `SECRET_KEY`.
- Claims: `iss: "wanaka"`, `aud: "nudgelabapi"`, `sub: "<uid>"`, `iat`, `exp` (15 minutes), `jti` (random).
- `kid` in the header so the key can be rotated: nudgelabapi accepts the current and the next public key.

**nudgelabapi:**
- Holds only the **public** key (`APP_PASS_PUBLIC_KEYS` in `.env`; not secret, but kept out of the repo so a
  rotation is a config change).
- Checks signature, `iss`, `aud`, `exp` (30 s leeway), and that `sub` is an **active** employee (`vw_trainees`).
- Uses `sub` as the uid for everything. **No endpoint takes a uid from the app.**
- A 401 tells the app to get a new pass. If Wanaka refuses that too, the Wanaka session has expired: sign in again.

Why a key pair rather than sharing `SECRET_KEY`: whoever holds `SECRET_KEY` can mint tokens for **every** Wanaka
route. With a key pair, a leak from the nudgelabapi server can't sign anything, and Wanaka's own sign-in is
untouched.

## 3. App endpoints (nudgelabapi)

All under `/app/v1`, NudgeLab pass only. The dashboard's logins can't call them, and passes can't call the
dashboard's endpoints. Rate limit **per uid** (not per IP: a store's Wi-Fi puts many people behind one address), e.g.
60/min for reads and 6/min for session starts.

### 3.1 `GET /app/v1/trainings`

**Same shape as `usp_ai_trainer_assignments_json` today**, so the app's list screen keeps working, plus new fields:

```jsonc
{
  "uid": 53, "name": "Akbar Mohamed", "job_id": 29, "store_id": "0000",
  "job_title": "Chief Executive Officer", "store_name": null, "market_name": null,
  "region_name": null, "district_name": null,
  "assignment_month": "2026-09",
  "assigned_trainers": [
    {
      // unchanged keys
      "trainer_id": 14, "trainer_key": "rsc_sales_plan_q4", "trainer_name": "RSC Sales Incentive Plan",
      "trainer_category": "Sales", "trainer_description": "This is an AI Voice based training ...",
      "tags": null, "ai_flag": null, "is_required": true, "is_completed": false,
      "assigned_at": "2026-10-01 06:00:00.000000",          // now the real date it was first assigned
      "trainer_person_name": null, "trainer_picture": null,  // still the employee's district manager
      "matched_rule_group_id": 86, "matched_rule_name": "RSC Q4 Compensation Plan",
      "elevenlabs_agent_id": "agent_7601m34txm9rencay43s0dvbzczh",

      // new keys
      "training_id": "q4_comp_2026",         // example; what the app sends to start a session
      "completion_type": "quiz",             // quiz | walkthrough | acknowledgment
      "status": "in_progress",               // not_started | in_progress | completed
      "progress": {"topics_done": 3, "topics_total": 7, "quiz_retry": false},
      "due_at": null
    }
  ]
}
```

- `is_completed` and `status` come from `training_progress` (NudgeLab) instead of `prime_nudge_ai_completions`.
- Sorted: required and not completed first, then by `assigned_at`.
- `elevenlabs_agent_id` carries the training's completion key (`trainings.completion_key`), so the key keeps its
  meaning for the app; it's renamed in a later `/app/v2`.

### 3.2 `GET /app/v1/trainings/pending-count`

`{"uid": 53, "name": "Akbar Mohamed", "incompleted_count": 2}`, as today. Computed from **the same query as the
list** (required and not completed), so the badge and the list can never disagree. Today they do (Appendix A, 2).

### 3.3 `POST /app/v1/trainings/{training_id}/session`

Body: `{"start_over": false, "trainer_name": "...", "trainer_voice": "..."}` (the last two optional). Returns
`{"server_url", "participant_token", "room_name", "expires_in", "trainer_name", "trainer_voice"}`.

**Trainer persona** (`app/mobile/persona.py`, decided 2026-10-04): one function, `trainer_persona`, gives each
employee and training a name and a voice; the list shows them (`trainer_person_name`, `trainer_voice`, plus
`default_trainer_name`), and a session start accepts back only those or the training's default name, and any
active voice. Today: the district manager's name and the voice the agent would pick anyway (the setup's voice,
else the `default_marker` voice). Changing the rule means changing that function only; the app sends back
whatever it was given. The trainer says the first name only; names that aren't plain letters aren't spoken
(the default is used). The agent (step 2) uses `trainer_name` and says it's an AI trainer if asked.

- Refused (403) unless the training is **assigned** to this uid, not cancelled, and has a published version.
- The token is signed with the LiveKit secret and carries the dispatch metadata (`uid`, `training_id`, `reset`,
  `client: "flutter"`). The app can't change it, so the agent can trust the uid without new agent code.
- Room name `nl-<training>-<uid>-<random>`, as today: the dashboard's Live page and reports work unchanged.
- One token per call, valid 30 minutes (to join, or to rejoin after a dropped connection, as the tester page).
- Each start is logged in `app_session_starts` (uid, training, room, pass id, IP).

### 3.4 Errors

| Status | `error.code` | When |
|---|---|---|
| 401 | `invalid_pass` | pass missing, expired, or not valid: get a new pass from Wanaka |
| 403 | `inactive_employee` | the uid isn't an active employee |
| 403 | `not_assigned` | session start for a training that isn't in this employee's list |
| 404 | `not_found` | unknown training |
| 422 | `validation_error` | bad training id or body (the body takes only `start_over`) |
| 429 | `rate_limited` | 60 reads or 6 session starts a minute per employee (defaults) |
| 503 | `app_not_configured` / `sessions_unavailable` | pass keys or LiveKit not set on the server |

Body: `{"error": {"code": "...", "message": "...", "details": {}}}`, the API's usual shape. Never a database
message (the current procedures return `sql_state` and the database error to the app).

## 4. Assignments and catalog in nudgeai

### 4.1 Now: assigned by hand

`nudgeai.training_assignments` is the **only** source of who has which training. It feeds both the app and the
dashboard's "assigned" reports (D1). Until rules exist (4.2), the owner adds rows by hand, with a ready-made script
(`deploy/assign-training.sql`: assign, unassign, list for a uid).

- `assigned_at` is the real date it was assigned; `assigned_by` the owner's uid.
- Unassign = `status = 'cancelled'`, never a delete (reports keep history); re-assign sets it back.
- `matched_rule_group_id` / `matched_rule_name` / `ai_flag` are NULL for hand-made rows (the app gets `null`, a
  key it already handles).
- Unlike today's full-access rule, nobody sees every training automatically: it's whatever is assigned.

**Catalog** (D4): `trainings` already has `title` and `description`; additive columns: `app_title` (optional,
falls back to `title`), `category`, `tags`, `is_required`, `app_status` (`active` | `archived`),
`wanaka_trainer_id` (the old `trainer_id`, sent to the app as `trainer_id` so nothing in the app changes). A1
ships a short UPDATE script for the owner to fill them once; Phase 11's settings page edits them afterwards.

`training_assignments` gains (additive): `matched_rule_group_id`, `matched_rule_name`, `ai_flag`.

### 4.2 Later: rules (not in A1)

When wanted, the rules move to nudgeai (`assignment_rules`, `assignment_rule_uids`), and a DB-team procedure
evaluates them hourly into the same `training_assignments` table: upsert, cancel what no longer matches **except
anything started and not passed** (D5), runnable by hand, logged per run. Sales performance and CSAT flags stay in
Wanaka and are read in place (`wanaka.*`) by that procedure; the API never reads them. The fixes in Appendix A go
into it. Nothing in the app or the API changes when this arrives.

### 4.3 ElevenLabs trainings

None in the new path: the owner moves every ElevenLabs training to NudgeLab before go-live (D3). Until then the
new path is used by testers only, with NudgeLab trainings. NudgeLab never reads Wanaka.

## 5. Flutter app

Full guide for the app developer: `docs/FLUTTER_APP_GUIDE.md`. In short:

1. Right after login: `POST /v1/nudge/token` (Wanaka), keep the pass in memory with its expiry, and load the
   badge (`GET /app/v1/trainings/pending-count`) wherever the app loads it today (app start, resume, home).
   Before any `/app` call: if the pass expires within a minute, get a new one first; on a 401 from
   `nudgelabapi`, get a new pass and retry once. If Wanaka refuses the exchange too, the Wanaka session has
   ended: the app's existing re-login applies.
2. List and badge from `/app/v1/...`. Same JSON as today, plus the new keys.
3. Start: `POST .../session`, then join with the LiveKit Flutter SDK (`livekit_client`) using `server_url` and
   `participant_token`; microphone only. The ElevenLabs code path is removed at go-live.
4. Re-fetch the list after a session ends (the agent writes progress during the call).

## 6. Decisions (owner, 2026-10-03)

| # | Decision |
|---|---|
| D1 | **A table** (`training_assignments`) is the one source for the app and the reports. Filled by hand now; by an hourly rules procedure later (4.2). May change later. |
| D2 | CSAT flag cards (one card per flag for the same training): **later**, on the TODO list. Hand-made rows have no flag. |
| D3 | The owner **moves all ElevenLabs trainings to NudgeLab before go-live**, so the new path is NudgeLab only (no `engine` field, no Wanaka completions). Replaces the earlier `engine` field idea. |
| D4 | Catalog fields live on **`nudgeai.trainings`**; Phase 11 edits them. |
| D5 | A started training **stays until passed**, even if a rule stops matching (applies once rules exist). |

## 7. Phases (before Phase 11)

- **A1 — API (me):** migration (catalog columns, assignment columns, profile view), pass verification, the three
  endpoints, per-uid limits, audit of session starts, the assignment and catalog scripts for the owner, tests
  (authorization for every route; a pass for one uid can never read another's list), `openapi.json`. Deployed as
  usual. *Accept:* with a test pass and seeded data, the list matches the sample above field for field.
- **A2 — Owner:** assign testers (`deploy/assign-testers.sql`, can run now); fill the catalog columns after A1.
- **A3 — Wanaka (owner):** the key pair and `POST /v1/nudge/token`: `docs/WANAKA_NUDGE_TOKEN.md` is the brief for
  Claude in the Wanaka repo.
- **A4 — Flutter:** Section 5, released to testers first (the tester uids), then everyone.
- **A5 — Go-live:** ElevenLabs trainings moved (D3), the app's Nudge menu switched for everyone. Wanaka's old
  procedures and routes are left alone and cleaned up later by the owner; `nudgeapi`'s webhook and
  `ASSIGNMENT_SYNC.md` retire.
- **Later:** assignment rules (4.2), CSAT flag cards (D2).

## Appendix A. Issues in the current procedures (for the DB team)

Worth fixing in the new procedure; some may also be worth fixing in the old ones now.

1. **Missing data counts as a match.** `all_matched` is `MIN(...)` over the rule group's conditions, and `MIN`
   ignores NULLs. A NULL comparison is skipped instead of failing. So:
   - An employee with **no DSR row** (or a zero goal) **matches** any group that combines a PERFORMANCE condition
     with another condition, such as ROLE + PERFORMANCE. The comment says they should be excluded.
   - An employee with a NULL `market_name` / `region_name` / `job_title` (e.g. home office) matches a MARKET /
     REGION / EMPLOYEE_TYPE condition in a combined group.

   A group with a single PERFORMANCE condition is fine (`MIN(NULL)` is NULL, so `= 1` fails).
   *Fix:* `COALESCE(<condition>, 0)` inside the `MIN`.
2. **The badge undercounts.** The pending-count procedure has no `AI_FLAG` branch (no `user_csat_flags`), so every
   AI_FLAG rule falls into `ELSE 0` and never matches. Trainings assigned by a CSAT flag appear in the list but not
   in the count. The list also counts one card per flag, the count one per training.
3. **`assigned_at` is `NOW()`**, the time of the call, not when it was assigned.
4. **The CSAT flags come from `csat_review_case_ai_flag_test`**, which by its name is a test table.
5. `p_uid` is `INT` in one procedure and `VARCHAR(50)` in the other.
6. Errors return the raw database message (`sql_state`, `message`) to the app.

## Appendix B. Wanaka security (owner, separate from this project)

1. `POST /v1/login` and `POST /v1/token` issue a valid token from an email and uid, **with no password**. Anyone who
   knows an employee's email and uid can sign in as them.
2. `GET /v1/trainer-assignments/{uid}` doesn't check that the token's uid is the uid in the path, so any signed-in
   user can read anyone's assignments. (Unused after go-live; cleaned up later.)
