# Suncom: the database (2026-11-03)

Suncom's NudgeLab database is `nudgeai` on Suncom's RDS server `database-1`
(`database-1.clxw6d5bqw8i.us-east-2.rds.amazonaws.com`, account 013039979168, us-east-2), next to Suncom's
Portal database `suncomportal`. The servers are in the GreenTech account (us-east-1) and reach it through their
Elastic IPs, which the RDS security group allows:

| Server | Elastic IP | Database logins |
|---|---|---|
| suncom-nudgelab-web (API + dashboard) | 100.59.145.97 | `nudgelab_api`, `nudgelab_api_migrate`, `nudgelab_sync`, `nudgelab_portal_ro` |
| suncom-nudgelab-main | 34.239.75.17 | `nudgeai_agent` |
| suncom-nudgelab-worker-1 | 54.161.141.244 | `nudgeai_agent` (same password) |

Every login has `REQUIRE SSL`: it can't connect without TLS, and nothing else on `database-1` is affected. The
logins are the same as Prime's, with the same grants (`build_grants.py` builds them from Prime's deploy files;
checked against Prime's live agent login on 2026-11-03). Moving `nudgeai` to us-east-1 later only changes the
host in the settings and the logins.

Run every `.sql` file as the RDS master user, e.g. `mysql -h <endpoint> -u <master> -p < 01-logins.sql`, or paste
it at the `mysql>` prompt. Never put the passwords in a file you keep or send them in chat or email.

## Order

1. **`01-logins.sql`** (you). First replace the five `<..._PASSWORD>` placeholders with strong generated
   passwords (`AGENT` appears twice: the same one for both agent hosts) and keep them for the servers' settings.
   `nudgeai` must exist and be empty (`CREATE DATABASE nudgeai CHARACTER SET utf8mb4;` if it doesn't).
   Check: `SELECT user, host, ssl_type FROM mysql.user WHERE user LIKE 'nudge%';` shows 6 rows with `ANY`.
2. **`02-portal-completions-table.sql`** (you): creates `suncomportal.suncom_nudge_ai_completions` (where passes
   go, so Portal shows the class completed), unless it already exists.
3. **Migrations** (on the web server, once the API is installed there): `venv/bin/alembic upgrade head` with
   `DATABASE_MIGRATION_URL` set to the `nudgelab_api_migrate` login. On an empty `nudgeai` it creates every
   table (0001 to 0024). `venv/bin/alembic current` then shows `0024_app_handoffs (head)`.
4. **`03-grants.sql`** (you): the 136 grants on those tables and on `suncomportal`.
5. **`04-seed-voices-setups.sql`** (you): Prime's 9 voices (Tiffany the default) and 3 setups (Standard the
   default). Change them later on Admin > Voices and Admin > Setups.

Tested on 2026-11-03 against an empty local database in this order: every statement ran.

## Portal views

The reference sync copies `v_users_all`, `v_users`, `v_stores` and `v_stores_all` from `suncomportal` into
`nudgeai` (names, stores, managers). Its `--dry-run` reports any view that's missing or has different columns
from Prime's.
