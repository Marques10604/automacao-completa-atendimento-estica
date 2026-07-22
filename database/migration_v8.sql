-- ─────────────────────────────────────────────────────────────────────────────
-- Migration v8 — Cancelamento e remarcação de agendamento pelo próprio lead
-- Execute no SQL Editor do Supabase DEPOIS de migration_v6.sql e migration_v7.sql
-- ─────────────────────────────────────────────────────────────────────────────

-- 1. Cancelamento é lógico, não físico: a linha continua no banco com o carimbo de
--    quando foi cancelada. Isso é o que permite medir taxa de cancelamento e no-show
--    por clínica depois (o resumo diário pro dono depende disso). DELETE apagaria a
--    informação de que o cancelamento existiu.
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMPTZ;

-- 2. O UNIQUE (tenant_id, scheduled_at) da migration_v6 impedia dois leads fecharem o
--    mesmo horário — isso continua valendo, MAS ele não sabe o que é cancelamento:
--    com a coluna acima, um horário cancelado continuaria bloqueando o slot pra
--    sempre, porque a linha cancelada ainda ocupa o par (tenant_id, scheduled_at).
--    A troca por índice parcial faz a trava valer só entre agendamentos ATIVOS:
--    cancelou, o horário volta a ser vendável na hora.
ALTER TABLE appointments DROP CONSTRAINT IF EXISTS appointments_tenant_scheduled_at_unique;

DROP INDEX IF EXISTS appointments_tenant_slot_ativo;
CREATE UNIQUE INDEX appointments_tenant_slot_ativo
    ON appointments (tenant_id, scheduled_at)
    WHERE cancelled_at IS NULL;

-- 3. Verificação — deve devolver zero linhas. Se devolver algo, existem dois
--    agendamentos ATIVOS no mesmo horário e o índice acima não foi criado.
SELECT tenant_id, scheduled_at, COUNT(*)
FROM appointments
WHERE cancelled_at IS NULL
GROUP BY tenant_id, scheduled_at
HAVING COUNT(*) > 1;

-- 4. followup_jobs.status precisa aceitar 'cancelled'. A migration_v2 criou a coluna
--    com CHECK (status IN ('pending','done','failed')) — sem ampliar esse CHECK, o
--    UPDATE feito por _cancelar_lembretes_pendentes() é rejeitado pelo Postgres e o
--    lembrete da data antiga continuaria disparando.
--    'cancelled' é distinto de 'done' de propósito: 'done' significa que o lembrete
--    foi enviado ao lead; 'cancelled' significa que ele nunca foi enviado porque o
--    agendamento mudou ou foi desmarcado. Juntar os dois estragaria a métrica de
--    quantos lembretes realmente saíram.
ALTER TABLE followup_jobs DROP CONSTRAINT IF EXISTS followup_jobs_status_check;
ALTER TABLE followup_jobs ADD CONSTRAINT followup_jobs_status_check
    CHECK (status IN ('pending', 'done', 'failed', 'cancelled'));

-- 4b. Verificação — deve devolver zero linhas (nenhum status fora do CHECK novo).
SELECT status, COUNT(*)
FROM followup_jobs
WHERE status NOT IN ('pending', 'done', 'failed', 'cancelled')
GROUP BY status;
