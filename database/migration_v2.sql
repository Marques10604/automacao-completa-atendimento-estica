-- Adiciona campo ig_user_id e status em leads
ALTER TABLE leads ADD COLUMN IF NOT EXISTS ig_user_id TEXT;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'novo';

-- Agendamentos
CREATE TABLE IF NOT EXISTS appointments (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  lead_id      UUID REFERENCES leads(id) ON DELETE CASCADE,
  tenant_id    UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  service      TEXT,
  scheduled_at TIMESTAMPTZ NOT NULL,
  confirmed    BOOLEAN DEFAULT FALSE,
  created_at   TIMESTAMPTZ DEFAULT NOW()
);

-- Jobs de follow-up
CREATE TABLE IF NOT EXISTS followup_jobs (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  lead_id      UUID REFERENCES leads(id) ON DELETE CASCADE,
  tenant_id    UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  channel      TEXT NOT NULL CHECK (channel IN ('whatsapp', 'instagram')),
  phone        TEXT,
  ig_user_id   TEXT,
  job_type     TEXT NOT NULL CHECK (job_type IN ('appointment_reminder', 'payment_recovery', 'pos_venda')),
  scheduled_at TIMESTAMPTZ NOT NULL,
  executed_at  TIMESTAMPTZ,
  status       TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'done', 'failed')),
  payload      JSONB DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_followup_pending ON followup_jobs(scheduled_at)
  WHERE status = 'pending';

-- Consentimento LGPD
CREATE TABLE IF NOT EXISTS consent_log (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  lead_id      UUID REFERENCES leads(id) ON DELETE CASCADE,
  tenant_id    UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  channel      TEXT,
  consent_text TEXT NOT NULL,
  consented_at TIMESTAMPTZ DEFAULT NOW()
);

-- RLS para novas tabelas
ALTER TABLE appointments   ENABLE ROW LEVEL SECURITY;
ALTER TABLE followup_jobs  ENABLE ROW LEVEL SECURITY;
ALTER TABLE consent_log    ENABLE ROW LEVEL SECURITY;

CREATE POLICY "service_role_appointments"  ON appointments  FOR ALL TO service_role USING (true);
CREATE POLICY "service_role_followup_jobs" ON followup_jobs FOR ALL TO service_role USING (true);
CREATE POLICY "service_role_consent_log"   ON consent_log   FOR ALL TO service_role USING (true);

-- Adiciona campos Meta para tenants
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS ig_access_token TEXT;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS asaas_api_key   TEXT;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS servicos        JSONB;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS horarios        JSONB;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS ig_page_id      TEXT;
