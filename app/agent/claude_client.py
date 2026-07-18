# app/agent/claude_client.py
import asyncio
import logging
import anthropic
import memory as mem
from app.agent.tools import TOOL_DEFINITIONS, execute_tool

logger = logging.getLogger(__name__)

_anthropic_client = anthropic.AsyncAnthropic()
MODELO = "claude-sonnet-5"
MAX_TOKENS = 1024

_PROMPT_FALLBACK = "Você é uma assistente de vendas de alta performance. Ajude o lead a agendar e fechar negócio."


def _get_system_prompt(tenant: dict, canal: str) -> str:
    try:
        from app.agent.prompts import build_prompt
        return build_prompt(tenant, canal)
    except ImportError:
        return _PROMPT_FALLBACK


async def processar_mensagem(
    tenant: dict,
    phone: str,
    mensagem_usuario: str,
    canal: str = "whatsapp",
    ig_user_id: str = "",
    ja_salvo: bool = False,
) -> dict:
    tenant_id = str(tenant["id"])
    identifier = phone or ig_user_id

    lead = mem.get_or_create_lead(tenant_id, identifier, canal)
    lead_id = str(lead["id"])

    # Lead marcado como "frio" (deu SAIR antes) voltou a mandar mensagem por conta própria —
    # reativa o estágio pra não ficar registrado como frio nos relatórios enquanto está
    # conversando ativamente de novo. Não precisa de reset manual, nem a IA fica muda: ela
    # simplesmente retoma o atendimento normalmente.
    if lead.get("stage") == "frio":
        sb = mem.get_client()
        await asyncio.to_thread(
            lambda: sb.table("leads").update({"stage": "novo"}).eq("id", lead_id).execute()
        )
        lead["stage"] = "novo"

    # Salvar mensagem do usuário (pulado se o chamador já salvou antes — caso do buffer
    # de rajada no webhook, que salva a mensagem na hora e só chama isso depois do debounce)
    if not ja_salvo:
        mem.save_message(tenant_id, identifier, "user", mensagem_usuario)

    # Handoff pra humano: se um atendente já assumiu esse lead (leads.escalado = true),
    # a IA fica muda até ser reativada — nunca responde por cima de um humano.
    if lead.get("escalado"):
        logger.info("Lead %s está escalado para atendimento humano — IA não responde", lead_id)
        return {
            "response":  "",
            "stage":     lead.get("stage", "qualificacao"),
            "canal":     canal,
            "tenant_id": tenant_id,
            "lead_id":   lead_id,
            "escalado":  True,
        }

    # Tratar opt-out SAIR antes de chamar Claude
    if mensagem_usuario.strip().upper() == "SAIR":
        sb = mem.get_client()
        await asyncio.to_thread(
            lambda: sb.table("leads").update({"stage": "frio"}).eq("id", lead_id).execute()
        )
        resposta_sair = "Entendido! Removemos seus dados do nosso sistema. Se quiser retornar, é só nos chamar. 💛"
        mem.save_message(tenant_id, identifier, "assistant", resposta_sair)
        return {
            "response":  resposta_sair,
            "stage":     "frio",
            "canal":     canal,
            "tenant_id": tenant_id,
            "lead_id":   lead_id,
        }

    # Uma única query de histórico (já inclui a mensagem do usuário recém salva)
    historico = mem.get_messages(tenant_id, identifier)

    # Salvar consentimento LGPD (opt-in implícito) — checa se já existe em vez de contar
    # mensagens no histórico (com o buffer de rajada, a primeira interação pode já
    # chegar com várias mensagens salvas de uma vez, então "len(historico) == 1" não
    # é mais confiável pra saber se é a primeira vez que falamos com esse lead).
    try:
        sb = mem.get_client()
        existente = await asyncio.to_thread(
            lambda: sb.table("consent_log").select("id").eq("lead_id", lead_id).limit(1).execute()
        )
        if not existente.data:
            await asyncio.to_thread(
                lambda: sb.table("consent_log").insert({
                    "lead_id":      lead_id,
                    "tenant_id":    tenant_id,
                    "channel":      canal,
                    "consent_text": "Opt-in implícito: lead iniciou conversa. LGPD informada na primeira mensagem.",
                }).execute()
            )
    except Exception as e:
        logger.error("Falha ao verificar/salvar consent_log para lead %s: %s", lead_id, e)
        # continua — falha de consent_log não deve interromper o atendimento

    system_prompt = _get_system_prompt(tenant, canal)
    mensagens_api = [{"role": m["role"], "content": m["content"]} for m in historico]

    for _ in range(5):
        resposta = await _anthropic_client.messages.create(
            model=MODELO,
            max_tokens=MAX_TOKENS,
            system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
            tools=TOOL_DEFINITIONS,
            messages=mensagens_api,
        )

        if resposta.stop_reason == "end_turn":
            texto = next((b.text for b in resposta.content if hasattr(b, "text")), "")
            if texto:  # guard: não salvar resposta vazia
                mem.save_message(tenant_id, identifier, "assistant", texto)
            mem.update_session(tenant_id, identifier, lead.get("stage", "qualificacao"))
            return {
                "response": texto,
                "stage":    lead.get("stage", "qualificacao"),
                "canal":    canal,
                "tenant_id": tenant_id,
                "lead_id":  lead_id,
            }

        if resposta.stop_reason == "tool_use":
            mensagens_api.append({"role": "assistant", "content": resposta.content})
            tool_results = []
            for block in resposta.content:
                if block.type == "tool_use":
                    tool_input = dict(block.input)
                    if "lead_id" in tool_input:
                        # O modelo nunca sabe o UUID real do lead (não está no prompt nem
                        # em nenhum tool_result) — se deixado por conta própria, inventa um
                        # placeholder tipo "lead_temp_001", que quebra no Postgres (uuid inválido).
                        # O servidor sempre sabe o lead_id certo desta conversa; usamos ele.
                        tool_input["lead_id"] = lead_id
                    result = await execute_tool(block.name, tool_input, tenant, phone)
                    tool_results.append({
                        "type":        "tool_result",
                        "tool_use_id": block.id,
                        "content":     str(result),
                    })
            if tool_results:  # guard: não enviar content vazio à API
                mensagens_api.append({"role": "user", "content": tool_results})

    logger.error("Loop tool_use esgotou 5 iterações sem end_turn para lead %s", lead_id)
    return {
        "response":  "Desculpe, ocorreu um erro interno. Tente novamente.",
        "stage":     "qualificacao",
        "canal":     canal,
        "tenant_id": tenant_id,
        "lead_id":   lead_id,
    }
