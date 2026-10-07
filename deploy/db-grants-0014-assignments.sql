-- 2026-10-07: assigning training from the dashboard (one person, a CSV of uids, cancel, due dates) and
-- Admin > Permissions. Run once as an admin AFTER `alembic upgrade head` has applied 0014_dashboard_assignments.
--
-- training_assignments stops being read-only for the API (DECISIONS #117): it may add rows, and change only the
-- columns the code changes (re-activate, cancel, due date, who assigned it). Never DELETE: cancelling sets the
-- status. The owner's own database query keeps working as before; rows the dashboard made are tagged
-- assigned_via = 'dashboard' or 'upload'.

GRANT SELECT, INSERT, UPDATE ON nudgeai.dash_permissions TO 'nudgelab_api'@'10.0.1.148';
GRANT INSERT ON nudgeai.training_assignments TO 'nudgelab_api'@'10.0.1.148';
GRANT UPDATE (status, due_at, assigned_at, assigned_via, assigned_by_user_id) ON nudgeai.training_assignments TO 'nudgelab_api'@'10.0.1.148';

-- Check:
-- SHOW GRANTS FOR 'nudgelab_api'@'10.0.1.148';
