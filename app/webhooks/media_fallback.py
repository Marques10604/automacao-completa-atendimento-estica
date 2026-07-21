# app/webhooks/media_fallback.py
# Resposta padrão quando o lead manda mídia (áudio, foto, documento, ...) que o
# agente ainda não processa (transcrição/visão fica pra uma fase futura).
# Compartilhado entre o webhook do WhatsApp e do Instagram pra manter o texto
# consistente nos dois canais.

_MENSAGENS_MIDIA_NAO_SUPORTADA = {
    "audio":    ("áudio", "Recebi seu áudio! Por enquanto só consigo processar texto — pode me escrever a mensagem?"),
    "image":    ("foto", "Recebi sua foto! Por enquanto só consigo processar texto — pode me contar em palavras?"),
    "document": ("documento", "Recebi seu documento! Por enquanto só consigo processar texto — pode me escrever o que precisa?"),
    "video":    ("vídeo", "Recebi seu vídeo! Por enquanto só consigo processar texto — pode me escrever a mensagem?"),
}
_FALLBACK = ("mídia", "Recebi seu envio! Por enquanto só consigo processar texto — pode me escrever a mensagem?")


def resposta_midia_nao_suportada(tipo: str) -> tuple[str, str]:
    """Devolve (rótulo pro marcador de histórico, texto de resposta ao lead) pro
    tipo de mídia recebido. Tipos sem entrada específica caem no fallback genérico."""
    return _MENSAGENS_MIDIA_NAO_SUPORTADA.get(tipo, _FALLBACK)
