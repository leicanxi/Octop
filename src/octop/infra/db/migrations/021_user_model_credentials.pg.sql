-- Schema v21: per-user model credentials (bring-your-own-key).

CREATE TABLE IF NOT EXISTS user_model_credentials (
  user_id         INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  base_url        TEXT,
  credential_blob BYTEA,
  created_at      INTEGER NOT NULL,
  updated_at      INTEGER NOT NULL
);

UPDATE _schema_version SET version = 21;
