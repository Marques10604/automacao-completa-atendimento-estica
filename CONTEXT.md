# CONTEXT.md — Automação Completa
> Leia este arquivo inteiro antes de qualquer ação no projeto.

## O que é este projeto

**Automação Completa** — agente de IA de vendas end-to-end criado por **Ivonilson Marques**.
Substitui completamente o atendente humano: do primeiro contato ao fechamento, sem intervenção humana.

- Pasta local: `C:/Dev/Projetos/Automação Completa`
- Deploy: Railway
- Repositório: conectado ao GitHub via Railway

---

## Base de onde partimos

Este projeto é uma **evolução direta do Agente de Atendimento IA** já buildado anteriormente.
A estrutura base (FastAPI + Claude API + Supabase + Railway) já existe e foi validada.
Não reescrever do zero — estender e adicionar features em cima da base.

---

## Stack técnica

| Camada | Tecnologia |
|--------|-----------|
| Servidor | FastAPI (Python 3.11+) |
| IA / Cérebro | Claude API — modelo `claude-3-5-sonnet-20241022` |
| Banco de dados | Supabase (PostgreSQL) |
| Deploy | Railway |
| WhatsApp | Meta Cloud API **oficial** (migrado de Evolution API) |
| Instagram | Meta Graph API oficial |
| Pagamento | Asaas ou Pagar.me (Pix + cartão) |
| Scheduler | APScheduler (in-process, estado no Supabase) |

---

## Decisão crítica já tomada: Meta Cloud API oficial

**A Evolution API foi abandonada.** A Meta iniciou banimentos em massa em jan/2026
de APIs não oficiais (Baileys, Evolution API). Qualquer número usando Evolution
está em risco de ban permanente sem recuperação.

**Todo o código de WhatsApp deve usar a Meta Cloud API oficial.**
Documentação: https://developers.facebook.com/docs/whatsapp/cloud-api

---

## Estrutura de pastas do projeto

```
automacao-completa/
├── app/
│   ├── main.py                   # FastAPI app + lifespan (APScheduler aqui)
│   ├── config.py                 # Settings via pydantic-settings
│   ├── webhooks/
│   │   ├── whatsapp.py           # POST /webhook/whatsapp (Meta Cloud API)
│   │   └── instagram.py          # POST + GET /webhook/instagram (Meta Graph API)
│   ├── agent/
│   │   ├── claude_client.py      # Wrapper Claude API com tool use
│   │   ├── tools.py              # Definição + execução das 6 tools
│   │   ├── prompts.py            # System prompt por canal (WA vs IG)
│   │   └── dispatcher.py         # Envia resposta no canal certo
│   ├── services/
│   │   ├── lead_service.py       # CRUD leads Supabase
│   │   ├── appointment_service.py
│   │   ├── payment_service.py    # Gera link Pix/cartão via Asaas
│   │   └── followup_service.py   # Schedule + execução jobs D+1
│   ├── jobs/
│   │   └── scheduler.py          # Loop APScheduler (roda via lifespan)
│   └── db/
│       ├── supabase_client.py
│       └── models.py             # Tabelas: leads, messages, appointments, followup_jobs
├── .env
├── requirements.txt
└── railway.toml
```

---

## Features completas do produto

### Já existia no agente de atendimento (base)
- [x] Estrutura FastAPI com lifespan
- [x] Webhook WhatsApp (precisa migrar Evolution → Meta Cloud API)
- [x] Wrapper Claude API básico
- [x] Supabase client
- [x] Memória de conversa por sessão

### A implementar na Automação Completa

**Fase 1 — Urgente (infra)**
- [ ] Migrar webhook WhatsApp: Evolution API → Meta Cloud API oficial
- [ ] Adaptar payload de entrada (formato Meta Cloud API é diferente)
- [ ] Configurar templates HSM aprovados para mensagens proativas

