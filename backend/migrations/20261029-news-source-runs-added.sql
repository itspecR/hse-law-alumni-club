BEGIN;
SET LOCAL lock_timeout = '10s';
ALTER TABLE club_news_source_runs ADD COLUMN IF NOT EXISTS added integer NOT NULL DEFAULT 0;
COMMIT;
