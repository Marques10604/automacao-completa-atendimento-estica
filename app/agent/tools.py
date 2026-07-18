# app/agent/tools.py

TOOL_DEFINITIONS = [
    {
        "name": "check_availability",
        "description": "Consulta slots livres na agenda. Use antes de book_appointment.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Data no formato DD/MM/YYYY"},
                "time": {"type": "string", "description": "Horário no formato HH:MM ou HHh"},
            },
            "required": ["date"],
        },
    },
    {
        "name": "book_appointment",
        "description": "Cria o agendamento confirmado no Supabase.",
        "input_schema": {
            "type": "object",
            "properties": {
                "lead_id":      {"type": "string"},
                "service":      {"type": "string", "description": "Nome do procedimento"},
                "scheduled_at": {"type": "string", "description": "ISO 8601: 2026-04-20T14:00:00"},
            },
            "required": ["lead_id", "service", "scheduled_at"],
        },
    },
    {
        "name": "generate_payment_link",
        "description": "Gera link de pagamento Pix ou cartão via Asaas. Só use após qualificação confirmada.",
        "input_schema": {
            "type": "object",
            "properties": {
                "lead_id":      {"type": "string"},
                "value":        {"type": "number", "description": "Valor em reais"},
                "description":  {"type": "string"},
                "billing_type": {"type": "string", "enum": ["PIX", "CREDIT_CARD", "BOLETO"]},
            },
            "required": ["lead_id", "value", "description", "billing_type"],
        },
    },
    {
        "name": "migrate_to_whatsapp",
        "description": "Usado no Instagram: envia mensagem WA para migrar o lead do IG para WhatsApp.",
        "input_schema": {
            "type": "object",
            "properties": {
                "phone":   {"type": "string", "description": "Número com DDI, ex: 5585999999999"},
                "message": {"type": "string", "description": "Texto da mensagem de boas-vindas no WA"},
            },
            "required": ["phone", "message"],
        },
    },
    {
        "name": "update_lead_status",
        "description": "Atualiza o status/estágio do lead: novo → qualificado → agendado → fechado → frio",
        "input_schema": {
            "type": "object",
            "properties": {
                "lead_id": {"type": "string"},
                "status":  {"type": "string", "enum": ["novo", "qualificado", "agendado", "fechado", "frio"]},
            },
            "required": ["lead_id", "status"],
        },
    },
    {
        "name": "schedule_followup",
        "description": "Agenda job de follow-up no Supabase. Por padrão dispara em D+1, mas aceita 'days' pra qualquer intervalo (ex: recall de procedimento daqui a 180 dias).",
        "input_schema": {
            "type": "object",
            "properties": {
                "lead_id":    {"type": "string"},
                "job_type":   {"type": "string", "enum": ["appointment_reminder", "payment_recovery", "pos_venda", "recall_procedimento"]},
                "channel":    {"type": "string", "enum": ["whatsapp", "instagram"]},
                "phone":      {"type": "string"},
                "ig_user_id": {"type": "string"},
                "days":       {"type": "integer", "description": "Dias a partir de agora até disparar. Default: 1."},
                "payload":    {"type": "object", "description": "Dados extras pra personalizar a mensagem, ex: {\"procedimento\": \"Botox\", \"nome\": \"Maria\"}"},
            },
            "required": ["lead_id", "job_type", "channel"],
        },
    },
    {
        "name": "escalate_to_human",
        "description": "Transfere o atendimento pra um humano da equipe — a IA para de responder esse lead até alguém reativar manualmente. Use quando o lead pedir explicitamente pra falar com uma pessoa, relatar uma reação pós-procedimento grave, ou qualquer situação que a IA não deva resolver sozinha.",
        "input_schema": {
            "type": "object",
            "properties": {
                "lead_id": {"type": "string"},
                "motivo":  {"type": "string", "description": "Por que está escalando, ex: 'pediu para falar com humano' ou 'relatou reação alérgica'"},
            },
            "required": ["lead_id", "motivo"],
        },
    },
]

import httpx
from datetime import datetime, timedelta, timezone
import memory as mem


async def execute_tool(tool_name: str, tool_input: dict, tenant: dict, phone: str) -> dict:
    dispatch = {
        "check_availability":    _check_availability,
        "book_appointment":      _book_appointment,
        "generate_payment_link": _generate_payment_link,
        "migrate_to_whatsapp":   _migrate_to_whatsapp,
        "update_lead_status":    _update_lead_status,
        "schedule_followup":     _schedule_followup,
        "escalate_to_human":     _escalate_to_human,
    }
    fn = dispatch.get(tool_name)
    if not fn:
        return {"error": f"Tool desconhecida: {tool_name}"}
    try:
        return await fn(tool_input, tenant, phone)
    except Exception as e:
        import logging
        logging.getLogger(__name__).error("Erro ao executar tool %s: %s", tool_name, e)
        return {"error": str(e)}


