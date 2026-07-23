# app/agent/dispatcher.py
import asyncio
import random
import httpx
from app.config import settings


def _so_digitos(phone: str) -> str:
    return "".join(c for c in (phone or "") if c.isdigit())


def alternar_nono_digito_br(phone: str) -> str | None:
    """Devolve a MESMA linha brasileira no formato alternativo do 9º dígito, ou None
    se não se aplica.

    O WhatsApp trata o 9º dígito do celular brasileiro de forma inconsistente: em
    DDDs de SP/RJ/ES o ID vem com o 9 (13 dígitos); em outros DDDs (ex.: 85 Fortaleza)
    vem sem (12 dígitos). Não dá pra saber qual formato a Meta aceita sem tentar —
    então quem envia tenta o número como veio e, se a Meta recusar (erro 131030),
    reenvia com o 9 alternado por esta função.

    - 55 + DDD + 8 dígitos (12) → insere o 9 → 13 dígitos
    - 55 + DDD + 9XXXXXXXX (13) → remove o 9 → 12 dígitos
    """
    d = _so_digitos(phone)
    if not d.startswith("55"):
        return None
    if len(d) == 12:                      # sem o 9 → adiciona
        return d[:4] + "9" + d[4:]
    if len(d) == 13 and d[4] == "9":      # com o 9 → remove
        return d[:4] + d[5:]
    return None


async def _delay_digitacao(texto: str) -> None:
    """Pausa proporcional ao tamanho do texto, imitando ritmo real de digitação
    (~70ms/caractere, entre 1.6s e 6s, com variação aleatória)."""
    base = min(6.0, max(1.6, len(texto) * 0.07))
    await asyncio.sleep(base * (0.85 + random.random() * 0.3))


async def _post_texto_wa(client: httpx.AsyncClient, url: str, headers: dict, destino: str, corpo: str) -> httpx.Response:
    payload = {"messaging_product": "whatsapp", "to": destino, "type": "text", "text": {"body": corpo}}
    return await client.post(url, json=payload, headers=headers)


async def send_whatsapp(phone: str, text: str, wa_token: str, phone_number_id: str) -> None:
    """Envia mensagem via Meta Cloud API — quebrada em várias bolhas (por parágrafo,
    separado por linha em branco), com pausa de digitação entre elas. Uma pessoa real
    não manda um bloco gigante de texto de uma vez só; manda várias mensagens curtas.

    Se a Meta recusar o destinatário com 131030 (número não está no formato/lista que
    ela aceita), tenta uma vez com o 9º dígito brasileiro alternado — resolve o caso do
    celular BR que a Meta entrega sem o 9 mas só aceita com, e vice-versa. A escolha que
    der certo passa a valer pras próximas bolhas da mesma mensagem."""
    partes = [p.strip() for p in text.split("\n\n") if p.strip()] or [text]
    # A Meta orienta enviar celular BR sempre com o 9º dígito (13 dígitos), em dev e
    # produção. Então tentamos primeiro o formato com o 9 e deixamos o outro (sem o 9)
    # como fallback caso a Meta recuse com 131030 — cobre também números legados cujo
    # wa_id ainda é o de 8 dígitos.
    puro = _so_digitos(phone)
    com_nono = alternar_nono_digito_br(phone) if len(puro) == 12 else None
    destino = com_nono or puro
    alternativo = puro if com_nono else alternar_nono_digito_br(phone)
    url = f"https://graph.facebook.com/v19.0/{phone_number_id}/messages"
    headers = {"Authorization": f"Bearer {wa_token}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=10) as client:
        for i, parte in enumerate(partes):
            await _delay_digitacao(parte)
            r = await _post_texto_wa(client, url, headers, destino, parte)
            if r.status_code == 400 and alternativo and '131030' in r.text:
                # Formato do 9º dígito recusado — troca pro alternativo e fixa a escolha.
                destino = alternativo
                alternativo = None
                r = await _post_texto_wa(client, url, headers, destino, parte)
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
