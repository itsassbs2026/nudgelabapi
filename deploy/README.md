# Deploying NudgeLab Stage 1 (dashboard + API)

The whole go-live, in order (SPEC §14, Phase 9). Two servers and one database:

| What | Where | Address |
|---|---|---|
| API (`nudgelabapi`) | pingitapi server, `204.236.179.185`, next to pingitapi (8001) and joynapi (8000) | `https://nudgelabapi.myprimeportal.com`, port **8002** |
| Dashboard (`nudgelabdashboard`) | pingit frontend server, next to pingit | `https://nudgelab.myprimeportal.com` |
| Database | `nudgeai` on the production RDS (the voice agent writes to it all day) | — |

The voice agent (its own EC2 server) is not touched by any of this.

Code reaches the servers only with `git pull` (SPEC §14.0). Each step says how to check it worked. Every
script here that only reads (`preflight-check.sh`, `scripts/preflight.py`) is safe to run any time.

**Production database rules** (SPEC §0.4): never run the test suite, `alembic downgrade`, `alembic stamp base`
or `scripts/e2e_seed.py` against `nudgeai` (the test and seed scripts refuse anything that isn't a local test
database anyway). The only schema change in this deploy is step 6, which only **adds** the API's own tables.

---

## Before you start (you)

1. **Settled:** the role and the bucket are both in account 825245835842 (no bucket policy needed), and the
   server reaches RDS from its private address **10.0.1.148**, which the logins use.
2. **DNS (Route 53)**:
   - `nudgelabapi.myprimeportal.com` → same target as `pingitapi.myprimeportal.com`.
   - `nudgelab.myprimeportal.com` → same target as `pingit.myprimeportal.com`.
   - Check: `dig +short nudgelabapi.myprimeportal.com` and `dig +short nudgelab.myprimeportal.com` return those
     servers.
3. **GitHub deploy keys** (read-only), one per repo, like pingit's: on each server,
   `ssh-keygen -t ed25519 -f ~/.ssh/nudgelabapi_deploy -N ""` (or `nudgelabdashboard_deploy`), add the `.pub` file
   under GitHub → repo → Settings → Deploy keys (read-only), and add a host alias to `~/.ssh/config`:
   ```
   Host github-nudgelabapi
       HostName github.com
       User git
       IdentityFile ~/.ssh/nudgelabapi_deploy
       IdentitiesOnly yes
   ```
   Check: `ssh -T github-nudgelabapi` says "successfully authenticated".

## 1. Database logins (you, on RDS)

If not done yet: open `deploy/db-logins.sql`, replace both `CHANGE_ME` passwords with strong generated ones, and
run parts 1 and 2. Keep the passwords for the `.env` in step 4 only. If you created the logins with the earlier
version of the file (host `204.236.179.185`), run `deploy/db-logins-fix-host.sql` to move them to `10.0.1.148`.

Check: `SELECT user, host FROM mysql.user WHERE user LIKE 'nudgelab%';` shows `nudgelab_api` and
`nudgelab_api_migrate`.

## 2. IAM (you, in the AWS console)

Add `deploy/iam-policy-stage1.json` as an inline policy on `Prime-nudgeapi-ec2-role` (it can only read
`nudgeailab/recordings/*`, nothing else).

If the bucket is in **another** AWS account, also add this to the bucket's policy (with that role's ARN):
```json
{
  "Sid": "NudgeLabDashboardPlayRecordings",
  "Effect": "Allow",
  "Principal": { "AWS": "arn:aws:iam::ROLE_ACCOUNT_ID:role/Prime-nudgeapi-ec2-role" },
  "Action": "s3:GetObject",
  "Resource": "arn:aws:s3:::nudgeailab/recordings/*"
}
```
Step 4's preflight checks it ("recordings: can read the newest recording").

## 3. The API server: code and settings (you, on 204.236.179.185, as ubuntu)

```bash
sudo mkdir -p /srv/nudgelabapi && sudo chown ubuntu:ubuntu /srv/nudgelabapi
git clone github-nudgelabapi:itsassbs2026/nudgelabapi.git /srv/nudgelabapi
cd /srv/nudgelabapi

python3.12 -m venv venv                 # the lock file targets Python 3.12
venv/bin/pip install -r requirements.lock

sudo mkdir -p /var/lib/nudgelabapi/exports && sudo chown -R ubuntu:ubuntu /var/lib/nudgelabapi
```

