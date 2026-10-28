BEGIN;
SET LOCAL lock_timeout = '10s';
CREATE TABLE IF NOT EXISTS club_captcha_uses (
  salt_hash character varying(64) PRIMARY KEY,
  expires_at timestamp with time zone NOT NULL
);
CREATE INDEX IF NOT EXISTS club_captcha_uses_expires_idx ON club_captcha_uses (expires_at);
COMMIT;
