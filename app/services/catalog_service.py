# app/services/catalog_service.py
# Catálogo de serviços e FAQ do tenant, formatados pro system prompt.
#
# Antes, os serviços viviam como JSONB solto em tenants.servicos e o FAQ não existia —
# se o lead perguntasse "vocês têm estacionamento?", o agente não sabia responder.
# Agora vêm de tabelas próprias (migration_v10), que é o que permite o dono da clínica
# configurar sozinho quando o painel existir.

import logging
import memory as mem

logger = logging.getLogger(__name__)


def _formatar_preco(preco, a_partir_de: bool) -> str:
    """Preço em reais no formato brasileiro. Serviço sem preço cadastrado sai como
    'sob consulta' em vez de sumir — o agente precisa saber que o serviço existe
    mesmo quando o valor não está definido."""
    if preco is None:
        return "sob consulta"
    valor = f"R$ {float(preco):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"a partir de {valor}" if a_partir_de else valor


def _formatar_servicos(linhas: list[dict]) -> str:
    partes = []
    for s in linhas:
        preco = _formatar_preco(s.get("preco"), bool(s.get("preco_a_partir_de")))
        item = f"- {s.get('nome')} ({preco}"
        duracao = s.get("duracao_min")
        if duracao:
            item += f", ~{duracao} min"
        item += ")"
        if s.get("descricao"):
            item += f" — {s['descricao']}"
        partes.append(item)
    return "\n".join(partes)


def _formatar_faq(linhas: list[dict]) -> str:
    return "\n".join(f"P: {f.get('pergunta')}\nR: {f.get('resposta')}" for f in linhas)


def carregar_catalogo(tenant_id: str) -> dict:
    """Devolve {"servicos": texto, "faq": texto} pro prompt.

    Síncrono porque o client do Supabase é síncrono — quem chama roda em
    asyncio.to_thread. Falha de leitura devolve string vazia em vez de estourar: o
    atendimento continua com o fallback do prompt, degradado mas vivo.
    """
    sb = mem.get_client()

    try:
        servicos = (
            sb.table("services")
            .select("nome, descricao, preco, preco_a_partir_de, duracao_min")
            .eq("tenant_id", tenant_id)
            .eq("ativo", True)
            .order("nome")
            .execute()
        ).data or []
    except Exception as e:
        logger.error("Falha ao carregar serviços do tenant %s: %s", tenant_id, e)
        servicos = []

    try:
        faq = (
            sb.table("faq")
            .select("pergunta, resposta")
            .eq("tenant_id", tenant_id)
            .eq("ativo", True)
            .order("ordem")
            .execute()
        ).data or []
    except Exception as e:
        logger.error("Falha ao carregar FAQ do tenant %s: %s", tenant_id, e)
        faq = []

    return {
        "servicos": _formatar_servicos(servicos),
        "faq": _formatar_faq(faq),
    }
