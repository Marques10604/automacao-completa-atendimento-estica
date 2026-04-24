# app/agent/dispatcher.py
import httpx
from app.config import settings


async def send_whatsapp(phone: str, text: str, wa_token: str, phone_number_id: str) -> None:
    """Envia mensagem de texto via Meta Cloud API."""
    url = f"https://graph.facebook.com/v19.0/{phone_number_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "text",
        "text": {"body": text},
    }
    headers = {"Authorization": f"Bearer {wa_token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(url, json=payload, headers=headers)
        r.raise_for_status()


async def send_instagram(ig_user_id: str, text: str, ig_access_token: str) -> None:
    """Envia mensagem de texto via Meta Graph API (Instagram DM)."""
    url = "https://graph.facebook.com/v19.0/me/messages"
    payload = {
        "recipient": {"id": ig_user_id},
        "message": {"text": text},
    }
    params = {"access_token": ig_access_token}
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(url, json=payload, params=params)
        r.raise_for_status()


async def send_message(channel: str, phone: str, ig_user_id: str, text: str, tenant: dict) -> None:
    """Roteia envio para o canal correto."""
    wa_token = tenant.get("whatsapp_token") or settings.meta_wa_token
    phone_number_id = tenant.get("phone_number_id") or settings.meta_wa_phone_number_id
    ig_token = tenant.get("ig_access_token") or settings.meta_ig_access_token

    if channel == "whatsapp" and phone:
        await send_whatsapp(phone, text, wa_token, phone_number_id)
    elif channel == "instagram" and ig_user_id:
        await send_instagram(ig_user_id, text, ig_token)
    else:
        raise ValueError(f"Canal inválido ou identificador ausente: channel={channel}")
