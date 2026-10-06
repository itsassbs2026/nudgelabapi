-- 2026-10-06: logins for the PortalLive reference-table sync (scripts/sync_reference_tables.py). Run as an admin,
-- AFTER `alembic upgrade head` has applied 0012_sync_run_log and AFTER deploy/reference-tables-utf8mb4.sql.
-- Type the passwords yourself, then put them (URL-encoded) in the API server's .env:
--   PORTALLIVE_DATABASE_URL=mysql+pymysql://nudgelab_portal_ro:<password>@<PortalLive host>:3306/primetwok?charset=utf8mb4
--   REFERENCE_SYNC_DATABASE_URL=mysql+pymysql://nudgelab_sync:<password>@<nudgeai host>:3306/nudgeai?charset=utf8mb4
--   REFERENCE_SYNC_ALERT_EMAIL=nudgeai@primecomms.com

-- Part 1, on the NUDGEAI database: the sync's login. It can read and replace the four tables and write its log,
-- nothing else. The host is the API server as nudgeai sees it (the same as nudgelab_api's).
CREATE USER 'nudgelab_sync'@'10.0.1.148' IDENTIFIED BY '<a new password>';
GRANT SELECT, INSERT, DELETE ON nudgeai.v_users_all TO 'nudgelab_sync'@'10.0.1.148';
GRANT SELECT, INSERT, DELETE ON nudgeai.v_users TO 'nudgelab_sync'@'10.0.1.148';
GRANT SELECT, INSERT, DELETE ON nudgeai.v_stores TO 'nudgelab_sync'@'10.0.1.148';
GRANT SELECT, INSERT, DELETE ON nudgeai.v_stores_all TO 'nudgelab_sync'@'10.0.1.148';
GRANT SELECT, INSERT ON nudgeai.sync_run_log TO 'nudgelab_sync'@'10.0.1.148';

-- The API reads the log (for an Admin view later); it still has no access to the four tables themselves.
GRANT SELECT ON nudgeai.sync_run_log TO 'nudgelab_api'@'10.0.1.148';

-- Check:
-- SHOW GRANTS FOR 'nudgelab_sync'@'10.0.1.148';

-- Part 2, on the PORTALLIVE database (primetwok, its own server): a read-only login on exactly the four sources.
-- Replace <API server IP> with the API server's address as PortalLive sees it, and allow that address through
-- PortalLive's security group on 3306.
CREATE USER 'nudgelab_portal_ro'@'<API server IP>' IDENTIFIED BY '<another new password>';
GRANT SELECT ON primetwok.v_users_all TO 'nudgelab_portal_ro'@'<API server IP>';
GRANT SELECT ON primetwok.v_users TO 'nudgelab_portal_ro'@'<API server IP>';
GRANT SELECT ON primetwok.v_stores TO 'nudgelab_portal_ro'@'<API server IP>';
GRANT SELECT ON primetwok.v_stores_all TO 'nudgelab_portal_ro'@'<API server IP>';

-- Check:
-- SHOW GRANTS FOR 'nudgelab_portal_ro'@'<API server IP>';
