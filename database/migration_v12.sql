-- Persiste motivo e horário da escalação pra humano, hoje só existiam na mensagem
-- de notificação (perdidos depois de enviados) — necessário pro painel de leads
-- escalados (Lovable) mostrar o que aconteceu e há quanto tempo.
ALTER TABLE leads ADD COLUMN IF NOT EXISTS motivo_escalonamento TEXT;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS escalado_at TIMESTAMPTZ;
