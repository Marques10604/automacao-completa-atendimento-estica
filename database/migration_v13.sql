-- ─────────────────────────────────────────────────────────────────────────────
-- Migration v13 — Follow-up de resgate por silêncio
-- Execute no SQL Editor do Supabase DEPOIS de migration_v12.sql
-- Seguro rodar mais de uma vez; nenhum comando apaga dado.
-- ─────────────────────────────────────────────────────────────────────────────

-- followup_jobs.job_type precisa aceitar 'resgate_silencio'. A migration_v9 deixou
-- o CHECK com 5 tipos (v10/v11/v12 não mexem nele); sem ampliar de novo, o INSERT
-- feito pelo agendamento reativo em processar_mensagem() (Task 2) é rejeitado pelo
-- Postgres.
ALTER TABLE followup_jobs DROP CONSTRAINT IF EXISTS followup_jobs_job_type_check;
ALTER TABLE followup_jobs ADD CONSTRAINT followup_jobs_job_type_check
    CHECK (job_type IN ('appointment_reminder', 'payment_recovery', 'pos_venda',
                        'recall_procedimento', 'cross_sell', 'resgate_silencio'));

-- VERIFICAÇÃO — deve devolver 1.
SELECT COUNT(*) AS check_aceita_resgate_silencio
FROM pg_constraint
WHERE conname = 'followup_jobs_job_type_check'
  AND pg_get_constraintdef(oid) LIKE '%resgate_silencio%';
