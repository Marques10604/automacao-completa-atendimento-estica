# main.py - FastAPI com webhook WhatsApp Cloud API

from dotenv import load_dotenv

# Precisa rodar antes de qualquer import interno — módulos como app.agent.claude_client
# instanciam clientes (ex: AsyncAnthropic()) na hora do import, lendo os.environ nesse
# momento. Se load_dotenv() rodar depois desses imports, ANTHROPIC_API_KEY ainda não
# existe no processo e a autenticação falha.
load_dotenv()

import asyncio
import hashlib
import hmac
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from app.limiter import limiter

logger = logging.getLogger(__name__)
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel

from app.agent.claude_client import processar_mensagem
import memory as mem
from app.agent.dispatcher import send_message
from app.webhooks.instagram import router as instagram_router
from app.jobs.scheduler import get_scheduler
from app.services.failure_service import registrar_falha, escalar_por_falhas
from app.services.transcription_service import transcrever_audio_whatsapp
from app.services.report_service import detectar_comando_relatorio, montar_relatorio, remetente_e_staff
from app.webhooks.media_fallback import resposta_midia_nao_suportada
from app.config import settings

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

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# CORS liberado só pra permitir o mockup HTML (rodando local, fora do backend)
# conversar com o /chat durante testes e gravação. Sem isso o navegador bloqueia
# a chamada com "Failed to fetch" mesmo com o servidor no ar.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
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


# ─────────────────────────────────────────────────────────────────────────────
# OBSERVABILIDADE DE FALHA — nunca deixa o lead em silêncio, sempre registra
# ─────────────────────────────────────────────────────────────────────────────
FALLBACK_INDISPONIVEL = "Tive um imprevisto aqui, já volto! 💛"
ENVIO_MAX_TENTATIVAS = 3          # 1 tentativa original + 2 retries
ENVIO_BACKOFF_SEGUNDOS = [1, 2]   # pausa curta antes de cada retry
FALHAS_CONSECUTIVAS_PARA_ESCALAR = 3


async def _enviar_com_retry(phone: str, texto: str, tenant: dict) -> bool:
    """Tenta enviar até ENVIO_MAX_TENTATIVAS vezes, com pausa curta entre elas.
    Devolve True se algum envio deu certo, False se todas as tentativas falharam."""
    for tentativa in range(ENVIO_MAX_TENTATIVAS):
        try:
            await send_message(channel="whatsapp", phone=phone, ig_user_id="", text=texto, tenant=tenant)
            return True
        except Exception as e:
            logger.error("Falha ao enviar (tentativa %d/%d) para %s: %s", tentativa + 1, ENVIO_MAX_TENTATIVAS, phone, e)
            if tentativa < len(ENVIO_BACKOFF_SEGUNDOS):
                await asyncio.sleep(ENVIO_BACKOFF_SEGUNDOS[tentativa])
    return False


async def _tentar_enviar_fallback(phone: str, tenant: dict) -> bool:
    """Uma única tentativa (não usa o retry de _enviar_com_retry — se o envio já
    está com problema, insistir 3x no fallback só adiciona latência sem ganho)."""
    try:
        await send_message(channel="whatsapp", phone=phone, ig_user_id="", text=FALLBACK_INDISPONIVEL, tenant=tenant)
        return True
    except Exception as e:
        logger.error("Falha ao enviar fallback de indisponibilidade para %s: %s", phone, e)
        return False


async def _registrar_falha_e_escalar(tenant: dict, phone: str, tipo_falha: str, detalhe: str, lead_id: str | None) -> None:
    """Grava a falha em agent_failures; se o lead acumulou falhas demais numa
    janela curta, escala pra humano e notifica staff_phone."""
    if lead_id is None:
        try:
            lead = await asyncio.to_thread(mem.get_or_create_lead, str(tenant["id"]), phone, "whatsapp")
            lead_id = str(lead["id"])
        except Exception as e:
            logger.error("Não consegui recuperar lead_id pra registrar falha de %s: %s", phone, e)

    qtd = await asyncio.to_thread(
        registrar_falha, str(tenant["id"]), lead_id, phone, "whatsapp", tipo_falha, detalhe,
    )
    if lead_id and qtd >= FALHAS_CONSECUTIVAS_PARA_ESCALAR:
        await escalar_por_falhas(tenant, lead_id, phone, motivo=f"{qtd} falhas consecutivas ({tipo_falha})")


