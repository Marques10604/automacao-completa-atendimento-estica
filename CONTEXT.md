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

### Follow-up — lógica de negócio

O scheduler roda a cada 60 segundos verificando `followup_jobs` no Supabase. Tipos de job, cada um com lógica própria:

| Tipo | Quando dispara | Objetivo |
|------|---------------|----------|
| `appointment_reminder` | 24h após agendamento | Confirmar presença, reduzir no-show |
| `payment_recovery` | 24h após link enviado sem pagamento | Recuperar lead que não pagou |
| `pos_venda` | 24h após pagamento confirmado | Fidelizar, pedir indicação, oferecer retorno |
| `recall_procedimento` | N dias após o procedimento (config por tenant) | Trazer o lead de volta pra repetir o procedimento |
| `cross_sell` | N dias após o procedimento (config por tenant) | Oferecer um procedimento complementar |
| `resgate_silencio` | 3h sem resposta do lead cujo `stage` NÃO é `agendado`/`fechado`/`frio` (e não escalado), escalonado até 3x (3h/D+1/D+3) — exclusão em vez de allowlist porque o default do schema é `'qualificacao'`, que não está no vocabulário `novo`/`qualificado`/etc do funil | Reengajar lead que sumiu no meio da conversa, com mensagem gerada pelo Claude retomando o assunto — marca `frio` se não responder às 3 tentativas |

Estado 100% no Supabase — reinicializações do Railway não perdem nenhum job. Spec do `resgate_silencio`: `docs/superpowers/specs/2026-08-07-followup-resgate-lead-silencioso-design.md`.

---

## Status em andamento — Error tracking via Better Stack (pausado em 2026-07-25, retomar daqui)

**Objetivo:** receber alerta (e-mail e/ou telefone) sempre que o agente der erro em produção.

**Já feito (commitado e em produção — commit `083aaf2`, push confirmado):**
- Conta criada no Better Stack, produto **Error tracking** (é o certo — Sentry-compatible; os outros produtos da conta — Uptime, Telemetry/Logs, Real user monitoring — não servem pra esse caso)
- Aplicação "Agente de Atendimento Estetica" criada lá, platform=FastAPI, id `2627458`
- `sentry-sdk[fastapi]` adicionado ao `requirements.txt`
- `app/config.py`: campo `better_stack_dsn` (opcional — sem a chave o agente roda normal, só não reporta erro)
- `.env.example` documentado com `BETTER_STACK_DSN=`
- `.env` local com o DSN real preenchido
- `main.py`: `sentry_sdk.init(dsn=settings.better_stack_dsn)` logo após carregar `settings`, só roda se a chave existir
- Testado local: evento de teste chegou em Better Stack (`Errors` list, id `ef8c2964...`)
- Variável `BETTER_STACK_DSN` cadastrada no Railway (usuário confirmou) e deploy feito — mas **ainda não confirmamos que um erro real em produção dispara o evento** (só foi testado local)

**Falta (próximo passo ao retomar):**
- Achar onde configurar o **alerta automático de e-mail/telefone** para novos erros do Error tracking. Já eliminamos: "Report incident" (é manual, dispara um incidente por vez, não é regra automática) e a área de **Uptime → Incidents/Escalation policies** (isso é pra monitores de uptime, não confirmado se está linkado ao Error tracking).
- Próxima tentativa: aba **"Advanced settings"** dentro da aplicação em `errors.betterstack.com/team/t574632/applications/2627458` (ainda não vista — era uma das abas ao lado de "Ingest", "Frontend", "Group & transform")
- Se não tiver lá, tentar `betterstack.com/settings/alerts` (área geral de Settings da conta, ainda não visitada)
- Depois de configurar, testar de novo (gerar um erro de propósito) pra confirmar que o alerta chega no e-mail/telefone

Ver memória `better_stack_pendente` para o histórico completo da sessão.

---

