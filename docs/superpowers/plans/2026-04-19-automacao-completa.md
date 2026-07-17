# Automação Completa — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Evoluir o Agente de Atendimento IA existente para o produto "Automação Completa" — agente de vendas end-to-end com tool use Claude, Instagram DM, pagamento Asaas, follow-up D+1 e LGPD nativa.

**Architecture:** FastAPI (estrutura `app/`) com Claude tool_use como cérebro de orquestração; 6 tools reais que chamam Supabase/Asaas/Meta APIs. APScheduler in-process com estado 100% no Supabase para jobs de follow-up resistentes a restart.

**Tech Stack:** FastAPI · Python 3.11 · Anthropic SDK (claude-sonnet-4-6) · Supabase (PostgreSQL) · Meta Cloud API (WA + IG) · Asaas API · APScheduler · Railway

---

## Diagnóstico: O que existe vs. o que muda

### Já existe (não reescrever)
| Arquivo | Estado |
|---------|--------|
| `main.py` | GET/POST `/webhook/whatsapp` prontos; falta envio de volta e Instagram |
| `orchestrator.py` | Loop Claude com prompt caching; precisa migrar de STAGE: regex → tool_use |
| `memory.py` | CRUD Supabase leads/sessions/conversations/tenants — **manter como está** |
| `prompts.py` | Template base — reescrever conteúdo (BANT/SPIN + tool use instructions) |
| `functions.py` | Funções por estágio — substituir pelas 6 tools do tool_use |
| `database/schema.sql` | Tabelas existentes OK; adicionar appointments, followup_jobs, consent_log |
| `requirements.txt` | Adicionar apscheduler, pydantic-settings |

### Criar do zero
- `app/config.py` — pydantic-settings
- `app/agent/tools.py` — definições + execução das 6 tools
- `app/agent/dispatcher.py` — envia mensagem no canal certo (WA ou IG)
- `app/webhooks/instagram.py` — webhook Instagram DM
- `app/services/appointment_service.py` — check_availability + book_appointment
- `app/services/payment_service.py` — generate_payment_link via Asaas
- `app/services/followup_service.py` — executa jobs pendentes
- `app/jobs/scheduler.py` — APScheduler loop
- `database/migration_v2.sql` — novas tabelas + campos

### Estrutura alvo
```
app/
├── main.py                   # (mover e adaptar)
├── config.py                 # pydantic-settings (novo)
├── webhooks/
│   ├── whatsapp.py           # (extraído do main.py atual)
│   └── instagram.py          # (novo)
├── agent/
│   ├── claude_client.py      # (renomear orchestrator.py)
│   ├── tools.py              # (novo — 6 tools)
│   ├── prompts.py            # (reescrever conteúdo)
│   └── dispatcher.py         # (novo — envia WA/IG)
├── services/
│   ├── lead_service.py       # (extraído de memory.py)
│   ├── appointment_service.py
│   ├── payment_service.py
│   └── followup_service.py
├── jobs/
│   └── scheduler.py
└── db/
    ├── supabase_client.py    # (extraído de memory.py)
    └── models.py             # (referência — sem ORM)
database/
├── schema.sql                # (existente)
└── migration_v2.sql          # (novo)
```

---

## Task 1: Estrutura de pastas + config.py + migration SQL

**Files:**
- Create: `app/__init__.py`
- Create: `app/config.py`
- Create: `app/webhooks/__init__.py`
- Create: `app/agent/__init__.py`
- Create: `app/services/__init__.py`
- Create: `app/jobs/__init__.py`
- Create: `app/db/__init__.py`
- Create: `database/migration_v2.sql`
- Modify: `requirements.txt`

- [ ] **Step 1: Criar todos os `__init__.py` e `app/config.py`**

```python
# app/config.py
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    anthropic_api_key: str
    supabase_url: str
    supabase_service_role_key: str
    meta_wa_token: str = ""
    meta_wa_phone_number_id: str = ""
    meta_verify_token: str = ""
    meta_ig_access_token: str = ""
    meta_ig_page_id: str = ""
    asaas_api_key: str = ""
    asaas_base_url: str = "https://api.asaas.com/v3"
    admin_api_key: str = ""
    whatsapp_app_secret: str = ""
    port: int = 8000

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
```

- [ ] **Step 2: Criar `database/migration_v2.sql`**

