# orchestrator.py - Orquestrador central: lê estágio do Supabase, chama funções especializadas

import re
import anthropic
from prompts import build_prompt
from functions import executar_acao_por_estagio
import memory as mem

client = anthropic.AsyncAnthropic()

MODELO = "claude-sonnet-4-6"
MAX_TOKENS = 1024
ESTAGIOS_VALIDOS = {"qualificacao", "apresentacao", "agendamento", "escalado"}


async def processar_mensagem(
    tenant: dict,
    phone: str,
    mensagem_usuario: str,
    canal: str = "whatsapp",
) -> dict:
    """
    Orquestrador principal. Fluxo completo por mensagem:

    1. Garante que o lead existe no Supabase
    2. Lê o estágio atual do lead
    3. Persiste a mensagem do usuário
    4. Busca histórico da conversa
    5. Monta o system prompt com dados do tenant
    6. Chama Claude API com prompt caching
    7. Extrai novo estágio da resposta
    8. Se estágio mudou → executa função especializada
    9. Injeta resultado da função na resposta (ex: link de pagamento)
    10. Persiste resposta e atualiza estágio no Supabase
    11. Retorna resposta limpa + metadados

    Args:
        tenant: dados do tenant vindos do Supabase
        phone: telefone ou ID do contato
        mensagem_usuario: texto recebido
        canal: "whatsapp" ou "instagram"

    Returns:
        dict com response, stage, canal, tenant_id
    """
    tenant_id = str(tenant["id"])

    # 1. Garante lead e lê estágio atual
    lead = mem.get_or_create_lead(tenant_id, phone, canal)
    stage_anterior = lead.get("stage", "qualificacao")

    # 2. Persiste mensagem do usuário
    mem.save_message(tenant_id, phone, "user", mensagem_usuario)

    # 3. Busca histórico (sem a msg atual para o prompt)
    historico_completo = mem.get_messages(tenant_id, phone)
    historico_para_prompt = historico_completo[:-1]

    # 4. Monta system prompt com dados do tenant e histórico
    system_prompt = build_prompt(tenant, historico_para_prompt)

    # 5. Monta mensagens para a API
    mensagens_api = [
        {"role": m["role"], "content": m["content"]}
        for m in historico_completo
    ]

    # 6. Chama Claude API com prompt caching
    try:
        resposta_api = await client.messages.create(
            model=MODELO,
            max_tokens=MAX_TOKENS,
            system=[
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=mensagens_api,
        )
    except anthropic.APIStatusError as e:
        raise RuntimeError(f"Anthropic API error {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise RuntimeError("Falha de conexão com Anthropic API") from e

    texto_bruto = resposta_api.content[0].text

    # 7. Extrai novo estágio e limpa o texto
    novo_stage = _extrair_stage(texto_bruto)
    texto_limpo = _limpar_stage(texto_bruto)

    # 8. Executa função especializada se estágio mudou ou requer ação
    resultado_funcao = None
    if novo_stage != stage_anterior or novo_stage == "escalado":
        contexto = _extrair_contexto_da_conversa(historico_completo, lead)
        resultado_funcao = await executar_acao_por_estagio(
            stage=novo_stage,
            tenant=tenant,
            phone=phone,
            canal=canal,
            contexto=contexto,
        )

    # 9. Injeta resultado da função na resposta ao cliente
    if resultado_funcao:
        texto_limpo = _injetar_resultado(texto_limpo, novo_stage, resultado_funcao, tenant)

    # 10. Persiste resposta e atualiza estágio
    mem.save_message(tenant_id, phone, "assistant", texto_limpo)
    mem.update_lead(tenant_id, phone, {"stage": novo_stage})
    mem.update_session(tenant_id, phone, novo_stage)

    return {
        "response": texto_limpo,
        "stage": novo_stage,
        "canal": canal,
        "tenant_id": tenant_id,
    }


def _extrair_stage(texto: str) -> str:
    """Extrai o estágio via regex da resposta do Claude."""
    match = re.search(r"STAGE:\s*(\w+)", texto, re.IGNORECASE)
    if match:
        stage = match.group(1).lower()
        if stage in ESTAGIOS_VALIDOS:
            return stage
    return "qualificacao"


def _limpar_stage(texto: str) -> str:
    """Remove a linha STAGE: do texto antes de enviar ao cliente."""
    linhas = texto.strip().split("\n")
    return "\n".join(
        l for l in linhas
        if not re.match(r"^\s*STAGE:\s*\w+\s*$", l, re.IGNORECASE)
    ).strip()


def _extrair_contexto_da_conversa(historico: list[dict], lead: dict) -> dict:
    """
    Extrai nome, procedimento, data e horário da conversa completa.
    Prioriza o histórico de mensagens sobre os campos do lead (que podem estar
    desatualizados antes do commit do turno atual).
    """
    contexto = {
        "nome": lead.get("name", "") or "",
        "procedimento": lead.get("procedimento", "") or "",
        "data": "",
        "horario": "",
    }

    # Reconstrói dados de agendamento salvos no lead
    agendamento_salvo = lead.get("data_agendamento", "") or ""
    partes = agendamento_salvo.split(" ")
    if len(partes) >= 1 and partes[0]:
        contexto["data"] = partes[0]
    if len(partes) >= 2 and partes[1]:
        contexto["horario"] = partes[1]

    # Varre mensagens do usuário para capturar dados mencionados neste turno
    texto_usuario = " ".join(
        m["content"] for m in historico if m["role"] == "user"
    )

    data_match = re.search(r"\b(\d{1,2}/\d{1,2}(?:/\d{2,4})?)\b", texto_usuario)
    if data_match:
        contexto["data"] = data_match.group(1)

    horario_match = re.search(r"\b(\d{1,2}[h:]\d{2}|\d{1,2}h)\b", texto_usuario, re.IGNORECASE)
    if horario_match:
        contexto["horario"] = horario_match.group(1)

    return contexto


def _injetar_resultado(
    texto: str,
    stage: str,
    resultado: dict,
    tenant: dict,
) -> str:
    """Complementa a resposta do agente com dados das funções especializadas."""
    if stage == "agendamento" and resultado.get("disponivel"):
        if "Nossa equipe" not in texto:
            texto += "\n\nPerfeito! Nossa equipe vai entrar em contato para finalizar. Te esperamos! 💛"

    elif stage == "agendamento" and not resultado.get("disponivel", True):
        alternativas = resultado.get("alternativas", [])
        if alternativas:
            texto += f"\n\n⚠️ Este horário não está disponível. Alternativas: {', '.join(alternativas)}"

    elif stage == "escalado":
        texto += "\n\nVou chamar um membro da nossa equipe para continuar te atendendo. Um momento! 💛"

    return texto