If `python3.12` is missing: `sudo apt install python3.12 python3.12-venv` (from the deadsnakes PPA on older
Ubuntu).

## 4. Settings (you)

```bash
cp deploy/env.production.example .env
chmod 600 .env
nano .env        # fill in every FILL; GRAPH_* from pingit's .env; LIVEKIT_* from the agent server
bash deploy/preflight-check.sh
```

At this point the preflight should pass everything except: the API tables (WARN, created in step 6), the
migrations line, the systemd units and the nginx site (not installed yet). **Fix any FAIL before going on**,
especially `database`, `read …` and `recordings`.

## 5. Migrations: check where the database is (you)

```bash
venv/bin/alembic current
```

It uses `DATABASE_MIGRATION_URL`. One of three answers:

| Answer | Meaning | Do |
|---|---|---|
| `0001_baseline` | The baseline was stamped earlier (Phase 1). | Go to step 6. |
| nothing (or "alembic_version doesn't exist") | Never stamped. | First `venv/bin/python scripts/check_baseline.py` (read-only): it must end with "OK to stamp". Then `venv/bin/alembic stamp 0001_baseline`, and `alembic current` shows `0001_baseline`. **Stamp, never `upgrade`, for the baseline**: it only records that the agent's existing tables are there. |
| `0003_jobs (head)` | Already migrated. | Skip step 6. |

## 6. Migrations: add the API's tables (you)

Review what will run first (prints SQL, executes nothing):

```bash
venv/bin/alembic upgrade 0001_baseline:head --sql
```

You should see only `CREATE TABLE` for `dash_users`, `dash_refresh_tokens`, `dash_password_reset_tokens`,
`dash_audit_log`, `dash_email_outbox`, `review_queue`, `saved_views` and `jobs` (plus their indexes and the
`alembic_version` update). Nothing alters or drops an existing table. Then:

```bash
venv/bin/alembic upgrade head
venv/bin/alembic current          # 0003_jobs (head)
```

## 7. Grants on the new tables, and the first Admin (you)

Run `deploy/db-grants-api-tables.sql` on RDS (same host pattern as step 1). Then:

```bash
venv/bin/python -m scripts.bootstrap_admin     # creates bgupta@primecomms.com with the temporary password
nano .env                                      # delete the BOOTSTRAP_ADMIN_PASSWORD line
bash deploy/preflight-check.sh                 # API tables now PASS
```

## 8. Services and nginx (you)

```bash
sudo cp deploy/nudgelabapi.service deploy/nudgelabapi-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now nudgelabapi nudgelabapi-worker
sudo journalctl -u nudgelabapi-worker -n 20     # expect worker_starting

grep ssl_certificate /etc/nginx/sites-available/pingitapi   # same paths as deploy/nginx-nudgelabapi.conf?
sudo cp deploy/nginx-nudgelabapi.conf /etc/nginx/sites-available/nudgelabapi
sudo ln -s /etc/nginx/sites-available/nudgelabapi /etc/nginx/sites-enabled/nudgelabapi
sudo nginx -t && sudo systemctl reload nginx

curl https://nudgelabapi.myprimeportal.com/api/v1/health      # {"status":"ok","db":"ok","version":"…"}
bash deploy/preflight-check.sh                                 # no FAIL
```

## 9. The dashboard (you, on the pingit frontend server)

Follow `nudgelabdashboard/deploy/README.md` (clone, `.env`, `deploy.sh`, nginx). Its preflight checks that the
API answers and allows the dashboard's origin.

## 10. Smoke test: Stage 1 is live when all of these pass (you, then me)

1. `https://nudgelab.myprimeportal.com` → sign in as `bgupta@primecomms.com` with the temporary password →
   asked to set a new one → Overview.