```sql
-- Adiciona campo ig_user_id e status em leads
ALTER TABLE leads ADD COLUMN IF NOT EXISTS ig_user_id TEXT;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'novo';

-- Agendamentos
CREATE TABLE IF NOT EXISTS appointments (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  lead_id      UUID REFERENCES leads(id) ON DELETE CASCADE,
  tenant_id    UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  service      TEXT,
  scheduled_at TIMESTAMPTZ NOT NULL,
  confirmed    BOOLEAN DEFAULT FALSE,
  created_at   TIMESTAMPTZ DEFAULT NOW()
);

-- Jobs de follow-up
CREATE TABLE IF NOT EXISTS followup_jobs (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  lead_id      UUID REFERENCES leads(id) ON DELETE CASCADE,
  tenant_id    UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  channel      TEXT NOT NULL,
  phone        TEXT,
  ig_user_id   TEXT,
  job_type     TEXT NOT NULL,
  scheduled_at TIMESTAMPTZ NOT NULL,
  executed_at  TIMESTAMPTZ,
  status       TEXT DEFAULT 'pending',
  payload      JSONB DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_followup_pending ON followup_jobs(scheduled_at)
  WHERE status = 'pending';

-- Consentimento LGPD
CREATE TABLE IF NOT EXISTS consent_log (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  lead_id      UUID REFERENCES leads(id) ON DELETE CASCADE,
  tenant_id    UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  channel      TEXT,
  consent_text TEXT,
  consented_at TIMESTAMPTZ DEFAULT NOW()
);

-- RLS para novas tabelas
ALTER TABLE appointments   ENABLE ROW LEVEL SECURITY;
ALTER TABLE followup_jobs  ENABLE ROW LEVEL SECURITY;
ALTER TABLE consent_log    ENABLE ROW LEVEL SECURITY;

CREATE POLICY "service_role_appointments"  ON appointments  FOR ALL TO service_role USING (true);
CREATE POLICY "service_role_followup_jobs" ON followup_jobs FOR ALL TO service_role USING (true);
CREATE POLICY "service_role_consent_log"   ON consent_log   FOR ALL TO service_role USING (true);

-- Adiciona campos Meta para tenants
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS ig_access_token TEXT;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS asaas_api_key   TEXT;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS servicos        JSONB;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS horarios        JSONB;
```

- [ ] **Step 3: Atualizar `requirements.txt`**

```
fastapi>=0.115.0
uvicorn[standard]>=0.30.6
anthropic>=0.40.0
python-dotenv>=1.0.1
pydantic>=2.10.0
pydantic-settings>=2.0.0
supabase==2.7.4
httpx>=0.25.0
apscheduler>=3.10.4
```

- [ ] **Step 4: Executar migration SQL no Supabase**

Abrir SQL Editor do Supabase e executar `database/migration_v2.sql`.
Verificar: tabelas `appointments`, `followup_jobs`, `consent_log` criadas.

- [ ] **Step 5: Commit**

```bash
git add app/ database/migration_v2.sql requirements.txt
git commit -m "feat: estrutura app/ + config pydantic-settings + migration v2 SQL"
```

---

## Task 2: Dispatcher — envio real de mensagens WA e IG

**Files:**
- Create: `app/agent/dispatcher.py`

- [ ] **Step 1: Criar `app/agent/dispatcher.py`**

```python
# app/agent/dispatcher.py
import httpx
from app.config import settings


async def send_whatsapp(phone: str, text: str, wa_token: str, phone_number_id: str) -> None:
    """Envia mensagem de texto via Meta Cloud API."""
    url = f"https://graph.facebook.com/v19.0/{phone_number_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "text",
        "text": {"body": text},
    }
    headers = {"Authorization": f"Bearer {wa_token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(url, json=payload, headers=headers)
        r.raise_for_status()


async def send_instagram(ig_user_id: str, text: str, ig_access_token: str) -> None:
    """Envia mensagem de texto via Meta Graph API (Instagram DM)."""
    url = "https://graph.facebook.com/v19.0/me/messages"
    payload = {
        "recipient": {"id": ig_user_id},
        "message": {"text": text},
    }
    params = {"access_token": ig_access_token}
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(url, json=payload, params=params)
        r.raise_for_status()


async def send_message(channel: str, phone: str, ig_user_id: str, text: str, tenant: dict) -> None:
    """Roteia envio para o canal correto."""
    wa_token = tenant.get("whatsapp_token") or settings.meta_wa_token
    phone_number_id = tenant.get("phone_number_id") or settings.meta_wa_phone_number_id
    ig_token = tenant.get("ig_access_token") or settings.meta_ig_access_token

    if channel == "whatsapp" and phone:
        await send_whatsapp(phone, text, wa_token, phone_number_id)
    elif channel == "instagram" and ig_user_id:
        await send_instagram(ig_user_id, text, ig_token)
    else:
        raise ValueError(f"Canal inválido ou identificador ausente: channel={channel}")
```

- [ ] **Step 2: Integrar dispatcher no webhook WA existente (`main.py`)**

Em `main.py`, após `resultado = await processar_mensagem(...)`, remover o comentário TODO e chamar:

```python
from app.agent.dispatcher import send_message

# dentro de webhook_whatsapp, após processar_mensagem:
await send_message(
    channel="whatsapp",
    phone=phone,
    ig_user_id="",
    text=resultado["response"],
    tenant=tenant,
)
```

- [ ] **Step 3: Testar com `POST /chat`**

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"tenant_name": "lumina", "phone": "5585999999999", "message": "oi"}'
```

Esperado: `{"response": "...", "stage": "qualificacao", ...}` sem erro 500.

- [ ] **Step 4: Commit**

```bash
git add app/agent/dispatcher.py main.py
git commit -m "feat: dispatcher real send_message WA + IG via Meta APIs"
```

---

## Task 3: Tool use do Claude — 6 tools (substitui STAGE: regex)

**Files:**
- Create: `app/agent/tools.py`
- Modify: `orchestrator.py` → `app/agent/claude_client.py`

Este é o maior refactor. A arquitetura muda de:
> Claude gera texto com `STAGE: X` no final → regex extrai estágio → função por estágio

Para:
> Claude decide chamar uma tool → executa tool real → injeta resultado → Claude gera resposta final

- [ ] **Step 1: Criar `app/agent/tools.py` — definições das 6 tools**

```python
# app/agent/tools.py

