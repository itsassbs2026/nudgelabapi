-- The app's catalog fields on nudgeai.trainings (APP_HANDOFF.md 4.1). Run as an admin, after migration 0005.
--
-- What the app shows per training:
--   trainer_name         app_title, or the training's title when app_title is NULL
--   trainer_category     category
--   trainer_description  description
--   tags                 tags
--   is_required          is_required (1/0, default 1; the badge counts required, not-completed trainings)
--   trainer_id           wanaka_trainer_id (the old Wanaka trainer_id, so the app's ids don't change)
--   elevenlabs_agent_id  completion_key (already set: it's how completions reach Wanaka and Portal)
-- app_status = 'archived' hides a training from the app without touching assignments or reports.
--
-- Option A copies the values once from Wanaka's catalog (matched on completion key). Option B sets them by hand.
-- Either way, check with step 3. Phase 11's training settings page edits these later.

USE nudgeai;

-- 1. Preview: what Option A would copy (read-only) ---------------------------------------------
SELECT t.training_id, t.title, t.completion_key,
       w.trainer_id, w.trainer_name, w.trainer_category, w.is_required
FROM   trainings t
LEFT JOIN wanaka.prime_ai_training_agents w
       ON w.elevenlabs_agent_id = t.completion_key AND w.deleted_at IS NULL
ORDER  BY t.training_id;

-- 2A. Copy from Wanaka's catalog (one time; uncomment to run) ------------------------------------
-- UPDATE trainings t
-- JOIN   wanaka.prime_ai_training_agents w
--        ON w.elevenlabs_agent_id = t.completion_key AND w.deleted_at IS NULL
-- SET    t.app_title         = w.trainer_name,
--        t.category          = w.trainer_category,
--        t.description       = w.trainer_description,
--        t.tags              = CAST(w.tags AS CHAR(500)),
--        t.is_required       = COALESCE(w.is_required, 1),
--        t.wanaka_trainer_id = w.trainer_id;

-- 2B. Or by hand, one training at a time (uncomment and edit) ---------------------------------
-- UPDATE trainings
-- SET    app_title         = 'RSC Sales Incentive Plan',
--        category          = 'Sales',
--        description       = 'This is an AI Voice based training course and quiz on ...',
--        is_required       = 1,
--        wanaka_trainer_id = 14
-- WHERE  training_id = 'q4_comp_2026';

-- Hide the two sample trainings from the app (they stay in reports and on the tester page):
-- UPDATE trainings SET app_status = 'archived' WHERE training_id IN ('sample_store_safety', 'sample_return_policy');

-- 3. Check ---------------------------------------------------------------------------------------
SELECT training_id, COALESCE(app_title, title) AS app_name, category, is_required, app_status,
       wanaka_trainer_id, completion_key, active_version_id IS NOT NULL AS published
FROM   trainings
ORDER  BY training_id;
