-- ─────────────────────────────────────────────────────────────────────────────
-- Migration v10 — Catálogo de serviços + FAQ (etapa 1 da fundação do painel)
-- Execute no SQL Editor do Supabase DEPOIS de migration_v9.sql
-- Seguro rodar mais de uma vez; nenhum comando apaga dado.
-- ─────────────────────────────────────────────────────────────────────────────

-- 1. Catálogo de serviços. Sai do JSONB solto em tenants.servicos pra tabela própria:
--    é o que permite o dono da clínica configurar sozinho depois, e é o que dá um id
--    estável pras regras de recall/cross-sell apontarem (hoje elas casam por substring
--    do nome, e uma variação de escrita faz a regra não disparar em silêncio).
CREATE TABLE IF NOT EXISTS services (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id         UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  nome              TEXT NOT NULL,
  descricao         TEXT,
  -- Preço numérico (não texto) pra continuar calculável: somar ticket médio, gerar
  -- link de pagamento com o valor certo, filtrar por faixa.
  preco             NUMERIC(10,2),
  -- TRUE = "a partir de R$ 600"; FALSE = "R$ 250" fechado.
  preco_a_partir_de BOOLEAN NOT NULL DEFAULT FALSE,
  -- Substitui o SLOT_DURATION_MINUTES = 60 fixo de app/agent/tools.py. Passa a ser
  -- usado pelo check_availability na etapa 2.
  duracao_min       INTEGER NOT NULL DEFAULT 60 CHECK (duracao_min > 0),
  -- Desativa em vez de apagar: agendamentos antigos apontam pro serviço, e apagar
  -- quebraria o histórico e o faturamento por procedimento.
  ativo             BOOLEAN NOT NULL DEFAULT TRUE,
  created_at        TIMESTAMPTZ DEFAULT NOW(),
  updated_at        TIMESTAMPTZ DEFAULT NOW()
);

-- Nome único só entre os ATIVOS do mesmo tenant: desativar um serviço libera o nome
-- pra ser cadastrado de novo, sem colidir com o histórico.
DROP INDEX IF EXISTS services_tenant_nome_ativo;
CREATE UNIQUE INDEX services_tenant_nome_ativo
    ON services (tenant_id, lower(nome)) WHERE ativo;

CREATE INDEX IF NOT EXISTS idx_services_tenant_ativo ON services (tenant_id) WHERE ativo;

-- 2. FAQ — pares livres de pergunta/resposta, na ordem que a clínica quiser.
--    Vai como texto no system prompt (não RAG): uma clínica tem poucas políticas e
--    cabe folgado no contexto. Ver docs/superpowers/decisoes/2026-07-22-catalogo...
CREATE TABLE IF NOT EXISTS faq (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id  UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  pergunta   TEXT NOT NULL,
  resposta   TEXT NOT NULL,
  ordem      INTEGER NOT NULL DEFAULT 0,
  ativo      BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_faq_tenant_ativo ON faq (tenant_id, ordem) WHERE ativo;

-- 3. RLS — mesmo padrão das demais tabelas: só o backend (service_role) enxerga.
ALTER TABLE services ENABLE ROW LEVEL SECURITY;
ALTER TABLE faq      ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "service_role_services" ON services;
CREATE POLICY "service_role_services" ON services FOR ALL TO service_role USING (true);

DROP POLICY IF EXISTS "service_role_faq" ON faq;
CREATE POLICY "service_role_faq" ON faq FOR ALL TO service_role USING (true);

-- 4. Migra o que já existe em tenants.servicos (formato [{"nome":..,"preco":"R$ 250"}]).
--    O preço vem como texto livre e é convertido: tira tudo que não é dígito/vírgula/
--    ponto, remove separador de milhar e troca vírgula decimal por ponto.
--    "a partir de" no texto original vira a flag preco_a_partir_de.
--    duracao_min entra como 60 pra todos — a clínica ajusta depois, serviço a serviço.
INSERT INTO services (tenant_id, nome, preco, preco_a_partir_de, duracao_min)
SELECT
  t.id,
  s->>'nome',
  NULLIF(replace(replace(regexp_replace(COALESCE(s->>'preco',''), '[^0-9,\.]', '', 'g'), '.', ''), ',', '.'), '')::numeric,
  COALESCE(s->>'preco', '') ILIKE '%a partir%',
  60
FROM tenants t, jsonb_array_elements(t.servicos) s
WHERE jsonb_typeof(t.servicos) = 'array'
  AND COALESCE(s->>'nome', '') <> ''
ON CONFLICT DO NOTHING;

-- ═════════════════════════════════════════════════════════════════════════════
-- VERIFICAÇÃO — confira se os preços foram convertidos certo antes de confiar.
-- ═════════════════════════════════════════════════════════════════════════════
SELECT t.name AS tenant, s.nome, s.preco, s.preco_a_partir_de, s.duracao_min, s.ativo
FROM services s
JOIN tenants t ON t.id = s.tenant_id
ORDER BY t.name, s.nome;
