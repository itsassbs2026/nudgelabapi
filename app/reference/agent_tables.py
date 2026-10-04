"""The voice agent's tables and views, described for read-only queries (SPEC §6.2).

They live on their own MetaData, never on the API's Base, so Alembic autogenerate can't see them and nothing
here can create, alter or drop them. Only the columns the reports use are listed; the database has more. All
datetimes are naive UTC (the agent writes `datetime.now(UTC)` without tzinfo).
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
)

agent_metadata = MetaData()

trainings = Table(
    "trainings",
    agent_metadata,
    Column("training_id", String(50), primary_key=True),
    Column("title", String(200)),
    Column("status", String(20)),
    Column("completion_type", String(20)),
    Column("uses_location", Boolean),
    Column("completion_key", String(64)),
    Column("profile_id", String(30)),
    Column("active_version_id", Integer),
    Column("description", Text),
    # The Flutter app's catalog (migration 0005, docs/APP_HANDOFF.md).
    Column("app_title", String(200)),
    Column("category", String(100)),
    Column("tags", String(500)),
    Column("is_required", Boolean),
    Column("app_status", String(16)),
    Column("wanaka_trainer_id", Integer),
)

training_versions = Table(
    "training_versions",
    agent_metadata,
    Column("version_id", Integer, primary_key=True),
    Column("training_id", String(50)),
    Column("version_label", String(50)),
    Column("content_hash", String(64)),
    Column("published_at", DateTime),
    Column("published_by", Integer),
    Column("notes", Text),
    # Phase 10 (migration 0004): the training as one JSON document, and where the version is in its workflow.
    Column("status", String(16)),
    Column("content", JSON),
    Column("created_by", Integer),
    Column("source_upload_id", Integer),
    # Migration 0006: editing drafts in the dashboard (optimistic locking on `revision`).
    Column("revision", Integer),
    Column("created_at", DateTime),
    Column("updated_at", DateTime),
    Column("updated_by", Integer),
)

training_topics = Table(
    "training_topics",
    agent_metadata,
    Column("version_id", Integer, primary_key=True),
    Column("topic_number", Integer, primary_key=True),
    Column("title", String(200)),
)

training_questions = Table(
    "training_questions",
    agent_metadata,
    Column("version_id", Integer, primary_key=True),
    Column("question_number", Integer, primary_key=True),
    Column("location_variant", String(20), primary_key=True),
    Column("section_code", String(5)),
    Column("section_name", String(100)),
    Column("question_text", Text),
    Column("options", JSON),
    Column("correct_option", String(1)),
)

training_progress = Table(
    "training_progress",
    agent_metadata,
    Column("uid", Integer, primary_key=True),
    Column("training_id", String(50), primary_key=True),
    Column("version_id", Integer),
    Column("status", String(20)),
    Column("topics_covered", JSON),
    Column("walkthrough_finished_at", DateTime),
    Column("quiz_attempted", Boolean),
    Column("correct_questions", JSON),
    Column("sessions_count", Integer),
    Column("first_started_at", DateTime),
    Column("passed_at", DateTime),
    Column("updated_at", DateTime),
)

training_sessions = Table(
    "training_sessions",
    agent_metadata,
    Column("session_id", String(36), primary_key=True),
    Column("uid", Integer),
    Column("training_id", String(50)),
    Column("version_id", Integer),
    Column("started_at", DateTime),
    Column("ended_at", DateTime),
    Column("duration_sec", Integer),
    Column("start_point", String(20)),
    Column("outcome", String(20)),
    Column("end_reason", String(20)),
    Column("store_id_at_session", String(12)),
    Column("job_title_at_session", String(255)),
    Column("client", String(20)),
    Column("profile_id", String(30)),
    Column("llm_model", String(60)),
    Column("voice_id", String(40)),
    Column("trainer_name", String(40)),  # migration 0007: the name the trainer used (persona), NULL before
    Column("recording_s3_key", String(300)),
    Column("agent_version", String(40)),
    Column("summary", Text),
)

session_transcripts = Table(
    "session_transcripts",
    agent_metadata,
    Column("session_id", String(36), primary_key=True),
    Column("seq", Integer, primary_key=True),
    Column("role", String(10)),  # trainer | trainee
    Column("message", Text),
    Column("seconds_into_session", Integer),
    Column("interrupted", Boolean),
)

session_issues = Table(
    "session_issues",
    agent_metadata,
    Column("issue_id", Integer, primary_key=True),
    Column("session_id", String(36)),
    Column("issue_type", String(40)),  # guardrail | end_call_refused | error
    Column("detail", Text),
    Column("occurred_at", DateTime),
)

session_topic_events = Table(
    "session_topic_events",
    agent_metadata,
    Column("session_id", String(36), primary_key=True),
    Column("topic_number", Integer, primary_key=True),
    Column("reached_at", DateTime),
)

session_usage = Table(
    "session_usage",
    agent_metadata,
    Column("session_id", String(36), primary_key=True),
    Column("llm_model", String(60)),
    Column("llm_requests", Integer),
    Column("llm_input_tokens", Integer),
    Column("llm_cached_tokens", Integer),
    Column("llm_cache_write_tokens", Integer),
    Column("llm_output_tokens", Integer),
    Column("tts_characters", Integer),
    Column("recorded_seconds", Integer),
    Column("stt_audio_seconds", Numeric(10, 1)),
    Column("est_llm_cost", Numeric(10, 5)),
    Column("est_tts_cost", Numeric(10, 5)),
    Column("est_stt_cost", Numeric(10, 5)),
    Column("est_total_cost", Numeric(10, 5)),
)

session_reviews = Table(
    "session_reviews",
    agent_metadata,
    Column("session_id", String(36), primary_key=True),
    Column("uid", Integer),
    Column("training_id", String(50)),
    Column("session_started", DateTime),
    Column("reviewed_at", DateTime),
    Column("review_model", String(100)),
    Column("score", Integer),
    Column("summary", String(500)),
    Column("flagged", Boolean),
    Column("issue_count", Integer),
    Column("issues", JSON),
)

quiz_answers = Table(
    "quiz_answers",
    agent_metadata,
    Column("answer_id", Integer, primary_key=True),
    Column("session_id", String(36)),
    Column("uid", Integer),
    Column("training_id", String(50)),
    Column("version_id", Integer),
    Column("question_number", Integer),
    Column("round", Integer),
    Column("given_option", String(1)),
    Column("is_correct", Boolean),
    Column("heard_as", String(100)),
    Column("answered_at", DateTime),
)

training_feedback = Table(
    "training_feedback",
    agent_metadata,
    Column("session_id", String(36), primary_key=True),
    Column("uid", Integer),
    Column("training_id", String(50)),
    Column("rating", Integer),
    Column("comment", Text),
    Column("trainee_quote", Text),
    Column("created_at", DateTime),
)

training_acknowledgments = Table(
    "training_acknowledgments",
    agent_metadata,
    Column("ack_id", Integer, primary_key=True),
    Column("session_id", String(36)),
    Column("uid", Integer),
    Column("training_id", String(50)),
    Column("version_id", Integer),
    Column("statement", String(1000)),
    Column("trainee_quote", String(2000)),
    Column("acknowledged_at", DateTime),
)

training_assignments = Table(
    "training_assignments",
    agent_metadata,
    Column("assignment_id", Integer, primary_key=True),
    Column("uid", Integer),
    Column("training_id", String(50)),
    Column("assigned_at", DateTime),
    Column("due_at", DateTime),
    Column("status", String(20)),
    # Migration 0005: why it was assigned, once rules exist (NULL for rows assigned by hand).
    Column("matched_rule_group_id", Integer),
    Column("matched_rule_name", String(150)),
    Column("ai_flag", String(50)),
)

training_profiles = Table(
    "training_profiles",
    agent_metadata,
    Column("profile_id", String(30), primary_key=True),
    Column("display_name", String(60)),
    Column("description", String(255)),
    Column("llm_model", String(100)),
    Column("llm_effort", String(10)),
    Column("llm_max_output_tokens", Integer),
    Column("tts_engine", String(20)),
    Column("voice_id", String(40)),
    Column("llm_input_per_m", Numeric(8, 4)),
    Column("llm_cached_per_m", Numeric(8, 4)),
    Column("llm_cache_write_per_m", Numeric(8, 4)),
    Column("llm_output_per_m", Numeric(8, 4)),
    Column("tts_per_m_chars", Numeric(8, 4)),
    Column("stt_per_minute", Numeric(8, 5)),
    Column("is_default", Boolean),
    Column("is_active", Boolean),
    Column("allow_request", Boolean),
    Column("notes", String(255)),
)

training_voices = Table(
    "training_voices",
    agent_metadata,
    Column("voice_id", String(40), primary_key=True),
    Column("display_name", String(60)),
    Column("language_code", String(10)),
    Column("gender", String(10)),
    Column("engine", String(20)),
    Column("sort_order", Integer),
    Column("notes", String(255)),
    Column("is_active", Boolean),
    Column("is_default", Boolean),
)

# Views: the org hierarchy without exposing v_users* personal-data columns (SPEC §6.2).
vw_trainees = Table(
    "vw_trainees",
    agent_metadata,
    Column("uid", Integer, primary_key=True),
    Column("name", String(150)),
    Column("is_active", Boolean),
    Column("job_title", String(255)),
    Column("store_id", String(12)),
    Column("store_name", String(150)),
    Column("district_id", Integer),
    Column("district_name", String(150)),
    Column("market_id", Integer),
    Column("market_name", String(150)),
    Column("region_id", Integer),
    Column("region_name", String(150)),
)

vw_training_stores = Table(
    "vw_training_stores",
    agent_metadata,
    Column("store_id", String(12), primary_key=True),
    Column("store_name", String(150)),
    Column("store_active", Boolean),
    Column("district_id", Integer),
    Column("district_name", String(150)),
    Column("market_id", Integer),
    Column("market_name", String(150)),
    Column("region_id", Integer),
    Column("region_name", String(150)),
)

# The app's list header (migration 0005): an active employee (v_users, status 1) and their district manager.
vw_app_profile = Table(
    "vw_app_profile",
    agent_metadata,
    Column("uid", Integer, primary_key=True),
    Column("name", String(60)),
    Column("job_id", Integer),
    Column("job_title", String(255)),
    Column("store_id", String(12)),
    Column("store_name", String(155)),
    Column("district_name", String(40)),
    Column("market_name", String(55)),
    Column("region_name", String(50)),
    Column("district_manager_name", String(60)),
    Column("district_manager_picture", Text),
)

# Sessions that never count in reports unless an Admin asks (SPEC §5): automated tests and trainer previews.
EXCLUDED_CLIENTS = ("bot_test", "preview")
