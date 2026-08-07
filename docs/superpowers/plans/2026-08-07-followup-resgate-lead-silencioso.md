# Follow-up de Resgate — Lead Silencioso Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Quando um lead `novo`/`qualificado` fica 3h sem responder, o agente reengaja sozinho com uma mensagem gerada pelo Claude retomando o assunto de onde parou — até 3 tentativas escalonadas (3h → D+1 → D+3), depois marca o lead como `frio`.

**Architecture:** Reusa a tabela `followup_jobs` e o loop de 60s do APScheduler que já existem — sem tabela nova. Agendamento é **reativo**: acontece dentro de `processar_mensagem()` a cada mensagem recebida do lead (cancela o resgate pendente e agenda um novo pra +3h), não por varredura periódica. A execução (geração da mensagem via Claude + encadeamento da próxima tentativa) fica em `followup_service.py`, mesmo lugar que já executa os outros `job_type`.

**Tech Stack:** FastAPI · Python 3.11 · Anthropic SDK (`claude-sonnet-5`) · Supabase (PostgreSQL) · APScheduler (já configurado)

## Global Constraints

- Threshold de silêncio: **3 horas** sem mensagem do lead dispara a 1ª tentativa.
- **3 tentativas, escalonadas: 3h → D+1 → D+3.** Cada uma só é agendada depois que a anterior foi enviada.
- Depois da 3ª tentativa sem resposta, `leads.stage = 'frio'` automaticamente.
- Só se aplica a leads com `stage` em `novo` ou `qualificado`.
- Não agenda se o lead já tem outro `followup_job` `pending` de tipo diferente (evita duplicar mensagem de reengajamento).
- Mensagem gerada pelo Claude (não template fixo), puxando o histórico da conversa — tom escala por tentativa.
- Nunca agenda nem envia se `leads.escalado = true`.
- Nenhuma tabela nova — reusa `followup_jobs`, campo `payload` (JSONB) guarda `tentativa` e `last_msg_at_snapshot`.

Spec completa: `docs/superpowers/specs/2026-08-07-followup-resgate-lead-silencioso-design.md`

---

## Task 1: Migration — `followup_jobs.job_type` aceita `resgate_silencio`

**Files:**
- Create: `database/migration_v10.sql`

**Interfaces:**
- Produces: valor `'resgate_silencio'` passa a ser aceito pela constraint `followup_jobs_job_type_check` — sem isso, qualquer `INSERT` das Tasks 2 e 3 é rejeitado pelo Postgres.

Contexto: `migration_v9.sql` deixou o CHECK em `('appointment_reminder', 'payment_recovery', 'pos_venda', 'recall_procedimento', 'cross_sell')`. Precisa ampliar de novo, mesmo padrão de `v3`/`v8`/`v9`.

- [ ] **Step 1: Criar `database/migration_v10.sql`**

```sql
-- ─────────────────────────────────────────────────────────────────────────────
-- Migration v10 — Follow-up de resgate por silêncio
-- Execute no SQL Editor do Supabase DEPOIS de migration_v9.sql
-- Seguro rodar mais de uma vez; nenhum comando apaga dado.
-- ─────────────────────────────────────────────────────────────────────────────

-- followup_jobs.job_type precisa aceitar 'resgate_silencio'. A migration_v9 deixou
-- o CHECK com 5 tipos; sem ampliar de novo, o INSERT feito pelo agendamento reativo
-- em processar_mensagem() (Task 2) é rejeitado pelo Postgres.
ALTER TABLE followup_jobs DROP CONSTRAINT IF EXISTS followup_jobs_job_type_check;
ALTER TABLE followup_jobs ADD CONSTRAINT followup_jobs_job_type_check
    CHECK (job_type IN ('appointment_reminder', 'payment_recovery', 'pos_venda',
                        'recall_procedimento', 'cross_sell', 'resgate_silencio'));

-- VERIFICAÇÃO — deve devolver 1.
SELECT COUNT(*) AS check_aceita_resgate_silencio
FROM pg_constraint
WHERE conname = 'followup_jobs_job_type_check'
  AND pg_get_constraintdef(oid) LIKE '%resgate_silencio%';
```

