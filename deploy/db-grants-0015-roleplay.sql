-- Role Play trainings (docs/ROLEPLAY.md). Run once as an admin AFTER `alembic upgrade head` has applied
-- 0015_roleplay, AT GO-LIVE ONLY (Role Play is built on the roleplay branch and isn't live before then).
--
-- The agent reads the reason a person was assigned (the track), keeps practice scores, archives progress when a new
-- reason starts them fresh, and logs every pass. The API reads the new tables for the reports.

-- The API: reads the new tables, and sets the reason (ai_flag) when a Role Play training is assigned.
GRANT UPDATE (ai_flag) ON nudgeai.training_assignments TO 'nudgelab_api'@'10.0.1.148';
GRANT SELECT ON nudgeai.roleplay_attempts TO 'nudgelab_api'@'10.0.1.148';
GRANT SELECT ON nudgeai.training_progress_history TO 'nudgelab_api'@'10.0.1.148';
GRANT SELECT ON nudgeai.training_pass_log TO 'nudgelab_api'@'10.0.1.148';

-- Every agent server (the same five lines for each IP).
GRANT SELECT (uid, training_id, status, ai_flag) ON nudgeai.training_assignments TO 'nudgeai_agent'@'35.173.142.66';
GRANT SELECT, INSERT ON nudgeai.roleplay_attempts TO 'nudgeai_agent'@'35.173.142.66';
GRANT INSERT ON nudgeai.training_progress_history TO 'nudgeai_agent'@'35.173.142.66';
GRANT INSERT ON nudgeai.training_pass_log TO 'nudgeai_agent'@'35.173.142.66';

GRANT SELECT (uid, training_id, status, ai_flag) ON nudgeai.training_assignments TO 'nudgeai_agent'@'44.221.150.77';
GRANT SELECT, INSERT ON nudgeai.roleplay_attempts TO 'nudgeai_agent'@'44.221.150.77';
GRANT INSERT ON nudgeai.training_progress_history TO 'nudgeai_agent'@'44.221.150.77';
GRANT INSERT ON nudgeai.training_pass_log TO 'nudgeai_agent'@'44.221.150.77';

GRANT SELECT (uid, training_id, status, ai_flag) ON nudgeai.training_assignments TO 'nudgeai_agent'@'18.204.20.139';
GRANT SELECT, INSERT ON nudgeai.roleplay_attempts TO 'nudgeai_agent'@'18.204.20.139';
GRANT INSERT ON nudgeai.training_progress_history TO 'nudgeai_agent'@'18.204.20.139';
GRANT INSERT ON nudgeai.training_pass_log TO 'nudgeai_agent'@'18.204.20.139';

GRANT SELECT (uid, training_id, status, ai_flag) ON nudgeai.training_assignments TO 'nudgeai_agent'@'34.200.118.174';
GRANT SELECT, INSERT ON nudgeai.roleplay_attempts TO 'nudgeai_agent'@'34.200.118.174';
GRANT INSERT ON nudgeai.training_progress_history TO 'nudgeai_agent'@'34.200.118.174';
GRANT INSERT ON nudgeai.training_pass_log TO 'nudgeai_agent'@'34.200.118.174';

GRANT SELECT (uid, training_id, status, ai_flag) ON nudgeai.training_assignments TO 'nudgeai_agent'@'54.82.245.163';
GRANT SELECT, INSERT ON nudgeai.roleplay_attempts TO 'nudgeai_agent'@'54.82.245.163';
GRANT INSERT ON nudgeai.training_progress_history TO 'nudgeai_agent'@'54.82.245.163';
GRANT INSERT ON nudgeai.training_pass_log TO 'nudgeai_agent'@'54.82.245.163';

-- Check:
-- SHOW GRANTS FOR 'nudgelab_api'@'10.0.1.148';
-- SHOW GRANTS FOR 'nudgeai_agent'@'35.173.142.66';
