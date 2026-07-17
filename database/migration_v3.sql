-- ─────────────────────────────────────────────────────────────────────────────
-- Migration v3 — Recall de procedimento (lembrete de retorno)
-- Execute no SQL Editor do Supabase DEPOIS de schema.sql e migration_v2.sql
-- ─────────────────────────────────────────────────────────────────────────────

-- 1. Novo campo no tenant: mapa "nome do procedimento" -> "dias até o recall"
--    Exemplo de valor pra colocar depois:
--    {"botox": 180, "harmonizacao facial": 180, "limpeza de pele": 30, "peeling": 90}
--    O doutor/operador edita esse JSON direto na tabela `tenants` no Supabase
--    (Table Editor -> tenants -> coluna procedimentos_recall -> editar célula).
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS procedimentos_recall JSONB DEFAULT '{}'::jsonb;

-- 2. Permite o novo job_type 'recall_procedimento' em followup_jobs.
--    Postgres não deixa só "adicionar" um valor a um CHECK — recria a constraint.
ALTER TABLE followup_jobs DROP CONSTRAINT IF EXISTS followup_jobs_job_type_check;
ALTER TABLE followup_jobs ADD CONSTRAINT followup_jobs_job_type_check
    CHECK (job_type IN ('appointment_reminder', 'payment_recovery', 'pos_venda', 'recall_procedimento'));

-- ─────────────────────────────────────────────────────────────────────────────
-- Exemplo de como configurar o recall pra um tenant específico
-- (troca 'minha-clinica' pelo nome/slug real do tenant)
-- ─────────────────────────────────────────────────────────────────────────────
-- UPDATE tenants
-- SET procedimentos_recall = '{
--   "botox": 180,
--   "harmonizacao facial": 180,
--   "limpeza de pele": 30,
--   "peeling": 90,
--   "lentes de porcelana": 730
-- }'::jsonb
-- WHERE name = 'minha-clinica';
