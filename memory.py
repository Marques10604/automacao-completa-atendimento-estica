# memory.py - Sessão e histórico persistidos no Supabase (substitui dicionário Python)

import os
import logging
from datetime import datetime, timedelta, timezone
from supabase import create_client, Client

logger = logging.getLogger(__name__)

_supabase: Client | None = None


def get_client() -> Client:
    """Retorna o cliente Supabase (singleton)."""
    global _supabase
    if _supabase is None:
        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
        _supabase = create_client(url, key)
    return _supabase


# ─────────────────────────────────────────────────────────────────────────────
# LEADS
# ─────────────────────────────────────────────────────────────────────────────

def get_or_create_lead(tenant_id: str, phone: str, canal: str = "whatsapp") -> dict:
    """Retorna o lead existente ou cria um novo."""
    try:
        sb = get_client()
        result = (
            sb.table("leads")
            .select("*")
            .eq("tenant_id", tenant_id)
            .eq("phone", phone)
            .limit(1)
            .execute()
        )
        if result.data:
            return result.data[0]

        novo = (
            sb.table("leads")
            .insert({"tenant_id": tenant_id, "phone": phone, "canal": canal})
            .execute()
        )
        return novo.data[0]
    except Exception as e:
        logger.error("get_or_create_lead failed: %s", e)
        raise


def update_lead(tenant_id: str, phone: str, campos: dict) -> None:
    """Atualiza campos do lead (name, stage, procedimento, data_agendamento, etc.)."""
    try:
        sb = get_client()
        sb.table("leads").update(campos).eq("tenant_id", tenant_id).eq("phone", phone).execute()
    except Exception as e:
        logger.error("update_lead failed: %s", e)
        raise


def get_lead_stage(tenant_id: str, phone: str) -> str:
    """Retorna o estágio atual do lead."""
    sb = get_client()
    result = (
        sb.table("leads")
        .select("stage")
        .eq("tenant_id", tenant_id)
        .eq("phone", phone)
        .limit(1)
        .execute()
    )
    return result.data[0]["stage"] if result.data else "qualificacao"


# ─────────────────────────────────────────────────────────────────────────────
# CONVERSATIONS
# ─────────────────────────────────────────────────────────────────────────────

def save_message(tenant_id: str, phone: str, role: str, content: str) -> None:
    """Persiste uma mensagem no histórico de conversas."""
    try:
        sb = get_client()
        sb.table("conversations").insert({
            "tenant_id": tenant_id,
            "phone": phone,
            "role": role,
            "content": content,
        }).execute()
    except Exception as e:
        logger.error("save_message failed: %s", e)
        raise


def get_messages(tenant_id: str, phone: str, limit: int = 20) -> list[dict]:
    """Retorna as últimas N mensagens da conversa."""
    try:
        sb = get_client()
        result = (
            sb.table("conversations")
            .select("role, content")
            .eq("tenant_id", tenant_id)
            .eq("phone", phone)
            .order("created_at", desc=False)
            .limit(limit)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.error("get_messages failed: %s", e)
        return []


# ─────────────────────────────────────────────────────────────────────────────
# IDEMPOTÊNCIA — evita reprocessar o mesmo webhook (Meta reenvia se demorar a responder)
# ─────────────────────────────────────────────────────────────────────────────

def is_duplicate_message(wamid: str) -> bool:
    """Registra o wamid como processado; retorna True se ele já tinha sido visto antes."""
    if not wamid:
        return False
    try:
        sb = get_client()
        sb.table("processed_messages").insert({"wamid": wamid}).execute()
        return False
    except Exception as e:
        if "duplicate key" in str(e).lower() or "23505" in str(e):
            return True
        logger.error("is_duplicate_message falhou para wamid %s: %s", wamid, e)
        return False  # em dúvida, não bloqueia o atendimento


# ─────────────────────────────────────────────────────────────────────────────
# SESSIONS
# ─────────────────────────────────────────────────────────────────────────────

def update_session(tenant_id: str, phone: str, stage: str) -> None:
    """Cria ou atualiza a sessão com o estágio atual."""
    sb = get_client()
    sb.table("sessions").upsert({
        "tenant_id": tenant_id,
        "phone": phone,
        "stage": stage,
        "last_activity": datetime.now(timezone.utc).isoformat(),
    }, on_conflict="tenant_id,phone").execute()


# ─────────────────────────────────────────────────────────────────────────────
# TENANTS
# ─────────────────────────────────────────────────────────────────────────────

def get_tenant_by_phone_number_id(phone_number_id: str) -> dict | None:
    """Busca o tenant pelo phone_number_id do WhatsApp (identifica qual cliente é)."""
    sb = get_client()
    result = (
        sb.table("tenants")
        .select("*")
        .eq("phone_number_id", phone_number_id)
        .eq("ativo", True)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def get_tenant_by_name(name: str) -> dict | None:
    """Busca o tenant pelo slug de nome."""
    sb = get_client()
    result = (
        sb.table("tenants")
        .select("*")
        .eq("name", name)
        .eq("ativo", True)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def get_all_tenants() -> list[dict]:
    """Retorna todos os tenants ativos."""
    sb = get_client()
    result = sb.table("tenants").select("id, name, clinic_name, ativo").eq("ativo", True).execute()
    return result.data or []
