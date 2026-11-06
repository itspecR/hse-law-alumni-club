BEGIN;
SET LOCAL lock_timeout = '10s';
CREATE TABLE IF NOT EXISTS club_donations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  number character varying(32) NOT NULL UNIQUE,
  amount bigint NOT NULL,
  period character varying(16) NOT NULL DEFAULT 'once',
  email character varying(254) NOT NULL,
  name character varying(200),
  alumni_id uuid REFERENCES alumni(id) ON DELETE SET NULL,
  status character varying(16) NOT NULL DEFAULT 'new',
  payment_id character varying(64),
  paid_at timestamp with time zone,
  consents jsonb,
  created_at timestamp with time zone NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS club_donations_status_created ON club_donations (status, created_at);
COMMIT;
