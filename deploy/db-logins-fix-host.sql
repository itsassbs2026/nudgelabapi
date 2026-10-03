-- NudgeLab API: move the two logins to the host RDS actually sees (run once by an admin, on RDS).
--
-- The pingitapi server reaches RDS from its private address, 10.0.1.148 (the preflight's "Access denied for user
-- 'nudgelab_api'@'10.0.1.148'"), but deploy/db-logins.sql first created the logins for the public IP. RENAME USER
-- keeps each login's password and grants; nothing else changes.

-- Before: see what's there (and how pingit's login is set up, for comparison).
SELECT user, host FROM mysql.user WHERE user LIKE 'nudgelab%' OR user LIKE 'pingit%';

RENAME USER 'nudgelab_api'@'204.236.179.185'         TO 'nudgelab_api'@'10.0.1.148';
RENAME USER 'nudgelab_api_migrate'@'204.236.179.185' TO 'nudgelab_api_migrate'@'10.0.1.148';

-- After: both logins at 10.0.1.148, with their grants.
SELECT user, host FROM mysql.user WHERE user LIKE 'nudgelab%';
SHOW GRANTS FOR 'nudgelab_api'@'10.0.1.148';
SHOW GRANTS FOR 'nudgelab_api_migrate'@'10.0.1.148';