async def _processar_e_responder_whatsapp(tenant: dict, phone: str, mensagem: str, ja_salvo: bool = False) -> None:
    """Roda em background (após o 200 já ter sido devolvido pra Meta): processa a
    mensagem com a Claude e envia a resposta pelo WhatsApp. A resposta só é salva
    no histórico depois que o envio é confirmado — nunca antes, pra não registrar
    que o lead recebeu algo que na verdade falhou."""
    try:
        resultado = await processar_mensagem(
            tenant=tenant,
            phone=phone,
            mensagem_usuario=mensagem,
            ja_salvo=ja_salvo,
            salvar_resposta=False,
        )
    except Exception as e:
        logger.error("Falha ao processar mensagem WA de %s: %s", phone, e)
        await _registrar_falha_e_escalar(tenant, phone, "processamento", str(e), lead_id=None)
        if await _tentar_enviar_fallback(phone, tenant):
            await asyncio.to_thread(mem.save_message, str(tenant["id"]), phone, "assistant", FALLBACK_INDISPONIVEL)
        return

    if resultado.get("escalado"):
        # Atendimento assumido por humano — a IA fica muda, não envia nada.
        return

    texto = resultado.get("response", "")
    if not texto:
        return

    if await _enviar_com_retry(phone, texto, tenant):
        await asyncio.to_thread(mem.save_message, resultado["tenant_id"], phone, "assistant", texto)
        return

    await _registrar_falha_e_escalar(
        tenant, phone, "envio", "Falha ao enviar resposta via WhatsApp após retries", lead_id=resultado.get("lead_id"),
    )
    if await _tentar_enviar_fallback(phone, tenant):
        await asyncio.to_thread(mem.save_message, resultado["tenant_id"], phone, "assistant", FALLBACK_INDISPONIVEL)


# ─────────────────────────────────────────────────────────────────────────────
# BUFFER DE RAJADA — agrupa mensagens rápidas em sequência antes de chamar a IA
# ─────────────────────────────────────────────────────────────────────────────
# Se o lead manda "oi" e "quero botox" em duas mensagens seguidas rapidinho, sem
# isso o webhook dispara duas chamadas concorrentes pra Claude sobre o mesmo lead
# (cada uma sem saber da outra) — respostas duplicadas/contraditórias. Aqui, cada
# mensagem nova cancela o processamento pendente anterior do mesmo número e
# reagenda; só a última mensagem da rajada efetivamente aciona a IA, mas todas já
# foram salvas no histórico antes disso, então nada se perde.
DEBOUNCE_SECONDS = 2.5
_debounce_tasks: dict[str, asyncio.Task] = {}


async def _aguardar_e_processar(tenant: dict, phone: str, mensagem: str, key: str) -> None:
    try:
        await asyncio.sleep(DEBOUNCE_SECONDS)
    except asyncio.CancelledError:
        return  # chegou mensagem mais nova do mesmo número — quem responde é o próximo agendamento
    _debounce_tasks.pop(key, None)
    await _processar_e_responder_whatsapp(tenant, phone, mensagem, ja_salvo=True)


async def _transcrever_e_processar(tenant: dict, phone: str, media_id: str, key: str) -> None:
    """Transcreve a nota de voz e injeta o texto no mesmo pipeline das mensagens
    digitadas. Se a transcrição falhar (Groq fora do ar, chave ausente, áudio grande
    demais), cai no aviso educado de mídia não suportada — o lead nunca fica mudo."""
    tenant_id = str(tenant["id"])
    wa_token = tenant.get("whatsapp_token") or settings.meta_wa_token

    texto = await transcrever_audio_whatsapp(media_id, wa_token)

    if not texto:
        rotulo, texto_resposta = resposta_midia_nao_suportada("audio")
        await asyncio.to_thread(mem.save_message, tenant_id, phone, "user", f"[lead enviou {rotulo}]")
        if await _enviar_com_retry(phone, texto_resposta, tenant):
            await asyncio.to_thread(mem.save_message, tenant_id, phone, "assistant", texto_resposta)
        return

    # Daqui pra frente é idêntico ao caminho de texto: salva no histórico e entra no
    # debounce, pra áudio seguido de texto na mesma rajada virar uma resposta só.
    await asyncio.to_thread(mem.save_message, tenant_id, phone, "user", texto)

    anterior = _debounce_tasks.get(key)
    if anterior and not anterior.done():
        anterior.cancel()
    _debounce_tasks[key] = asyncio.create_task(_aguardar_e_processar(tenant, phone, texto, key))


