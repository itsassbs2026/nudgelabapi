-- NudgeLab API: database logins on the nudgeai database (run once by an admin, before Phase 9).
--
-- Before running:
--   1. Replace both CHANGE_ME passwords with strong generated ones (e.g. 32 random characters). Don't reuse them,
--      don't send them in chat or email. They go only into /srv/nudgelabapi/.env on the pingitapi server.
--   2. HOST: '204.236.179.185' is the pingitapi server's public IP. If pingit's own login connects with a different
--      host pattern (e.g. a private 10.x / 172.x address because the server is in the same VPC as RDS), use the same
--      pattern here. Check with:  SELECT user, host FROM mysql.user WHERE user LIKE 'pingit%';
--
-- Part 2 (the API's own tables) can only run after Phase 9 creates them; it's at the bottom, commented out.

-- ---------------------------------------------------------------------------
-- 1. The running API: read-only on the agent's data. No access to v_users* (personal data) or Wanaka/Portal.
-- ---------------------------------------------------------------------------
CREATE USER 'nudgelab_api'@'204.236.179.185' IDENTIFIED BY 'CHANGE_ME_API_PASSWORD';

GRANT SELECT ON nudgeai.trainings                 TO 'nudgelab_api'@'204.236.179.185';
GRANT SELECT ON nudgeai.training_versions         TO 'nudgelab_api'@'204.236.179.185';
GRANT SELECT ON nudgeai.training_topics           TO 'nudgelab_api'@'204.236.179.185';
GRANT SELECT ON nudgeai.training_questions        TO 'nudgelab_api'@'204.236.179.185';
GRANT SELECT ON nudgeai.training_progress         TO 'nudgelab_api'@'204.236.179.185';
GRANT SELECT ON nudgeai.training_sessions         TO 'nudgelab_api'@'204.236.179.185';
GRANT SELECT ON nudgeai.session_transcripts       TO 'nudgelab_api'@'204.236.179.185';
GRANT SELECT ON nudgeai.session_topic_events      TO 'nudgelab_api'@'204.236.179.185';
GRANT SELECT ON nudgeai.session_usage             TO 'nudgelab_api'@'204.236.179.185';
GRANT SELECT ON nudgeai.session_issues            TO 'nudgelab_api'@'204.236.179.185';
GRANT SELECT ON nudgeai.session_reviews           TO 'nudgelab_api'@'204.236.179.185';
GRANT SELECT ON nudgeai.daily_review_reports      TO 'nudgelab_api'@'204.236.179.185';
GRANT SELECT ON nudgeai.quiz_answers              TO 'nudgelab_api'@'204.236.179.185';
GRANT SELECT ON nudgeai.training_feedback         TO 'nudgelab_api'@'204.236.179.185';
GRANT SELECT ON nudgeai.training_acknowledgments  TO 'nudgelab_api'@'204.236.179.185';
GRANT SELECT ON nudgeai.training_assignments      TO 'nudgelab_api'@'204.236.179.185';
GRANT SELECT ON nudgeai.completion_writes         TO 'nudgelab_api'@'204.236.179.185';
GRANT SELECT ON nudgeai.training_voices           TO 'nudgelab_api'@'204.236.179.185';
GRANT SELECT ON nudgeai.training_profiles         TO 'nudgelab_api'@'204.236.179.185';
GRANT SELECT ON nudgeai.store_location_types      TO 'nudgelab_api'@'204.236.179.185';
-- Views: these join the org hierarchy and trainee names without exposing v_users* columns.
GRANT SELECT ON nudgeai.vw_trainees               TO 'nudgelab_api'@'204.236.179.185';
GRANT SELECT ON nudgeai.vw_training_stores        TO 'nudgelab_api'@'204.236.179.185';
GRANT SELECT ON nudgeai.vw_session_report         TO 'nudgelab_api'@'204.236.179.185';
GRANT SELECT ON nudgeai.vw_question_stats         TO 'nudgelab_api'@'204.236.179.185';
GRANT SELECT ON nudgeai.vw_assignment_status      TO 'nudgelab_api'@'204.236.179.185';

-- ---------------------------------------------------------------------------
-- 2. Migrations only (Alembic, run by hand during a deploy; never used by the running app).
--    No DROP: a migration can add and change, but can't drop tables. Anything destructive is done by an admin.
-- ---------------------------------------------------------------------------
CREATE USER 'nudgelab_api_migrate'@'204.236.179.185' IDENTIFIED BY 'CHANGE_ME_MIGRATE_PASSWORD';

GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, REFERENCES, CREATE VIEW, SHOW VIEW
    ON nudgeai.* TO 'nudgelab_api_migrate'@'204.236.179.185';

-- ---------------------------------------------------------------------------
-- 3. After Phase 9 creates the API's own tables, run this (uncomment):
-- ---------------------------------------------------------------------------
-- GRANT SELECT, INSERT, UPDATE, DELETE ON nudgeai.dash_users                 TO 'nudgelab_api'@'204.236.179.185';
-- GRANT SELECT, INSERT, UPDATE, DELETE ON nudgeai.dash_refresh_tokens        TO 'nudgelab_api'@'204.236.179.185';
-- GRANT SELECT, INSERT, UPDATE, DELETE ON nudgeai.dash_password_reset_tokens TO 'nudgelab_api'@'204.236.179.185';
-- GRANT SELECT, INSERT                 ON nudgeai.dash_audit_log             TO 'nudgelab_api'@'204.236.179.185';
-- GRANT SELECT, INSERT, UPDATE, DELETE ON nudgeai.dash_email_outbox          TO 'nudgelab_api'@'204.236.179.185';
-- GRANT SELECT, INSERT, UPDATE, DELETE ON nudgeai.review_queue               TO 'nudgelab_api'@'204.236.179.185';
-- GRANT SELECT, INSERT, UPDATE, DELETE ON nudgeai.saved_views                TO 'nudgelab_api'@'204.236.179.185';
-- GRANT SELECT, INSERT, UPDATE         ON nudgeai.jobs                       TO 'nudgelab_api'@'204.236.179.185';
