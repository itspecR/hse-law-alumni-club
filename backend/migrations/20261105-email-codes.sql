BEGIN;
SET LOCAL lock_timeout = '10s';
CREATE TABLE IF NOT EXISTS club_email_codes (
  id bigserial PRIMARY KEY,
  user_id uuid NOT NULL,
  purpose character varying(32) NOT NULL,
  code_hash character varying(64),
  attempts integer NOT NULL DEFAULT 0,
  expires_at timestamp with time zone,
  sent_at timestamp with time zone NOT NULL,
  window_started_at timestamp with time zone NOT NULL,
  window_count integer NOT NULL DEFAULT 0,
  CONSTRAINT club_email_codes_user_purpose UNIQUE (user_id, purpose)
);
COMMIT;
