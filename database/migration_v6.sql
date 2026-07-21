-- ─────────────────────────────────────────────────────────────────────────────
-- Migration v6 — Disponibilidade real (check_availability) + trava de corrida no
-- agendamento (book_appointment)
-- Execute no SQL Editor do Supabase DEPOIS de schema.sql e migration_v2.sql
-- ─────────────────────────────────────────────────────────────────────────────

-- 1a. Dedup necessário ANTES do UNIQUE: o _check_availability antigo (mock, sempre
--     "disponível") deixou passar overbooking real em produção — linhas diferentes
--     de appointments com o mesmo (tenant_id, scheduled_at), às vezes de leads
--     diferentes. Sem remover essas duplicatas, o CREATE UNIQUE INDEX abaixo falha
--     com 23505 (unique_violation) na hora de construir o índice.
--     Mantém, em cada grupo (tenant_id, scheduled_at), só a linha de created_at mais
--     antigo; apaga as demais. Em grupos sem duplicata, ROW_NUMBER()=1 pra linha
--     única e nada é removido — idempotente em bases já limpas.
--     ATENÇÃO: isso apaga appointments de verdade. Rode antes a query de auditoria
--     (SELECT ... GROUP BY tenant_id, scheduled_at HAVING COUNT(*) > 1) e confirme
--     que as linhas removidas não correspondem a clientes reais que precisam ser
--     recontatados pra remarcar — no ambiente onde esta migration foi escrita eram
--     todos dados de teste.
DELETE FROM appointments a
USING (
  SELECT id,
         ROW_NUMBER() OVER (
           PARTITION BY tenant_id, scheduled_at
           ORDER BY created_at ASC
         ) AS rn
  FROM appointments
) ranked
WHERE a.id = ranked.id
  AND ranked.rn > 1;

-- 1b. Confirma que a limpeza acima funcionou — deve devolver zero linhas antes de
--     seguir pro ALTER TABLE. Se devolver algo, pare e investigue antes de continuar.
SELECT tenant_id, scheduled_at, COUNT(*)
FROM appointments
GROUP BY tenant_id, scheduled_at
HAVING COUNT(*) > 1;

-- 1c. Impede dois leads fecharem o mesmo horário na mesma clínica (corrida: ambos
--    chamam check_availability, veem o mesmo slot livre, confirmam quase ao mesmo
--    tempo). Com esse constraint, a segunda tentativa de INSERT/UPDATE em
--    appointments pro mesmo (tenant_id, scheduled_at) é rejeitada pelo Postgres —
--    app/agent/tools.py::_book_appointment captura esse erro (23505 / duplicate
--    key) e devolve mensagem tratada ao lead em vez de estourar exceção crua.
ALTER TABLE appointments DROP CONSTRAINT IF EXISTS appointments_tenant_scheduled_at_unique;
ALTER TABLE appointments ADD CONSTRAINT appointments_tenant_scheduled_at_unique
    UNIQUE (tenant_id, scheduled_at);

-- 2. Formato estruturado de tenants.horarios (usado por check_availability)
-- Não cria coluna nova — tenants.horarios já existe (migration_v2.sql) como JSONB,
-- mas até agora era só texto livre exibido no prompt (ex: "Segunda a sexta: 9h às
-- 19h"). A partir de app/agent/tools.py::_check_availability, o valor passa a ser
-- lido programaticamente pra consultar disponibilidade real contra a tabela
-- appointments, e PRECISA seguir este formato: um objeto com uma chave por dia da
-- semana (seg/ter/qua/qui/sex/sab/dom), cada uma com [abertura, fechamento] em
-- "HH:MM", ou null se a clínica não atende nesse dia.
--
-- Se tenants.horarios estiver vazio ou não for um dict nesse formato, o código usa
-- um expediente padrão embutido (DEFAULT_HORARIOS em tools.py: seg-sex 9h-19h,
-- sáb 9h-14h, dom fechado) — mesmo horário hoje exibido como texto no prompt.

-- Exemplo de como configurar o expediente estruturado pra um tenant específico
-- (troca 'minha-clinica' pelo nome/slug real do tenant)
-- UPDATE tenants
-- SET horarios = '{
--   "seg": ["09:00", "19:00"],
--   "ter": ["09:00", "19:00"],
--   "qua": ["09:00", "19:00"],
--   "qui": ["09:00", "19:00"],
--   "sex": ["09:00", "19:00"],
--   "sab": ["09:00", "14:00"],
--   "dom": null
-- }'::jsonb
-- WHERE name = 'minha-clinica';
