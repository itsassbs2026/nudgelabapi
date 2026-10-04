-- Phase 16 grants: publishing a version. Run once as an admin, AFTER `alembic upgrade head` (0010_publish_jobs).
-- Column-level, added to the studio's (db-grants-0006-studio.sql); tests/test_deploy.py checks them against the
-- code. No DELETE anywhere. The agent's grants don't change.

-- The version's topic and question rows (the reports read them), written once when it's first published.
GRANT INSERT ON nudgeai.training_topics TO 'nudgelab_api'@'10.0.1.148';
GRANT INSERT ON nudgeai.training_questions TO 'nudgelab_api'@'10.0.1.148';
-- Switching the live version.
GRANT UPDATE (active_version_id) ON nudgeai.trainings TO 'nudgelab_api'@'10.0.1.148';
-- Submit, send back, publish and retire.
GRANT UPDATE (status, published_at, published_by, notes) ON nudgeai.training_versions TO 'nudgelab_api'@'10.0.1.148';

-- Check:
-- SHOW GRANTS FOR 'nudgelab_api'@'10.0.1.148';
