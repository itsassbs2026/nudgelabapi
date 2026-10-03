-- NudgeLab API: grants for migration 0005 (the Flutter app's endpoints, docs/APP_HANDOFF.md).
--
-- Run once as an admin, AFTER `alembic upgrade head` has applied 0005_app_handoff (it creates both objects).
-- These are the only new privileges: the profile view (read) and the app's session log (append-only).
-- The view runs with its definer's rights, so the API login still has no access to v_users itself.

GRANT SELECT         ON nudgeai.vw_app_profile     TO 'nudgelab_api'@'10.0.1.148';
GRANT SELECT, INSERT ON nudgeai.app_session_starts TO 'nudgelab_api'@'10.0.1.148';

-- Check:
-- SHOW GRANTS FOR 'nudgelab_api'@'10.0.1.148';