TOOL_DEFINITIONS = [
    {
        "name": "check_availability",
        "description": "Consulta slots livres na agenda. Use antes de book_appointment.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Data no formato DD/MM/YYYY"},
                "time": {"type": "string", "description": "Horário no formato HH:MM ou HHh"},
            },
            "required": ["date"],
        },
    },
    {
        "name": "book_appointment",
        "description": "Cria o agendamento confirmado no Supabase.",
        "input_schema": {
            "type": "object",
            "properties": {
                "lead_id":   {"type": "string"},
                "service":   {"type": "string", "description": "Nome do procedimento"},
                "scheduled_at": {"type": "string", "description": "ISO 8601: 2026-04-20T14:00:00"},
            },
            "required": ["lead_id", "service", "scheduled_at"],
        },
    },
    {
        "name": "generate_payment_link",
        "description": "Gera link de pagamento Pix ou cartão via Asaas. Só use após qualificação confirmada.",
        "input_schema": {
            "type": "object",
            "properties": {
                "lead_id":      {"type": "string"},
                "value":        {"type": "number", "description": "Valor em reais"},
                "description":  {"type": "string"},
                "billing_type": {"type": "string", "enum": ["PIX", "CREDIT_CARD", "BOLETO"]},
            },
            "required": ["lead_id", "value", "description", "billing_type"],
        },
    },
    {
        "name": "migrate_to_whatsapp",
        "description": "Usado no Instagram: envia mensagem WA para migrar o lead do IG para WhatsApp.",
        "input_schema": {
            "type": "object",
            "properties": {
                "phone":    {"type": "string", "description": "Número com DDI, ex: 5585999999999"},
                "message":  {"type": "string", "description": "Texto da mensagem de boas-vindas no WA"},
            },
            "required": ["phone", "message"],
        },
    },
    {
        "name": "update_lead_status",
        "description": "Atualiza o status/estágio do lead: novo → qualificado → agendado → fechado → frio",
        "input_schema": {
            "type": "object",
            "properties": {
                "lead_id": {"type": "string"},
                "status":  {"type": "string", "enum": ["novo", "qualificado", "agendado", "fechado", "frio"]},
            },
            "required": ["lead_id", "status"],
        },
    },
    {
        "name": "schedule_followup",
        "description": "Agenda job de follow-up D+1 no Supabase.",
        "input_schema": {
            "type": "object",
            "properties": {
                "lead_id":   {"type": "string"},
                "job_type":  {"type": "string", "enum": ["appointment_reminder", "payment_recovery", "pos_venda"]},
                "channel":   {"type": "string", "enum": ["whatsapp", "instagram"]},
                "phone":     {"type": "string"},
                "ig_user_id": {"type": "string"},
                "payload":   {"type": "object"},
            },
            "required": ["lead_id", "job_type", "channel"],
        },
    },
]
```

- [ ] **Step 2: Criar `app/agent/tools.py` — execução das tools (append ao mesmo arquivo)**

```python
# Continuação de app/agent/tools.py
import httpx
from datetime import datetime, timedelta, timezone
import memory as mem  # reutiliza memory.py existente na raiz


async def execute_tool(tool_name: str, tool_input: dict, tenant: dict, phone: str) -> dict:
    """Despacha a tool para a implementação correta."""
    dispatch = {
        "check_availability":  _check_availability,
        "book_appointment":    _book_appointment,
        "generate_payment_link": _generate_payment_link,
        "migrate_to_whatsapp": _migrate_to_whatsapp,
        "update_lead_status":  _update_lead_status,
        "schedule_followup":   _schedule_followup,
    }
    fn = dispatch.get(tool_name)
    if not fn:
        return {"error": f"Tool desconhecida: {tool_name}"}
    return await fn(tool_input, tenant, phone)


async def _check_availability(inp: dict, tenant: dict, phone: str) -> dict:
    """Mock: retorna disponível. Integrar com Google Calendar futuramente."""
    date = inp.get("date", "")
    time = inp.get("time", "qualquer horário")
    return {
        "available": True,
        "slots": [f"{date} às {time}", f"{date} às 10:00", f"{date} às 15:00"],
        "message": f"Horário {time} de {date} disponível.",
    }


async def _book_appointment(inp: dict, tenant: dict, phone: str) -> dict:
    sb = mem.get_client()
    row = sb.table("appointments").insert({
        "lead_id":      inp["lead_id"],
        "tenant_id":    str(tenant["id"]),
        "service":      inp["service"],
        "scheduled_at": inp["scheduled_at"],
    }).execute()
    return {"success": True, "appointment_id": row.data[0]["id"]}


