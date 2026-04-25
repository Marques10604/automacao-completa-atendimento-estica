# Pré-requisito: ALTER TABLE tenants ADD COLUMN IF NOT EXISTS ig_page_id TEXT;
# Executar no SQL Editor do Supabase antes de usar este webhook.

# app/webhooks/instagram.py
import json
import logging
from fastapi import APIRouter, HTTPException, Request
from app.limiter import limiter
from fastapi.responses import PlainTextResponse, JSONResponse
import memory as mem
from app.agent.claude_client import processar_mensagem
from app.agent.dispatcher import send_message
from app.config import settings

logger = logging.getLogger(__name__)

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
@limiter.limit("10/minute")
async def instagram_webhook(request: Request):
    """Recebe mensagens do Instagram DM via Meta Graph API."""
    try:
        body = json.loads(await request.body())
    except Exception:
        raise HTTPException(status_code=400, detail="Payload JSON inválido")

    ig_user_id, mensagem, page_id = _extrair_mensagem_instagram(body)

    if not ig_user_id or not mensagem:
        return JSONResponse(content={"status": "ignorado"})

    tenant = _get_tenant_by_page_id(page_id)
    if not tenant:
        return JSONResponse(status_code=404, content={"status": "erro", "motivo": f"Tenant não encontrado para page_id={page_id}"})

    try:
        resultado = await processar_mensagem(
            tenant=tenant,
            phone="",
            mensagem_usuario=mensagem,
            canal="instagram",
            ig_user_id=ig_user_id,
        )
        resposta_texto = resultado.get("response", "")
    except Exception as e:
        logger.error("Erro ao processar mensagem IG para %s: %s", ig_user_id, e)
        return JSONResponse(content={"status": "erro_interno"})

    try:
        await send_message(
            channel="instagram",
            phone="",
            ig_user_id=ig_user_id,
            text=resposta_texto,
            tenant=tenant,
        )
    except Exception as e:
        logger.error("Falha ao enviar resposta IG para %s: %s", ig_user_id, e)

    return JSONResponse(content={"status": "ok"})


def _extrair_mensagem_instagram(body: dict) -> tuple[str, str, str]:
    """Extrai ig_user_id, texto e page_id do payload Instagram."""
    try:
        entry = body["entry"][0]
        page_id = entry.get("id", "")
        messaging = entry.get("messaging", [])
        if not messaging:
            return "", "", ""
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
