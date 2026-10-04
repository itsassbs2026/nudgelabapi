-- NudgeLab API: grant for migration 0008 (training content uploads, Phase 12). Run once as an admin, AFTER
-- `alembic upgrade head` has created the table. No DELETE: uploads are kept with their extracted text.

GRANT SELECT, INSERT, UPDATE ON nudgeai.content_uploads TO 'nudgelab_api'@'10.0.1.148';

-- Check:
-- SHOW GRANTS FOR 'nudgelab_api'@'10.0.1.148';
