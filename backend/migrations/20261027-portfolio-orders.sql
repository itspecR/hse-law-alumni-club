BEGIN;
SET LOCAL lock_timeout = '10s';
ALTER TABLE orders ADD COLUMN IF NOT EXISTS program_id uuid REFERENCES programs(id) ON DELETE SET NULL;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS participants integer NOT NULL DEFAULT 1;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS organization character varying(300);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS consents json;
CREATE INDEX IF NOT EXISTS idx_orders_program ON orders (program_id);
COMMIT;
