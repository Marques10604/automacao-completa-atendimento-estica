# app/agent/dispatcher.py
import asyncio
import random
import httpx
from app.config import settings


async def _delay_digitacao(texto: str) -> None:
    """Pausa proporcional ao tamanho do texto, imitando ritmo real de digitação
    (~70ms/caractere, entre 1.6s e 6s, com variação aleatória)."""
    base = min(6.0, max(1.6, len(texto) * 0.07))
    await asyncio.sleep(base * (0.85 + random.random() * 0.3))


async def send_whatsapp(phone: str, text: str, wa_token: str, phone_number_id: str) -> None:
    """Envia mensagem via Meta Cloud API — quebrada em várias bolhas (por parágrafo,
    separado por linha em branco), com pausa de digitação entre elas. Uma pessoa real
    não manda um bloco gigante de texto de uma vez só; manda várias mensagens curtas."""
    partes = [p.strip() for p in text.split("\n\n") if p.strip()] or [text]
    url = f"https://graph.facebook.com/v19.0/{phone_number_id}/messages"
    headers = {"Authorization": f"Bearer {wa_token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=10) as client:
        for i, parte in enumerate(partes):
            await _delay_digitacao(parte)
            payload = {
                "messaging_product": "whatsapp",
                "to": phone,
                "type": "text",
                "text": {"body": parte},
            }
            r = await client.post(url, json=payload, headers=headers)
            r.raise_for_status()
            if i < len(partes) - 1:
                await asyncio.sleep(0.6 + random.random() * 0.4)


async def send_instagram(ig_user_id: str, text: str, ig_access_token: str) -> None:
    """Envia mensagem de texto via Meta Graph API (Instagram DM)."""
    url = "https://graph.facebook.com/v19.0/me/messages"
    payload = {
        "recipient": {"id": ig_user_id},
        "message": {"text": text},
    }
    headers = {"Authorization": f"Bearer {ig_access_token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(url, json=payload, headers=headers)
        r.raise_for_status()


async def send_message(channel: str, phone: str, ig_user_id: str, text: str, tenant: dict) -> None:
    """Roteia envio para o canal correto."""
    wa_token = tenant.get("whatsapp_token") or settings.meta_wa_token
    phone_number_id = tenant.get("phone_number_id") or settings.meta_wa_phone_number_id
    ig_token = tenant.get("ig_access_token") or settings.meta_ig_access_token

    if channel == "whatsapp":
        await send_whatsapp(phone, text, wa_token, phone_number_id)
    elif channel == "instagram":
        await send_instagram(ig_user_id, text, ig_token)
    else:
        raise ValueError(f"Canal inválido ou identificador ausente: channel={channel}")
