BEGIN;
SET LOCAL lock_timeout = '10s';
ALTER TABLE programs ADD COLUMN IF NOT EXISTS cover_source character varying(500);
CREATE TABLE IF NOT EXISTS club_dpo_sync (
  id smallint PRIMARY KEY CONSTRAINT club_dpo_sync_single_row CHECK (id = 1),
  checked_at timestamptz NOT NULL DEFAULT now(),
  found integer NOT NULL DEFAULT 0,
  created integer NOT NULL DEFAULT 0,
  updated integer NOT NULL DEFAULT 0,
  archived integer NOT NULL DEFAULT 0,
  failures integer NOT NULL DEFAULT 0,
  error text
);
COMMIT;
