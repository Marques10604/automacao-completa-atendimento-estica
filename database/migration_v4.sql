-- ─────────────────────────────────────────────────────────────────────────────
-- Migration v4 — Idempotência de webhook (evita reprocessar mensagem duplicada
-- quando a Meta reenvia o mesmo webhook por timeout)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS processed_messages (
  wamid      TEXT PRIMARY KEY,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE processed_messages ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "service_role_processed_messages" ON processed_messages;
CREATE POLICY "service_role_processed_messages" ON processed_messages FOR ALL TO service_role USING (true);
