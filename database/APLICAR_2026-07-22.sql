-- ═════════════════════════════════════════════════════════════════════════════
-- APLICAR NO SUPABASE — projeto mpkucwmbylpzbmvidydu
-- Gerado em 2026-07-22 após conferir o schema real do banco.
--
-- COMO USAR: cole este arquivo inteiro no SQL Editor do Supabase e execute.
-- Rode ANTES de subir o código novo (itens 1-3 do roadmap dependem disso).
--
-- É seguro rodar mais de uma vez: tudo aqui é idempotente (IF NOT EXISTS /
-- IF EXISTS) e NENHUM comando apaga dado. A migration_v6, que tem DELETE
-- destrutivo, NÃO está incluída — ela já foi aplicada neste banco.
--
-- Conteúdo: migration_v4 (que estava faltando) + migration_v8 (nova).
-- As migrations originais continuam sendo a fonte de verdade em database/.
-- ═════════════════════════════════════════════════════════════════════════════


-- ─────────────────────────────────────────────────────────────────────────────
-- PARTE 1 — migration_v4: idempotência de webhook  [ESTAVA FALTANDO NESTE BANCO]
-- ─────────────────────────────────────────────────────────────────────────────
-- Sem esta tabela, memory.is_duplicate_message() falha em aberto: o INSERT
-- estoura "tabela não existe", que não é erro de chave duplicada, então a função
-- loga e devolve False. Resultado: NENHUMA mensagem é reconhecida como repetida,
-- e toda vez que a Meta reenvia o webhook por timeout o lead recebe a mesma
-- resposta duas vezes.

CREATE TABLE IF NOT EXISTS processed_messages (
  wamid      TEXT PRIMARY KEY,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE processed_messages ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "service_role_processed_messages" ON processed_messages;
CREATE POLICY "service_role_processed_messages" ON processed_messages FOR ALL TO service_role USING (true);


-- ─────────────────────────────────────────────────────────────────────────────
-- PARTE 2 — migration_v8: cancelamento e remarcação de agendamento
-- ─────────────────────────────────────────────────────────────────────────────

-- 2a. Cancelamento é lógico, não físico: a linha continua no banco com o carimbo
--     de quando foi cancelada. É esse histórico que vira taxa de cancelamento no
--     relatório pro dono. DELETE apagaria a informação de que houve cancelamento.
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMPTZ;

-- 2b. O UNIQUE (tenant_id, scheduled_at) da migration_v6 impede dois leads
--     fecharem o mesmo horário — isso continua valendo. Mas ele não sabe o que é
--     cancelamento: a linha cancelada continuaria ocupando o par (tenant_id,
--     scheduled_at) e bloquearia aquele horário para sempre. Trocando por índice
--     parcial, a trava passa a valer só entre agendamentos ATIVOS — cancelou, o
--     horário volta a ser vendável na hora.
ALTER TABLE appointments DROP CONSTRAINT IF EXISTS appointments_tenant_scheduled_at_unique;

DROP INDEX IF EXISTS appointments_tenant_slot_ativo;
CREATE UNIQUE INDEX appointments_tenant_slot_ativo
    ON appointments (tenant_id, scheduled_at)
    WHERE cancelled_at IS NULL;

-- 2c. followup_jobs.status precisa aceitar 'cancelled'. A migration_v2 criou a
--     coluna com CHECK (status IN ('pending','done','failed')); sem ampliar, o
--     UPDATE feito por _cancelar_lembretes_pendentes() é rejeitado pelo Postgres
--     e o lembrete da data ANTIGA continuaria disparando ("seu agendamento é
--     amanhã" sobre um horário que não existe mais).
--     'cancelled' é distinto de 'done' de propósito: done = lembrete enviado ao
--     lead; cancelled = nunca enviado porque o agendamento mudou ou foi desmarcado.
ALTER TABLE followup_jobs DROP CONSTRAINT IF EXISTS followup_jobs_status_check;
ALTER TABLE followup_jobs ADD CONSTRAINT followup_jobs_status_check
    CHECK (status IN ('pending', 'done', 'failed', 'cancelled'));


-- ═════════════════════════════════════════════════════════════════════════════
-- VERIFICAÇÃO — as 4 colunas do resultado devem vir todas com valor 1.
-- Se alguma vier 0, aquele passo não foi aplicado: pare e investigue.
-- ═════════════════════════════════════════════════════════════════════════════
SELECT
  (SELECT COUNT(*) FROM information_schema.tables
     WHERE table_schema = 'public' AND table_name = 'processed_messages')      AS tabela_processed_messages,
  (SELECT COUNT(*) FROM information_schema.columns
     WHERE table_name = 'appointments' AND column_name = 'cancelled_at')       AS coluna_cancelled_at,
  (SELECT COUNT(*) FROM pg_indexes
     WHERE indexname = 'appointments_tenant_slot_ativo')                       AS indice_slot_ativo,
  (SELECT COUNT(*) FROM pg_constraint
     WHERE conname = 'followup_jobs_status_check'
       AND pg_get_constraintdef(oid) LIKE '%cancelled%')                       AS check_aceita_cancelled;
