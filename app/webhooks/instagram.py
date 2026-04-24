# Pré-requisito: ALTER TABLE tenants ADD COLUMN IF NOT EXISTS ig_page_id TEXT;
# Executar no SQL Editor do Supabase antes de usar este webhook.

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

    try:
        await send_message(
            channel="instagram",
            phone="",
            ig_user_id=ig_user_id,
            text=resultado["response"],
            tenant=tenant,
        )
    except Exception as e:
        import logging
        logging.getLogger(__name__).error("Falha ao enviar resposta IG para %s: %s", ig_user_id, e)

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
