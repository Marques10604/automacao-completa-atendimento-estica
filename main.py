# main.py - FastAPI com webhook WhatsApp Cloud API

import hashlib
import hmac
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, Header, HTTPException, Request

logger = logging.getLogger(__name__)
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel
from dotenv import load_dotenv

from app.agent.claude_client import processar_mensagem
import memory as mem
from app.agent.dispatcher import send_message
from app.webhooks.instagram import router as instagram_router
from app.jobs.scheduler import get_scheduler

load_dotenv()

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "")
WHATSAPP_APP_SECRET = os.getenv("WHATSAPP_APP_SECRET", "")
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "")


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = get_scheduler()
    scheduler.start()
    logger.info("APScheduler iniciado — follow-up runner ativo")
    yield
    scheduler.shutdown(wait=False)
    logger.info("APScheduler encerrado")


app = FastAPI(
    title="Agente de Atendimento IA — Produto Real",
    description="Orquestrador multi-tenant com Supabase. Qualifica e agenda via WhatsApp.",
    version="2.0.0",
    lifespan=lifespan,
)

app.include_router(instagram_router)


# ─────────────────────────────────────────────────────────────────────────────
# MODELOS
# ─────────────────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    tenant_name: str   # slug do tenant no Supabase (ex: "lumina")
    phone: str
    message: str

