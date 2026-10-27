BEGIN;
SET LOCAL lock_timeout = '10s';
ALTER TABLE programs ADD COLUMN IF NOT EXISTS catalog character varying(32) NOT NULL DEFAULT 'dpo';
ALTER TABLE programs ADD COLUMN IF NOT EXISTS competencies json;
ALTER TABLE programs ADD COLUMN IF NOT EXISTS communication json;
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'programs_catalog_check' AND conrelid = 'public.programs'::regclass
  ) THEN
    ALTER TABLE programs ADD CONSTRAINT programs_catalog_check CHECK (catalog IN ('dpo', 'portfolio'));
  END IF;
END
$$;
CREATE TABLE IF NOT EXISTS trainers (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  fio character varying(255) NOT NULL,
  photo character varying(255),
  bio text,
  status character varying(255) NOT NULL DEFAULT 'draft',
  created_at timestamp with time zone NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS program_trainers (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  program_id uuid NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
  trainer_id uuid NOT NULL REFERENCES trainers(id) ON DELETE CASCADE,
  sort integer NOT NULL DEFAULT 0,
  CONSTRAINT uq_program_trainers_program_trainer UNIQUE (program_id, trainer_id)
);
CREATE INDEX IF NOT EXISTS idx_program_trainers_trainer ON program_trainers (trainer_id);
COMMIT;
