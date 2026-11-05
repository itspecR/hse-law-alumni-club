BEGIN;
SET LOCAL lock_timeout = '10s';
ALTER TABLE alumni ADD COLUMN IF NOT EXISTS mailing_consent_at timestamp with time zone;
ALTER TABLE alumni ADD COLUMN IF NOT EXISTS mailing_consent_version character varying(32);
COMMIT;