## Onboarding self-service — formulário de cadastro do cliente (concluído em 2026-07-25)

Formulário HTML standalone (`cadastro-clinica.html`, raiz do projeto) permite que o cliente novo se cadastre sozinho, sem o desenvolvedor editar JSONB na mão: dados da clínica, horário de funcionamento por dia da semana, serviços (nome/preço/duração/descrição), FAQ, e uma seção avançada opcional de recall e cross-sell.

Envia `POST` para `https://automacao-completa-atendimento-estica-production.up.railway.app/onboarding/intake`, protegido por `ONBOARDING_SECRET` (header `X-Onboarding-Key`). O backend já existe e está em produção desde o commit `87e1cf8` — `main.py:531` recebe, `app/services/onboarding_service.py::processar_intake()` grava tenant + tabelas `services`/`faq` no Supabase (substituindo o catálogo por completo a cada envio).

Isso é a "tela" prevista na decisão `docs/superpowers/decisoes/2026-07-22-catalogo-de-servicos-e-painel.md` — não é o painel multi-tenant com autenticação (isso segue fora de escopo). É um formulário único sem login, protegido só pela chave compartilhada, pensado para o primeiro cliente se cadastrar.

**Falta manual, fora do formulário:** registrar o número de WhatsApp do cliente na Meta e vincular `phone_number_id` ao tenant (o próprio retorno do endpoint lembra disso em `proximo_passo`).

---

## Status em andamento — Dashboard real + botão de desescalar (pausado em 2026-08-02, retomar daqui)

**Objetivo:** o dashboard do cliente (repositório separado, feito no Lovable) hoje é 100% mock. Conectar dados reais do backend + adicionar botão pra atendente devolver um lead escalado pra IA.

**Repositório do dashboard (fonte de verdade, NÃO é este repo):** `https://github.com/Marques10604/aesthetic-dashboard-ai.git` — usuário não quer nada conectado ao Lovable, só usa ele pra gerar o front e depois trabalha 100% via GitHub. Stack: TanStack Start (React 19 + Vite + SSR real, tem `server.ts`/`start.ts`), shadcn/ui, dados hoje 100% em `src/lib/mock-leads.ts`.

**Já feito:**
- Spec completa escrita e commitada neste repo: `docs/superpowers/specs/2026-08-02-dashboard-integracao-real-desescalar-design.md` (commit `c21988b`) — leia ela inteira antes de continuar, tem a arquitetura completa.
- Decisões fechadas com o usuário (via brainstorming): single-tenant por enquanto (sem login), todas as chamadas ao backend acontecem só no servidor SSR do dashboard (a `x-admin-key` nunca chega no navegador), sem botão de escalar manual nem de desescalar (escalar continua automático via `_registrar_falha_e_escalar` e a tool `escalate_to_human`; desescalar removido do escopo em 2026-08-06 — ver abaixo), KPI novo "Taxa de resolução automática" (containment rate) validado por pesquisa de mercado substitui o "Recuperado por follow-up automático" (decidido 2026-08-03, ver spec), sininho do topbar passa a mostrar contagem real de leads escalados.
- **Botão "Devolver pra IA" (desescalar) removido do escopo em 2026-08-06.** Motivo do usuário: ainda em fase de teste, sem cliente pagante — construir esse botão agora não dá retorno. O plano real é diferente: ao fechar um cliente, o dinheiro do setup financia a instalação de um Chatwoot próprio numa VPS do cliente, e é o Chatwoot que resolve o handoff humano de verdade (não um botão customizado no dashboard). Revisitar quando a oferta com Chatwoot existir.
- 4 tasks restantes (podem não persistir entre sessões — a lista abaixo é a fonte de verdade):
  1. Criar `src/lib/backend-client.ts` (server-only: `getLeads()`, lê `BACKEND_URL`/`ADMIN_API_KEY`/`TENANT_SLUG` do `process.env`)
  2. Criar `src/lib/dashboard-metrics.ts` (funções puras: `Lead[]` → KPIs, série 30 dias, top serviços, contagem de escalados)
  3. Loader server-side em `src/routes/index.tsx` chamando os dois acima
  4. `kpi-cards.tsx`, `topbar.tsx`, `leads-chart.tsx`, `top-services.tsx`, `highlight-band.tsx`, `leads-table.tsx` (badge "Aguardando humano", só leitura) passam a receber dados via props em vez de importar `mock-leads` direto
  5. `.env` local do dashboard + `bun dev` pra visualizar