- [ ] **Step 2: Rodar no SQL Editor do Supabase**

Ação manual (fora deste ambiente): colar o conteúdo do arquivo no SQL Editor do projeto Supabase e executar. Conferir que a query de verificação do Step 1 devolve `1`.

- [ ] **Step 3: Commit**

```bash
git add database/migration_v10.sql
git commit -m "feat: migration v10 — job_type resgate_silencio em followup_jobs"
```

---

## Task 2: Agendamento reativo em `processar_mensagem()`

**Files:**
- Modify: `app/agent/claude_client.py`

**Interfaces:**
- Consumes: `mem.get_client()` (já importado); nenhuma função nova de outro arquivo.
- Produces: `_agendar_resgate_silencio(tenant: dict, tenant_id: str, identifier: str, canal: str, phone: str, ig_user_id: str, lead_id: str) -> None` — usada só dentro deste arquivo. Cria linhas `followup_jobs` com `job_type='resgate_silencio'` que a Task 3 consome.

Depende da Task 1 (o `INSERT` do `job_type` novo só funciona depois da migration).

- [ ] **Step 1: Adicionar imports no topo de `app/agent/claude_client.py`**

Trocar:
```python
# app/agent/claude_client.py
import asyncio
import logging
import anthropic
import memory as mem
from app.agent.tools import TOOL_DEFINITIONS, execute_tool
```
Por:
```python
# app/agent/claude_client.py
import asyncio
import logging
from datetime import datetime, timedelta, timezone
import anthropic
import memory as mem
from app.agent.tools import TOOL_DEFINITIONS, execute_tool
```

- [ ] **Step 2: Criar `_agendar_resgate_silencio()` — inserir depois de `_get_system_prompt()`, antes de `async def processar_mensagem(`**

```python
async def _agendar_resgate_silencio(
    tenant: dict,
    tenant_id: str,
    identifier: str,
    canal: str,
    phone: str,
    ig_user_id: str,
    lead_id: str,
) -> None:
    """Reagenda o resgate por silêncio a cada mensagem do lead: cancela qualquer
    tentativa pendente (o "relógio" reseta a cada mensagem recebida) e agenda uma
    tentativa 1 nova pra daqui 3h — só se o lead seguir em aberto (novo/qualificado),
    sem estar escalado pra humano, e sem já ter outro follow-up pendente de outro
    tipo (evita duas mensagens de reengajamento diferentes chegando juntas).

    Sempre lê o estado mais recente do lead direto do Supabase em vez de usar o
    dict `lead` já carregado em memória: tools chamadas nesse mesmo turno (ex:
    update_lead_status, escalate_to_human) escrevem direto no banco sem atualizar
    esse dict, então confiar nele aqui poderia agendar (ou deixar de cancelar) com
    base num estágio que já mudou.
    """
    sb = mem.get_client()

    await asyncio.to_thread(
        lambda: sb.table("followup_jobs")
            .update({"status": "cancelled"})
            .eq("lead_id", lead_id)
            .eq("job_type", "resgate_silencio")
            .eq("status", "pending")
            .execute()
    )

    fresh = await asyncio.to_thread(
        lambda: sb.table("leads").select("stage, escalado").eq("id", lead_id).limit(1).execute()
    )
    if not fresh.data:
        return
    stage = fresh.data[0].get("stage")
    escalado = fresh.data[0].get("escalado")
    if stage not in ("novo", "qualificado") or escalado:
        return

    outro_pendente = await asyncio.to_thread(
        lambda: sb.table("followup_jobs")
            .select("id")
            .eq("lead_id", lead_id)
            .eq("status", "pending")
            .neq("job_type", "resgate_silencio")
            .limit(1)
            .execute()
    )
    if outro_pendente.data:
        return

    agora = datetime.now(timezone.utc)
    scheduled_at = (agora + timedelta(hours=3)).isoformat()
    await asyncio.to_thread(
        lambda: sb.table("followup_jobs").insert({
            "lead_id":      lead_id,
            "tenant_id":    tenant_id,
            "channel":      canal,
            "phone":        phone,
            "ig_user_id":   ig_user_id,
            "job_type":     "resgate_silencio",
            "scheduled_at": scheduled_at,
            "payload":      {"tentativa": 1, "last_msg_at_snapshot": agora.isoformat()},
        }).execute()
    )
```

