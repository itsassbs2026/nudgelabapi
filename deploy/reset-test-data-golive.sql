-- Go-live clean-up (2026-10-06): delete every test training session and everything recorded about it, so
-- NudgeLab starts with no sessions, progress or passes. Every session so far was a test (testers, bots).
--
-- KEPT: preview calls (client 'preview', from the training studio) and their transcripts: publishing a draft
-- needs a preview call on it, and drafts are waiting (the new RSM and RSC versions). Previews are never counted
-- in reports. Also kept: trainings and all their versions, setups, voices, testers, dashboard users, the audit
-- log, agent servers, the sync log, jobs and uploads. Assignments are kept by default (step 4 to clear them).
--
-- Run as an admin, in this order, with NO live calls (ask Claude to check, or the dashboard's Live now).
--   Step 1  look (read-only), and SAVE the Prime Portal list it prints: after step 2 nothing else records it
--   Step 2  delete, in one transaction
--   Step 3  check: every count 0 (previews aside)
--   Step 4  optional: assignments

USE nudgeai;

-- Step 1: look ------------------------------------------------------------------------------------------------
SELECT IF(client = 'preview', 'preview (kept)', 'test (deleted)') AS kind, COUNT(*) AS sessions,
       COUNT(DISTINCT uid) AS people
FROM training_sessions GROUP BY kind;
SELECT 'session_transcripts' t, COUNT(*) n FROM session_transcripts
UNION ALL SELECT 'session_topic_events', COUNT(*) FROM session_topic_events
UNION ALL SELECT 'session_issues', COUNT(*) FROM session_issues
UNION ALL SELECT 'session_usage', COUNT(*) FROM session_usage
UNION ALL SELECT 'quiz_answers', COUNT(*) FROM quiz_answers
UNION ALL SELECT 'training_feedback', COUNT(*) FROM training_feedback
UNION ALL SELECT 'training_acknowledgments', COUNT(*) FROM training_acknowledgments
UNION ALL SELECT 'session_reviews', COUNT(*) FROM session_reviews
UNION ALL SELECT 'daily_review_reports', COUNT(*) FROM daily_review_reports
UNION ALL SELECT 'completion_writes', COUNT(*) FROM completion_writes
UNION ALL SELECT 'training_progress', COUNT(*) FROM training_progress
UNION ALL SELECT 'review_queue', COUNT(*) FROM review_queue
UNION ALL SELECT 'app_session_starts', COUNT(*) FROM app_session_starts
UNION ALL SELECT 'training_assignments (kept)', COUNT(*) FROM training_assignments;

-- The passes NudgeLab copied to Prime Portal (primetwok.prime_nudge_ai_completions). SAVE THIS: completion_writes
-- is the only record of them. See the note at the end before removing any in Portal.
SELECT target, uid, training_id, completion_key, MIN(written_at) AS first_written, MAX(status) AS status
FROM completion_writes GROUP BY target, uid, training_id, completion_key ORDER BY training_id, uid;

-- Step 2: delete (children first; one transaction) -------------------------------------------------------------
START TRANSACTION;
DELETE FROM session_transcripts WHERE session_id IS NULL OR session_id NOT IN (SELECT session_id FROM training_sessions WHERE client = 'preview');
DELETE FROM session_topic_events WHERE session_id IS NULL OR session_id NOT IN (SELECT session_id FROM training_sessions WHERE client = 'preview');
DELETE FROM session_issues WHERE session_id IS NULL OR session_id NOT IN (SELECT session_id FROM training_sessions WHERE client = 'preview');
DELETE FROM session_usage WHERE session_id IS NULL OR session_id NOT IN (SELECT session_id FROM training_sessions WHERE client = 'preview');
DELETE FROM quiz_answers WHERE session_id IS NULL OR session_id NOT IN (SELECT session_id FROM training_sessions WHERE client = 'preview');
DELETE FROM training_feedback WHERE session_id IS NULL OR session_id NOT IN (SELECT session_id FROM training_sessions WHERE client = 'preview');
DELETE FROM training_acknowledgments WHERE session_id IS NULL OR session_id NOT IN (SELECT session_id FROM training_sessions WHERE client = 'preview');
DELETE FROM session_reviews;
DELETE FROM daily_review_reports;
DELETE FROM completion_writes;
DELETE FROM review_queue;
DELETE FROM app_session_starts;
DELETE FROM training_progress;
DELETE FROM training_sessions WHERE client IS NULL OR client <> 'preview';

-- Expect: only preview sessions left. If anything else shows up: ROLLBACK; and ask.
SELECT client, COUNT(*) FROM training_sessions GROUP BY client;
COMMIT;

-- Step 3: check (all 0) ------------------------------------------------------------------------------------------
SELECT 'non-preview sessions' t, COUNT(*) n FROM training_sessions WHERE client IS NULL OR client <> 'preview'
UNION ALL SELECT 'quiz_answers', COUNT(*) FROM quiz_answers
UNION ALL SELECT 'training_feedback', COUNT(*) FROM training_feedback
UNION ALL SELECT 'training_acknowledgments', COUNT(*) FROM training_acknowledgments
UNION ALL SELECT 'session_reviews', COUNT(*) FROM session_reviews
UNION ALL SELECT 'completion_writes', COUNT(*) FROM completion_writes
UNION ALL SELECT 'training_progress', COUNT(*) FROM training_progress
UNION ALL SELECT 'review_queue', COUNT(*) FROM review_queue
UNION ALL SELECT 'app_session_starts', COUNT(*) FROM app_session_starts
UNION ALL SELECT 'orphan transcripts', COUNT(*) FROM session_transcripts t
  WHERE NOT EXISTS (SELECT 1 FROM training_sessions s WHERE s.session_id = t.session_id);

-- Step 4 (optional): assignments ----------------------------------------------------------------------------------
-- Kept by default: they're who sees what in the app. To start with none (and assign everyone fresh), run:
-- DELETE FROM training_assignments;

-- Notes -------------------------------------------------------------------------------------------------------------
-- Prime Portal: the testers' passes NudgeLab copied there stay (step 1's list). The same table holds real passes
-- from the ElevenLabs trainings under the same class ids, so never clear it wholesale. To remove one test pass:
--   DELETE FROM primetwok.prime_nudge_ai_completions WHERE training_id = '<completion_key>' AND uid = <uid>;
-- Outside the database (Claude can do the agent-server part): session logs on the 5 agent servers and their S3
-- copies (nudgelab-releases/session-logs/), and call recordings (nudgeailab/recordings/, deleted after 90 days
-- anyway).
