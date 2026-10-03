# Wanaka → nudgeai assignment sync

For whoever builds the sync. The NudgeLab dashboard's "assigned vs completed" reports read
`nudgeai.training_assignments`. Wanaka builds assignments dynamically, so a scheduled job copies them
into that table. The dashboard API only reads it. (SPEC §6.4.)

## 1. Target table (already exists, empty)

```sql
nudgeai.training_assignments (
    assignment_id  INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    uid            INT UNSIGNED NOT NULL,        -- employee uid
    training_id    VARCHAR(50)  NOT NULL,        -- nudgeai training id, e.g. 'big4' (FK → trainings)
    assigned_by    INT UNSIGNED NULL,            -- assigning uid, or NULL
    assigned_at    DATETIME(3)  NOT NULL,
    due_at         DATETIME(3)  NULL,
    status         ENUM('assigned','completed','cancelled') NOT NULL DEFAULT 'assigned',
    UNIQUE KEY uq_assignment (uid, training_id)
)
```

## 2. Rules

| Rule | Detail |
|---|---|
| **Mapping** | A Wanaka class (`prime_ai_training_agents.elevenlabs_agent_id`) maps to `nudgeai.trainings.completion_key`, and from there to `trainings.training_id`. |
| **Skip** | Classes with no matching nudgeai training (e.g. ElevenLabs-only classes). The foreign key rejects them anyway. |
| **Upsert** | On (`uid`, `training_id`). Update `assigned_at` / `due_at` / `assigned_by` if Wanaka changed them. |
| **Unassigned** | Set `status = 'cancelled'`. **Never delete rows**: reports keep the history. If a cancelled assignment comes back, set it to `'assigned'` again. |
| **Completed** | **Don't set `'completed'`.** Completion comes from NudgeLab's own `training_progress`. Leave `'assigned'` rows as they are when a trainee completes. |
| **Frequency** | As often as Wanaka's assignments change in practice. Daily is fine to start; hourly if trainers want near-real-time. |

## 3. Login for the sync job

Run once as an admin. Replace the password and `SYNC_HOST` (the server or IP the sync runs from).

```sql
CREATE USER 'nudgeai_sync'@'SYNC_HOST' IDENTIFIED BY 'CHANGE_ME_SYNC_PASSWORD';
GRANT SELECT ON nudgeai.trainings TO 'nudgeai_sync'@'SYNC_HOST';                                      -- for the mapping
GRANT SELECT, INSERT, UPDATE ON nudgeai.training_assignments TO 'nudgeai_sync'@'SYNC_HOST';
```

If the sync runs inside the database server (a stored procedure or event in the same RDS instance, with the
data already in the `wanaka` schema), it can use whichever login owns that job instead: it just needs these
same three privileges on nudgeai.

## 4. Example (same RDS instance, wanaka and nudgeai side by side)

`wanaka_assignments` stands for whatever query or table yields the current assignments
(uid, class key, assigned date, due date). Replace it with the real source.

```sql
-- 1. Upsert current assignments for classes that exist in NudgeLab.
INSERT INTO nudgeai.training_assignments (uid, training_id, assigned_by, assigned_at, due_at, status)
SELECT  a.uid, t.training_id, a.assigned_by, a.assigned_at, a.due_at, 'assigned'
FROM    wanaka_assignments a
JOIN    nudgeai.trainings t ON t.completion_key = a.class_key
ON DUPLICATE KEY UPDATE
        assigned_by = VALUES(assigned_by),
        assigned_at = VALUES(assigned_at),
        due_at      = VALUES(due_at),
        status      = IF(status = 'cancelled', 'assigned', status);

-- 2. Cancel assignments that are no longer in Wanaka.
UPDATE  nudgeai.training_assignments ta
JOIN    nudgeai.trainings t ON t.training_id = ta.training_id
LEFT JOIN wanaka_assignments a ON a.uid = ta.uid AND a.class_key = t.completion_key
SET     ta.status = 'cancelled'
WHERE   a.uid IS NULL AND ta.status = 'assigned';
```

Run both in one transaction. If Wanaka's assignment list is per-user dynamic, generating `wanaka_assignments`
once per sync (e.g. into a temporary table) keeps the two statements consistent.

## 5. Checking it worked

```sql
SELECT training_id, status, COUNT(*) FROM nudgeai.training_assignments GROUP BY training_id, status;
SELECT * FROM nudgeai.vw_assignment_status WHERE training_id = 'big4' LIMIT 20;   -- joins names and stores
```
