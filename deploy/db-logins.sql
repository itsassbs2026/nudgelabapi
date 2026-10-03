-- NudgeLab API: database logins on the nudgeai database (run once by an admin, before Phase 9).
--
-- Before running:
--   1. Replace both CHANGE_ME passwords with strong generated ones (e.g. 32 random characters). Don't reuse them,
--      don't send them in chat or email. They go only into /srv/nudgelabapi/.env on the pingitapi server.
--   2. HOST: '10.0.1.148' is the pingitapi server's private address, which is how it reaches RDS (found by the
--      preflight on 2026-10-03; this file first used the public IP, 204.236.179.185, which RDS never sees). An EC2
--      instance keeps its private address across stop/start. Logins created with the old host are renamed with
--      deploy/db-logins-fix-host.sql.
--
-- The grants on the API's own tables come later, after the migration creates them: deploy/db-grants-api-tables.sql.

-- ---------------------------------------------------------------------------
-- 1. The running API: read-only on the agent's data. No access to v_users* (personal data) or Wanaka/Portal.
-- ---------------------------------------------------------------------------
CREATE USER 'nudgelab_api'@'10.0.1.148' IDENTIFIED BY 'CHANGE_ME_API_PASSWORD';

GRANT SELECT ON nudgeai.trainings                 TO 'nudgelab_api'@'10.0.1.148';
GRANT SELECT ON nudgeai.training_versions         TO 'nudgelab_api'@'10.0.1.148';
GRANT SELECT ON nudgeai.training_topics           TO 'nudgelab_api'@'10.0.1.148';
GRANT SELECT ON nudgeai.training_questions        TO 'nudgelab_api'@'10.0.1.148';
GRANT SELECT ON nudgeai.training_progress         TO 'nudgelab_api'@'10.0.1.148';
GRANT SELECT ON nudgeai.training_sessions         TO 'nudgelab_api'@'10.0.1.148';
GRANT SELECT ON nudgeai.session_transcripts       TO 'nudgelab_api'@'10.0.1.148';
GRANT SELECT ON nudgeai.session_topic_events      TO 'nudgelab_api'@'10.0.1.148';
GRANT SELECT ON nudgeai.session_usage             TO 'nudgelab_api'@'10.0.1.148';
GRANT SELECT ON nudgeai.session_issues            TO 'nudgelab_api'@'10.0.1.148';
GRANT SELECT ON nudgeai.session_reviews           TO 'nudgelab_api'@'10.0.1.148';
GRANT SELECT ON nudgeai.daily_review_reports      TO 'nudgelab_api'@'10.0.1.148';
GRANT SELECT ON nudgeai.quiz_answers              TO 'nudgelab_api'@'10.0.1.148';
GRANT SELECT ON nudgeai.training_feedback         TO 'nudgelab_api'@'10.0.1.148';
GRANT SELECT ON nudgeai.training_acknowledgments  TO 'nudgelab_api'@'10.0.1.148';
GRANT SELECT ON nudgeai.training_assignments      TO 'nudgelab_api'@'10.0.1.148';
GRANT SELECT ON nudgeai.completion_writes         TO 'nudgelab_api'@'10.0.1.148';
GRANT SELECT ON nudgeai.training_voices           TO 'nudgelab_api'@'10.0.1.148';
GRANT SELECT ON nudgeai.training_profiles         TO 'nudgelab_api'@'10.0.1.148';
GRANT SELECT ON nudgeai.store_location_types      TO 'nudgelab_api'@'10.0.1.148';
-- Views: these join the org hierarchy and trainee names without exposing v_users* columns.
GRANT SELECT ON nudgeai.vw_trainees               TO 'nudgelab_api'@'10.0.1.148';
GRANT SELECT ON nudgeai.vw_training_stores        TO 'nudgelab_api'@'10.0.1.148';
GRANT SELECT ON nudgeai.vw_session_report         TO 'nudgelab_api'@'10.0.1.148';
GRANT SELECT ON nudgeai.vw_question_stats         TO 'nudgelab_api'@'10.0.1.148';
GRANT SELECT ON nudgeai.vw_assignment_status      TO 'nudgelab_api'@'10.0.1.148';
-- Added by migration 0005 (the Flutter app); on an existing install run deploy/db-grants-0005-app.sql instead.
GRANT SELECT ON nudgeai.vw_app_profile            TO 'nudgelab_api'@'10.0.1.148';

-- ---------------------------------------------------------------------------
-- 2. Migrations only (Alembic, run by hand during a deploy; never used by the running app).
--    No DROP: a migration can add and change, but can't drop tables. Anything destructive is done by an admin.
-- ---------------------------------------------------------------------------
CREATE USER 'nudgelab_api_migrate'@'10.0.1.148' IDENTIFIED BY 'CHANGE_ME_MIGRATE_PASSWORD';

GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, REFERENCES, CREATE VIEW, SHOW VIEW
    ON nudgeai.* TO 'nudgelab_api_migrate'@'10.0.1.148';

-- Part 3, grants on the API's own tables, is in deploy/db-grants-api-tables.sql: run it after
-- `alembic upgrade head` has created those tables (deploy/README.md, step 5).