async def _check_availability(inp: dict, tenant: dict, phone: str) -> dict:
    # TODO(prod): integrar com Google Calendar ou tabela availability no Supabase
    # Ainda é mock — mas gera horários plausíveis (não repete o input, respeita
    # horário comercial e não sugere fim de semana) pra servir de demo decente
    # até existir o primeiro cliente real. NÃO usar com cliente pagante.
    import logging
    import random
    logging.getLogger(__name__).warning("_check_availability: retornando dados mock — não usar em produção com cliente real")

    date_str = inp.get("date", "")
    parsed_date = None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            parsed_date = datetime.strptime(date_str, fmt)
            break
        except (ValueError, TypeError):
            continue

    # Se caiu num domingo, empurra pra segunda — clínica não abre domingo
    if parsed_date and parsed_date.weekday() == 6:
        parsed_date = parsed_date + timedelta(days=1)

    display_date = parsed_date.strftime("%d/%m/%Y") if parsed_date else date_str

    business_hours = ["09:00", "10:30", "11:00", "14:00", "15:30", "16:00", "17:00"]
    # Sábado só até meio-dia
    if parsed_date and parsed_date.weekday() == 5:
        business_hours = ["09:00", "10:00", "11:00"]

    requested_time = inp.get("time", "").strip()
    slots = random.sample(business_hours, k=min(3, len(business_hours)))
    slots.sort()

    # Se o horário pedido bate com algo plausível, prioriza ele como primeira opção
    if requested_time and requested_time not in slots:
        slots = [requested_time] + slots[:2]

    return {
        "available": True,
        "slots": [f"{display_date} às {s}" for s in slots],
        "message": f"Temos esses horários livres em {display_date}: {', '.join(slots)}.",
    }


async def _book_appointment(inp: dict, tenant: dict, phone: str) -> dict:
    sb = mem.get_client()

    # Evita duplicar: se o lead já tem um agendamento futuro em aberto, atualiza em vez de
    # inserir outro (acontece quando o lead confirma de novo depois de já confirmado).
    agora = datetime.now(timezone.utc).isoformat()
    existentes = (
        sb.table("appointments")
        .select("id")
        .eq("lead_id", inp["lead_id"])
        .gte("scheduled_at", agora)
        .order("scheduled_at", desc=False)
        .limit(1)
        .execute()
    ).data

    if existentes:
        appointment_id = existentes[0]["id"]
        sb.table("appointments").update({
            "service":      inp["service"],
            "scheduled_at": inp["scheduled_at"],
        }).eq("id", appointment_id).execute()
    else:
        row = sb.table("appointments").insert({
            "lead_id":      inp["lead_id"],
            "tenant_id":    str(tenant["id"]),
            "service":      inp["service"],
            "scheduled_at": inp["scheduled_at"],
        }).execute()
        appointment_id = row.data[0]["id"]

    resultado = {"success": True, "appointment_id": appointment_id}

    recall_info = _agendar_recall_se_configurado(sb, inp, tenant, phone)
    if recall_info:
        resultado["recall_agendado"] = recall_info

    return resultado


def _agendar_recall_se_configurado(sb, inp: dict, tenant: dict, phone: str) -> dict | None:
    """
    Se o tenant tiver configurado procedimentos_recall (JSONB: {"nome do procedimento": dias}),
    procura o serviço agendado nesse mapa (case-insensitive, por substring) e já cria
    automaticamente o followup_job de recall — sem depender do modelo lembrar de chamar isso.
    """
    import logging
    logger = logging.getLogger(__name__)

    regras = tenant.get("procedimentos_recall") or {}
    if not regras:
        return None

    servico = (inp.get("service") or "").strip().lower()
    if not servico:
        return None

    dias_recall = None
    procedimento_encontrado = None
    for nome_regra, dias in regras.items():
        nome_regra_lower = str(nome_regra).strip().lower()
        if nome_regra_lower in servico or servico in nome_regra_lower:
            dias_recall = dias
            procedimento_encontrado = nome_regra
            break

    if dias_recall is None:
        logger.info("Nenhuma regra de recall bate com o serviço '%s' — recall não agendado", servico)
        return None

    try:
        agendamento_em = datetime.fromisoformat(inp["scheduled_at"])
    except (ValueError, TypeError):
        agendamento_em = datetime.now(timezone.utc)

    scheduled_at = (agendamento_em + timedelta(days=int(dias_recall))).isoformat()

    row = sb.table("followup_jobs").insert({
        "lead_id":      inp["lead_id"],
        "tenant_id":    str(tenant["id"]),
        "channel":      "whatsapp",
        "phone":        phone,
        "job_type":     "recall_procedimento",
        "scheduled_at": scheduled_at,
        "payload":      {"procedimento": procedimento_encontrado, "dias": dias_recall},
    }).execute()

    logger.info(
        "Recall agendado: procedimento='%s' dias=%s scheduled_at=%s job_id=%s",
        procedimento_encontrado, dias_recall, scheduled_at, row.data[0]["id"],
    )
    return {"job_id": row.data[0]["id"], "procedimento": procedimento_encontrado, "scheduled_at": scheduled_at}


