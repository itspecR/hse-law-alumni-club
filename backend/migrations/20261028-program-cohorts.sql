BEGIN;
SET LOCAL lock_timeout = '10s';
CREATE TABLE IF NOT EXISTS program_cohorts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  program_id uuid NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
  starts_on date NOT NULL,
  price bigint,
  capacity integer,
  enrollment character varying(32) NOT NULL DEFAULT 'actual',
  created_at timestamp with time zone NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_program_cohorts_program ON program_cohorts (program_id);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS cohort_id uuid REFERENCES program_cohorts(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_orders_cohort ON orders (cohort_id);
COMMIT;