- [ ] **Step 3: Chamar `_agendar_resgate_silencio()` nos 5 pontos de saída de `processar_mensagem()`**

**3a — saída "escalado" (perto do início da função):**

Trocar:
```python
    if lead.get("escalado"):
        logger.info("Lead %s está escalado para atendimento humano — IA não responde", lead_id)
        return {
            "response":  "",
            "stage":     lead.get("stage", "qualificacao"),
            "canal":     canal,
            "tenant_id": tenant_id,
            "lead_id":   lead_id,
            "escalado":  True,
        }
```
Por:
```python
    if lead.get("escalado"):
        logger.info("Lead %s está escalado para atendimento humano — IA não responde", lead_id)
        await _agendar_resgate_silencio(tenant, tenant_id, identifier, canal, phone, ig_user_id, lead_id)
        return {
            "response":  "",
            "stage":     lead.get("stage", "qualificacao"),
            "canal":     canal,
            "tenant_id": tenant_id,
            "lead_id":   lead_id,
            "escalado":  True,
        }
```

**3b — saída "SAIR":**

Trocar:
```python
        resposta_sair = "Entendido! Removemos seus dados do nosso sistema. Se quiser retornar, é só nos chamar. 💛"
        if salvar_resposta:
            mem.save_message(tenant_id, identifier, "assistant", resposta_sair)
        return {
            "response":  resposta_sair,
            "stage":     "frio",
            "canal":     canal,
            "tenant_id": tenant_id,
            "lead_id":   lead_id,
        }
```
Por:
```python
        resposta_sair = "Entendido! Removemos seus dados do nosso sistema. Se quiser retornar, é só nos chamar. 💛"
        if salvar_resposta:
            mem.save_message(tenant_id, identifier, "assistant", resposta_sair)
        await _agendar_resgate_silencio(tenant, tenant_id, identifier, canal, phone, ig_user_id, lead_id)
        return {
            "response":  resposta_sair,
            "stage":     "frio",
            "canal":     canal,
            "tenant_id": tenant_id,
            "lead_id":   lead_id,
        }
```

**3c — saída "primeira mensagem / aviso LGPD":**

Trocar:
```python
        if salvar_resposta:
            mem.save_message(tenant_id, identifier, "assistant", resposta_lgpd)
        mem.update_session(tenant_id, identifier, lead.get("stage", "qualificacao"))
        return {
            "response":  resposta_lgpd,
            "stage":     lead.get("stage", "qualificacao"),
            "canal":     canal,
            "tenant_id": tenant_id,
            "lead_id":   lead_id,
        }
```
Por:
```python
        if salvar_resposta:
            mem.save_message(tenant_id, identifier, "assistant", resposta_lgpd)
        mem.update_session(tenant_id, identifier, lead.get("stage", "qualificacao"))
        await _agendar_resgate_silencio(tenant, tenant_id, identifier, canal, phone, ig_user_id, lead_id)
        return {
            "response":  resposta_lgpd,
            "stage":     lead.get("stage", "qualificacao"),
            "canal":     canal,
            "tenant_id": tenant_id,
            "lead_id":   lead_id,
        }
```

**3d — saída normal (`end_turn`, dentro do loop de tool_use):**

