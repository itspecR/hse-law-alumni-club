BEGIN;
SET LOCAL lock_timeout = '10s';
ALTER TABLE club_news_source_runs ADD COLUMN IF NOT EXISTS failures integer NOT NULL DEFAULT 0;
CREATE TABLE IF NOT EXISTS club_news_settings (
  id smallint PRIMARY KEY CONSTRAINT club_news_settings_single_row CHECK (id = 1),
  enabled boolean NOT NULL DEFAULT false,
  interval_hours integer NOT NULL DEFAULT 3 CONSTRAINT club_news_settings_interval_check CHECK (interval_hours IN (1, 3, 6, 12, 24)),
  updated_at timestamptz NOT NULL DEFAULT now()
);
COMMIT;
