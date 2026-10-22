-- Reset a person's training progress (migration 0023_progress_reset, 2026-10-22).
-- Run as the RDS master login, AFTER `alembic upgrade head` has created portal_clears.
--
-- The API resets progress (archive to the history table, clear the progress row) and queues a request to clear the
-- person's Prime Portal completion. The agent (main server) carries the request out. Until these are run the
-- reset button fails with a database permission error and changes nothing.

-- API login: change progress (only these columns), keep the archive, queue Portal clears.
GRANT UPDATE (status, topics_covered, walkthrough_finished_at, quiz_attempted, correct_questions, quiz_draw,
              first_started_at, passed_at, version_id)
    ON nudgeai.training_progress TO 'nudgelab_api'@'10.0.1.148';
GRANT INSERT ON nudgeai.training_progress_history TO 'nudgelab_api'@'10.0.1.148';
GRANT SELECT, INSERT ON nudgeai.portal_clears TO 'nudgelab_api'@'10.0.1.148';

-- Agent logins: read the pending clears and mark them done (the main server runs the job; the others get the
-- same so any of them can).
GRANT SELECT, UPDATE (status, attempts, done_at, error) ON nudgeai.portal_clears TO 'nudgeai_agent'@'35.173.142.66';
GRANT SELECT, UPDATE (status, attempts, done_at, error) ON nudgeai.portal_clears TO 'nudgeai_agent'@'44.221.150.77';
GRANT SELECT, UPDATE (status, attempts, done_at, error) ON nudgeai.portal_clears TO 'nudgeai_agent'@'18.204.20.139';
GRANT SELECT, UPDATE (status, attempts, done_at, error) ON nudgeai.portal_clears TO 'nudgeai_agent'@'34.200.118.174';
GRANT SELECT, UPDATE (status, attempts, done_at, error) ON nudgeai.portal_clears TO 'nudgeai_agent'@'54.82.245.163';

-- Prime Portal's database (not nudgeai): the agent's Portal login must be able to delete a completion row, or the
-- clear fails with a permission error (the pending request keeps its error and retries). Run on the Portal DB:
--   GRANT DELETE ON <portal database>.prime_nudge_ai_completions TO '<the agent's Portal login>'@'<host>';
-- Check what it has now with: SHOW GRANTS FOR '<the agent's Portal login>'@'<host>';

SHOW GRANTS FOR 'nudgelab_api'@'10.0.1.148';
