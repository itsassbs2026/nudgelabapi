-- Phase 14 grants: voices, setups and testers. Run once as an admin, AFTER `alembic upgrade head` has applied
-- 0009_testers. Column-level UPDATE: the API can change only what the admin pages edit (tests/test_deploy.py
-- checks these lists against the code). No DELETE anywhere.

-- The API: its new testers table, and the agent's voices and setups.
GRANT SELECT, INSERT, UPDATE ON nudgeai.testers TO 'nudgelab_api'@'10.0.1.148';
GRANT INSERT ON nudgeai.training_voices TO 'nudgelab_api'@'10.0.1.148';
GRANT UPDATE (is_active, is_default, notes, sort_order) ON nudgeai.training_voices TO 'nudgelab_api'@'10.0.1.148';
GRANT UPDATE (display_name, description, llm_model, llm_effort, llm_max_output_tokens, tts_engine, voice_id, llm_input_per_m, llm_cached_per_m, llm_cache_write_per_m, llm_output_per_m, tts_per_m_chars, stt_per_minute, is_default, is_active, allow_request, notes) ON nudgeai.training_profiles TO 'nudgelab_api'@'10.0.1.148';

-- The agent: its tester page signs testers in from the table (read only).
GRANT SELECT ON nudgeai.testers TO 'nudgeai_agent'@'35.173.142.66';

-- Check:
-- SHOW GRANTS FOR 'nudgelab_api'@'10.0.1.148';
-- SHOW GRANTS FOR 'nudgeai_agent'@'35.173.142.66';
