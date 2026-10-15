-- What's new (2026-10-15). Run once as the RDS admin (master) login, AFTER `alembic upgrade head` has applied
-- 0018_announcements_seen. The API records which announcement tours each dashboard user has seen.

GRANT SELECT, INSERT ON nudgeai.dash_announcements_seen TO 'nudgelab_api'@'10.0.1.148';
