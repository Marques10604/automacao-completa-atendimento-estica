# AGENTE.md — Capacidades do Agente de Atendimento (Automação Completa Atendimento Estica)

> Snapshot do que o código faz **hoje**, gerado por leitura direta do repositório em 2026-07-21. Não é um plano — é o estado real.

## 1. Visão geral

Agente de IA (Claude com *tool use*) que atende clínicas de estética de alto ticket via WhatsApp, substituindo o atendimento humano do primeiro contato até o agendamento. Ele conduz a conversa com qualificação BANT natural (sem parecer formulário), consulta e reserva horários reais na agenda, gera link de pagamento via Asaas e faz follow-up automático (lembrete, recuperação de pagamento, pós-venda, recall de procedimento). Multi-tenant: uma instância atende várias clínicas isoladas por Row Level Security no Supabase.

## 2. Capacidades por área

### Qualificação e conversa
- **O que faz:** mantém histórico persistido no Supabase (`conversations`) e reenvia as últimas mensagens a cada turno como contexto para a Claude; qualifica BANT via prompt, não por máquina de estados.
- **Arquivo/função:** `memory.get_messages()` (`memory.py:96`) — janela de **20 mensagens** (`limit=20` default), em ordem cronológica. Orquestração em `processar_mensagem()` (`app/agent/claude_client.py:25`), loop de até 5 rodadas de tool_use por turno.
- **Status:** ✅ ativo.

### Agendamento
- **O que faz:** `check_availability` calcula slots reais de 60 min a partir de `tenants.horarios` (JSONB por dia da semana, com fallback padrão seg-sex 9h-19h / sáb 9h-14h) e **exclui horários já ocupados**, consultando `appointments` de verdade. `book_appointment` grava/atualiza o agendamento; se o lead já tem um agendamento futuro em aberto, atualiza em vez de duplicar.
- **Trava de corrida:** `appointments` tem `UNIQUE (tenant_id, scheduled_at)` (`database/migration_v6.sql`) — se dois leads confirmarem o mesmo slot quase ao mesmo tempo, o Postgres rejeita a segunda gravação e o código devolve uma mensagem tratada ("horário acabou de ser reservado") em vez de deixar a exceção estourar.
- **Arquivo/função:** `_check_availability()` e `_book_appointment()` (`app/agent/tools.py:157` e `:256`).
- **Status:** ✅ ativo (não é mais mock — o README ainda descreve a versão antiga).

### Tratamento de falha
- **O que faz:** todo envio de WhatsApp tenta até 3x com backoff curto (`_enviar_com_retry`, `main.py:164`); se todas falharem, tenta um fallback simples ("tive um imprevisto, já volto"). Toda falha (processamento ou envio) é gravada em `agent_failures`. Se o mesmo lead acumular **3 falhas em 30 minutos**, o sistema escala automaticamente para humano e notifica `tenants.staff_phone` via WhatsApp — sem depender do modelo decidir isso.
- **Arquivo/função:** `_registrar_falha_e_escalar()` (`main.py:189`), `registrar_falha()` / `escalar_por_falhas()` (`app/services/failure_service.py:15`/`:61`). Handoff manual via tool `escalate_to_human` (`app/agent/tools.py:397`) quando o próprio lead pede humano ou relata reação grave.
- **Status:** ✅ ativo (implementado nos commits B2 / `1eb33d0`).

### Mídia
- **O que é suportado hoje:** qualquer mensagem não-texto (áudio, foto, documento, vídeo) recebe um aviso educado e específico por tipo, e fica registrada no histórico (`[lead enviou áudio]` etc.) — não é mais descartada em silêncio (fix B1 fase 1, commit `7aeb8f4`). Mesma lógica espelhada em WhatsApp e Instagram via `resposta_midia_nao_suportada()` (`app/webhooks/media_fallback.py:16`).
- **O que falta:** transcrição de áudio e visão computacional (foto/documento) — nenhum conteúdo de mídia é interpretado pela IA ainda. Isso é a "B1 fase 2" pendente.
- **Status:** 🟡 parcial (não descarta mais, mas não processa).