2. The Overview shows real numbers. Compare **Sessions** for "Last 7 days" with what you know of the testers'
   week (or I check it against the agent's logs).
3. Sessions → open a recent session with a recording → press play: it plays, and clicking a transcript line
   jumps there. (This is the first real test of the IAM policy.)
4. Trainings → The Big 4 → the funnel and most-missed questions look right.
5. Export any table as CSV and as Excel (Excel checks the worker and `EXPORT_DIR`).
6. Admin → Audit log shows your sign-in, the session you opened, the recording you played and the exports.
7. Admin → Users → create a Trainer with a temporary password; they sign in and must change it.
8. "Live now" on the Overview shows a test call while one is running (if `LIVEKIT_*` are set).

Then I tag the release in both repos (`v1.0.0`) so it can be rolled back to.

---

## Redeploying (after a change)

```bash
bash /srv/nudgelabapi/deploy/deploy.sh          # pull main, install, restart, health check
```

It stops, without restarting anything, if the release adds migrations: review them
(`venv/bin/alembic upgrade <current>:head --sql`), run `venv/bin/alembic upgrade head`, add grants for any new
table, then run `deploy.sh` again.

**Rollback:** `bash deploy/deploy.sh v1.0.0` (any tag). If the bad release had a migration, ask before going
back: migrations only add things, so older code normally runs fine on the newer schema.

## Phase A1: the Flutter app's endpoints (docs/APP_HANDOFF.md)

One migration (`0005_app_handoff`), two new grants, one new setting. Nothing changes for the dashboard or the agent.

1. `bash /srv/nudgelabapi/deploy/deploy.sh`: it pulls and stops at the pending migration.
2. Review it: `venv/bin/alembic upgrade 0004_training_content:0005_app_handoff --sql`. It adds columns to
   `trainings` and `training_assignments` (instant on MySQL 8), creates the view `vw_app_profile` and the table
   `app_session_starts`.
3. Run it at a quiet moment (the dashboard's "Live now" empty, so no agent session is mid-write):
   `venv/bin/alembic upgrade head`.
4. As an admin on RDS: `deploy/db-grants-0005-app.sql`.
5. `bash /srv/nudgelabapi/deploy/deploy.sh` again: restarts and checks health. The preflight shows
   `WARN app passes` until step 6; the app endpoints answer 503 meanwhile.
6. When Wanaka's key pair exists (docs/WANAKA_NUDGE_TOKEN.md): put the **public** key at
   `/srv/nudgelabapi/keys/nudge_pass_public.pem`, add `APP_PASS_PUBLIC_KEYS=<kid>=/srv/nudgelabapi/keys/nudge_pass_public.pem`
   to `.env`, `sudo systemctl restart nudgelabapi`, and run the preflight: `PASS app passes`.
7. Data (any time after step 3, as an admin): `deploy/app-catalog.sql` (names, categories, required), then
   `deploy/assign-testers.sql`.

Smoke test:
- `curl -s https://nudgelabapi.myprimeportal.com/app/v1/trainings` → 401 `invalid_pass` (503 before step 6).
- After Wanaka's endpoint is live: a tester's app (or a pass from `POST /v1/nudge/token`) lists their trainings,
  and starting one connects to Anne.

## Phase 11: training studio API

One migration (`0006_version_editing`), one grant file. No change to the agent or to what it runs.

1. `bash /srv/nudgelabapi/deploy/deploy.sh`: pulls, stops at the pending migration.
2. Review: `venv/bin/alembic upgrade 0005_app_handoff:0006_version_editing --sql` (4 `ADD COLUMN` on
   `training_versions`, instant on MySQL 8). Then, with "Live now" empty: `venv/bin/alembic upgrade head`.
3. As an admin on RDS: `deploy/db-grants-0006-studio.sql`, then `SHOW GRANTS FOR 'nudgelab_api'@'10.0.1.148';`.
4. `bash /srv/nudgelabapi/deploy/deploy.sh` again, then the preflight: `PASS studio grants`.

## Trainer persona, step 1 (API)

One migration (`0007_trainer_persona`, three nullable columns), no new grants (the API already inserts into its
own `app_session_starts`; `training_sessions.trainer_name` is the agent's, step 2).

1. `bash deploy/deploy.sh` (stops at the migration) → review `venv/bin/alembic upgrade 0006_version_editing:0007_trainer_persona --sql`
   → with "Live now" empty, `venv/bin/alembic upgrade head` → `bash deploy/deploy.sh` → preflight.

## Where things are

| | |
|---|---|
| Logs | `journalctl -u nudgelabapi -f`, `journalctl -u nudgelabapi-worker -f`, `/var/log/nginx/nudgelabapi-*.log` |
| Settings | `/srv/nudgelabapi/.env` (mode 600) |
| Exports | `/var/lib/nudgelabapi/exports` (deleted after 24 hours by the worker) |
| Restart | `sudo systemctl restart nudgelabapi nudgelabapi-worker` |
| Health | `curl https://nudgelabapi.myprimeportal.com/api/v1/health` |