Trocar:
```python
        if resposta.stop_reason == "end_turn":
            texto = next((b.text for b in resposta.content if hasattr(b, "text")), "")
            if texto and salvar_resposta:  # guard: não salvar resposta vazia
                mem.save_message(tenant_id, identifier, "assistant", texto)
            mem.update_session(tenant_id, identifier, lead.get("stage", "qualificacao"))
            return {
                "response": texto,
                "stage":    lead.get("stage", "qualificacao"),
                "canal":    canal,
                "tenant_id": tenant_id,
                "lead_id":  lead_id,
            }
```
Por:
```python
        if resposta.stop_reason == "end_turn":
            texto = next((b.text for b in resposta.content if hasattr(b, "text")), "")
            if texto and salvar_resposta:  # guard: não salvar resposta vazia
                mem.save_message(tenant_id, identifier, "assistant", texto)
            mem.update_session(tenant_id, identifier, lead.get("stage", "qualificacao"))
            await _agendar_resgate_silencio(tenant, tenant_id, identifier, canal, phone, ig_user_id, lead_id)
            return {
                "response": texto,
                "stage":    lead.get("stage", "qualificacao"),
                "canal":    canal,
                "tenant_id": tenant_id,
                "lead_id":  lead_id,
            }
```

**3e — saída de fallback (loop de tool_use esgotou 5 iterações, fim da função):**

Trocar:
```python
    logger.error("Loop tool_use esgotou 5 iterações sem end_turn para lead %s", lead_id)
    return {
        "response":  "Desculpe, ocorreu um erro interno. Tente novamente.",
        "stage":     "qualificacao",
        "canal":     canal,
        "tenant_id": tenant_id,
        "lead_id":   lead_id,
    }
```
Por:
```python
    logger.error("Loop tool_use esgotou 5 iterações sem end_turn para lead %s", lead_id)
    await _agendar_resgate_silencio(tenant, tenant_id, identifier, canal, phone, ig_user_id, lead_id)
    return {
        "response":  "Desculpe, ocorreu um erro interno. Tente novamente.",
        "stage":     "qualificacao",
        "canal":     canal,
        "tenant_id": tenant_id,
        "lead_id":   lead_id,
    }
```

- [ ] **Step 4: Testar manualmente**

Rodar localmente:
```bash
uvicorn main:app --reload
```

Mandar uma mensagem de teste (troque `5585999999999` por um telefone de teste que não vá receber WhatsApp de verdade, ou use um tenant/lead de teste já combinado):
```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"tenant_name": "lumina", "phone": "5585999999999", "message": "oi, quero saber sobre botox"}'
```

No SQL Editor do Supabase, conferir que foi criado um `followup_jobs` novo:
```sql
select job_type, status, scheduled_at, payload
from followup_jobs
where lead_id = (select id from leads where phone = '5585999999999' limit 1)
order by scheduled_at desc;
```
Esperado: uma linha `job_type='resgate_silencio'`, `status='pending'`, `scheduled_at` ≈ agora + 3h, `payload.tentativa = 1`.

Mandar uma segunda mensagem do mesmo telefone:
```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"tenant_name": "lumina", "phone": "5585999999999", "message": "quanto custa?"}'
```
Rodar a mesma query SQL de novo — esperado: a linha anterior virou `status='cancelled'` e existe uma nova `pending` com `scheduled_at` recalculado a partir da 2ª mensagem.

**Checagem extra 1 — não duplica com outro follow-up pendente:** no SQL Editor, inserir manualmente um job de outro tipo pra esse lead (`insert into followup_jobs (lead_id, tenant_id, channel, phone, job_type, scheduled_at) select id, tenant_id, 'whatsapp', '5585999999999', 'payment_recovery', now() + interval '1 day' from leads where phone = '5585999999999' limit 1;`), mandar uma nova mensagem de teste via `/chat`, e conferir que **nenhum** `resgate_silencio` novo foi criado (só o `payment_recovery` que você inseriu). Depois, apagar essa linha de teste (`delete from followup_jobs where job_type = 'payment_recovery' and phone = '5585999999999';`) antes de continuar.

**Checagem extra 2 — respeita `escalado`:** marcar o lead de teste como escalado (`update leads set escalado = true where phone = '5585999999999';`), mandar uma nova mensagem via `/chat`, e conferir que nenhum `resgate_silencio` novo foi criado. Reverter depois (`update leads set escalado = false where phone = '5585999999999';`) pra não atrapalhar o teste da Task 3.

- [ ] **Step 5: Commit**

```bash
git add app/agent/claude_client.py
git commit -m "feat: agenda resgate por silêncio (3h) a cada mensagem do lead"
```

