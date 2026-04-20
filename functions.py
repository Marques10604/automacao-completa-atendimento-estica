# functions.py - Funções especializadas chamadas pelo orquestrador por estágio

from memory import update_lead


# ─────────────────────────────────────────────────────────────────────────────
# QUALIFICAÇÃO
# ─────────────────────────────────────────────────────────────────────────────

async def qualificar_lead(tenant_id: str, phone: str, nome: str, procedimento: str) -> dict:
    """Salva nome e interesse do lead no Supabase após qualificação."""
    update_lead(tenant_id, phone, {"name": nome, "procedimento": procedimento})
    return {"qualificado": True, "nome": nome, "procedimento": procedimento}


# ─────────────────────────────────────────────────────────────────────────────
# APRESENTAÇÃO
# ─────────────────────────────────────────────────────────────────────────────

async def apresentar_servicos(tenant_id: str, procedimento: str, tenant: dict | None = None) -> dict:
    """
    Retorna serviços relevantes para o interesse da cliente.
    Usa tenant["servicos"] (JSON no Supabase) quando disponível; caso contrário,
    usa o catálogo interno como fallback.
    """
    # Tenta usar catálogo do tenant (campo JSON "servicos" no Supabase)
    catalogo_tenant = (tenant or {}).get("servicos") if tenant else None
    if catalogo_tenant and isinstance(catalogo_tenant, dict):
        relevantes = []
        termo = (procedimento or "").lower()
        for chave, servicos in catalogo_tenant.items():
            if chave in termo:
                relevantes.extend(servicos if isinstance(servicos, list) else [servicos])
        return {"servicos": relevantes or list(catalogo_tenant.values())[0]}

    # Fallback — catálogo genérico
    catalogo = {
        "pele": ["Limpeza de pele profunda (R$ 180)", "Peeling químico (R$ 300)", "Carboxiterapia (R$ 350)"],
        "hidratacao": ["Hidratação facial com ácido hialurônico (R$ 250)"],
        "botox": ["Botox preventivo (a partir de R$ 600)"],
        "preenchimento": ["Preenchimento labial (a partir de R$ 700)"],
    }

    relevantes = []
    termo = (procedimento or "").lower()
    for chave, servicos in catalogo.items():
        if chave in termo:
            relevantes.extend(servicos)

    return {"servicos": relevantes or list(catalogo.values())[0]}


# ─────────────────────────────────────────────────────────────────────────────
# DISPONIBILIDADE
# ─────────────────────────────────────────────────────────────────────────────

async def checar_disponibilidade(tenant_id: str, data: str, horario: str) -> dict:
    """
    Verifica disponibilidade de horário.
    Retorna mock — integrar com Google Calendar ou sistema próprio.
    """
    # TODO: integrar com Google Calendar API ou agenda própria
    return {
        "disponivel": True,
        "alternativas": [],
        "mensagem": f"Horário {horario} de {data} disponível.",
    }


# ─────────────────────────────────────────────────────────────────────────────
# AGENDAMENTO
# ─────────────────────────────────────────────────────────────────────────────

async def criar_agendamento(
    tenant_id: str,
    phone: str,
    data: str,
    horario: str,
    procedimento: str,
) -> dict:
    """Persiste o agendamento confirmado no Supabase."""
    update_lead(tenant_id, phone, {
        "data_agendamento": f"{data} {horario}",
        "procedimento": procedimento,
        "stage": "agendamento",
    })
    return {
        "sucesso": True,
        "confirmacao": f"Agendamento criado: {procedimento} em {data} às {horario}.",
    }


# ─────────────────────────────────────────────────────────────────────────────
# LEMBRETE 24H
# ─────────────────────────────────────────────────────────────────────────────

async def enviar_lembrete_24h(tenant_id: str, phone: str) -> dict:
    """
    Agenda lembrete 24h antes da consulta.
    Implementar com task queue (Celery, APScheduler) ou n8n.
    """
    # TODO: integrar com sistema de agendamento de tarefas ou n8n
    return {"agendado": True, "mensagem": "Lembrete 24h registrado para envio."}


# ─────────────────────────────────────────────────────────────────────────────
# ESCALAÇÃO HUMANA
# ─────────────────────────────────────────────────────────────────────────────

async def escalar_humano(tenant_id: str, phone: str, motivo: str = "") -> dict:
    """
    Marca o lead como escalado no Supabase.
    Integrar com Chatwoot, Zendesk ou notificação interna.
    """
    update_lead(tenant_id, phone, {"stage": "escalado", "escalado": True})
    # TODO: notificar equipe via e-mail, Slack ou WhatsApp interno
    return {"escalado": True, "motivo": motivo}


# ─────────────────────────────────────────────────────────────────────────────
# DISPATCHER — chamado pelo orquestrador após mudança de estágio
# ─────────────────────────────────────────────────────────────────────────────

async def executar_acao_por_estagio(
    stage: str,
    tenant: dict,
    phone: str,
    canal: str,
    contexto: dict,
) -> dict | None:
    """
    Dado o novo estágio, executa a função correspondente.

    Args:
        stage: estágio detectado pelo orquestrador
        tenant: dados completos do tenant
        phone: telefone/ID do contato
        canal: "whatsapp" ou "instagram"
        contexto: dados extraídos do lead (nome, data, horario, procedimento)
    """
    tenant_id = str(tenant["id"])

    if stage == "qualificacao":
        nome = contexto.get("nome", "")
        procedimento = contexto.get("procedimento", "")
        if nome:
            return await qualificar_lead(tenant_id, phone, nome, procedimento)

    elif stage == "apresentacao":
        procedimento = contexto.get("procedimento", "")
        return await apresentar_servicos(tenant_id, procedimento, tenant)

    elif stage == "agendamento":
        data = contexto.get("data", "")
        horario = contexto.get("horario", "")
        procedimento = contexto.get("procedimento", "")
        if data and horario:
            disponibilidade = await checar_disponibilidade(tenant_id, data, horario)
            if disponibilidade["disponivel"] and procedimento:
                await criar_agendamento(tenant_id, phone, data, horario, procedimento)
                await enviar_lembrete_24h(tenant_id, phone)
            return disponibilidade

    elif stage == "escalado":
        motivo = contexto.get("motivo", "Solicitado pela cliente")
        return await escalar_humano(tenant_id, phone, motivo)

    return None
