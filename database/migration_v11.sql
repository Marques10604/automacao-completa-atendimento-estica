-- ─────────────────────────────────────────────────────────────────────────────
-- Migration v11 — Duração real por agendamento (etapa 2 do catálogo)
-- Execute no SQL Editor do Supabase DEPOIS de migration_v10.sql
-- Seguro rodar mais de uma vez; nenhum comando apaga dado.
-- ─────────────────────────────────────────────────────────────────────────────

-- Até aqui, check_availability tratava todo agendamento como um bloco fixo de 60min,
-- não importa o serviço. Isso já causava dois problemas reais: serviço mais curto
-- desperdiçava agenda (bloqueava 60min pra um procedimento de 30), e serviço mais
-- longo arriscava overbooking (um procedimento de 90min só bloqueava 60, liberando
-- o restante pra outro cliente marcar em cima).
--
-- A duração é capturada NO MOMENTO do agendamento (não recalculada depois, lendo
-- services toda vez) — assim, se a clínica mudar a duração cadastrada de um serviço
-- no futuro, os agendamentos já feitos não mudam de tamanho retroativamente.
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS duracao_min INTEGER NOT NULL DEFAULT 60
    CHECK (duracao_min > 0);

-- Verificação — deve devolver zero linhas (nenhum valor inválido além do default).
SELECT id, duracao_min FROM appointments WHERE duracao_min <= 0;
