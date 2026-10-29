BEGIN;
SET LOCAL lock_timeout = '10s';
ALTER TABLE news ADD COLUMN IF NOT EXISTS cover character varying(500);
ALTER TABLE news ADD COLUMN IF NOT EXISTS origin character varying(32) NOT NULL DEFAULT 'club';
UPDATE news SET origin = 'faculty' WHERE origin = 'club' AND source_url LIKE 'https://pravo.hse.ru/%';
COMMIT;