class ChatResponse(BaseModel):
    response: str
    stage: str
    tenant_id: str


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    tenants = mem.get_all_tenants()
    return {
        "status": "online",
        "versao": "2.0.0 — Produto Real",
        "tenants_ativos": len(tenants),
    }


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    Endpoint de teste direto (sem WhatsApp).
    Identifica o tenant pelo slug (tenant_name).

    Exemplo:
        POST /chat
        { "tenant_name": "lumina", "phone": "5585999999999", "message": "oi" }
    """
    tenant = mem.get_tenant_by_name(request.tenant_name)
    if not tenant:
        raise HTTPException(status_code=404, detail=f"Tenant '{request.tenant_name}' não encontrado ou inativo")

    resultado = await processar_mensagem(
        tenant=tenant,
        phone=request.phone.strip(),
        mensagem_usuario=request.message.strip(),
    )
    return ChatResponse(**resultado)


# ─────────────────────────────────────────────────────────────────────────────
# WEBHOOK — WhatsApp Cloud API
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/webhook/whatsapp")
async def webhook_verify(request: Request):
    """Verificação do webhook pela Meta (WhatsApp Cloud API)."""
    params = dict(request.query_params)
    if params.get("hub.mode") == "subscribe" and params.get("hub.verify_token") == VERIFY_TOKEN:
        return PlainTextResponse(content=params.get("hub.challenge", ""))
    raise HTTPException(status_code=403, detail="Token de verificação inválido")


def _validar_assinatura_meta(payload: bytes, signature_header: str) -> bool:
    """Valida X-Hub-Signature-256 enviada pela Meta."""
    if not WHATSAPP_APP_SECRET:
        return True  # Sem secret configurado, skip (não recomendado em prod)
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(
        WHATSAPP_APP_SECRET.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header[7:])


@app.post("/webhook/whatsapp")
async def webhook_whatsapp(request: Request):
    """
    Recebe mensagens do WhatsApp Cloud API.
    Identifica o tenant pelo phone_number_id.

    Payload esperado:
    { "entry": [{ "changes": [{ "value": {
        "metadata": { "phone_number_id": "..." },
        "messages": [{ "from": "...", "text": { "body": "..." } }]
    }}]}]}
    """
    raw_body = await request.body()
    sig = request.headers.get("X-Hub-Signature-256", "")
    if not _validar_assinatura_meta(raw_body, sig):
        raise HTTPException(status_code=403, detail="Assinatura inválida")

    try:
        import json
        body = json.loads(raw_body)
    except Exception:
        raise HTTPException(status_code=400, detail="Payload JSON inválido")

    phone, mensagem, phone_number_id = _extrair_mensagem_whatsapp(body)

    if not phone or not mensagem:
        return JSONResponse(content={"status": "ignorado", "motivo": "mensagem sem texto"})

    tenant = mem.get_tenant_by_phone_number_id(phone_number_id) if phone_number_id else None
    if not tenant:
        return JSONResponse(
            status_code=404,
            content={"status": "erro", "motivo": f"Tenant não encontrado para phone_number_id={phone_number_id}"},
        )

    resultado = await processar_mensagem(
        tenant=tenant,
        phone=phone,
        mensagem_usuario=mensagem,
    )

    try:
        await send_message(
            channel="whatsapp",
            phone=phone,
            ig_user_id="",
            text=resultado["response"],
            tenant=tenant,
        )
    except Exception as e:
        logger.error("Falha ao enviar resposta WA para %s: %s", phone, e)

    return JSONResponse(content={"status": "ok", "phone": phone, **resultado})


def _extrair_mensagem_whatsapp(body: dict) -> tuple[str, str, str]:
    """Extrai phone, mensagem e phone_number_id do payload WhatsApp Cloud API."""
    try:
        value = body["entry"][0]["changes"][0]["value"]
        phone_number_id = value.get("metadata", {}).get("phone_number_id", "")
        messages = value.get("messages", [])
        if not messages:
            return "", "", phone_number_id
        msg = messages[0]
        phone = msg["from"]
        mensagem = msg.get("text", {}).get("body", "").strip()
        return phone, mensagem, phone_number_id
    except (KeyError, IndexError, TypeError):
        return "", "", ""


# ─────────────────────────────────────────────────────────────────────────────
# UTILITÁRIOS
# ─────────────────────────────────────────────────────────────────────────────

def _verificar_admin(x_admin_key: str | None) -> None:
    """Rejeita requisições sem a chave de admin correta."""
    if ADMIN_API_KEY and x_admin_key != ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="Chave de admin inválida ou ausente")


@app.get("/tenants")
async def list_tenants(x_admin_key: str | None = Header(default=None)):
    """Lista tenants ativos (admin)."""
    _verificar_admin(x_admin_key)
    return mem.get_all_tenants()


@app.get("/leads/{tenant_name}")
async def list_leads(tenant_name: str, x_admin_key: str | None = Header(default=None)):
    """Lista leads de um tenant (admin)."""
    _verificar_admin(x_admin_key)
    tenant = mem.get_tenant_by_name(tenant_name)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant não encontrado")
    sb = mem.get_client()
    result = sb.table("leads").select("*").eq("tenant_id", tenant["id"]).order("created_at", desc=True).execute()
    return result.data or []


@app.delete("/lead/{tenant_name}/{phone}")
async def reset_lead(tenant_name: str, phone: str, x_admin_key: str | None = Header(default=None)):
    """Reseta lead e histórico de um contato (testes)."""
    _verificar_admin(x_admin_key)
    tenant = mem.get_tenant_by_name(tenant_name)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant não encontrado")
    tenant_id = str(tenant["id"])
    sb = mem.get_client()
    sb.table("leads").delete().eq("tenant_id", tenant_id).eq("phone", phone).execute()
    sb.table("sessions").delete().eq("tenant_id", tenant_id).eq("phone", phone).execute()
    sb.table("conversations").delete().eq("tenant_id", tenant_id).eq("phone", phone).execute()
    return {"status": "ok", "mensagem": f"Lead {phone} resetado para tenant {tenant_name}"}


# ─────────────────────────────────────────────────────────────────────────────
# PAYMENT CALLBACK — Asaas
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/payment/confirm")
async def payment_confirm(request: Request):
    """Callback Asaas — disparado quando pagamento é confirmado."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Payload inválido")

    # Verifica token Asaas (configurado no painel Asaas como "Access Token")
    asaas_token = request.headers.get("asaas-access-token", "")
    expected_token = ADMIN_API_KEY  # reutiliza ADMIN_API_KEY como token do webhook Asaas
    if expected_token and asaas_token != expected_token:
        raise HTTPException(status_code=403, detail="Token inválido")

    event = body.get("event", "")
    payment = body.get("payment")

    # Guard: payment deve ser um dict
    if not isinstance(payment, dict):
        return JSONResponse(content={"status": "ignorado", "motivo": "payment ausente ou inválido"})

    # Asaas envia PAYMENT_RECEIVED ou PAYMENT_CONFIRMED para Pix confirmado
    if event not in ("PAYMENT_RECEIVED", "PAYMENT_CONFIRMED"):
        return JSONResponse(content={"status": "ignorado", "event": event})

    payment_id = payment.get("id", "")

    # Guard: payment_id não pode ser vazio
    if not payment_id:
        return JSONResponse(content={"status": "ignorado", "motivo": "payment_id ausente"})

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

    try:
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

        sb.table("leads").update({"status": "fechado"}).eq("id", lead_id).execute()
    except Exception as e:
        logger.error("Erro ao processar payment_confirm para lead %s: %s", lead_id, e)
        return JSONResponse(status_code=500, content={"status": "erro_interno"})

    return JSONResponse(content={"status": "ok", "lead_id": lead_id, "novo_status": "fechado"})


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
