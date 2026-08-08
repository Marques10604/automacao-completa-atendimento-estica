# app/agent/claude_client.py
import asyncio
import logging
from datetime import datetime, timedelta, timezone
import anthropic
import memory as mem
from app.agent.tools import TOOL_DEFINITIONS, execute_tool

logger = logging.getLogger(__name__)

_anthropic_client = anthropic.AsyncAnthropic()
MODELO = "claude-sonnet-5"
MAX_TOKENS = 1024

_PROMPT_FALLBACK = "Você é uma assistente de vendas de alta performance. Ajude o lead a agendar e fechar negócio."


async def _get_system_prompt(tenant: dict, canal: str) -> str:
    """Monta o system prompt já com o catálogo de serviços e o FAQ do tenant.

    A leitura do catálogo roda em to_thread porque o client do Supabase é síncrono.
    São duas consultas indexadas por turno; se um dia pesar na latência, o caminho é
    cache por tenant com TTL curto — não vale complicar antes de medir.
    """
    try:
        from app.agent.prompts import build_prompt
        from app.services.catalog_service import carregar_catalogo
    except ImportError:
        return _PROMPT_FALLBACK

    catalogo = await asyncio.to_thread(carregar_catalogo, str(tenant["id"]))
    return build_prompt(
        tenant,
        canal,
        servicos_texto=catalogo.get("servicos") or None,
        faq_texto=catalogo.get("faq") or None,
    )


async def _agendar_resgate_silencio(
    tenant: dict,
    tenant_id: str,
    identifier: str,
    canal: str,
    phone: str,
    ig_user_id: str,
    lead_id: str,
) -> None:
    """Reagenda o resgate por silêncio a cada mensagem do lead: cancela qualquer
    tentativa pendente (o "relógio" reseta a cada mensagem recebida) e agenda uma
    tentativa 1 nova pra daqui 3h — só se o lead seguir em aberto (qualquer stage
    exceto agendado/fechado/frio — cobre o default do schema 'qualificacao' e o
    vocabulário do funil novo/qualificado, que só aparece depois de uma tool call),
    sem estar escalado pra humano, e sem já ter outro follow-up pendente de outro
    tipo (evita duas mensagens de reengajamento diferentes chegando juntas).

    Sempre lê o estado mais recente do lead direto do Supabase em vez de usar o
    dict `lead` já carregado em memória: tools chamadas nesse mesmo turno (ex:
    update_lead_status, escalate_to_human) escrevem direto no banco sem atualizar
    esse dict, então confiar nele aqui poderia agendar (ou deixar de cancelar) com
    base num estágio que já mudou.

    Best-effort: roda depois que a resposta ao lead já foi computada (e às vezes já
    persistida via mem.save_message). Qualquer falha aqui (rede, erro transiente do
    Supabase) é logada e engolida — nunca deve derrubar o return do chamador nem virar
    um "erro interno" pro lead que já recebeu uma resposta válida.
    """
    try:
        sb = mem.get_client()

        await asyncio.to_thread(
            lambda: sb.table("followup_jobs")
                .update({"status": "cancelled"})
                .eq("lead_id", lead_id)
                .eq("job_type", "resgate_silencio")
                .eq("status", "pending")
                .execute()
        )

        fresh = await asyncio.to_thread(
            lambda: sb.table("leads").select("stage, escalado").eq("id", lead_id).limit(1).execute()
        )
        if not fresh.data:
            return
        stage = fresh.data[0].get("stage")
        escalado = fresh.data[0].get("escalado")
        # Exclusão em vez de allowlist: o default do schema é 'qualificacao' (não
        # 'qualificado'), e só vira 'novo'/'qualificado'/etc depois de uma tool call
        # explícita — uma allowlist ("novo", "qualificado") deixava de fora todo lead
        # recém-criado que ainda não recebeu nenhuma chamada de update_lead_status.
        if stage in ("agendado", "fechado", "frio") or escalado:
            return

        outro_pendente = await asyncio.to_thread(
            lambda: sb.table("followup_jobs")
                .select("id")
                .eq("lead_id", lead_id)
                .eq("status", "pending")
                .neq("job_type", "resgate_silencio")
                .limit(1)
                .execute()
        )
        if outro_pendente.data:
            return

        agora = datetime.now(timezone.utc)
        scheduled_at = (agora + timedelta(hours=3)).isoformat()
        await asyncio.to_thread(
            lambda: sb.table("followup_jobs").insert({
                "lead_id":      lead_id,
                "tenant_id":    tenant_id,
                "channel":      canal,
                "phone":        phone,
                "ig_user_id":   ig_user_id,
                "job_type":     "resgate_silencio",
                "scheduled_at": scheduled_at,
                "payload":      {"tentativa": 1, "last_msg_at_snapshot": agora.isoformat()},
            }).execute()
        )
    except Exception as e:
        logger.error("Falha ao agendar resgate por silêncio para lead %s: %s", lead_id, e)


