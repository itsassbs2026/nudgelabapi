-- Training in the employee portal (migration 0024_app_handoffs, 2026-10-29).
-- Run as the RDS master login, AFTER `alembic upgrade head` has created app_handoffs.
--
-- The API stores a one-time code when the portal asks for one, and marks it used when the training page redeems
-- it. Until this is run, Start in the portal fails with a database permission error.

GRANT SELECT, INSERT, UPDATE (used_at) ON nudgeai.app_handoffs TO 'nudgelab_api'@'10.0.1.148';

SHOW GRANTS FOR 'nudgelab_api'@'10.0.1.148';
