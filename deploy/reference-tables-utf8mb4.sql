-- 2026-10-06: the four PortalLive copies in nudgeai become utf8mb4, like every NudgeLab table. Run once as an admin,
-- before the first sync. Their mixed utf8mb3/latin1 columns forced a slow row-by-row join from sessions to stores
-- (DECISIONS #112); with one character set the indexes work.
--
-- Each ALTER rebuilds its table (seconds; v_users_all, about 50,000 rows, is the largest). Reads keep working
-- meanwhile. Run at a quiet time anyway. Nothing else changes: same columns, keys and data.

USE nudgeai;

-- Before: which columns aren't utf8mb4 yet (expect many rows)
SELECT table_name, column_name, character_set_name, collation_name
FROM information_schema.columns
WHERE table_schema = 'nudgeai' AND table_name IN ('v_users_all', 'v_users', 'v_stores', 'v_stores_all')
  AND character_set_name IS NOT NULL AND character_set_name <> 'utf8mb4';

ALTER TABLE v_users_all CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
ALTER TABLE v_users CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
ALTER TABLE v_stores CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
ALTER TABLE v_stores_all CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

-- After: the same query returns no rows, and the tables are still tables with their rows
SELECT table_name, column_name, character_set_name
FROM information_schema.columns
WHERE table_schema = 'nudgeai' AND table_name IN ('v_users_all', 'v_users', 'v_stores', 'v_stores_all')
  AND character_set_name IS NOT NULL AND character_set_name <> 'utf8mb4';
SELECT table_name, table_type, table_collation, table_rows
FROM information_schema.tables
WHERE table_schema = 'nudgeai' AND table_name IN ('v_users_all', 'v_users', 'v_stores', 'v_stores_all');
