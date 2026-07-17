# Automação Completa — Agente de Vendas IA

Agente de IA de vendas end-to-end para clínicas de estética de alto ticket (harmonização facial, implante, cirurgia estética). Substitui o atendente humano do primeiro contato ao fechamento: qualifica (BANT), agenda, gera link de pagamento (Pix/cartão via Asaas), faz follow-up automático D+1 e roda em WhatsApp + Instagram via API oficial da Meta. Multi-tenant, persistência no Supabase com RLS.

> Ver `CONTEXT.md` para arquitetura completa, schema Supabase, pesquisa de mercado e narrativa de venda do produto.

---

## O que faz

| Tool (Claude function calling) | O que faz |
|---|---|
| `check_availability` | Consulta slots livres na agenda (⚠️ ainda mock — ver "O que falta") |
| `book_appointment` | Cria agendamento confirmado no Supabase |
| `generate_payment_link` | Gera link de pagamento Pix/cartão via Asaas |
| `migrate_to_whatsapp` | No Instagram: manda o lead pro WhatsApp pra fechar |
| `update_lead_status` | Atualiza estágio: novo → qualificado → agendado → fechado → frio |
| `schedule_followup` | Agenda job D+1: `appointment_reminder`, `payment_recovery`, `pos_venda` |

O agente se comporta como vendedor de alta performance (BANT no system prompt), não como atendente de suporte. LGPD é feature: opt-in explícito na primeira mensagem, `consent_log`, comando "SAIR" pra opt-out.

---

## Arquitetura

```
WhatsApp / Instagram (Meta Cloud API / Graph API oficiais)
    ↓
POST /webhook/whatsapp | GET+POST /webhook/instagram   (main.py / app/webhooks/instagram.py)
    ↓
app/agent/claude_client.py
  ├── Lê histórico + estágio do lead no Supabase   (memory.py)
  ├── Monta prompt por canal (WA vs IG)             (app/agent/prompts.py)
  ├── Chama Claude API com tool_use                 (app/agent/tools.py — 6 tools acima)
  └── app/agent/dispatcher.py envia resposta no canal certo

app/jobs/scheduler.py (APScheduler, roda a cada 60s)
  └── Executa followup_jobs pendentes no Supabase (estado sobrevive restart do Railway)
```

---

## Estrutura do projeto

```
Automação Completa Atendimento Estica/
├── app/
│   ├── agent/            # claude_client, tools (6 tools), prompts, dispatcher
│   ├── webhooks/         # instagram.py (WhatsApp fica em main.py)
│   ├── services/         # followup_service.py
│   ├── jobs/             # scheduler.py (APScheduler)
│   ├── db/                # supabase_client, models
│   ├── config.py          # Settings via pydantic-settings
│   └── limiter.py         # Rate limiting (slowapi)
├── database/
├── main.py                # FastAPI — /chat, /webhook/whatsapp, /tenants, /leads, /payment/confirm
├── orchestrator.py, functions.py, prompts.py, memory.py   # base legado, ainda usado por main.py
├── requirements.txt
├── railway.toml
├── .env.example
└── CONTEXT.md              # fonte da verdade — leia antes de mexer no código
```

---

## Configuração

### 1. Supabase
1. Crie um projeto em [supabase.com](https://supabase.com)
2. Execute `database/schema.sql` no SQL Editor, depois `database/migration_v2.sql` (adiciona `ig_access_token`, `asaas_api_key`, `servicos`, `horarios`, `ig_page_id` em `tenants`)
3. Copie a URL e a service_role key em Settings → API

### 2. .env
```bash
cp .env.example .env
```
```env
ANTHROPIC_API_KEY=sk-ant-...
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_SERVICE_ROLE_KEY=eyJ...
META_VERIFY_TOKEN=qualquer_string_secreta
# META_WA_TOKEN, META_WA_PHONE_NUMBER_ID, ASAAS_API_KEY etc. — ver tabela completa abaixo
```

### 3. Adicionar tenant
```sql
INSERT INTO tenants (name, clinic_name, professional_name, phone_number_id, whatsapp_token)
VALUES ('minha-clinica', 'Clínica X', 'Dra. Y', '123456789', 'EAAxxxx');
```

### 4. Rodar
```bash
python -m pip install -r requirements.txt
python main.py
```

### 5. Testar
```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"tenant_name": "minha-clinica", "phone": "5585999999999", "message": "oi"}'
```

---

## Endpoints

| Método | Rota | Descrição |
|---|---|---|
| GET | `/` | Status + total de tenants ativos |
| POST | `/chat` | Teste direto por tenant_name |
| GET / POST | `/webhook/whatsapp` | Verificação Meta + recebe mensagens WhatsApp |
| GET / POST | `/webhook/instagram` | Verificação Meta + recebe DMs Instagram |
| POST | `/payment/confirm` | Callback Asaas — marca lead como fechado, dispara job pos_venda |
| GET | `/tenants` | Lista tenants ativos |
| GET | `/leads/{tenant_name}` | Lista leads do tenant, com filtros status/canal/desde |
| DELETE | `/lead/{tenant_name}/{phone}` | Reseta lead (testes) |

---

## Variáveis de ambiente

Ver `app/config.py` (fonte da verdade) e `.env.example`.

| Variável | Descrição | Obrigatória |
|---|---|---|
| `ANTHROPIC_API_KEY` | Chave Anthropic | Sim |
| `SUPABASE_URL` | URL do projeto Supabase | Sim |
| `SUPABASE_SERVICE_ROLE_KEY` | Chave service role | Sim |
| `META_WA_TOKEN` / `META_WA_PHONE_NUMBER_ID` | WhatsApp Cloud API oficial | Produção |
| `META_VERIFY_TOKEN` | Token verificação webhook (WA + IG) | Produção |
| `META_IG_ACCESS_TOKEN` / `META_IG_PAGE_ID` | Instagram Graph API oficial | Produção |
| `ASAAS_API_KEY` / `ASAAS_BASE_URL` | Geração de link de pagamento | Produção |
| `WHATSAPP_APP_SECRET` | Validação de assinatura do webhook | Produção |
| `ADMIN_API_KEY` | Autenticação de rotas admin | Produção |

---

## O que falta para produção

O core já está implementado (qualificação, agendamento, pagamento, follow-up, LGPD, rate limiting, RLS). O que falta é o que só aparece com cliente real:

- [ ] `check_availability` ainda retorna dados mock (`app/agent/tools.py`) — precisa ligar em agenda real (Google Calendar ou tabela `availability` no Supabase) assim que o primeiro cliente confirmar como agenda hoje
- [ ] Configurar templates HSM aprovados no Meta Business (obrigatório pra mensagem proativa — appointment_reminder, payment_recovery, pos_venda)
- [ ] Criar o primeiro tenant real no Supabase com dados do cliente
- [ ] Deploy no Railway (`railway up`) + configurar webhooks com o domínio final

**Não adicionar feature nova aqui até fechar o primeiro cliente e ele pedir algo específico.**

---

## Custo estimado

| Item | Custo |
|---|---|
| Claude API | ~R$ 0,25 por conversa |
| Supabase Free tier | Grátis até 500MB |
| Railway | ~$5/mês |
| WhatsApp Cloud API | Grátis até 1.000 conversas/mês |