### Canais
- **WhatsApp:** ativo, via **Meta Cloud API oficial** (`graph.facebook.com`, não Evolution/Baileys) — `app/agent/dispatcher.py:15`. Idempotência por `wamid` (tabela `processed_messages`), debounce de 2.5s para agrupar rajadas de mensagens, validação de assinatura `X-Hub-Signature-256`, rate limit de 10/min no webhook.
- **Instagram:** o código existe e está registrado em `main.py` (`app.include_router(instagram_router)`), inclusive com o mesmo tratamento de mídia — mas **não tem idempotência/dedup** equivalente ao `wamid` do WhatsApp (não há checagem de mensagem duplicada em `app/webhooks/instagram.py`). Por isso, operacionalmente, o canal Instagram está desativado em produção e atendido via ManyChat por enquanto.
- **Nota:** a estratégia original era começar pela UazAPI e migrar depois para a API oficial da Meta. O código deste repositório já reflete a migração feita — `dispatcher.py` fala direto com `graph.facebook.com` (Meta Cloud API oficial), sem vestígio de UazAPI.
- **Status:** WhatsApp ✅ ativo (já na API oficial) · Instagram 🟡 código pronto, desativado por falta de dedup.

### Segurança
- **RLS:** todas as tabelas (`tenants`, `leads`, `conversations`, `sessions`, `appointments`, `followup_jobs`, `consent_log`, `processed_messages`, `agent_failures`) têm Row Level Security habilitado, com policy liberando apenas `service_role` (backend) — nada exposto publicamente.
- **LGPD:** opt-in explícito e determinístico na primeira mensagem (não delegado ao modelo, para não misturar com a resposta do lead), registrado em `consent_log` uma vez por lead. Comando `SAIR` marca o lead como `frio` e para a automação; se o lead voltar a escrever por conta própria, reativa para `novo` automaticamente.
- **Outros:** rate limiting via `slowapi` nos webhooks (10/min), validação de assinatura Meta no WhatsApp, endpoints admin protegidos por header `X-Admin-Key`.
- **Status:** ✅ ativo.

## 3. O que é mock/limitado ainda

- **Transcrição de áudio e visão (foto/documento):** não existe — B1 fase 2 pendente.
- **Instagram em produção:** código pronto mas não usado, por falta de dedup de mensagem repetida (Meta reenvia webhook em timeout, como acontece no WhatsApp).
- **Cancelamento/remarcação de agendamento pelo próprio lead:** não existe — só criação/atualização via `book_appointment`.
- **Templates HSM aprovados no Meta Business:** necessários para mensagem proativa em produção (`appointment_reminder`, `payment_recovery`, `pos_venda`, `recall_procedimento`) — não confirmado neste repositório se já foram aprovados.
- **README.md desatualizado:** ainda descreve `check_availability` como mock; o código atual já bate contra `appointments` reais.

## 4. Schema principal (Supabase)

| Tabela | Para que serve |
|---|---|
| `tenants` | Um registro por clínica: credenciais Meta/Asaas, serviços, `horarios` (JSONB por dia da semana), `staff_phone`, `procedimentos_recall` |
| `leads` | Um contato por tenant: `stage` (funil novo→qualificado→agendado→fechado→frio), `escalado` (handoff humano) |
| `appointments` | Agendamentos confirmados — `lead_id`, `service`, `scheduled_at`, com `UNIQUE(tenant_id, scheduled_at)` contra corrida |
| `conversations` | Histórico completo de mensagens — vira o contexto enviado à Claude a cada turno |
| `agent_failures` | Falha registrada por tentativa (processamento/envio), usada para decidir escalação automática por N falhas consecutivas |
