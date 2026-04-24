# main.py - FastAPI com webhook WhatsApp Cloud API

import hashlib
import hmac
import logging
import os
from fastapi import FastAPI, Header, HTTPException, Request

logger = logging.getLogger(__name__)
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel
from dotenv import load_dotenv

from app.agent.claude_client import processar_mensagem
import memory as mem
from app.agent.dispatcher import send_message
from app.webhooks.instagram import router as instagram_router

load_dotenv()

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "")
WHATSAPP_APP_SECRET = os.getenv("WHATSAPP_APP_SECRET", "")
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "")

app = FastAPI(
    title="Agente de Atendimento IA — Produto Real",
    description="Orquestrador multi-tenant com Supabase. Qualifica e agenda via WhatsApp.",
    version="2.0.0",
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


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
