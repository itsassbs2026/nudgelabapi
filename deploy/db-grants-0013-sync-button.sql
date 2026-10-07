-- 2026-10-06: the dashboard's "Sync user/store list from Portal" (Admin only, confirmed by an emailed code).
-- Run once as an admin AFTER `alembic upgrade head` has applied 0013_manual_reference_sync.
-- The sync itself runs in the worker with the sync's own logins (deploy/db-grants-0012-reference-sync.sql);
-- the API's login only stores the one-time codes (never in clear). No DELETE.

GRANT SELECT, INSERT, UPDATE ON nudgeai.dash_action_codes TO 'nudgelab_api'@'10.0.1.148';

-- Check:
-- SHOW GRANTS FOR 'nudgelab_api'@'10.0.1.148';