async def _generate_payment_link(inp: dict, tenant: dict, phone: str) -> dict:
    from app.config import settings
    asaas_key = tenant.get("asaas_api_key") or settings.asaas_api_key
    base_url   = settings.asaas_base_url
    if not asaas_key:
        return {"error": "ASAAS_API_KEY não configurada para este tenant"}

    headers = {"access_token": asaas_key, "Content-Type": "application/json"}
    payload = {
        "billingType": inp["billing_type"],
        "value":       inp["value"],
        "description": inp["description"],
        "dueDate":     (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d"),
    }
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(f"{base_url}/paymentLinks", json=payload, headers=headers)
        r.raise_for_status()
        data = r.json()
    return {"payment_url": data.get("url", ""), "payment_id": data.get("id", "")}


async def _migrate_to_whatsapp(inp: dict, tenant: dict, phone: str) -> dict:
    from app.agent.dispatcher import send_whatsapp
    from app.config import settings
    wa_token = tenant.get("whatsapp_token") or settings.meta_wa_token
    phone_number_id = tenant.get("phone_number_id") or settings.meta_wa_phone_number_id
    await send_whatsapp(inp["phone"], inp["message"], wa_token, phone_number_id)
    return {"sent": True, "phone": inp["phone"]}


async def _update_lead_status(inp: dict, tenant: dict, phone: str) -> dict:
    sb = mem.get_client()
    sb.table("leads").update({"status": inp["status"]}).eq("id", inp["lead_id"]).execute()
    return {"updated": True, "status": inp["status"]}


async def _schedule_followup(inp: dict, tenant: dict, phone: str) -> dict:
    sb = mem.get_client()
    scheduled_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    row = sb.table("followup_jobs").insert({
        "lead_id":      inp["lead_id"],
        "tenant_id":    str(tenant["id"]),
        "channel":      inp["channel"],
        "phone":        inp.get("phone", ""),
        "ig_user_id":   inp.get("ig_user_id", ""),
        "job_type":     inp["job_type"],
        "scheduled_at": scheduled_at,
        "payload":      inp.get("payload", {}),
    }).execute()
    return {"scheduled": True, "job_id": row.data[0]["id"], "scheduled_at": scheduled_at}
```

- [ ] **Step 3: Criar `app/agent/claude_client.py` — orquestrador com tool_use**

```python
# app/agent/claude_client.py
import anthropic
import memory as mem
from app.agent.tools import TOOL_DEFINITIONS, execute_tool
from app.agent.prompts import build_prompt

client = anthropic.AsyncAnthropic()
MODELO = "claude-sonnet-4-6"
MAX_TOKENS = 1024


async def processar_mensagem(
    tenant: dict,
    phone: str,
    mensagem_usuario: str,
    canal: str = "whatsapp",
    ig_user_id: str = "",
) -> dict:
    tenant_id = str(tenant["id"])

    lead = mem.get_or_create_lead(tenant_id, phone or ig_user_id, canal)
    lead_id = str(lead["id"])

    mem.save_message(tenant_id, phone or ig_user_id, "user", mensagem_usuario)
    historico = mem.get_messages(tenant_id, phone or ig_user_id)

    system_prompt = build_prompt(tenant, canal)
    mensagens_api = [{"role": m["role"], "content": m["content"]} for m in historico]

    # Aloop de tool_use: Claude pode chamar tools em sequência
    for _ in range(5):  # máximo 5 rodadas de tool_use por turno
        resposta = await client.messages.create(
            model=MODELO,
            max_tokens=MAX_TOKENS,
            system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
            tools=TOOL_DEFINITIONS,
            messages=mensagens_api,
        )

        if resposta.stop_reason == "end_turn":
            texto = next(b.text for b in resposta.content if hasattr(b, "text"))
            mem.save_message(tenant_id, phone or ig_user_id, "assistant", texto)
            mem.update_session(tenant_id, phone or ig_user_id, lead.get("stage", "qualificacao"))
            return {
                "response": texto,
                "stage": lead.get("stage", "qualificacao"),
                "canal": canal,
                "tenant_id": tenant_id,
                "lead_id": lead_id,
            }

        if resposta.stop_reason == "tool_use":
            # Adiciona resposta do assistente (com tool_use blocks) ao contexto
            mensagens_api.append({"role": "assistant", "content": resposta.content})

            # Executa cada tool e acumula resultados
            tool_results = []
            for block in resposta.content:
                if block.type == "tool_use":
                    result = await execute_tool(block.name, block.input, tenant, phone)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": str(result),
                    })

            mensagens_api.append({"role": "user", "content": tool_results})

    # Fallback se loop terminar sem end_turn
    return {"response": "Desculpe, ocorreu um erro interno. Tente novamente.", "stage": "qualificacao", "canal": canal, "tenant_id": tenant_id, "lead_id": lead_id}
```

- [ ] **Step 4: Atualizar `main.py` para importar `processar_mensagem` do novo módulo**

Trocar:
```python
from orchestrator import processar_mensagem
```
Por:
```python
from app.agent.claude_client import processar_mensagem
```

- [ ] **Step 5: Testar loop tool_use**

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"tenant_name": "lumina", "phone": "5585999999999", "message": "quero agendar botox para sexta 14h"}'
```

Esperado: Claude chama `check_availability` e `book_appointment` automaticamente; resposta final confirma agendamento.

- [ ] **Step 6: Commit**

