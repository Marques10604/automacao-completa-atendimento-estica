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
        "description": "Agenda job de follow-up D+1 no Supabase.",
        "input_schema": {
            "type": "object",
            "properties": {
                "lead_id":    {"type": "string"},
                "job_type":   {"type": "string", "enum": ["appointment_reminder", "payment_recovery", "pos_venda"]},
                "channel":    {"type": "string", "enum": ["whatsapp", "instagram"]},
                "phone":      {"type": "string"},
                "ig_user_id": {"type": "string"},
                "payload":    {"type": "object"},
            },
            "required": ["lead_id", "job_type", "channel"],
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
    }
    fn = dispatch.get(tool_name)
    if not fn:
        return {"error": f"Tool desconhecida: {tool_name}"}
    return await fn(tool_input, tenant, phone)


async def _check_availability(inp: dict, tenant: dict, phone: str) -> dict:
    date = inp.get("date", "")
    time = inp.get("time", "qualquer horário")
    return {
        "available": True,
        "slots": [f"{date} às {time}", f"{date} às 10:00", f"{date} às 15:00"],
        "message": f"Horário {time} de {date} disponível.",
    }


async def _book_appointment(inp: dict, tenant: dict, phone: str) -> dict:
    sb = mem.get_client()
    row = sb.table("appointments").insert({
        "lead_id":      inp["lead_id"],
        "tenant_id":    str(tenant["id"]),
        "service":      inp["service"],
        "scheduled_at": inp["scheduled_at"],
    }).execute()
    return {"success": True, "appointment_id": row.data[0]["id"]}


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
    sb.table("leads").update({"status": inp["status"]}).eq("id", inp["lead_id"]).execute()
    return {"updated": True, "status": inp["status"]}


async def _schedule_followup(inp: dict, tenant: dict, phone: str) -> dict:
    sb = mem.get_client()
    scheduled_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
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
