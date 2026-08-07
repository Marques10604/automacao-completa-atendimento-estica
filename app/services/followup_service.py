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

TEMPLATES = {
    "appointment_reminder": "Olá! 🗓️ Só passando para confirmar seu agendamento amanhã. Você vem, né? Qualquer dúvida é só falar!",
    "payment_recovery":     "Olá! Vi que você não finalizou o pagamento. O link ainda está válido — posso te ajudar com alguma dúvida? ✨",
    "pos_venda":            "Olá! Esperamos que tenha adorado o resultado! 😊 Tem alguém que você indicaria para conhecer nossos serviços?",
    "recall_procedimento":  "Olá! 💫 Já faz um tempinho desde o seu {procedimento} — geralmente é nessa época que dá aquela renovada pra manter o resultado. Quer que eu já veja um horário pra você?",
    # Cross-sell não cita preço de propósito: a mensagem só desperta interesse, e o
    # valor entra depois na conversa, quando o lead perguntar — mesma regra do prompt
    # ("PREÇO SÓ QUANDO PERGUNTADO"). Com preço, o follow-up viraria anúncio.
    "cross_sell":           "Oi! Como você ficou do {feito}? 💛 Muita gente que faz {feito} acaba curtindo o {oferecer} pra completar o resultado. Quer que eu te conte como funciona?",
}


def _montar_texto(job: dict) -> str:
    job_type = job.get("job_type")
    template = TEMPLATES.get(job_type, "Olá! Tudo bem por aí?")
    payload = job.get("payload") or {}
    try:
        return template.format(**payload)
    except (KeyError, IndexError):
        # Se faltar alguma variável no payload, cai pra uma versão genérica em vez de quebrar o envio
        if job_type == "recall_procedimento":
            return "Olá! 💫 Já faz um tempinho desde seu último procedimento — geralmente é nessa época que dá aquela renovada. Quer que eu já veja um horário pra você?"
        if job_type == "cross_sell":
            return "Oi! Como você ficou do seu último procedimento? 💛 Temos outros que combinam bem com ele — quer que eu te conte?"
        return template


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


async def executar_jobs_pendentes() -> None:
    """Executa todos os followup_jobs com scheduled_at <= agora e status=pending."""
    sb = mem.get_client()
    agora = datetime.now(timezone.utc).isoformat()

    result = await asyncio.to_thread(
        lambda: sb.table("followup_jobs")
            .select("*, tenants(*)")
            .lte("scheduled_at", agora)
            .eq("status", "pending")
            .execute()
    )
    jobs = result.data or []

    for job in jobs:
        try:
            await _executar_job(job, sb)
        except Exception as e:
            logger.error("Falha ao executar job %s: %s", job["id"], e)
            try:
                await asyncio.to_thread(
                    lambda: sb.table("followup_jobs").update({"status": "failed"}).eq("id", job["id"]).execute()
                )
            except Exception as update_err:
                logger.error("Falha ao marcar job %s como failed: %s", job["id"], update_err)


async def _executar_job(job: dict, sb) -> None:
    tenant = job.get("tenants") or {}
    if not tenant:
        logger.warning("Job %s sem tenant associado — usando credenciais globais", job["id"])

    if job.get("job_type") == "resgate_silencio":
        await _executar_resgate_silencio(job, sb, tenant)
        return

    text = _montar_texto(job)

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

    logger.info("Job %s executado: %s → %s", job["id"], job.get("job_type"), job.get("phone") or job.get("ig_user_id"))
