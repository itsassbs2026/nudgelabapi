-- 02: Suncom Portal's completions table, where the agent records each pass (completions.py), the same shape as
-- Prime's primetwok.prime_nudge_ai_completions. Run as the RDS master user. Skips it if the table already exists.
-- training_id is the training's completion key (Studio > training settings), so Portal can show the class passed.
CREATE TABLE IF NOT EXISTS suncomportal.suncom_nudge_ai_completions (
  training_id varchar(64) NOT NULL,
  uid int NOT NULL,
  passed tinyint(1) NOT NULL DEFAULT '0',
  passed_at datetime DEFAULT NULL,
  first_attempt_at datetime DEFAULT NULL,
  updated_at datetime NOT NULL,
  PRIMARY KEY (training_id, uid)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
