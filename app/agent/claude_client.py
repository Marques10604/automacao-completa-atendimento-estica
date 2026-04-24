# app/agent/claude_client.py
import anthropic
import memory as mem
from app.agent.tools import TOOL_DEFINITIONS, execute_tool

_anthropic_client = anthropic.AsyncAnthropic()
MODELO = "claude-sonnet-4-6"
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
) -> dict:
    tenant_id = str(tenant["id"])
    identifier = phone or ig_user_id

    lead = mem.get_or_create_lead(tenant_id, identifier, canal)
    lead_id = str(lead["id"])

    mem.save_message(tenant_id, identifier, "user", mensagem_usuario)
    historico = mem.get_messages(tenant_id, identifier)

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
                    result = await execute_tool(block.name, block.input, tenant, phone)
                    tool_results.append({
                        "type":        "tool_result",
                        "tool_use_id": block.id,
                        "content":     str(result),
                    })
            if tool_results:  # guard: não enviar content vazio à API
                mensagens_api.append({"role": "user", "content": tool_results})

    return {
        "response":  "Desculpe, ocorreu um erro interno. Tente novamente.",
        "stage":     "qualificacao",
        "canal":     canal,
        "tenant_id": tenant_id,
        "lead_id":   lead_id,
    }
