-- Assign trainings to a few people by hand (APP_HANDOFF.md 4.1).
-- Run on nudgeai with an admin login, in one session (it uses temporary tables). Safe to re-run.
--
--   1. Edit the two lists in step 1 (uids, training ids).
--   2. Run step 2 and read the output: both checks should return no rows.
--   3. Run step 3 (assign), then step 4 to see the result.
--   Step 5 unassigns (uses the same two lists).
--
-- Rows are never deleted: unassigning sets status = 'cancelled', assigning again sets it back.
-- The dashboard's "assigned" reports read this table, so testers will show there too.

USE nudgeai;

-- 1. Who and what -------------------------------------------------------------------------------
SET @assigned_by = NULL;  -- optional: your own uid

DROP TEMPORARY TABLE IF EXISTS tmp_uids;
CREATE TEMPORARY TABLE tmp_uids (uid INT UNSIGNED PRIMARY KEY);
INSERT INTO tmp_uids (uid) VALUES
    (11111),   -- replace with the testers' uids
    (22222);

DROP TEMPORARY TABLE IF EXISTS tmp_trainings;
CREATE TEMPORARY TABLE tmp_trainings (
    training_id VARCHAR(50) COLLATE utf8mb4_0900_ai_ci PRIMARY KEY
);
INSERT INTO tmp_trainings (training_id) VALUES
    ('big4'),
    ('q4_comp_2026');
    -- also available: ('sample_store_safety'), ('sample_return_policy')

-- 2. Checks (both should return no rows) ---------------------------------------------------------
SELECT u.uid AS not_an_active_employee
FROM   tmp_uids u
LEFT JOIN v_users v ON v.uid = u.uid
WHERE  v.uid IS NULL;

SELECT t.training_id AS unknown_training
FROM   tmp_trainings t
LEFT JOIN trainings x ON x.training_id = t.training_id
WHERE  x.training_id IS NULL;

-- 3. Assign (skips anyone or anything that failed a check) ---------------------------------------
INSERT INTO training_assignments (uid, training_id, assigned_by, assigned_at, status)
SELECT * FROM (
    SELECT u.uid, t.training_id, @assigned_by AS assigned_by, UTC_TIMESTAMP(3) AS assigned_at, 'assigned' AS status
    FROM   tmp_uids u
    JOIN   v_users v   ON v.uid = u.uid
    CROSS JOIN tmp_trainings t
    JOIN   trainings x ON x.training_id = t.training_id
) AS new_rows
ON DUPLICATE KEY UPDATE status = 'assigned';   -- re-activates a cancelled row; assigned_at keeps the first date

-- 4. Result -------------------------------------------------------------------------------------
SELECT a.uid, a.training_id, a.status, a.assigned_at
FROM   training_assignments a
JOIN   tmp_uids u ON u.uid = a.uid
ORDER  BY a.uid, a.training_id;

-- 5. Unassign (uncomment to run) ----------------------------------------------------------------
-- UPDATE training_assignments a
-- JOIN   tmp_uids u      ON u.uid = a.uid
-- JOIN   tmp_trainings t ON t.training_id = a.training_id
-- SET    a.status = 'cancelled'
-- WHERE  a.status = 'assigned';
