-- ─────────────────────────────────────────────────────────────────────────────
-- Migration v7 — agent_failures (observabilidade de falha do agente)
-- Execute no SQL Editor do Supabase DEPOIS de schema.sql, migration_v2.sql e
-- migration_v6.sql
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS agent_failures (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id  UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  lead_id    UUID REFERENCES leads(id) ON DELETE CASCADE,
  phone      TEXT,
  canal      TEXT NOT NULL CHECK (canal IN ('whatsapp', 'instagram')),
  -- loop_esgotado e tool_error já fazem parte do enum pra não precisar de outra
  -- migration quando forem ligados no código — só processamento/envio são
  -- gravados nesta versão (ver app/services/failure_service.py).
  tipo_falha TEXT NOT NULL CHECK (tipo_falha IN ('processamento', 'envio', 'loop_esgotado', 'tool_error')),
  detalhe    TEXT,
  payload    JSONB DEFAULT '{}',
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Suporta a query de "falhas recentes desse lead" usada por
-- app/services/failure_service.py::registrar_falha pra decidir se escala pra humano.
CREATE INDEX IF NOT EXISTS idx_agent_failures_lead_created
  ON agent_failures (lead_id, created_at);

ALTER TABLE agent_failures ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "service_role_agent_failures" ON agent_failures;
CREATE POLICY "service_role_agent_failures" ON agent_failures FOR ALL TO service_role USING (true);
