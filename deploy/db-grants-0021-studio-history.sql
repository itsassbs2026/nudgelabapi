-- Studio autosave and version history (2026-10-22). Run once as the RDS admin (master) login, AFTER
-- `alembic upgrade head` has applied 0021_studio_history. The API keeps earlier saves of a draft, and trims the
-- oldest (DELETE) beyond studio.history_keep. Until this runs, saving a draft still works; history just isn't kept.

GRANT SELECT, INSERT, DELETE ON nudgeai.dash_version_history TO 'nudgelab_api'@'10.0.1.148';

-- Check:
-- SHOW GRANTS FOR 'nudgelab_api'@'10.0.1.148';
