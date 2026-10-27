BEGIN;
SET LOCAL lock_timeout = '10s';
ALTER TABLE orders ADD COLUMN IF NOT EXISTS office_discount integer;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS office_reason character varying(300);
COMMIT;
