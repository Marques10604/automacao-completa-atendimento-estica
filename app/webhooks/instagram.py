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
from app.webhooks.media_fallback import resposta_midia_nao_suportada

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

    ig_user_id, mensagem, page_id, tipo = _extrair_mensagem_instagram(body)

    if not ig_user_id:
        return JSONResponse(content={"status": "ignorado"})

    tenant = _get_tenant_by_page_id(page_id)
    if not tenant:
        return JSONResponse(status_code=404, content={"status": "erro", "motivo": f"Tenant não encontrado para page_id={page_id}"})

    # Mídia (áudio, imagem, vídeo, arquivo, ...) ainda não é processada pela IA —
    # mesma lógica do webhook do WhatsApp (main.py::webhook_whatsapp), espelhada
    # aqui pra já ficar pronta quando/se o canal Instagram for reativado.
    if tipo and tipo != "text":
        rotulo, texto_resposta = resposta_midia_nao_suportada(tipo)
        tenant_id = str(tenant["id"])
        try:
            mem.get_or_create_lead(tenant_id, ig_user_id, "instagram")
            mem.save_message(tenant_id, ig_user_id, "user", f"[lead enviou {rotulo}]")
        except Exception as e:
            logger.error("Falha ao registrar mídia recebida do IG %s: %s", ig_user_id, e)
        try:
            await send_message(channel="instagram", phone="", ig_user_id=ig_user_id, text=texto_resposta, tenant=tenant)
            mem.save_message(tenant_id, ig_user_id, "assistant", texto_resposta)
        except Exception as e:
            logger.error("Falha ao avisar lead IG %s sobre mídia não suportada (%s): %s", ig_user_id, tipo, e)
        return JSONResponse(content={"status": "midia_nao_suportada", "tipo": tipo, "ig_user_id": ig_user_id})

    if not mensagem:
        return JSONResponse(content={"status": "ignorado"})

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


def _extrair_mensagem_instagram(body: dict) -> tuple[str, str, str, str]:
    """Extrai ig_user_id, texto, page_id e tipo (text/image/audio/video/file/...) do
    payload Instagram. Mensagem com anexo (sem texto) vem com texto vazio mas tipo
    preenchido a partir do primeiro attachment — quem chama decide o que fazer."""
    try:
        entry = body["entry"][0]
        page_id = entry.get("id", "")
        messaging = entry.get("messaging", [])
        if not messaging:
            return "", "", "", ""
        msg = messaging[0]
        ig_user_id = msg["sender"]["id"]
        message = msg.get("message", {})
        text = message.get("text", "").strip()
        if text:
            return ig_user_id, text, page_id, "text"
        attachments = message.get("attachments") or []
        if attachments:
            tipo = attachments[0].get("type", "") or "midia"
            return ig_user_id, "", page_id, tipo
        return ig_user_id, "", page_id, ""
    except (KeyError, IndexError, TypeError):
        return "", "", "", ""


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
