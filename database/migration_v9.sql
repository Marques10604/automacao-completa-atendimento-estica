-- ─────────────────────────────────────────────────────────────────────────────
-- Migration v9 — Cross-sell pós-procedimento
-- Execute no SQL Editor do Supabase DEPOIS de migration_v8.sql
-- Seguro rodar mais de uma vez; nenhum comando apaga dado.
-- ─────────────────────────────────────────────────────────────────────────────

-- 1. Regras de cross-sell por tenant.
--    Formato: {"procedimento feito": {"oferecer": "procedimento complementar", "dias": N}}
--    Ex.: {"botox": {"oferecer": "preenchimento labial", "dias": 30}}
--
--    Coluna separada de procedimentos_recall de propósito: são duas intenções de
--    negócio diferentes e podem coexistir no mesmo procedimento. O mesmo botox pode
--    gerar recall em 180 dias (repetir botox) E cross-sell em 30 dias (oferecer
--    preenchimento). Num mapa só, uma regra sobrescreveria a outra.
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS cross_sell JSONB;

-- 2. followup_jobs.job_type precisa aceitar 'cross_sell'. A migration_v2 criou o CHECK
--    com 3 tipos e a v3 acrescentou 'recall_procedimento'; sem ampliar de novo, o
--    INSERT de _agendar_cross_sell_se_configurado() é rejeitado pelo Postgres.
ALTER TABLE followup_jobs DROP CONSTRAINT IF EXISTS followup_jobs_job_type_check;
ALTER TABLE followup_jobs ADD CONSTRAINT followup_jobs_job_type_check
    CHECK (job_type IN ('appointment_reminder', 'payment_recovery', 'pos_venda',
                        'recall_procedimento', 'cross_sell'));

-- ═════════════════════════════════════════════════════════════════════════════
-- VERIFICAÇÃO — as duas colunas devem vir 1.
-- ═════════════════════════════════════════════════════════════════════════════
SELECT
  (SELECT COUNT(*) FROM information_schema.columns
     WHERE table_name='tenants' AND column_name='cross_sell')          AS coluna_cross_sell,
  (SELECT COUNT(*) FROM pg_constraint
     WHERE conname='followup_jobs_job_type_check'
       AND pg_get_constraintdef(oid) LIKE '%cross_sell%')              AS check_aceita_cross_sell;
