-- 2026-10-04: NudgeLab no longer uses any Wanaka table (DECISIONS #114). Passes go to Prime Portal only and
-- completion keys come from nudgeai.trainings. Run once as an admin, AFTER the agent release that stopped writing
-- to Wanaka is live (otherwise the old agent's Wanaka writes fail until it's restarted; they'd only be logged).

REVOKE SELECT, INSERT, UPDATE ON wanaka.prime_nudge_ai_completions FROM 'nudgeai_agent'@'35.173.142.66';
REVOKE SELECT ON wanaka.prime_ai_training_agents FROM 'nudgeai_agent'@'35.173.142.66';

-- Check: no line mentioning `wanaka` should remain.
-- SHOW GRANTS FOR 'nudgeai_agent'@'35.173.142.66';