async def processar_mensagem(
    tenant: dict,
    phone: str,
    mensagem_usuario: str,
    canal: str = "whatsapp",
    ig_user_id: str = "",
    ja_salvo: bool = False,
    salvar_resposta: bool = True,
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
        await _agendar_resgate_silencio(tenant, tenant_id, identifier, canal, phone, ig_user_id, lead_id)
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
        if salvar_resposta:
            mem.save_message(tenant_id, identifier, "assistant", resposta_sair)
        await _agendar_resgate_silencio(tenant, tenant_id, identifier, canal, phone, ig_user_id, lead_id)
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
    primeira_mensagem = False
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
            primeira_mensagem = True
    except Exception as e:
        logger.error("Falha ao verificar/salvar consent_log para lead %s: %s", lead_id, e)
        # continua — falha de consent_log não deve interromper o atendimento

    # O aviso de LGPD na 1a mensagem é determinístico, não delegado ao modelo: depender
    # só do prompt deixava a IA às vezes já responder ao pedido do lead misturado com o
    # aviso (ou pular o aviso). A pergunta original do lead fica pro próximo turno, depois
    # que ele confirmar (nem que seja só continuando a conversa).
    if primeira_mensagem:
        professional_name = tenant.get("professional_name") or "Assistente Virtual"
        clinic_name = tenant.get("clinic_name") or "Clínica"
        resposta_lgpd = (
            f"Oi! Aqui é a {professional_name}, da {clinic_name} 😊 Antes de começar, seguimos a LGPD: "
            "suas informações são usadas apenas para este atendimento, e pra parar é só digitar SAIR. "
            "Posso continuar?"
        )
        if salvar_resposta:
            mem.save_message(tenant_id, identifier, "assistant", resposta_lgpd)
        mem.update_session(tenant_id, identifier, lead.get("stage", "qualificacao"))
        await _agendar_resgate_silencio(tenant, tenant_id, identifier, canal, phone, ig_user_id, lead_id)
        return {
            "response":  resposta_lgpd,
            "stage":     lead.get("stage", "qualificacao"),
            "canal":     canal,
            "tenant_id": tenant_id,
            "lead_id":   lead_id,
        }

    system_prompt = await _get_system_prompt(tenant, canal)
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
            if texto and salvar_resposta:  # guard: não salvar resposta vazia
                mem.save_message(tenant_id, identifier, "assistant", texto)
            mem.update_session(tenant_id, identifier, lead.get("stage", "qualificacao"))
            await _agendar_resgate_silencio(tenant, tenant_id, identifier, canal, phone, ig_user_id, lead_id)
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
    await _agendar_resgate_silencio(tenant, tenant_id, identifier, canal, phone, ig_user_id, lead_id)
    return {
        "response":  "Desculpe, ocorreu um erro interno. Tente novamente.",
        "stage":     "qualificacao",
        "canal":     canal,
        "tenant_id": tenant_id,
        "lead_id":   lead_id,
    }
