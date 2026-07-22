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
                "scheduled_at": {"type": "string", "description": "Horário LOCAL de Fortaleza (UTC-3), ISO 8601 sem offset: 2026-04-20T14:00:00. Não inclua timezone — o sistema já assume America/Fortaleza."},
            },
            "required": ["lead_id", "service", "scheduled_at"],
        },
    },
    {
        "name": "cancel_appointment",
        "description": (
            "Cancela o próximo agendamento do lead. Use quando ele disser claramente que "
            "não vai mais (cancelar, desmarcar, desistir). Para TROCAR de data/horário não "
            "use isso — chame book_appointment com o horário novo, que remarca sozinho."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "lead_id": {"type": "string"},
                "motivo":  {"type": "string", "description": "Motivo dito pelo lead, se ele explicou. Opcional."},
            },
            "required": ["lead_id"],
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
from zoneinfo import ZoneInfo
import memory as mem

FORTALEZA_TZ = ZoneInfo("America/Fortaleza")

_DIAS_SEMANA_KEYS = ["seg", "ter", "qua", "qui", "sex", "sab", "dom"]  # datetime.weekday(): 0=segunda

# Formato esperado em tenants.horarios (JSONB) — um horário [abertura, fechamento] em
# "HH:MM" por dia da semana, ou null se fechado nesse dia (ver database/migration_v6.sql).
# Usado como fallback quando o tenant não tem horarios configurado nesse formato ainda.
DEFAULT_HORARIOS = {
    "seg": ["09:00", "19:00"],
    "ter": ["09:00", "19:00"],
    "qua": ["09:00", "19:00"],
    "qui": ["09:00", "19:00"],
    "sex": ["09:00", "19:00"],
    "sab": ["09:00", "14:00"],
    "dom": None,
}

SLOT_DURATION_MINUTES = 60  # appointments não tem coluna de duração — cada agendamento
# ocupa um slot inteiro desse tamanho pra fins de checagem de colisão.


def _localizar_scheduled_at(raw: str) -> str:
    """A tool book_appointment pede horário local de Fortaleza sem offset (ex:
    "2026-07-21T11:00:00"). Sem isso, o Postgres grava esse valor como se já fosse
    UTC, jogando o agendamento 3h pra frente do horário real combinado. Aqui a
    gente interpreta qualquer string sem timezone como America/Fortaleza antes de
    devolver o ISO 8601 (com offset correto) que vai pro banco."""
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=FORTALEZA_TZ)
    return dt.isoformat()


