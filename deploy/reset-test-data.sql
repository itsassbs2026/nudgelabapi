-- Start fresh: delete every training session and everything recorded about it (test data), keeping the
-- trainings, their versions and content, setups, voices, testers, dashboard users and the audit log.
--
-- PRODUCTION: nudgeai is live. Run as an admin, in this order, at a quiet time with NO live sessions (a call in
-- progress would fail to write its transcript and usage). One way to check: the dashboard's Overview "Live now",
-- or ask Claude to run live_rooms.py on the agent server.
--
--   Step 1  look (read-only): what will be deleted, and which Prime Portal rows NudgeLab wrote
--   Step 2  delete, in one transaction
--   Step 3  check: every count should be 0
--   Step 4  optional: assignments (kept by default: the testers need them to use the app)
--
-- Not touched (by design): trainings, training_versions, training_topics, training_questions,
-- training_profiles, training_voices, testers, dash_users, dash_audit_log (the audit trail keeps who opened
-- what, including session ids that no longer exist), jobs, content_uploads.
-- Elsewhere: call recordings in S3 (recordings/...) expire after 90 days by themselves; the agent server's
-- session log files (~/nudgelab/logs) and Prime Portal's completion rows are separate (see the notes at the end).

USE nudgeai;

-- Step 1: look ---------------------------------------------------------------------------------------------
SELECT 'training_sessions' t, COUNT(*) n FROM training_sessions
UNION ALL SELECT 'session_transcripts', COUNT(*) FROM session_transcripts
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

-- The completion rows NudgeLab wrote to Prime Portal (and, before 2026-10-04, Wanaka). Save this result BEFORE
-- step 2: completion_writes is the only record of them. See the note at the end before removing any in Portal.
SELECT target, uid, training_id, completion_key, MIN(written_at) AS first_written, MAX(status) AS status
FROM completion_writes
GROUP BY target, uid, training_id, completion_key
ORDER BY target, uid;

-- Step 2: delete (children first; one transaction) ----------------------------------------------------------
START TRANSACTION;
DELETE FROM session_transcripts;
DELETE FROM session_topic_events;
DELETE FROM session_issues;
DELETE FROM session_usage;
DELETE FROM quiz_answers;
DELETE FROM training_feedback;
DELETE FROM training_acknowledgments;
DELETE FROM session_reviews;
DELETE FROM daily_review_reports;
DELETE FROM completion_writes;
DELETE FROM review_queue;
DELETE FROM app_session_starts;
DELETE FROM training_sessions;
DELETE FROM training_progress;
COMMIT;

-- Step 3: check (every count 0) -----------------------------------------------------------------------------
SELECT 'training_sessions' t, COUNT(*) n FROM training_sessions
UNION ALL SELECT 'session_transcripts', COUNT(*) FROM session_transcripts
UNION ALL SELECT 'quiz_answers', COUNT(*) FROM quiz_answers
UNION ALL SELECT 'training_feedback', COUNT(*) FROM training_feedback
UNION ALL SELECT 'session_reviews', COUNT(*) FROM session_reviews
UNION ALL SELECT 'completion_writes', COUNT(*) FROM completion_writes
UNION ALL SELECT 'training_progress', COUNT(*) FROM training_progress
UNION ALL SELECT 'review_queue', COUNT(*) FROM review_queue
UNION ALL SELECT 'app_session_starts', COUNT(*) FROM app_session_starts;

-- Step 4 (optional): assignments ---------------------------------------------------------------------------
-- Kept by default. To clear them too, run this; then re-assign the testers (deploy/assign-testers.sql), or the
-- app shows them an empty Nudge screen.
-- DELETE FROM training_assignments;

-- Notes ------------------------------------------------------------------------------------------------------
-- Prime Portal (primetwok.prime_nudge_ai_completions, its own server): the rows NudgeLab wrote for testers stay
-- there. The same table also holds real completions from the ElevenLabs trainings, under the same class ids, so
-- don't clear it wholesale. Remove a test row only if you're sure it came from a NudgeLab test (step 1's list),
-- for example: DELETE FROM primetwok.prime_nudge_ai_completions WHERE training_id = '<completion_key>' AND uid = <uid>;
-- Wanaka's copies (before 2026-10-04) are the same: see step 1's 'wanaka' rows.
