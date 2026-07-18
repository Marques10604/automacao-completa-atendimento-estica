-- ─────────────────────────────────────────────────────────────────────────────
-- Migration v5 — Renomeia coluna de notificação da clínica pra algo genérico
-- (ivonilson_phone era o número pessoal usado em testes; cada cliente futuro
-- vai ter o próprio número de secretária/staff, não faz sentido nome pessoal)
-- ─────────────────────────────────────────────────────────────────────────────

ALTER TABLE tenants RENAME COLUMN ivonilson_phone TO staff_phone;