@app.post("/webhook/whatsapp")
@limiter.limit("10/minute")
async def webhook_whatsapp(request: Request):
    """
    Recebe mensagens do WhatsApp Cloud API.
    Identifica o tenant pelo phone_number_id.

    Responde 200 imediatamente. A mensagem é salva na hora (garante histórico
    completo mesmo em rajada), e o processamento pela IA é debounced — se chegar
    outra mensagem do mesmo número em menos de DEBOUNCE_SECONDS, o processamento
    anterior é cancelado e substituído, evitando respostas concorrentes/duplicadas.

    Mensagens não-texto (áudio, foto, documento, ...) ainda não são processadas
    pela IA — o lead recebe um aviso educado e a tentativa fica marcada no
    histórico, em vez de cair em silêncio total.

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

    phone, mensagem, phone_number_id, wamid, tipo, media_id = _extrair_mensagem_whatsapp(body)

    if not phone:
        return JSONResponse(content={"status": "ignorado", "motivo": "sem remetente"})

    if mem.is_duplicate_message(wamid):
        logger.info("Mensagem duplicada ignorada: wamid=%s phone=%s", wamid, phone)
        return JSONResponse(content={"status": "duplicado", "phone": phone})

    tenant = mem.get_tenant_by_phone_number_id(phone_number_id) if phone_number_id else None
    if not tenant:
        return JSONResponse(
            status_code=404,
            content={"status": "erro", "motivo": f"Tenant não encontrado para phone_number_id={phone_number_id}"},
        )

    tenant_id = str(tenant["id"])

    # Áudio vira texto e segue como mensagem normal (#B1 fase 2). A transcrição roda em
    # background porque baixar da Graph API + chamar a Groq leva alguns segundos: se o
    # 200 demorasse, a Meta reenviaria o webhook, e o reenvio seria descartado pelo
    # dedup de wamid logo acima — o áudio se perderia justamente por demorar.
    if tipo in ("audio", "voice") and media_id:
        await asyncio.to_thread(mem.get_or_create_lead, tenant_id, phone, "whatsapp")
        asyncio.create_task(
            _transcrever_e_processar(tenant, phone, media_id, f"{tenant_id}:{phone}")
        )
        return JSONResponse(content={"status": "audio_recebido", "phone": phone})

    # Demais mídias (foto, documento, vídeo) ainda não são processadas pela IA — sem
    # isso o lead ficava em silêncio total (#B1 fase 1). Avisa, marca no histórico o
    # que chegou (pra IA ter contexto se o lead explicar por texto depois) e não entra
    # no pipeline normal (não tem texto pra debounce/Claude processarem).
    if tipo and tipo != "text":
        rotulo, texto_resposta = resposta_midia_nao_suportada(tipo)
        await asyncio.to_thread(mem.get_or_create_lead, tenant_id, phone, "whatsapp")
        await asyncio.to_thread(mem.save_message, tenant_id, phone, "user", f"[lead enviou {rotulo}]")
        if await _enviar_com_retry(phone, texto_resposta, tenant):
            await asyncio.to_thread(mem.save_message, tenant_id, phone, "assistant", texto_resposta)
        return JSONResponse(content={"status": "midia_nao_suportada", "tipo": tipo, "phone": phone})

    if not mensagem:
        return JSONResponse(content={"status": "ignorado", "motivo": "mensagem sem texto"})

    # Dono/equipe pedindo relatório. Fica ANTES de get_or_create_lead de propósito: o
    # dono não pode virar lead no funil nem sujar o histórico de conversa com mensagem
    # administrativa. Os números saem direto do Supabase, sem passar pelo modelo —
    # relatório com número inventado destruiria a confiança no produto.
    if remetente_e_staff(phone, tenant):
        periodo = detectar_comando_relatorio(mensagem)
        if periodo:
            texto_relatorio = await asyncio.to_thread(montar_relatorio, tenant, periodo)
            enviado = await _enviar_com_retry(phone, texto_relatorio, tenant)
            return JSONResponse(content={
                "status": "relatorio_enviado" if enviado else "relatorio_falhou",
                "periodo": periodo,
            })

    key = f"{tenant_id}:{phone}"

    # Cancela o debounce da mensagem anterior da mesma rajada JÁ, antes de qualquer
    # trabalho lento — get_or_create_lead + save_message no Supabase levam ~3s no total
    # (mais que os 2.5s do DEBOUNCE_SECONDS). Se o cancelamento só acontecesse depois
    # dessas chamadas, o timer anterior tinha tempo de sobra pra disparar sozinho
    # enquanto essa mensagem ainda estava sendo salva — gerando duas respostas pra uma
    # rajada só. Cancelando primeiro, a corrida deixa de depender da latência do banco.
    anterior = _debounce_tasks.get(key)
    if anterior and not anterior.done():
        anterior.cancel()

    # Salva a mensagem imediatamente — independe do debounce, garante que nada se perde.
    # asyncio.to_thread pra não bloquear o event loop enquanto isso acontece.
    await asyncio.to_thread(mem.get_or_create_lead, tenant_id, phone, "whatsapp")
    await asyncio.to_thread(mem.save_message, tenant_id, phone, "user", mensagem)

    _debounce_tasks[key] = asyncio.create_task(_aguardar_e_processar(tenant, phone, mensagem, key))

    return JSONResponse(content={"status": "recebido", "phone": phone})


def _extrair_mensagem_whatsapp(body: dict) -> tuple[str, str, str, str, str, str]:
    """Extrai phone, mensagem, phone_number_id, wamid, tipo (text/audio/image/...) e
    media_id do payload WhatsApp Cloud API. Mensagens não-texto vêm com mensagem vazia
    mas tipo preenchido — quem chama decide o que fazer. Áudio ainda traz o media_id,
    que é o que permite baixar a nota de voz da Graph API pra transcrever."""
    try:
        value = body["entry"][0]["changes"][0]["value"]
        phone_number_id = value.get("metadata", {}).get("phone_number_id", "")
        messages = value.get("messages", [])
        if not messages:
            return "", "", phone_number_id, "", "", ""
        msg = messages[0]
        phone = msg["from"]
        tipo = msg.get("type", "")
        mensagem = msg.get("text", {}).get("body", "").strip() if tipo == "text" else ""
        wamid = msg.get("id", "")
        media_id = msg.get(tipo, {}).get("id", "") if tipo in ("audio", "voice") else ""
        return phone, mensagem, phone_number_id, wamid, tipo, media_id
    except (KeyError, IndexError, TypeError):
        return "", "", "", "", "", ""


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
async def list_leads(
    tenant_name: str,
    status: Optional[str] = None,
    canal: Optional[str] = None,
    desde: Optional[str] = None,
    x_admin_key: str | None = Header(default=None),
):
    """Lista leads de um tenant (admin) com filtros opcionais: status, canal, desde (ISO date)."""
    _verificar_admin(x_admin_key)
    tenant = mem.get_tenant_by_name(tenant_name)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant não encontrado")

    # Correção 2: Validar valores de status e canal
    STATUS_PERMITIDOS = {"novo", "qualificado", "agendado", "fechado", "frio"}
    CANAIS_PERMITIDOS = {"whatsapp", "instagram"}

    if status and status not in STATUS_PERMITIDOS:
        raise HTTPException(status_code=400, detail=f"status inválido. Permitidos: {sorted(STATUS_PERMITIDOS)}")
    if canal and canal not in CANAIS_PERMITIDOS:
        raise HTTPException(status_code=400, detail=f"canal inválido. Permitidos: {sorted(CANAIS_PERMITIDOS)}")

    # Correção 3: Logging básico de acesso
    logger.info("GET /leads tenant=%s filtros: status=%s canal=%s desde=%s", tenant_name, status, canal, desde)

    sb = mem.get_client()
    query = sb.table("leads").select("*").eq("tenant_id", tenant["id"])
    if status:
        query = query.eq("status", status)
    if canal:
        query = query.eq("canal", canal)
    # Correção 1: Validar formato de desde
    if desde:
        try:
            datetime.fromisoformat(desde)
        except ValueError:
            raise HTTPException(status_code=400, detail="Parâmetro 'desde' deve ser ISO 8601 (ex: 2026-01-01 ou 2026-01-01T00:00:00)")
        query = query.gte("created_at", desde)
    result = query.order("created_at", desc=True).execute()
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


def _set_escalado(tenant_name: str, phone: str, valor: bool) -> dict:
    tenant = mem.get_tenant_by_name(tenant_name)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant não encontrado")
    sb = mem.get_client()
    sb.table("leads").update({"escalado": valor}).eq("tenant_id", tenant["id"]).eq("phone", phone).execute()
    return {"status": "ok", "phone": phone, "escalado": valor}


@app.patch("/lead/{tenant_name}/{phone}/escalar")
async def escalar_lead(tenant_name: str, phone: str, x_admin_key: str | None = Header(default=None)):
    """Marca o lead como escalado pra humano — a IA para de responder até ser reativada."""
    _verificar_admin(x_admin_key)
    return _set_escalado(tenant_name, phone, True)


@app.patch("/lead/{tenant_name}/{phone}/desescalar")
async def desescalar_lead(tenant_name: str, phone: str, x_admin_key: str | None = Header(default=None)):
    """Devolve o lead pra IA responder normalmente de novo."""
    _verificar_admin(x_admin_key)
    return _set_escalado(tenant_name, phone, False)


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

        sb.table("leads").update({"stage": "fechado"}).eq("id", lead_id).execute()
    except Exception as e:
        logger.error("Erro ao processar payment_confirm para lead %s: %s", lead_id, e)
        return JSONResponse(status_code=500, content={"status": "erro_interno"})

    return JSONResponse(content={"status": "ok", "lead_id": lead_id, "novo_status": "fechado"})


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
