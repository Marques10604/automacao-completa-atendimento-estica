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
