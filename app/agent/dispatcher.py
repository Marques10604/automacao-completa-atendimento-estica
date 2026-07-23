# app/agent/dispatcher.py
import asyncio
import random
import httpx
from app.config import settings


def normalizar_telefone_br(phone: str) -> str:
    """Insere o 9º dígito em celular brasileiro quando a Meta entrega sem ele.

    A Meta reporta o remetente de números do Brasil no formato antigo, sem o 9
    inicial do celular (ex.: 55 85 97542412 = 12 dígitos). Mas pra ENVIAR, o número
    precisa estar no formato atual, com o 9 (55 85 9 97542412 = 13 dígitos) — que é
    como o dono cadastra na lista de permissão. Sem essa correção, toda resposta a
    lead brasileiro falha com erro 131030 ("recipient not in allowed list").

    Regra: 55 + DDD(2) + 8 dígitos → insere '9' logo após o DDD. Números que já têm
    13 dígitos, ou que não são brasileiros, passam intactos.
    """
    if not phone:
        return phone
    digitos = "".join(c for c in phone if c.isdigit())
    # 55 (país) + 2 (DDD) + 8 (número antigo, sem o 9) = 12 dígitos
    if len(digitos) == 12 and digitos.startswith("55"):
        return digitos[:4] + "9" + digitos[4:]
    return digitos


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
    destino = normalizar_telefone_br(phone)  # corrige o 9º dígito antes de enviar
    url = f"https://graph.facebook.com/v19.0/{phone_number_id}/messages"
    headers = {"Authorization": f"Bearer {wa_token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=10) as client:
        for i, parte in enumerate(partes):
            await _delay_digitacao(parte)
            payload = {
                "messaging_product": "whatsapp",
                "to": destino,
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
