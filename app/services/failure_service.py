# app/services/failure_service.py
import asyncio
import logging
from datetime import datetime, timedelta, timezone
import memory as mem

logger = logging.getLogger(__name__)

JANELA_FALHAS_CONSECUTIVAS_MINUTOS = 30  # falhas fora dessa janela não contam como
# "consecutivas" — evita reescalar um lead com base em falhas antigas já resolvidas
# por um humano (agent_failures só registra falha, nunca sucesso, então não tem
# como saber "quando a série resetou" sem esse corte de tempo).


def registrar_falha(
    tenant_id: str,
    lead_id: str | None,
    phone: str,
    canal: str,
    tipo_falha: str,
    detalhe: str,
    payload: dict | None = None,
) -> int:
    """Grava uma falha em agent_failures e devolve quantas falhas esse lead
    acumulou nos últimos JANELA_FALHAS_CONSECUTIVAS_MINUTOS minutos (incluindo
    essa). Sem lead_id, grava a falha mas devolve 0 (não dá pra contar/escalar
    sem saber de quem é)."""
    sb = mem.get_client()
    try:
        sb.table("agent_failures").insert({
            "tenant_id":  tenant_id,
            "lead_id":    lead_id,
            "phone":      phone,
            "canal":      canal,
            "tipo_falha": tipo_falha,
            "detalhe":    (detalhe or "")[:2000],
            "payload":    payload or {},
        }).execute()
    except Exception as e:
        logger.error("Falha ao gravar agent_failures (tenant=%s lead=%s tipo=%s): %s", tenant_id, lead_id, tipo_falha, e)
        return 0

    if not lead_id:
        return 0

    try:
        desde = (datetime.now(timezone.utc) - timedelta(minutes=JANELA_FALHAS_CONSECUTIVAS_MINUTOS)).isoformat()
        recentes = (
            sb.table("agent_failures")
            .select("id")
            .eq("lead_id", lead_id)
            .gte("created_at", desde)
            .execute()
        )
        return len(recentes.data or [])
    except Exception as e:
        logger.error("Falha ao contar agent_failures recentes do lead %s: %s", lead_id, e)
        return 0


async def escalar_por_falhas(tenant: dict, lead_id: str, phone: str, motivo: str) -> None:
    """Marca o lead como escalado e notifica staff_phone — mesmo padrão de
    app/agent/tools.py::_escalate_to_human, só que disparado por falhas técnicas
    em vez de decisão do modelo."""
    sb = mem.get_client()
    try:
        await asyncio.to_thread(
            lambda: sb.table("leads").update({"escalado": True}).eq("id", lead_id).execute()
        )
    except Exception as e:
        logger.error("Falha ao marcar lead %s como escalado por falhas técnicas: %s", lead_id, e)
        return

    staff_phone = tenant.get("staff_phone")
    if not staff_phone:
        logger.warning(
            "Tenant %s escalou lead %s por falhas técnicas sem staff_phone configurado — ninguém foi notificado",
            tenant.get("name"), lead_id,
        )
        return

    from app.agent.dispatcher import send_whatsapp
    from app.config import settings
    wa_token = tenant.get("whatsapp_token") or settings.meta_wa_token
    phone_number_id = tenant.get("phone_number_id") or settings.meta_wa_phone_number_id
    texto = f"⚠️ Atendimento escalado automaticamente por falha técnica.\nCliente: {phone}\nMotivo: {motivo}"
    try:
        await send_whatsapp(staff_phone, texto, wa_token, phone_number_id)
    except Exception as e:
        logger.error("Falha ao notificar staff_phone %s do tenant %s sobre escalação técnica: %s", staff_phone, tenant.get("name"), e)
