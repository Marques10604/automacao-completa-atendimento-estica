-- ─────────────────────────────────────────────────────────────────────────────
-- Migration v6 — Disponibilidade real (check_availability) + trava de corrida no
-- agendamento (book_appointment)
-- Execute no SQL Editor do Supabase DEPOIS de schema.sql e migration_v2.sql
-- ─────────────────────────────────────────────────────────────────────────────

-- 1. Impede dois leads fecharem o mesmo horário na mesma clínica (corrida: ambos
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