---

## Task 3: Execução do resgate — mensagem via Claude + encadeamento de tentativas

**Files:**
- Modify: `app/services/followup_service.py`

**Interfaces:**
- Consumes: `followup_jobs` com `job_type='resgate_silencio'` criados pela Task 2; `mem.get_client()`, `mem.get_messages(tenant_id: str, phone: str, limit: int = 20) -> list[dict]` (já existem em `memory.py`); `send_message(channel, phone, ig_user_id, text, tenant)` de `app/agent/dispatcher.py` (já importado no arquivo).
- Produces: mensagem enviada ao lead; próxima tentativa agendada ou `leads.stage='frio'` na 3ª.

Depende da Task 1 (constraint) e da Task 2 (é o que cria os jobs que esta task executa).

- [ ] **Step 1: Adicionar imports e constantes no topo de `app/services/followup_service.py`**

Trocar:
```python
# app/services/followup_service.py
import asyncio
import logging
from datetime import datetime, timezone
import memory as mem
from app.agent.dispatcher import send_message

logger = logging.getLogger(__name__)
```
Por:
```python
# app/services/followup_service.py
import asyncio
import logging
from datetime import datetime, timedelta, timezone
import anthropic
import memory as mem
from app.agent.dispatcher import send_message

logger = logging.getLogger(__name__)

_anthropic_client = anthropic.AsyncAnthropic()
MODELO = "claude-sonnet-5"
MAX_TOKENS_RESGATE = 200

_FALLBACK_RESGATE = "Oi! Ainda tem interesse? Quando quiser continuar, é só me chamar 😊"

_TOM_POR_TENTATIVA = {
    1: "Tom leve e casual, tipo checando se a pessoa ainda está por aí. Não tente vender agora, só reabra a conversa.",
    2: "Reforce o valor do que foi conversado até aqui e já ofereça ver um horário disponível.",
    3: "Última tentativa. Tom direto mas sem pressão — deixe claro que você está à disposição quando ela quiser retomar.",
}
```

- [ ] **Step 2: Criar `_gerar_mensagem_resgate()` — inserir depois de `_montar_texto()`, antes de `async def executar_jobs_pendentes()`**

```python
async def _gerar_mensagem_resgate(tenant: dict, historico: list[dict], tentativa: int) -> str:
    """Gera uma mensagem curta e personalizada retomando a conversa de onde parou,
    em vez de um template fixo — mais alinhado com a narrativa de 'vendedor de alta
    performance' do produto. Cai no texto genérico se a chamada ao Claude falhar,
    mesmo padrão de segurança já usado em _montar_texto() pros outros job_types."""
    professional_name = tenant.get("professional_name") or "Assistente Virtual"
    clinic_name = tenant.get("clinic_name") or "Clínica"
    tom = _TOM_POR_TENTATIVA.get(tentativa, _TOM_POR_TENTATIVA[1])

    system = (
        f"Você é {professional_name}, consultora de vendas da {clinic_name}. "
        "Um lead parou de responder no meio da conversa abaixo. Escreva UMA mensagem "
        "curta (1-2 frases) pra retomar o assunto de onde parou, baseada só no que já "
        f"foi dito. {tom} Nunca invente informação que não esteja no histórico. "
        "Responda só com o texto da mensagem, sem aspas e sem explicação."
    )
    mensagens_api = [{"role": m["role"], "content": m["content"]} for m in historico] or [
        {"role": "user", "content": "(sem histórico disponível)"}
    ]

    try:
        resposta = await _anthropic_client.messages.create(
            model=MODELO,
            max_tokens=MAX_TOKENS_RESGATE,
            system=system,
            messages=mensagens_api,
        )
        texto = next((b.text for b in resposta.content if hasattr(b, "text")), "").strip()
        return texto or _FALLBACK_RESGATE
    except Exception as e:
        logger.error("Falha ao gerar mensagem de resgate via Claude: %s", e)
        return _FALLBACK_RESGATE
```

- [ ] **Step 3: Criar `_executar_resgate_silencio()` — inserir depois de `_gerar_mensagem_resgate()`, antes de `_executar_job()`**