**Fase 2 — Core do funil**
- [ ] Webhook Instagram DM (POST + GET /webhook/instagram)
- [ ] Tool: `migrate_to_whatsapp` (lead chega IG → manda pro WA para fechar)
- [ ] Reescrever system prompt com BANT/SPIN + momento psicológico de venda
- [ ] Tool: `check_availability` (consulta agenda no Supabase)
- [ ] Tool: `book_appointment` (cria agendamento, salva lead_id + horário)
- [ ] Tool: `generate_payment_link` (Asaas — Pix e cartão)
- [ ] Tool: `update_lead_status` (novo → qualificado → agendado → fechado → frio)
- [ ] Tool: `schedule_followup` (agenda job D+1 no Supabase)
- [ ] POST /payment/confirm (callback Asaas — atualiza lead para fechado)

**Fase 3 — Pós-venda automático**
- [ ] `followup_service.py` + `scheduler.py` (APScheduler, estado no Supabase)
- [ ] Tipos de job: `appointment_reminder`, `payment_recovery`, `pos_venda`
- [ ] Tabela `followup_jobs` no Supabase (schema abaixo)
- [ ] Adaptar envio de follow-up para templates Meta Cloud API

**Fase 4 — LGPD como feature**
- [ ] Opt-in explícito na primeira mensagem (template aprovado Meta)
- [ ] Tabela `consent_log` no Supabase
- [ ] Comando "SAIR" para descadastro automático
- [ ] RLS no Supabase por cliente/tenant

**Fase 5 — Dashboard**
- [ ] GET /leads com filtros (canal, status, data)

---

## Tabelas Supabase necessárias

```sql
-- Leads
create table leads (
  id uuid primary key default gen_random_uuid(),
  name text,
  phone text,
  ig_user_id text,
  channel text not null, -- 'whatsapp' | 'instagram'
  status text default 'novo', -- novo | qualificado | agendado | fechado | frio
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

-- Mensagens (memória de conversa)
create table messages (
  id uuid primary key default gen_random_uuid(),
  lead_id uuid references leads(id),
  role text not null, -- 'user' | 'assistant'
  content text not null,
  created_at timestamptz default now()
);

-- Agendamentos
create table appointments (
  id uuid primary key default gen_random_uuid(),
  lead_id uuid references leads(id),
  service text,
  scheduled_at timestamptz not null,
  confirmed boolean default false,
  created_at timestamptz default now()
);

-- Jobs de follow-up
create table followup_jobs (
  id uuid primary key default gen_random_uuid(),
  lead_id uuid references leads(id),
  channel text not null,
  phone text,
  ig_user_id text,
  job_type text not null, -- 'appointment_reminder' | 'payment_recovery' | 'pos_venda'
  scheduled_at timestamptz not null,
  executed_at timestamptz,
  status text default 'pending', -- 'pending' | 'done' | 'failed'
  payload jsonb default '{}'
);
create index on followup_jobs (scheduled_at) where status = 'pending';

-- Consentimento LGPD
create table consent_log (
  id uuid primary key default gen_random_uuid(),
  lead_id uuid references leads(id),
  channel text,
  consent_text text,
  consented_at timestamptz default now()
);
```

---

## Tools do Claude (function calling)

O agente usa `tool_use` do Claude. Todas as tools são definidas em `app/agent/tools.py`.

| Tool | O que faz |
|------|-----------|
| `check_availability` | Consulta slots livres na agenda (Supabase) |
| `book_appointment` | Cria agendamento, salva lead_id + horário |
| `generate_payment_link` | Chama Asaas API, retorna link Pix ou cartão |
| `migrate_to_whatsapp` | Usado no IG: envia msg WA para migrar o lead |
| `update_lead_status` | Muda stage: novo → qualificado → agendado → fechado |
| `schedule_followup` | Insere job na tabela followup_jobs (D+1) |

---

## Variáveis de ambiente (.env)

