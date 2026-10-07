-- NudgeLab API: the running API's access to its own tables (run once by an admin, deploy/README.md step 5).
--
-- Run AFTER `alembic upgrade head` has created these tables, with the same host pattern you used for
-- 'nudgelab_api' in deploy/db-logins.sql. The audit log is append-only for the app: no UPDATE or DELETE.

GRANT SELECT, INSERT, UPDATE, DELETE ON nudgeai.dash_users                 TO 'nudgelab_api'@'10.0.1.148';
GRANT SELECT, INSERT, UPDATE, DELETE ON nudgeai.dash_refresh_tokens        TO 'nudgelab_api'@'10.0.1.148';
GRANT SELECT, INSERT, UPDATE, DELETE ON nudgeai.dash_password_reset_tokens TO 'nudgelab_api'@'10.0.1.148';
GRANT SELECT, INSERT                 ON nudgeai.dash_audit_log             TO 'nudgelab_api'@'10.0.1.148';
GRANT SELECT, INSERT, UPDATE, DELETE ON nudgeai.dash_email_outbox          TO 'nudgelab_api'@'10.0.1.148';
GRANT SELECT, INSERT, UPDATE, DELETE ON nudgeai.review_queue               TO 'nudgelab_api'@'10.0.1.148';
GRANT SELECT, INSERT, UPDATE, DELETE ON nudgeai.saved_views                TO 'nudgelab_api'@'10.0.1.148';
GRANT SELECT, INSERT, UPDATE         ON nudgeai.jobs                       TO 'nudgelab_api'@'10.0.1.148';
GRANT SELECT, INSERT                 ON nudgeai.app_session_starts         TO 'nudgelab_api'@'10.0.1.148';
GRANT SELECT, INSERT, UPDATE         ON nudgeai.content_uploads            TO 'nudgelab_api'@'10.0.1.148';
GRANT SELECT, INSERT, UPDATE         ON nudgeai.testers                    TO 'nudgelab_api'@'10.0.1.148';
GRANT SELECT                         ON nudgeai.agent_servers              TO 'nudgelab_api'@'10.0.1.148';
GRANT SELECT                         ON nudgeai.sync_run_log               TO 'nudgelab_api'@'10.0.1.148';
GRANT SELECT, INSERT, UPDATE         ON nudgeai.dash_action_codes          TO 'nudgelab_api'@'10.0.1.148';

-- Check:
-- SHOW GRANTS FOR 'nudgelab_api'@'10.0.1.148';