```python
async def _executar_resgate_silencio(job: dict, sb, tenant: dict) -> None:
    lead_id = job["lead_id"]
    identifier = job.get("phone") or job.get("ig_user_id") or ""
    payload = job.get("payload") or {}
    tentativa = int(payload.get("tentativa", 1))
    snapshot = payload.get("last_msg_at_snapshot")

    lead_result = await asyncio.to_thread(
        lambda: sb.table("leads").select("stage, escalado").eq("id", lead_id).limit(1).execute()
    )
    lead_row = (lead_result.data or [{}])[0]
    if lead_row.get("escalado") or lead_row.get("stage") not in ("novo", "qualificado"):
        await asyncio.to_thread(
            lambda: sb.table("followup_jobs").update({"status": "cancelled"}).eq("id", job["id"]).execute()
        )
        logger.info("Resgate por silêncio cancelado (lead %s não está mais em aberto): job %s", lead_id, job["id"])
        return

    ultima_msg = await asyncio.to_thread(
        lambda: sb.table("conversations")
            .select("created_at")
            .eq("tenant_id", job["tenant_id"])
            .eq("phone", identifier)
            .eq("role", "user")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
    )
    if ultima_msg.data and snapshot:
        try:
            if datetime.fromisoformat(ultima_msg.data[0]["created_at"]) > datetime.fromisoformat(snapshot):
                await asyncio.to_thread(
                    lambda: sb.table("followup_jobs").update({"status": "cancelled"}).eq("id", job["id"]).execute()
                )
                logger.info("Resgate por silêncio cancelado (lead %s já respondeu): job %s", lead_id, job["id"])
                return
        except (ValueError, TypeError):
            pass  # snapshot ou created_at em formato inesperado — segue e envia mesmo assim

    historico = mem.get_messages(job["tenant_id"], identifier)
    text = await _gerar_mensagem_resgate(tenant, historico, tentativa)

    await send_message(
        channel=job["channel"],
        phone=job.get("phone", ""),
        ig_user_id=job.get("ig_user_id", ""),
        text=text,
        tenant=tenant,
    )

    await asyncio.to_thread(
        lambda: sb.table("followup_jobs").update({
            "status": "done",
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", job["id"]).execute()
    )

    if tentativa < 3:
        dias = 1 if tentativa == 1 else 3
        proxima = datetime.now(timezone.utc) + timedelta(days=dias)
        await asyncio.to_thread(
            lambda: sb.table("followup_jobs").insert({
                "lead_id":      lead_id,
                "tenant_id":    job["tenant_id"],
                "channel":      job["channel"],
                "phone":        job.get("phone", ""),
                "ig_user_id":   job.get("ig_user_id", ""),
                "job_type":     "resgate_silencio",
                "scheduled_at": proxima.isoformat(),
                "payload":      {"tentativa": tentativa + 1, "last_msg_at_snapshot": snapshot},
            }).execute()
        )
    else:
        await asyncio.to_thread(
            lambda: sb.table("leads").update({"stage": "frio"}).eq("id", lead_id).execute()
        )

    logger.info("Resgate por silêncio tentativa %s enviado para lead %s", tentativa, lead_id)
```

- [ ] **Step 4: Fazer `_executar_job()` desviar pra `_executar_resgate_silencio()`**

Trocar:
```python
async def _executar_job(job: dict, sb) -> None:
    tenant = job.get("tenants") or {}
    if not tenant:
        logger.warning("Job %s sem tenant associado — usando credenciais globais", job["id"])

    text = _montar_texto(job)
```
Por:
```python
async def _executar_job(job: dict, sb) -> None:
    tenant = job.get("tenants") or {}
    if not tenant:
        logger.warning("Job %s sem tenant associado — usando credenciais globais", job["id"])

    if job.get("job_type") == "resgate_silencio":
        await _executar_resgate_silencio(job, sb, tenant)
        return

    text = _montar_texto(job)
```

- [ ] **Step 5: Testar manualmente**