```env
# Claude
ANTHROPIC_API_KEY=

# Supabase
SUPABASE_URL=
SUPABASE_KEY=

# Meta Cloud API (WhatsApp oficial)
META_WA_TOKEN=           # Token de acesso permanente
META_WA_PHONE_NUMBER_ID= # ID do número de telefone no Meta
META_VERIFY_TOKEN=       # Token de verificação do webhook (você define)

# Meta Graph API (Instagram)
META_IG_ACCESS_TOKEN=
META_IG_PAGE_ID=

# Pagamento
ASAAS_API_KEY=
ASAAS_BASE_URL=https://api.asaas.com/v3

# App
RAILWAY_ENVIRONMENT=production
```

---

## Payload do webhook Meta Cloud API (WhatsApp)

O formato é diferente da Evolution API. Exemplo de mensagem recebida:

```json
{
  "object": "whatsapp_business_account",
  "entry": [{
    "changes": [{
      "value": {
        "messages": [{
          "from": "5511999999999",
          "type": "text",
          "text": { "body": "Olá, quero agendar" },
          "id": "wamid.xxx"
        }],
        "contacts": [{
          "profile": { "name": "Nome do Lead" },
          "wa_id": "5511999999999"
        }]
      }
    }]
  }]
}
```

Verificação do webhook (GET):
```
?hub.mode=subscribe&hub.verify_token=SEU_TOKEN&hub.challenge=NUMERO
```
Deve retornar apenas o `hub.challenge`.

---

## Decisões de produto validadas por pesquisa de mercado

Fonte: Deep Research (Perplexity + ChatGPT Deep Research), abril/2026.

1. **Nicho alvo:** estética e saúde de alto ticket (harmonização, implante, cirurgia estética)
2. **Diferencial central:** orquestração agêntica — IA detecta o momento psicológico da venda, não apenas responde. Não vender como "atendimento 24h".
3. **Narrativa de venda:** "vendedor de alta performance que nunca dorme", não "bot de atendimento"
4. **Métrica de venda:** taxa de fechamento, não tempo de resposta
5. **LGPD:** posicionar como feature, não obrigação. Vira diferencial contra concorrentes
6. **Checkout in-chat** converte 6x mais que redirecionar para página externa
7. **Pricing:**
   - Starter: setup R$5k + R$1.5k/mês (até 250 leads)
   - Growth: setup R$8k + R$2.5k/mês (até 1.000 leads)
   - Enterprise: sob consulta
8. **Qualificação:** usar BANT (Budget, Authority, Need, Timeline) no system prompt
9. **Concorrentes diretos:** SocialHub (checkout in-chat), SDRBOT.ai (qualificação BANT)

---

## Inteligência de mercado — Deep Research abril/2026

Fonte: Perplexity Deep Research + ChatGPT Deep Research. Decisões técnicas e de produto baseadas nesses relatórios.

### Concorrentes diretos mapeados

| Player | O que faz | Ponto fraco |
|--------|-----------|-------------|
| SocialHub (BR) | Checkout in-chat, Pix/WA Pay integrado, catálogo | Não usa LLM como cérebro — fluxos fixos |
| SDRBOT.ai (BR) | Qualificação BANT, usa API oficial Meta | Foco em qualificação, não fecha com pagamento |
| Respond.io | Omnichannel enterprise, LLM agents | Caro, foco enterprise, não especializado em BR |
| ManyChat | Chatbot WA/IG fácil | Não fecha venda — manda pro humano |
| Helena CRM | Pix integrado no WA | Sem LLM — bot de fluxo |

**Nosso diferencial real:** nenhum deles combina LLM como cérebro + qualificação BANT + fechamento com pagamento + follow-up automático em um só agente focado em PMEs brasileiras.

### Dados de mercado relevantes

