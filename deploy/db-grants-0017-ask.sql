-- Ask NudgeLab (2026-10-13). Run once as the RDS admin (master) login, AFTER `alembic upgrade head` has applied
-- 0017_ask_nudgelab. The API keeps the settings, conversations, every message and the feedback on answers.

GRANT SELECT, UPDATE ON nudgeai.dash_settings TO 'nudgelab_api'@'10.0.1.148';
GRANT SELECT, INSERT, UPDATE ON nudgeai.ask_conversations TO 'nudgelab_api'@'10.0.1.148';
GRANT SELECT, INSERT ON nudgeai.ask_messages TO 'nudgelab_api'@'10.0.1.148';
GRANT SELECT, INSERT, UPDATE ON nudgeai.ask_feedback TO 'nudgelab_api'@'10.0.1.148';