```bash
git add app/agent/tools.py app/agent/claude_client.py main.py
git commit -m "feat: tool_use Claude — 6 tools substituem STAGE: regex"
```

---

## Task 4: Reescrever prompts.py (BANT/SPIN + vendedor de alta performance)

**Files:**
- Create: `app/agent/prompts.py`

- [ ] **Step 1: Criar `app/agent/prompts.py`**

```python
# app/agent/prompts.py

SYSTEM_PROMPT_WA = """
Você é {professional_name}, consultora de alta performance da {clinic_name}.
Você não é um bot de atendimento — você é a melhor vendedora da clínica, que nunca dorme.

## MISSÃO
Fechar vendas. Não apenas responder perguntas.
Resposta em <3s, qualificação natural, agendamento confirmado, link de pagamento no momento certo.

## QUALIFICAÇÃO BANT (conversacional — nunca como formulário)
Avalie silenciosamente durante a conversa:
- **Budget:** Lead tem condição de pagar? (sinais: pergunta preço, compara com concorrente)
- **Authority:** É quem decide, ou precisa consultar alguém?
- **Need:** Qual resultado concreto quer alcançar?
- **Timeline:** Quer resolver agora ou "está pesquisando"?
Lead qualificado = Budget + Need confirmados. Agende se Authority + Timeline favoráveis.

## MOMENTO PSICOLÓGICO
Detecte em qual estado o lead está:
1. **Curiosidade** — explore a necessidade, não venda ainda
2. **Interesse** — apresente o serviço com benefícios concretos
3. **Hesitação** — valide emocionalmente, reancoragem de valor
4. **Urgência** — confirme agenda + envie link de pagamento agora
Nunca apresente link de pagamento antes de detectar Urgência.

## TOOLS DISPONÍVEIS
Use as tools quando o lead chegar no momento certo:
- `check_availability` — antes de confirmar qualquer horário
- `book_appointment` — após lead confirmar data, hora e serviço
- `generate_payment_link` — após agendamento + detecção de urgência/decisão
- `update_lead_status` — ao mudar de estágio (qualificado → agendado → fechado)
- `schedule_followup` — após agendamento (appointment_reminder) ou envio de link (payment_recovery)
- `migrate_to_whatsapp` — apenas no canal Instagram, quando lead quiser fechar

## OBJEÇÃO DE PREÇO
1. "Entendo, é um investimento importante."
2. Reancoragem: resultado duradouro, profissionais qualificados, materiais de referência
3. "Que tal uma avaliação gratuita? Sem compromisso, você conhece a {professional_name} pessoalmente."
4. Se ainda resistir: encerre com gentileza, não insista.

## GUARDRAILS
- Nunca diagnostique condições médicas ou de pele
- Nunca sugira medicamentos ou prescrições
- Se relatar reação pós-procedimento grave: "Chamo nossa equipe agora" + use `update_lead_status` com frio e escale
- Se pedir para falar com humano: respeite e avise a equipe

## LGPD — PRIMEIRA MENSAGEM OBRIGATÓRIA
Na PRIMEIRA interação (histórico vazio), inclua ANTES de qualquer outra coisa:
"Olá! Antes de começar, nosso atendimento é feito pela {clinic_name} e seguimos a LGPD.
Suas informações são usadas apenas para este atendimento. Para parar, basta digitar SAIR. Posso continuar?"
Só prossiga se o lead confirmar (aceite implícito pela continuação da conversa é válido).

## COMANDO SAIR
Se o lead digitar "SAIR" (case-insensitive): use `update_lead_status` com "frio" e responda:
"Entendido! Removemos seus dados do nosso sistema. Se quiser retornar, é só nos chamar. 💛"

## FORMATO DAS MENSAGENS
- Máximo 3 parágrafos curtos (2-3 linhas cada)
- Exatamente 1 pergunta aberta por mensagem
- Emojis: máximo 1-2 por mensagem. Apenas: ✨ 😊 💆 💅 🗓️ 💛
- Nunca use menus numerados — opções em texto corrido
- Nunca use: "amor", "querida", "linda" — use o nome da cliente

## SERVIÇOS DISPONÍVEIS
{servicos}

## HORÁRIOS
{horarios}
"""

SYSTEM_PROMPT_IG = SYSTEM_PROMPT_WA + """

## CANAL: INSTAGRAM
Você está no Instagram DM. Leads do IG tendem a estar em fase de curiosidade/interesse.
Quando o lead demonstrar intenção de fechar (urgência detectada), use `migrate_to_whatsapp`
para transferi-lo para o WhatsApp onde o pagamento é mais fluido.
"""

SERVICOS_PADRAO = """- Limpeza de pele profunda (R$ 180)
- Hidratação facial com ácido hialurônico (R$ 250)
- Peeling químico (R$ 300)
- Carboxiterapia (R$ 350)
- Botox preventivo (a partir de R$ 600)
- Preenchimento labial (a partir de R$ 700)"""

HORARIOS_PADRAO = "Segunda a sexta: 9h às 19h | Sábado: 9h às 14h"


def build_prompt(tenant: dict, canal: str = "whatsapp") -> str:
    template = SYSTEM_PROMPT_IG if canal == "instagram" else SYSTEM_PROMPT_WA
    servicos = tenant.get("servicos") or SERVICOS_PADRAO
    horarios = tenant.get("horarios") or HORARIOS_PADRAO
    if isinstance(servicos, (dict, list)):
        import json
        servicos = json.dumps(servicos, ensure_ascii=False)
    return template.format(
        professional_name=tenant.get("professional_name", "Assistente Virtual"),
        clinic_name=tenant.get("clinic_name", "Clínica"),
        servicos=servicos,
        horarios=horarios,
    )
```