async def execute_tool(tool_name: str, tool_input: dict, tenant: dict, phone: str) -> dict:
    dispatch = {
        "check_availability":    _check_availability,
        "book_appointment":      _book_appointment,
        "cancel_appointment":    _cancel_appointment,
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
    date_str = inp.get("date", "")
    parsed_date = None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            parsed_date = datetime.strptime(date_str, fmt)
            break
        except (ValueError, TypeError):
            continue

    if not parsed_date:
        return {
            "available": False,
            "slots": [],
            "message": f"Não entendi a data '{date_str}'. Pode confirmar no formato DD/MM/AAAA?",
        }

    display_date = parsed_date.strftime("%d/%m/%Y")
    weekday_key = _DIAS_SEMANA_KEYS[parsed_date.weekday()]

    horarios = tenant.get("horarios")
    if not isinstance(horarios, dict):
        horarios = DEFAULT_HORARIOS

    expediente = horarios.get(weekday_key)
    if not expediente or not isinstance(expediente, (list, tuple)) or len(expediente) != 2:
        return {
            "available": False,
            "slots": [],
            "message": f"Não atendemos em {display_date} (fechado nesse dia).",
        }

    duracao = timedelta(minutes=SLOT_DURATION_MINUTES)
    abertura_str, fechamento_str = expediente
    abertura = datetime.combine(parsed_date.date(), datetime.strptime(abertura_str, "%H:%M").time(), tzinfo=FORTALEZA_TZ)
    fechamento = datetime.combine(parsed_date.date(), datetime.strptime(fechamento_str, "%H:%M").time(), tzinfo=FORTALEZA_TZ)

    candidatos = []
    cursor = abertura
    while cursor + duracao <= fechamento:
        candidatos.append(cursor)
        cursor += duracao

    if not candidatos:
        return {
            "available": False,
            "slots": [],
            "message": f"Não atendemos em {display_date} (fechado nesse dia).",
        }

    # Busca agendamentos já existentes nesse dia (limites do dia em horário de Fortaleza,
    # convertidos pro instante UTC correspondente — scheduled_at é gravado em UTC por
    # _localizar_scheduled_at).
    dia_inicio = datetime.combine(parsed_date.date(), datetime.min.time(), tzinfo=FORTALEZA_TZ)
    dia_fim = dia_inicio + timedelta(days=1)

    sb = mem.get_client()
    ocupados_raw = (
        sb.table("appointments")
        .select("scheduled_at")
        .eq("tenant_id", str(tenant["id"]))
        .is_("cancelled_at", "null")  # horário cancelado volta a ser vendável
        .gte("scheduled_at", dia_inicio.isoformat())
        .lt("scheduled_at", dia_fim.isoformat())
        .execute()
    ).data or []
    ocupados = [datetime.fromisoformat(r["scheduled_at"]).astimezone(FORTALEZA_TZ) for r in ocupados_raw]

    def _colide(inicio_candidato: datetime) -> bool:
        fim_candidato = inicio_candidato + duracao
        for ocupado_inicio in ocupados:
            ocupado_fim = ocupado_inicio + duracao
            if inicio_candidato < ocupado_fim and ocupado_inicio < fim_candidato:
                return True
        return False

    livres = [c.strftime("%H:%M") for c in candidatos if not _colide(c)]

    if not livres:
        return {
            "available": False,
            "slots": [],
            "message": f"Não temos horários livres em {display_date}. Quer tentar outro dia?",
        }

    # Se o horário pedido está entre os livres, prioriza ele como primeira opção
    requested_time = inp.get("time", "").strip()
    if requested_time and requested_time in livres:
        livres.remove(requested_time)
        livres.insert(0, requested_time)

    exibidos = livres[:5]

    return {
        "available": True,
        "slots": [f"{display_date} às {s}" for s in exibidos],
        "message": f"Temos esses horários livres em {display_date}: {', '.join(exibidos)}.",
    }


async def _book_appointment(inp: dict, tenant: dict, phone: str) -> dict:
    sb = mem.get_client()
    scheduled_at = _localizar_scheduled_at(inp["scheduled_at"])

    # Evita duplicar: se o lead já tem um agendamento futuro em aberto, atualiza em vez de
    # inserir outro (acontece quando o lead confirma de novo depois de já confirmado, e
    # também quando ele pede pra trocar de data — é assim que remarcação funciona).
    # Agendamento cancelado não conta: senão um UPDATE ressuscitaria a linha cancelada.
    agora = datetime.now(timezone.utc).isoformat()
    existentes = (
        sb.table("appointments")
        .select("id, scheduled_at")
        .eq("lead_id", inp["lead_id"])
        .is_("cancelled_at", "null")
        .gte("scheduled_at", agora)
        .order("scheduled_at", desc=False)
        .limit(1)
        .execute()
    ).data

    # appointments tem UNIQUE (tenant_id, scheduled_at) — database/migration_v6.sql — pra
    # impedir dois leads diferentes fecharem o mesmo horário numa corrida (ex: ambos
    # chamam check_availability, veem o mesmo slot livre, e confirmam quase ao mesmo
    # tempo). O Postgres rejeita a segunda tentativa; aqui a gente devolve isso como
    # resposta tratada em vez de deixar a exceção crua estourar pro lead.
    remarcado = False
    try:
        if existentes:
            appointment_id = existentes[0]["id"]
            remarcado = not _mesmo_instante(existentes[0].get("scheduled_at"), scheduled_at)
            sb.table("appointments").update({
                "service":      inp["service"],
                "scheduled_at": scheduled_at,
            }).eq("id", appointment_id).execute()
        else:
            row = sb.table("appointments").insert({
                "lead_id":      inp["lead_id"],
                "tenant_id":    str(tenant["id"]),
                "service":      inp["service"],
                "scheduled_at": scheduled_at,
            }).execute()
            appointment_id = row.data[0]["id"]
    except Exception as e:
        if "duplicate key" in str(e).lower() or "23505" in str(e):
            return {
                "success": False,
                "error":   "horario_indisponivel",
                "message": "Esse horário acabou de ser reservado por outra pessoa. Pode escolher outro horário?",
            }
        raise

    resultado = {"success": True, "appointment_id": appointment_id, "remarcado": remarcado}

    # Remarcou: o lembrete que já existia foi calculado pra data ANTIGA e dispararia no
    # dia errado ("seu agendamento é amanhã" sobre um horário que não existe mais).
    # Cancela o antigo aqui; o novo é criado pelo schedule_followup logo em seguida.
    if remarcado:
        resultado["lembretes_antigos_cancelados"] = _cancelar_lembretes_pendentes(sb, inp["lead_id"])

    recall_info = _agendar_recall_se_configurado(sb, inp, tenant, phone, scheduled_at)
    if recall_info:
        resultado["recall_agendado"] = recall_info

    return resultado


def _mesmo_instante(anterior: str | None, novo: str) -> bool:
    """Compara dois timestamps ISO como instantes, não como texto — o Postgres devolve
    a data num formato de offset diferente do que a gente grava, então comparar string
    com string acusaria mudança em agendamento que não mudou."""
    try:
        return datetime.fromisoformat(anterior) == datetime.fromisoformat(novo)
    except (ValueError, TypeError):
        return False


def _cancelar_lembretes_pendentes(sb, lead_id: str) -> int:
    """Tira de 'pending' os lembretes de agendamento já criados pro lead, devolvendo
    quantos foram cancelados. Usado no cancelamento e na remarcação: nos dois casos o
    lembrete existente aponta pra uma data que não vale mais. executar_jobs_pendentes()
    só busca status='pending', então 'cancelled' nunca dispara."""
    import logging
    try:
        r = (
            sb.table("followup_jobs")
            .update({"status": "cancelled"})
            .eq("lead_id", lead_id)
            .eq("job_type", "appointment_reminder")
            .eq("status", "pending")
            .execute()
        )
        return len(r.data or [])
    except Exception as e:
        logging.getLogger(__name__).error("Falha ao cancelar lembretes do lead %s: %s", lead_id, e)
        return 0


async def _cancel_appointment(inp: dict, tenant: dict, phone: str) -> dict:
    """Cancela o próximo agendamento ativo do lead. O cancelamento é lógico
    (preenche cancelled_at) e não DELETE: é esse histórico que vira taxa de
    cancelamento por clínica no resumo pro dono."""
    sb = mem.get_client()
    agora = datetime.now(timezone.utc)

    proximos = (
        sb.table("appointments")
        .select("id, service, scheduled_at")
        .eq("lead_id", inp["lead_id"])
        .eq("tenant_id", str(tenant["id"]))
        .is_("cancelled_at", "null")
        .gte("scheduled_at", agora.isoformat())
        .order("scheduled_at", desc=False)
        .limit(1)
        .execute()
    ).data

    if not proximos:
        return {
            "success": False,
            "error":   "sem_agendamento",
            "message": "Não encontrei nenhum agendamento futuro em aberto para esse lead.",
        }

    agendamento = proximos[0]
    sb.table("appointments").update(
        {"cancelled_at": agora.isoformat()}
    ).eq("id", agendamento["id"]).execute()

    # Sem isso o lead que acabou de cancelar ainda receberia "seu agendamento é amanhã".
    lembretes = _cancelar_lembretes_pendentes(sb, inp["lead_id"])

    quando = _formatar_quando(agendamento.get("scheduled_at"))
    return {
        "success":               True,
        "appointment_id":        agendamento["id"],
        "servico":               agendamento.get("service", ""),
        "quando":                quando,
        "lembretes_cancelados":  lembretes,
        "motivo":                inp.get("motivo", ""),
    }


def _formatar_quando(scheduled_at: str | None) -> str:
    """Data do agendamento em horário de Fortaleza, pro modelo confirmar ao lead
    exatamente o que foi cancelado."""
    try:
        return datetime.fromisoformat(scheduled_at).astimezone(FORTALEZA_TZ).strftime("%d/%m/%Y às %H:%M")
    except (ValueError, TypeError):
        return ""


def _agendar_recall_se_configurado(sb, inp: dict, tenant: dict, phone: str, scheduled_at_localizado: str) -> dict | None:
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

    agendamento_em = datetime.fromisoformat(scheduled_at_localizado)
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
