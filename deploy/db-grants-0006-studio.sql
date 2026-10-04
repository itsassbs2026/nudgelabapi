-- NudgeLab API: write access for the training studio (SPEC 8.3, Phase 11). Run once as an admin, AFTER
-- `alembic upgrade head` has applied 0006_version_editing (the version columns must exist).
--
-- The API can create trainings and draft versions and edit their settings and draft content. Column-level
-- UPDATE keeps the database itself from letting the API change what the voice agent runs:
--   trainings.active_version_id     not granted (publishing is Phase 16)
--   training_versions.status        not granted (submit / publish / retire are Phase 16)
-- No DELETE anywhere. tests/test_deploy.py checks these lists against the code.

GRANT INSERT ON nudgeai.trainings TO 'nudgelab_api'@'10.0.1.148';
GRANT UPDATE (title, status, completion_type, uses_location, completion_key, profile_id, app_title, category, description, tags, is_required, app_status) ON nudgeai.trainings TO 'nudgelab_api'@'10.0.1.148';

GRANT INSERT ON nudgeai.training_versions TO 'nudgelab_api'@'10.0.1.148';
GRANT UPDATE (content, revision, updated_at, updated_by) ON nudgeai.training_versions TO 'nudgelab_api'@'10.0.1.148';

-- Check:
-- SHOW GRANTS FOR 'nudgelab_api'@'10.0.1.148';
