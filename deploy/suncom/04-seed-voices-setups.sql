-- Suncom NudgeLab: the voices and setups Prime runs (copied from Prime's nudgeai on 2026-11-03).
-- Run as the RDS master user on nudgeai, after `alembic upgrade head`. Change them later on Admin > Voices / Setups.
-- Model ids stay in Bedrock form; with ANTHROPIC_API_KEY set they're sent to Anthropic as e.g. claude-sonnet-5-5.

INSERT INTO nudgeai.training_voices (voice_id, provider, display_name, language_code, gender, engine, is_default, is_active, sort_order, notes, created_at, updated_at)
    VALUES ('Amy', 'polly', 'Amy', 'en-GB', 'Female', 'generative', 0, 1, 80, NULL, UTC_TIMESTAMP(3), UTC_TIMESTAMP(3));
INSERT INTO nudgeai.training_voices (voice_id, provider, display_name, language_code, gender, engine, is_default, is_active, sort_order, notes, created_at, updated_at)
    VALUES ('Danielle', 'polly', 'Danielle', 'en-US', 'Female', 'generative', 0, 1, 20, NULL, UTC_TIMESTAMP(3), UTC_TIMESTAMP(3));
INSERT INTO nudgeai.training_voices (voice_id, provider, display_name, language_code, gender, engine, is_default, is_active, sort_order, notes, created_at, updated_at)
    VALUES ('Joanna', 'polly', 'Joanna', 'en-US', 'Female', 'generative', 0, 1, 30, NULL, UTC_TIMESTAMP(3), UTC_TIMESTAMP(3));
INSERT INTO nudgeai.training_voices (voice_id, provider, display_name, language_code, gender, engine, is_default, is_active, sort_order, notes, created_at, updated_at)
    VALUES ('Matthew', 'polly', 'Matthew', 'en-US', 'Male', 'generative', 0, 1, 60, NULL, UTC_TIMESTAMP(3), UTC_TIMESTAMP(3));
INSERT INTO nudgeai.training_voices (voice_id, provider, display_name, language_code, gender, engine, is_default, is_active, sort_order, notes, created_at, updated_at)
    VALUES ('Olivia', 'polly', 'Olivia', 'en-AU', 'Female', 'generative', 0, 1, 90, NULL, UTC_TIMESTAMP(3), UTC_TIMESTAMP(3));
INSERT INTO nudgeai.training_voices (voice_id, provider, display_name, language_code, gender, engine, is_default, is_active, sort_order, notes, created_at, updated_at)
    VALUES ('Ruth', 'polly', 'Ruth', 'en-US', 'Female', 'generative', 0, 1, 10, NULL, UTC_TIMESTAMP(3), UTC_TIMESTAMP(3));
INSERT INTO nudgeai.training_voices (voice_id, provider, display_name, language_code, gender, engine, is_default, is_active, sort_order, notes, created_at, updated_at)
    VALUES ('Salli', 'polly', 'Salli', 'en-US', 'Female', 'generative', 0, 1, 40, NULL, UTC_TIMESTAMP(3), UTC_TIMESTAMP(3));
INSERT INTO nudgeai.training_voices (voice_id, provider, display_name, language_code, gender, engine, is_default, is_active, sort_order, notes, created_at, updated_at)
    VALUES ('Stephen', 'polly', 'Stephen', 'en-US', 'Male', 'generative', 0, 1, 70, NULL, UTC_TIMESTAMP(3), UTC_TIMESTAMP(3));
INSERT INTO nudgeai.training_voices (voice_id, provider, display_name, language_code, gender, engine, is_default, is_active, sort_order, notes, created_at, updated_at)
    VALUES ('Tiffany', 'polly', 'Tiffany', 'en-US', 'Female', 'generative', 1, 1, 50, NULL, UTC_TIMESTAMP(3), UTC_TIMESTAMP(3));

INSERT INTO nudgeai.training_profiles (profile_id, display_name, description, llm_model, llm_effort, llm_max_output_tokens, tts_provider, tts_engine, voice_id, stt_provider, llm_input_per_m, llm_cached_per_m, llm_cache_write_per_m, llm_output_per_m, tts_per_m_chars, stt_per_minute, is_default, is_active, allow_request, notes, created_at, updated_at)
    VALUES ('enhanced', 'Enhanced', 'Claude Haiku 4. 5 (effort low), Polly generative, Transcribe. Better judgment, slightly slower.', 'us.anthropic.claude-haiku-4-5-20251001-v1:0', NULL, 600, 'polly', 'generative', NULL, 'transcribe', '2.0000', '0.2000', '2.5000', '10.0000', '30.0000', '0.01000', 0, 1, 1, 'First word ~0.9 s on Bedrock with effort low, ~2.6 s at default effort (it thinks first)', UTC_TIMESTAMP(3), UTC_TIMESTAMP(3));
INSERT INTO nudgeai.training_profiles (profile_id, display_name, description, llm_model, llm_effort, llm_max_output_tokens, tts_provider, tts_engine, voice_id, stt_provider, llm_input_per_m, llm_cached_per_m, llm_cache_write_per_m, llm_output_per_m, tts_per_m_chars, stt_per_minute, is_default, is_active, allow_request, notes, created_at, updated_at)
    VALUES ('sonnet46', 'Sonnet 4.6', 'Claude Sonnet 4.6 (effort low), Polly generative, Transcribe. Stronger judgment than Standard, about 0.15 s slower.', 'us.anthropic.claude-sonnet-4-6', 'low', 600, 'polly', 'generative', 'Tiffany', 'transcribe', '3.0000', '0.3000', '3.7500', '15.0000', '30.0000', '0.01000', 0, 1, 1, 'Added 2026-10-19; first word ~0.65 s on Bedrock', UTC_TIMESTAMP(3), UTC_TIMESTAMP(3));
INSERT INTO nudgeai.training_profiles (profile_id, display_name, description, llm_model, llm_effort, llm_max_output_tokens, tts_provider, tts_engine, voice_id, stt_provider, llm_input_per_m, llm_cached_per_m, llm_cache_write_per_m, llm_output_per_m, tts_per_m_chars, stt_per_minute, is_default, is_active, allow_request, notes, created_at, updated_at)
    VALUES ('standard', 'Standard', 'Claude Sonnet 5.5, Polly generative, Transcribe. Fastest replies, lowest cost.', 'us.anthropic.claude-sonnet-5-5', 'high', 600, 'polly', 'generative', 'Tiffany', 'transcribe', '1.0000', '0.1000', '1.2500', '5.0000', '30.0000', '0.01000', 1, 1, 1, 'First word ~0.5 s on Bedrock', UTC_TIMESTAMP(3), UTC_TIMESTAMP(3));

-- Check: SELECT voice_id, is_default FROM nudgeai.training_voices; SELECT profile_id, llm_model, is_default FROM nudgeai.training_profiles;