async def _generate_payment_link(inp: dict, tenant: dict, phone: str) -> dict:
    from app.config import settings
    asaas_key = tenant.get("asaas_api_key") or settings.asaas_api_key
    base_url   = settings.asaas_base_url
    if not asaas_key:
        return {"error": "ASAAS_API_KEY não configurada para este tenant"}

    headers = {"access_token": asaas_key, "Content-Type": "application/json"}
    payload = {
        "billingType": inp["billing_type"],
        "value":       inp["value"],
        "description": inp["description"],
        "dueDate":     (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d"),
    }
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(f"{base_url}/paymentLinks", json=payload, headers=headers)
        r.raise_for_status()
        data = r.json()
    return {"payment_url": data.get("url", ""), "payment_id": data.get("id", "")}


async def _migrate_to_whatsapp(inp: dict, tenant: dict, phone: str) -> dict:
    from app.agent.dispatcher import send_whatsapp
    from app.config import settings
    wa_token = tenant.get("whatsapp_token") or settings.meta_wa_token
    phone_number_id = tenant.get("phone_number_id") or settings.meta_wa_phone_number_id
    await send_whatsapp(inp["phone"], inp["message"], wa_token, phone_number_id)
    return {"sent": True, "phone": inp["phone"]}


async def _update_lead_status(inp: dict, tenant: dict, phone: str) -> dict:
    sb = mem.get_client()
    sb.table("leads").update({"stage": inp["status"]}).eq("id", inp["lead_id"]).execute()
    return {"updated": True, "status": inp["status"]}


async def _escalate_to_human(inp: dict, tenant: dict, phone: str) -> dict:
    import logging
    logger = logging.getLogger(__name__)

    sb = mem.get_client()
    sb.table("leads").update({"escalado": True}).eq("id", inp["lead_id"]).execute()

    motivo = inp.get("motivo", "sem motivo informado")
    staff_phone = tenant.get("staff_phone")
    notificado = False
    if staff_phone:
        from app.agent.dispatcher import send_whatsapp
        from app.config import settings
        wa_token = tenant.get("whatsapp_token") or settings.meta_wa_token
        phone_number_id = tenant.get("phone_number_id") or settings.meta_wa_phone_number_id
        texto = f"⚠️ Atendimento escalado para humano.\nCliente: {phone}\nMotivo: {motivo}"
        try:
            await send_whatsapp(staff_phone, texto, wa_token, phone_number_id)
            notificado = True
        except Exception as e:
            logger.error("Falha ao notificar staff_phone %s do tenant %s: %s", staff_phone, tenant.get("name"), e)
    else:
        logger.warning("Tenant %s escalou lead %s sem staff_phone configurado — ninguém foi notificado", tenant.get("name"), inp["lead_id"])

    return {"escalado": True, "notificado": notificado}


async def _schedule_followup(inp: dict, tenant: dict, phone: str) -> dict:
    sb = mem.get_client()
    dias = inp.get("days", 1)
    scheduled_at = (datetime.now(timezone.utc) + timedelta(days=int(dias))).isoformat()
    row = sb.table("followup_jobs").insert({
        "lead_id":      inp["lead_id"],
        "tenant_id":    str(tenant["id"]),
        "channel":      inp["channel"],
        "phone":        inp.get("phone", ""),
        "ig_user_id":   inp.get("ig_user_id", ""),
        "job_type":     inp["job_type"],
        "scheduled_at": scheduled_at,
        "payload":      inp.get("payload", {}),
    }).execute()
    return {"scheduled": True, "job_id": row.data[0]["id"], "scheduled_at": scheduled_at}