- Checkout in-chat converte **6x mais** que redirecionar para página externa
- Resposta em menos de 3 segundos maximiza conversão — cada minuto de espera reduz drasticamente a probabilidade de fechamento
- Agentes com LLM convertem **20–40% mais** que chatbots de fluxo em cenários com objeções e variações de lead
- Clínicas que implementaram automação reduziram tempo de resposta de 2h para <1min → **+40% em agendamentos em 90 dias**
- **+30% de ticket médio** com pós-venda automatizado (lembretes de retorno personalizados)
- TAM Brasil: ~US$ 300–500 milhões/ano em automação de atendimento + vendas com IA
- Crescimento do setor: 20–30% ao ano
- 95% das empresas médias/grandes que adotaram IA relatam ROI positivo

### Regra de ouro do sistema prompt

O agente deve se comportar como **vendedor de alta performance**, não como atendente. A IA avalia:
1. O momento psicológico do lead (interesse, hesitação, urgência)
2. A qualificação BANT em linguagem natural — sem parecer formulário
3. Quando apresentar o link de pagamento de forma natural, não abrupta
4. Como superar objeções antes de desistir do lead

Nunca apresentar o link de pagamento cedo demais. O Claude deve detectar o momento certo.

### Qualificação BANT — guia para o system prompt

Coletar de forma conversacional, nunca como formulário:
- **Budget (Orçamento):** o lead tem condição de pagar pelo serviço? Sinais: pergunta sobre preço, compara com concorrente
- **Authority (Autoridade):** é quem decide? Ou precisa consultar alguém?
- **Need (Necessidade):** qual é o problema real? Qual resultado quer alcançar?
- **Timeline (Prazo):** quer resolver agora ou "está pesquisando"? Urgência?

Lead qualificado = Budget + Need confirmados. Agenda se Authority + Timeline favoráveis.

### LGPD como feature — implementação obrigatória

Não é compliance — é argumento de venda. Posicionamento: "nosso agente é o único com LGPD nativa".

Implementar:
- Primeira mensagem sempre inclui opt-in explícito (template aprovado Meta)
- Toda conversa salva `consent_log` com timestamp e texto exato do consentimento
- Comando "SAIR" em qualquer momento remove o lead e para todas as automações
- RLS no Supabase garante que dados de um cliente não vazam para outro (multi-tenant)

### Narrativa de venda do produto (para o dono do negócio)

**Não vender como:** "bot de atendimento 24h" ou "automação de WhatsApp"

**Vender como:** "vendedor de alta performance que nunca dorme — responde em 3 segundos, qualifica, agenda, envia o link e ainda faz follow-up no dia seguinte. Você só toca quando quiser."

**Métricas para apresentar ao cliente:**
- Taxa de fechamento (não tempo de resposta)
- Leads recuperados pelo follow-up D+1
- Vendas fechadas sem o dono tocar no celular

### Follow-up D+1 — lógica de negócio

O scheduler roda a cada 60 segundos verificando `followup_jobs` no Supabase.
Três tipos de job, cada um com template diferente:

| Tipo | Quando dispara | Objetivo |
|------|---------------|----------|
| `appointment_reminder` | 24h após agendamento | Confirmar presença, reduzir no-show |
| `payment_recovery` | 24h após link enviado sem pagamento | Recuperar lead que não pagou |
| `pos_venda` | 24h após pagamento confirmado | Fidelizar, pedir indicação, oferecer retorno |

Estado 100% no Supabase — reinicializações do Railway não perdem nenhum job.

---

## Como trabalhar neste projeto

1. Leia este arquivo (`CONTEXT.md`) **inteiro** antes de qualquer tarefa
2. A pasta do projeto é `C:/Dev/Projetos/Automação Completa`
3. Esta pasta é uma **cópia da pasta do Agente de Atendimento IA** — não reescrever o que já existe, apenas estender
4. Sempre verificar se o arquivo que vai editar já existe antes de criar do zero
5. Prioridade atual: **migrar WhatsApp de Evolution API para Meta Cloud API oficial**
6. Segunda prioridade: **webhook Instagram DM + tools completas do Claude**