**Nenhuma dessas tasks foi implementada ainda** — paramos na fase de investigação/planejamento, nenhum código do dashboard foi escrito ou commitado no repo `aesthetic-dashboard-ai`. O clone local ficou numa pasta de scratchpad temporária (não persiste entre sessões) — é só re-clonar, nada foi perdido porque nada foi alterado lá ainda.

**Dados reais confirmados em produção (Railway) pra usar no `.env` local:**
- `BACKEND_URL=https://automacao-completa-atendimento-estica-production.up.railway.app`
- Tenants ativos: `lumina` (Clínica Lumina), `minha-clinica` (Clinica Sorriso), `bia` (BIN Master IA) — usar `TENANT_SLUG=lumina` pro teste local (tem leads reais, ainda que de teste — todos com `escalado=false` até agora)
- `ADMIN_API_KEY` já está no `.env` deste projeto (backend) e foi validado batendo em `/tenants` e `/leads/lumina` com sucesso

**Próximo passo ao retomar:** seguir as 4 tasks restantes em ordem.

---

## Status em andamento — Resgate por silêncio: acompanhar em produção (mergeado em 2026-08-08)

**Objetivo:** feature já mergeada na `master` (PR #1, commit `b1b729d`) e migration `v13` rodada. Falta só observar o primeiro lead real que passar pelo fluxo completo, porque a cadeia tentativa 2→3 nunca foi confirmada ao vivo — só revisada em código (2x) — devido a um teste local ter competido com o próprio deploy de produção pelos mesmos jobs.

**O que conferir quando um lead real ficar em silêncio** (query pronta pra colar no SQL Editor do Supabase):
```sql
select
  l.phone,
  l.stage,
  l.escalado,
  fj.payload->>'tentativa' as tentativa,
  fj.status,
  fj.scheduled_at,
  fj.executed_at
from followup_jobs fj
join leads l on l.id = fj.lead_id
where fj.job_type = 'resgate_silencio'
order by fj.scheduled_at desc;
```

**Sinal de alerta:** se uma tentativa 2 ou 3 ficar `failed` (ou nunca aparecer), é a limitação conhecida e já documentada na spec (`docs/superpowers/specs/2026-08-07-followup-resgate-lead-silencioso-design.md`, achado I2 do review final) — as tentativas D+1/D+3 caem fora da janela de 24h de mensagem livre do WhatsApp/Instagram, e o Meta pode rejeitar o envio silenciosamente. Não é um bug de lógica, é uma limitação de plataforma ainda não resolvida (precisaria de template HSM aprovado pra mensagem business-initiated fora da janela).

**Próximo passo ao retomar:** rodar a query acima, ver se tentativa 1 dispara certo (deve funcionar igual ao teste manual já confirmado) e se 2/3 completam ou travam.

---

## Como trabalhar neste projeto

1. Leia este arquivo (`CONTEXT.md`) **inteiro** antes de qualquer tarefa
2. A pasta do projeto é `C:/Dev/Projetos/Automação Completa`
3. Esta pasta é uma **cópia da pasta do Agente de Atendimento IA** — não reescrever o que já existe, apenas estender
4. Sempre verificar se o arquivo que vai editar já existe antes de criar do zero
5. Prioridade atual: **migrar WhatsApp de Evolution API para Meta Cloud API oficial**
6. Segunda prioridade: **webhook Instagram DM + tools completas do Claude**