Com o app já rodando (`uvicorn main:app --reload`) e um job `resgate_silencio` `pending` criado pelo teste da Task 2, forçar o disparo sem esperar 3h de verdade — no SQL Editor do Supabase:
```sql
update followup_jobs
set scheduled_at = now() - interval '1 minute'
where job_type = 'resgate_silencio'
  and status = 'pending'
  and lead_id = (select id from leads where phone = '5585999999999' limit 1);
```

Esperar até 60s (próximo ciclo do scheduler) e conferir:
1. A mensagem chegou no WhatsApp de teste (ou log do `send_message` se estiver testando sem número real).
2. `followup_jobs` da tentativa 1 virou `status='done'`.
3. Existe uma nova linha `pending`, `payload.tentativa=2`, `scheduled_at` ≈ agora + 1 dia.

```sql
select job_type, status, scheduled_at, payload
from followup_jobs
where lead_id = (select id from leads where phone = '5585999999999' limit 1)
order by scheduled_at desc;
```

Repetir o "forçar `scheduled_at` pro passado" pra tentativa 2 e depois tentativa 3, e confirmar no fim:
```sql
select stage from leads where phone = '5585999999999';
```
Esperado: `frio`, e nenhuma 4ª linha `resgate_silencio` foi criada.

Confirmar também que mandar uma mensagem nova do lead durante o meio da cadeia (Task 2, Step 4) cancela a tentativa pendente — já coberto no teste da Task 2, mas vale reconferir agora com a Task 3 aplicada.

- [ ] **Step 6: Commit**

```bash
git add app/services/followup_service.py
git commit -m "feat: executa resgate por silêncio — mensagem via Claude + encadeamento de tentativas"
```

---

## Task 4: Atualizar CONTEXT.md

**Files:**
- Modify: `CONTEXT.md`

Depende das Tasks 1-3 (documenta o que já existe, não implementa nada novo).

- [ ] **Step 1: Atualizar a seção "Follow-up D+1 — lógica de negócio"**

Trocar:
```markdown
### Follow-up D+1 — lógica de negócio

O scheduler roda a cada 60 segundos verificando `followup_jobs` no Supabase.
Três tipos de job, cada um com template diferente:

| Tipo | Quando dispara | Objetivo |
|------|---------------|----------|
| `appointment_reminder` | 24h após agendamento | Confirmar presença, reduzir no-show |
| `payment_recovery` | 24h após link enviado sem pagamento | Recuperar lead que não pagou |
| `pos_venda` | 24h após pagamento confirmado | Fidelizar, pedir indicação, oferecer retorno |

Estado 100% no Supabase — reinicializações do Railway não perdem nenhum job.
```
Por:
```markdown
### Follow-up — lógica de negócio

O scheduler roda a cada 60 segundos verificando `followup_jobs` no Supabase. Tipos de job, cada um com lógica própria:

| Tipo | Quando dispara | Objetivo |
|------|---------------|----------|
| `appointment_reminder` | 24h após agendamento | Confirmar presença, reduzir no-show |
| `payment_recovery` | 24h após link enviado sem pagamento | Recuperar lead que não pagou |
| `pos_venda` | 24h após pagamento confirmado | Fidelizar, pedir indicação, oferecer retorno |
| `recall_procedimento` | N dias após o procedimento (config por tenant) | Trazer o lead de volta pra repetir o procedimento |
| `cross_sell` | N dias após o procedimento (config por tenant) | Oferecer um procedimento complementar |
| `resgate_silencio` | 3h sem resposta do lead `novo`/`qualificado`, escalonado até 3x (3h/D+1/D+3) | Reengajar lead que sumiu no meio da conversa, com mensagem gerada pelo Claude retomando o assunto — marca `frio` se não responder às 3 tentativas |

Estado 100% no Supabase — reinicializações do Railway não perdem nenhum job. Spec do `resgate_silencio`: `docs/superpowers/specs/2026-08-07-followup-resgate-lead-silencioso-design.md`.
```

- [ ] **Step 2: Commit**

```bash
git add CONTEXT.md
git commit -m "docs: registra job_type resgate_silencio no CONTEXT.md"
```
