-- 2026-10-04: NudgeLab no longer uses any Wanaka table (DECISIONS #114). Passes go to Prime Portal only and
-- completion keys come from nudgeai.trainings. Run once as an admin, AFTER the agent release that stopped writing
-- to Wanaka is live (otherwise the old agent's Wanaka writes fail until it's restarted; they'd only be logged).

REVOKE SELECT, INSERT, UPDATE ON wanaka.prime_nudge_ai_completions FROM 'nudgeai_agent'@'35.173.142.66';
REVOKE SELECT ON wanaka.prime_ai_training_agents FROM 'nudgeai_agent'@'35.173.142.66';

-- Also unused: nudge.prime_nudge_ai_completions on this server. Prime Portal is the `primetwok` database on its
-- own server (the agent's PORTAL_DB_* settings, a separate account there), so this doesn't affect Portal copies.
REVOKE SELECT, INSERT, UPDATE ON nudge.prime_nudge_ai_completions FROM 'nudgeai_agent'@'35.173.142.66';

-- Check: no line mentioning `wanaka` or `nudge`.`prime_nudge_ai_completions` should remain.
-- SHOW GRANTS FOR 'nudgeai_agent'@'35.173.142.66';