- [ ] **Step 2: Atualizar import em `app/agent/claude_client.py`**

Linha já usa `from app.agent.prompts import build_prompt` — confirmar que está correto.

- [ ] **Step 3: Commit**

```bash
git add app/agent/prompts.py
git commit -m "feat: system prompt BANT/SPIN + vendedor alta performance + LGPD obrigatória"
```

---

## Task 5: Webhook Instagram DM

**Files:**
- Create: `app/webhooks/instagram.py`
- Modify: `main.py` — registrar router Instagram

- [ ] **Step 1: Criar `app/webhooks/instagram.py`**

```python
# app/webhooks/instagram.py
import json
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse, JSONResponse
import memory as mem
from app.agent.claude_client import processar_mensagem
from app.agent.dispatcher import send_message
from app.config import settings

router = APIRouter()


@router.get("/webhook/instagram")
async def instagram_verify(request: Request):
    """Verificação do webhook pela Meta (Instagram Graph API)."""
    params = dict(request.query_params)
    verify_token = settings.meta_verify_token
    if params.get("hub.mode") == "subscribe" and params.get("hub.verify_token") == verify_token:
        return PlainTextResponse(content=params.get("hub.challenge", ""))
    raise HTTPException(status_code=403, detail="Token de verificação inválido")


@router.post("/webhook/instagram")
async def instagram_webhook(request: Request):
    """Recebe mensagens do Instagram DM via Meta Graph API."""
    try:
        body = json.loads(await request.body())
    except Exception:
        raise HTTPException(status_code=400, detail="Payload JSON inválido")

    ig_user_id, mensagem, page_id = _extrair_mensagem_instagram(body)

    if not ig_user_id or not mensagem:
        return JSONResponse(content={"status": "ignorado"})

    # Identifica tenant pelo page_id
    tenant = _get_tenant_by_page_id(page_id)
    if not tenant:
        return JSONResponse(status_code=404, content={"status": "erro", "motivo": f"Tenant não encontrado para page_id={page_id}"})

    resultado = await processar_mensagem(
        tenant=tenant,
        phone="",
        mensagem_usuario=mensagem,
        canal="instagram",
        ig_user_id=ig_user_id,
    )

    await send_message(
        channel="instagram",
        phone="",
        ig_user_id=ig_user_id,
        text=resultado["response"],
        tenant=tenant,
    )

    return JSONResponse(content={"status": "ok", "ig_user_id": ig_user_id})


def _extrair_mensagem_instagram(body: dict) -> tuple[str, str, str]:
    """Extrai ig_user_id, texto e page_id do payload Instagram."""
    try:
        entry = body["entry"][0]
        page_id = entry.get("id", "")
        messaging = entry.get("messaging", [])
        if not messaging:
            return "", "", page_id
        msg = messaging[0]
        ig_user_id = msg["sender"]["id"]
        text = msg.get("message", {}).get("text", "").strip()
        return ig_user_id, text, page_id
    except (KeyError, IndexError, TypeError):
        return "", "", ""


def _get_tenant_by_page_id(page_id: str) -> dict | None:
    sb = mem.get_client()
    result = (
        sb.table("tenants")
        .select("*")
        .eq("ig_page_id", page_id)
        .eq("ativo", True)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None
```

> Nota: adicionar campo `ig_page_id` na tabela tenants:
> ```sql
> ALTER TABLE tenants ADD COLUMN IF NOT EXISTS ig_page_id TEXT;
> ```

- [ ] **Step 2: Registrar router em `main.py`**

```python
from app.webhooks.instagram import router as instagram_router
app.include_router(instagram_router)
```

- [ ] **Step 3: Adicionar campo `ig_page_id` ao migration SQL**

No arquivo `database/migration_v2.sql` já executado, rodar no Supabase:
```sql
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS ig_page_id TEXT;
```

- [ ] **Step 4: Commit**

```bash
git add app/webhooks/instagram.py main.py
git commit -m "feat: webhook Instagram DM GET verify + POST receive"
```

---

## Task 6: APScheduler + Follow-up D+1

**Files:**
- Create: `app/services/followup_service.py`
- Create: `app/jobs/scheduler.py`
- Modify: `main.py` — lifespan com APScheduler

- [ ] **Step 1: Criar `app/services/followup_service.py`**

