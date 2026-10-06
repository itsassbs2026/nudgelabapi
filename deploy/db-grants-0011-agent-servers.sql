-- 2026-10-06 grants: agent_servers (the app's busy check). Run once as an admin, AFTER `alembic upgrade head`
-- has applied 0011_agent_servers. Every agent server writes its own row every 30 seconds; the API reads them.
-- A new agent server needs the agent line for its own IP (ops/README.md in the agent repo, "Adding a server").

-- The API: reads the servers' capacity and check-ins.
GRANT SELECT ON nudgeai.agent_servers TO 'nudgelab_api'@'10.0.1.148';

-- The agent servers: each writes its own row.
GRANT SELECT, INSERT, UPDATE ON nudgeai.agent_servers TO 'nudgeai_agent'@'35.173.142.66';
GRANT SELECT, INSERT, UPDATE ON nudgeai.agent_servers TO 'nudgeai_agent'@'44.221.150.77';

-- Check:
-- SHOW GRANTS FOR 'nudgelab_api'@'10.0.1.148';
-- SHOW GRANTS FOR 'nudgeai_agent'@'35.173.142.66';
-- SHOW GRANTS FOR 'nudgeai_agent'@'44.221.150.77';
