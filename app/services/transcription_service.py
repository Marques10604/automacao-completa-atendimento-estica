# app/services/transcription_service.py
# Transcrição de nota de voz do WhatsApp via Groq (Whisper large-v3-turbo).
# Lead de clínica manda muito áudio, e até aqui ele só recebia o aviso de "só
# processo texto" — a conversa morria nesse ponto. O fluxo é:
#   media_id -> URL assinada da Graph API -> binário -> Groq -> texto,
# e o texto entra no pipeline normal como se o lead tivesse digitado.

import logging
import httpx
from app.config import settings

logger = logging.getLogger(__name__)

_GRAPH_BASE = "https://graph.facebook.com/v19.0"
_GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
_GROQ_MODEL = "whisper-large-v3-turbo"

# Nota de voz de atendimento não passa disso — acima é quase certo que não é o lead
# falando com a gente (áudio encaminhado, música). O limite da própria Groq é 25MB.
_MAX_BYTES = 20 * 1024 * 1024


def transcricao_ativa() -> bool:
    """Sem chave configurada a transcrição fica desligada e o webhook cai no aviso
    educado de mídia não suportada — exatamente o comportamento anterior."""
    return bool(settings.groq_api_key)


async def transcrever_audio_whatsapp(media_id: str, wa_token: str) -> str:
    """Devolve o texto da nota de voz, ou "" se qualquer etapa falhar.

    Nunca levanta exceção de propósito: quem chama trata "" como "não deu, usa o
    fallback". Áudio que não transcreve não pode derrubar o atendimento.
    """
    if not transcricao_ativa():
        logger.info("Transcrição desativada (GROQ_API_KEY ausente) — media_id=%s", media_id)
        return ""

    try:
        audio, mime = await _baixar_midia(media_id, wa_token)
        if not audio:
            return ""
        texto = await _transcrever(audio, mime)
        logger.info("Áudio transcrito: media_id=%s chars=%d", media_id, len(texto))
        return texto
    except Exception as e:
        logger.error("Falha ao transcrever áudio media_id=%s: %s", media_id, e)
        return ""


async def _baixar_midia(media_id: str, wa_token: str) -> tuple[bytes, str]:
    """Duas chamadas na Graph API: a primeira devolve uma URL assinada (que expira em
    poucos minutos), a segunda baixa o binário — e essa segunda também exige o Bearer,
    não basta a URL."""
    headers = {"Authorization": f"Bearer {wa_token}"}

    async with httpx.AsyncClient(timeout=20) as client:
        meta = await client.get(f"{_GRAPH_BASE}/{media_id}", headers=headers)
        meta.raise_for_status()
        info = meta.json()

        url = info.get("url") or ""
        if not url:
            logger.error("Graph API não devolveu URL para media_id=%s: %s", media_id, info)
            return b"", ""

        tamanho = int(info.get("file_size") or 0)
        if tamanho > _MAX_BYTES:
            logger.warning("Áudio grande demais (%d bytes) — media_id=%s", tamanho, media_id)
            return b"", ""

        # mime_type vem como "audio/ogg; codecs=opus" — o parâmetro extra atrapalha
        # o multipart da Groq, então fica só o tipo.
        mime = (info.get("mime_type") or "audio/ogg").split(";")[0].strip()

        binario = await client.get(url, headers=headers)
        binario.raise_for_status()
        return binario.content, mime


async def _transcrever(audio: bytes, mime: str) -> str:
    """A Groq roda Whisper large-v3-turbo em hardware próprio: nota de voz de 30s volta
    em ~2s, dentro do orçamento de resposta do agente."""
    extensao = mime.rsplit("/", 1)[-1] or "ogg"

    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            _GROQ_URL,
            headers={"Authorization": f"Bearer {settings.groq_api_key}"},
            files={"file": (f"audio.{extensao}", audio, mime or "audio/ogg")},
            data={
                "model": _GROQ_MODEL,
                # Idioma fixo: autodetecção erra com frequência em áudio curto e ruidoso,
                # e todo lead deste produto fala português.
                "language": "pt",
                "response_format": "text",
            },
        )
        r.raise_for_status()

    return r.text.strip()
