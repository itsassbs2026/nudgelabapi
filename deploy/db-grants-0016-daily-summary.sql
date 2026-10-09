-- The daily summary email (2026-10-09). Run once as the RDS admin (master) login, AFTER `alembic upgrade head`
-- has applied 0016_daily_summary. The API manages the recipient list and records each day's send.

GRANT SELECT, INSERT, UPDATE, DELETE ON nudgeai.dash_summary_recipients TO 'nudgelab_api'@'10.0.1.148';
GRANT SELECT, INSERT ON nudgeai.dash_summary_runs TO 'nudgelab_api'@'10.0.1.148';
