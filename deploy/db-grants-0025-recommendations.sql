-- Content recommendations (migration 0025_recommendations, 2026-11-05).
-- Run as the RDS master login, AFTER `alembic upgrade head` has created the two tables.
--
-- The nightly job (scripts/recommendations.py, run by the API's login) records each run and its recommendations,
-- and marks a training's earlier open ones "replaced". Trainers mark them done or dismissed in the Studio.
-- Until this is run, the Studio list fails with a database permission error, so run it before deploy.sh restarts
-- the API.

GRANT SELECT, INSERT ON nudgeai.dash_recommendation_runs TO 'nudgelab_api'@'10.0.1.148';
GRANT SELECT, INSERT, UPDATE (status, status_note, status_by, status_at)
    ON nudgeai.dash_recommendations TO 'nudgelab_api'@'10.0.1.148';

SHOW GRANTS FOR 'nudgelab_api'@'10.0.1.148';
