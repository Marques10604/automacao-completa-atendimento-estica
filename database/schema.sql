-- ─────────────────────────────────────────────────────────────────────────────
-- Schema Supabase — Produto Real (Agente de Atendimento Multi-Tenant)
-- Execute no SQL Editor do Supabase
-- ─────────────────────────────────────────────────────────────────────────────

-- Habilita extensão para UUIDs
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";


-- ─────────────────────────────────────────────────────────────────────────────
-- TENANTS — um registro por cliente (clínica, salão, etc.)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE tenants (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name                TEXT NOT NULL,                  -- nome interno (slug)
    clinic_name         TEXT NOT NULL,                  -- nome exibido ao cliente
    professional_name   TEXT NOT NULL,                  -- nome do assistente virtual
    payment_link        TEXT,                           -- link de pagamento do sinal
    whatsapp_token      TEXT,                           -- token WhatsApp Cloud API
    phone_number_id     TEXT,                           -- ID do número Meta
    instagram_token     TEXT,                           -- token Instagram Graph API
    verify_token        TEXT,                           -- token verificação webhook
    ativo               BOOLEAN DEFAULT TRUE,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

-- ─────────────────────────────────────────────────────────────────────────────
-- LEADS — um registro por contato em cada tenant
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE leads (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    phone               TEXT NOT NULL,                  -- identificador do contato
    name                TEXT,                           -- nome coletado na conversa
    stage               TEXT NOT NULL DEFAULT 'qualificacao',
    procedimento        TEXT,                           -- interesse identificado
    data_agendamento    TEXT,                           -- data/hora confirmada
    canal               TEXT DEFAULT 'whatsapp',        -- "whatsapp" ou "instagram"
    escalado            BOOLEAN DEFAULT FALSE,          -- se foi escalado para humano
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(tenant_id, phone)
);

-- ─────────────────────────────────────────────────────────────────────────────
-- SESSIONS — estado atual da sessão (substituí o dicionário Python)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE sessions (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    phone               TEXT NOT NULL,
    stage               TEXT NOT NULL DEFAULT 'qualificacao',
    last_activity       TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(tenant_id, phone)
);

-- ─────────────────────────────────────────────────────────────────────────────
-- CONVERSATIONS — histórico completo de mensagens
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE conversations (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    phone       TEXT NOT NULL,
    role        TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content     TEXT NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Índice para busca rápida do histórico por tenant + phone
CREATE INDEX idx_conversations_tenant_phone ON conversations(tenant_id, phone, created_at);


-- ─────────────────────────────────────────────────────────────────────────────
-- ROW LEVEL SECURITY (RLS)
-- Garante que cada tenant só acessa seus próprios dados
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE tenants      ENABLE ROW LEVEL SECURITY;
ALTER TABLE leads        ENABLE ROW LEVEL SECURITY;
ALTER TABLE sessions     ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;

-- tenants: service role lê tudo; role anon não acessa
CREATE POLICY "service_role_tenants" ON tenants
    FOR ALL TO service_role USING (true);

-- leads: cada tenant acessa só os seus
CREATE POLICY "tenant_isolation_leads" ON leads
    FOR ALL TO service_role USING (true);

-- sessions: cada tenant acessa só as suas
CREATE POLICY "tenant_isolation_sessions" ON sessions
    FOR ALL TO service_role USING (true);

-- conversations: cada tenant acessa só as suas
CREATE POLICY "tenant_isolation_conversations" ON conversations
    FOR ALL TO service_role USING (true);


-- ─────────────────────────────────────────────────────────────────────────────
-- TRIGGER — atualiza updated_at do lead automaticamente
-- ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER leads_updated_at
    BEFORE UPDATE ON leads
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();


-- ─────────────────────────────────────────────────────────────────────────────
-- TENANT DE EXEMPLO (remova em produção)
-- ─────────────────────────────────────────────────────────────────────────────
INSERT INTO tenants (name, clinic_name, professional_name, payment_link)
VALUES (
    'lumina',
    'Clínica Lumina Estética',
    'Dra. Ana Paula',
    'https://pay.lumina.com.br/sinal'
);
