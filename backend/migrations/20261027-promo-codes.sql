BEGIN;
SET LOCAL lock_timeout = '10s';
CREATE TABLE IF NOT EXISTS promo_codes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  code character varying(32) NOT NULL UNIQUE,
  percent integer NOT NULL,
  starts_on date NOT NULL,
  ends_on date NOT NULL,
  total_limit integer,
  per_person_limit integer,
  all_programs boolean NOT NULL DEFAULT true,
  status character varying(32) NOT NULL DEFAULT 'active',
  note text,
  created_at timestamp with time zone NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS promo_code_programs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  promo_id uuid NOT NULL REFERENCES promo_codes(id) ON DELETE CASCADE,
  program_id uuid NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
  CONSTRAINT uq_promo_code_programs_promo_program UNIQUE (promo_id, program_id)
);
CREATE INDEX IF NOT EXISTS idx_promo_code_programs_program ON promo_code_programs (program_id);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS promo_id uuid REFERENCES promo_codes(id) ON DELETE SET NULL;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS promo_code character varying(32);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS discount_source character varying(32);
CREATE INDEX IF NOT EXISTS idx_orders_promo ON orders (promo_id);
COMMIT;