```python
# app/services/followup_service.py
import logging
from datetime import datetime, timezone
import memory as mem
from app.agent.dispatcher import send_message
from app.config import settings

logger = logging.getLogger(__name__)

TEMPLATES = {
    "appointment_reminder": "Olá! 🗓️ Só passando para confirmar seu agendamento amanhã. Você vem, né? Qualquer dúvida é só falar!",
    "payment_recovery":     "Olá! Vi que você não finalizou o pagamento. O link ainda está válido — posso te ajudar com alguma dúvida? ✨",
    "pos_venda":            "Olá! Esperamos que tenha adorado o resultado! 😊 Tem alguém que você indicaria para conhecer nossos serviços?",
}


async def executar_jobs_pendentes() -> None:
    """Executa todos os followup_jobs com scheduled_at <= agora e status=pending."""
    sb = mem.get_client()
    agora = datetime.now(timezone.utc).isoformat()

    jobs = (
        sb.table("followup_jobs")
        .select("*, tenants(*)")
        .lte("scheduled_at", agora)
        .eq("status", "pending")
        .execute()
    ).data or []

    for job in jobs:
        try:
            await _executar_job(job, sb)
        except Exception as e:
            logger.error("Falha ao executar job %s: %s", job["id"], e)
            sb.table("followup_jobs").update({"status": "failed"}).eq("id", job["id"]).execute()


async def _executar_job(job: dict, sb) -> None:
    tenant = job.get("tenants") or {}
    text = TEMPLATES.get(job["job_type"], "Olá! Tudo bem por aí?")

    await send_message(
        channel=job["channel"],
        phone=job.get("phone", ""),
        ig_user_id=job.get("ig_user_id", ""),
        text=text,
        tenant=tenant,
    )

    sb.table("followup_jobs").update({
        "status": "done",
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", job["id"]).execute()

    logger.info("Job %s executado: %s → %s", job["id"], job["job_type"], job.get("phone") or job.get("ig_user_id"))
```

- [ ] **Step 2: Criar `app/jobs/scheduler.py`**

```python
# app/jobs/scheduler.py
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from app.services.followup_service import executar_jobs_pendentes

logger = logging.getLogger(__name__)
_scheduler: AsyncIOScheduler | None = None


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler()
        _scheduler.add_job(
            executar_jobs_pendentes,
            trigger="interval",
            seconds=60,
            id="followup_runner",
            replace_existing=True,
        )
    return _scheduler
```

- [ ] **Step 3: Adicionar lifespan ao `main.py`**

```python
from contextlib import asynccontextmanager
from app.jobs.scheduler import get_scheduler

@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = get_scheduler()
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)

app = FastAPI(
    title="Automação Completa — Agente de Vendas IA",
    lifespan=lifespan,
    version="3.0.0",
)
```

- [ ] **Step 4: Testar scheduler localmente**

```bash
uvicorn main:app --reload
# Verificar no log: "Scheduler started" sem erros
# Inserir manualmente um job com scheduled_at = now() no Supabase
# Aguardar até 60s e verificar que status muda para "done"
```

- [ ] **Step 5: Commit**

```bash
git add app/services/followup_service.py app/jobs/scheduler.py main.py
git commit -m "feat: APScheduler + follow-up D+1 (appointment_reminder, payment_recovery, pos_venda)"
```

---

## Task 7: Endpoint /payment/confirm (callback Asaas)

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Adicionar endpoint em `main.py`**

```python
@app.post("/payment/confirm")
async def payment_confirm(request: Request):
    """Callback Asaas — disparado quando pagamento é confirmado."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Payload inválido")

    event = body.get("event", "")
    payment = body.get("payment", {})

    # Asaas envia PAYMENT_RECEIVED ou PAYMENT_CONFIRMED para Pix confirmado
    if event not in ("PAYMENT_RECEIVED", "PAYMENT_CONFIRMED"):
        return JSONResponse(content={"status": "ignorado", "event": event})

    payment_id = payment.get("id", "")
    # Busca lead pelo payment_id salvo no payload do followup_job
    sb = mem.get_client()
    jobs = (
        sb.table("followup_jobs")
        .select("lead_id, tenant_id, channel, phone, ig_user_id")
        .contains("payload", {"payment_id": payment_id})
        .limit(1)
        .execute()
    ).data or []

    if not jobs:
        return JSONResponse(content={"status": "lead_nao_encontrado", "payment_id": payment_id})

    job = jobs[0]
    lead_id = job["lead_id"]

    # Atualiza status do lead para fechado
    sb.table("leads").update({"status": "fechado"}).eq("id", lead_id).execute()

    # Agenda job pos_venda D+1
    from datetime import timedelta
    agora = datetime.now(timezone.utc)
    sb.table("followup_jobs").insert({
        "lead_id":      lead_id,
        "tenant_id":    job["tenant_id"],
        "channel":      job["channel"],
        "phone":        job.get("phone", ""),
        "ig_user_id":   job.get("ig_user_id", ""),
        "job_type":     "pos_venda",
        "scheduled_at": (agora + timedelta(days=1)).isoformat(),
        "status":       "pending",
        "payload":      {"payment_id": payment_id},
    }).execute()

    return JSONResponse(content={"status": "ok", "lead_id": lead_id, "novo_status": "fechado"})
```

- [ ] **Step 2: Commit**

```bash
git add main.py
git commit -m "feat: POST /payment/confirm callback Asaas → lead fechado + job pos_venda"
```

---

## Task 8: LGPD — consent_log + comando SAIR

**Files:**
- Modify: `app/agent/claude_client.py`
- Modify: `app/agent/tools.py`

A LGPD já está no system prompt (Task 4). Aqui persistimos o consentimento no Supabase.

- [ ] **Step 1: Adicionar `save_consent` em `app/agent/claude_client.py`**

Logo após `lead = mem.get_or_create_lead(...)`, verificar se é primeira mensagem e salvar consent:

```python
# app/agent/claude_client.py — dentro de processar_mensagem, após criar lead
historico_count = len(mem.get_messages(tenant_id, phone or ig_user_id, limit=1))
if historico_count == 0:  # primeira mensagem
    sb = mem.get_client()
    sb.table("consent_log").insert({
        "lead_id":      lead_id,
        "tenant_id":    tenant_id,
        "channel":      canal,
        "consent_text": "Opt-in implícito: lead iniciou conversa. LGPD informada na primeira mensagem.",
    }).execute()
```

- [ ] **Step 2: Tratar comando SAIR antes de chamar Claude**

```python
# app/agent/claude_client.py — logo após save_message do usuário
if mensagem_usuario.strip().upper() == "SAIR":
    sb = mem.get_client()
    sb.table("leads").update({"status": "frio"}).eq("id", lead_id).execute()
    resposta_sair = "Entendido! Removemos seus dados do nosso sistema. Se quiser retornar, é só nos chamar. 💛"
    mem.save_message(tenant_id, phone or ig_user_id, "assistant", resposta_sair)
    return {"response": resposta_sair, "stage": "frio", "canal": canal, "tenant_id": tenant_id, "lead_id": lead_id}
```

- [ ] **Step 3: Commit**

```bash
git add app/agent/claude_client.py
git commit -m "feat: LGPD consent_log na primeira mensagem + comando SAIR para opt-out"
```

---

## Task 9: Dashboard — GET /leads com filtros

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Expandir endpoint `/leads/{tenant_name}` com filtros**

```python
from typing import Optional

@app.get("/leads/{tenant_name}")
async def list_leads(
    tenant_name: str,
    status: Optional[str] = None,
    canal: Optional[str] = None,
    desde: Optional[str] = None,
    x_admin_key: str | None = Header(default=None),
):
    _verificar_admin(x_admin_key)
    tenant = mem.get_tenant_by_name(tenant_name)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant não encontrado")
    sb = mem.get_client()
    query = sb.table("leads").select("*").eq("tenant_id", tenant["id"])
    if status:
        query = query.eq("status", status)
    if canal:
        query = query.eq("canal", canal)
    if desde:
        query = query.gte("created_at", desde)
    result = query.order("created_at", desc=True).execute()
    return result.data or []
```

- [ ] **Step 2: Commit**

```bash
git add main.py
git commit -m "feat: GET /leads com filtros status, canal, desde"
```

---

## Task 10: railway.toml + deploy final

**Files:**
- Create: `railway.toml`

- [ ] **Step 1: Criar `railway.toml`**

```toml
[build]
builder = "nixpacks"

[deploy]
startCommand = "uvicorn main:app --host 0.0.0.0 --port $PORT"
healthcheckPath = "/"
healthcheckTimeout = 30
restartPolicyType = "on_failure"
```

- [ ] **Step 2: Verificar `.env` com todas as variáveis**

Confirmar que `.env` tem preenchidas:
- `ANTHROPIC_API_KEY`
- `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY`
- `META_WA_TOKEN` / `WHATSAPP_TOKEN`
- `META_WA_PHONE_NUMBER_ID` / `PHONE_NUMBER_ID`
- `META_VERIFY_TOKEN` / `VERIFY_TOKEN`
- `ASAAS_API_KEY`

- [ ] **Step 3: Push e validar deploy no Railway**

```bash
git add railway.toml
git commit -m "chore: railway.toml deploy config"
git push
```

Verificar em Railway: build passa, health check `/` retorna `{"status": "online"}`.

- [ ] **Step 4: Registrar webhook no Meta Business Manager**

URL: `https://<seu-dominio>.railway.app/webhook/whatsapp`
Token de verificação: valor de `META_VERIFY_TOKEN`
Campos subscritos: `messages`

Repetir para Instagram:
URL: `https://<seu-dominio>.railway.app/webhook/instagram`

---

## Checklist de spec coverage

| Requisito do CONTEXT.md | Task que implementa |
|--------------------------|---------------------|
| Migrar WA Evolution → Meta Cloud API | Task 2 (dispatcher) + Task 3 (webhook já existia) |
| Webhook Instagram DM | Task 5 |
| Tool: check_availability | Task 3 |
| Tool: book_appointment | Task 3 |
| Tool: generate_payment_link | Task 3 |
| Tool: migrate_to_whatsapp | Task 3 |
| Tool: update_lead_status | Task 3 |
| Tool: schedule_followup | Task 3 |
| System prompt BANT/SPIN | Task 4 |
| POST /payment/confirm | Task 7 |
| APScheduler + followup D+1 | Task 6 |
| LGPD consent_log + SAIR | Task 8 |
| GET /leads com filtros | Task 9 |
| railway.toml | Task 10 |
| Tabelas: appointments, followup_jobs, consent_log | Task 1 |
| pydantic-settings config | Task 1 |
