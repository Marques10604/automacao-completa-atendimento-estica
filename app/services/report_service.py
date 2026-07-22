# app/services/report_service.py
# Relatório sob demanda pro dono da clínica.
#
# O dono manda "relatório" no WhatsApp do atendimento e recebe os números do dia.
# Puxar (dono pede) em vez de empurrar (sistema envia num horário fixo) resolve de
# graça o maior obstáculo do WhatsApp: mensagem iniciada pela empresa fora da janela
# de 24h exige template HSM aprovado pela Meta, e o dono nunca manda mensagem pro
# próprio número — a janela dele viveria fechada. Como ele inicia a conversa, a
# janela abre e a resposta em texto livre é permitida.
#
# Os números vêm direto do Supabase, nunca do modelo: relatório com número inventado
# destrói a confiança do cliente no produto inteiro.

import logging
import unicodedata
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import memory as mem

logger = logging.getLogger(__name__)

FORTALEZA_TZ = ZoneInfo("America/Fortaleza")

# Quantos dias à frente contam como "próximos agendamentos" no resumo.
DIAS_JANELA_FUTURA = 7

# Mínimo de dígitos pra arriscar comparar dois telefones. Abaixo disso a chance de
# dois números diferentes coincidirem no sufixo é alta demais pra liberar relatório.
_MIN_DIGITOS_TELEFONE = 10


def _normalizar(texto: str) -> str:
    """Minúsculas e sem acento — o dono pode escrever "relatório", "Relatorio" ou
    "RESUMO" e todas têm que funcionar."""
    limpo = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    return limpo.strip().lower()


def _so_digitos(valor: str | None) -> str:
    return "".join(c for c in (valor or "") if c.isdigit())


def remetente_e_staff(phone: str, tenant: dict) -> bool:
    """Diz se quem mandou a mensagem é o dono/equipe da clínica.

    Compara só os dígitos e pelo sufixo: staff_phone é preenchido à mão no Supabase e
    costuma vir com "+", espaço ou hífen, enquanto a Meta entrega só dígitos — e um
    dos dois lados pode ou não trazer o 55 do país.
    """
    staff = _so_digitos(tenant.get("staff_phone"))
    remetente = _so_digitos(phone)
    if not staff or not remetente:
        return False

    n = min(len(staff), len(remetente))
    if n < _MIN_DIGITOS_TELEFONE:
        return False
    return staff[-n:] == remetente[-n:]


def detectar_comando_relatorio(mensagem: str) -> str | None:
    """Devolve o período pedido ("hoje", "ontem", "semana") ou None se a mensagem não
    for um pedido de relatório.

    Exige a palavra "relatorio" ou "resumo" de propósito: sem isso, qualquer mensagem
    do dono viraria relatório e ele não conseguiria testar o agente como se fosse um
    cliente.
    """
    texto = _normalizar(mensagem)
    if not any(palavra in texto for palavra in ("relatorio", "resumo")):
        return None
    if "ontem" in texto:
        return "ontem"
    if "semana" in texto:
        return "semana"
    return "hoje"


# Título do cabeçalho por período. Separado do rótulo usado no corpo porque
# "Resumo de hoje" e "Nenhum movimento hoje" pedem construções diferentes.
_TITULOS = {"hoje": "de hoje", "ontem": "de ontem", "semana": "dos últimos 7 dias"}


def _intervalo(periodo: str) -> tuple[datetime, datetime, str]:
    """Início (inclusivo) e fim (exclusivo) do período, em horário de Fortaleza."""
    agora = datetime.now(FORTALEZA_TZ)
    hoje = agora.replace(hour=0, minute=0, second=0, microsecond=0)

    if periodo == "ontem":
        return hoje - timedelta(days=1), hoje, "ontem"
    if periodo == "semana":
        return hoje - timedelta(days=6), hoje + timedelta(days=1), "nos últimos 7 dias"
    return hoje, hoje + timedelta(days=1), "hoje"


def _contar(query) -> int:
    """Executa uma query de contagem. Um número que não deu pra apurar vira 0 e fica no
    log — melhor um relatório com uma linha zerada do que nenhum relatório."""
    try:
        r = query.execute()
        return r.count if r.count is not None else len(r.data or [])
    except Exception as e:
        logger.error("Falha ao contar métrica do relatório: %s", e)
        return 0


def montar_relatorio(tenant: dict, periodo: str) -> str:
    """Monta o texto do relatório. Síncrono (o client do Supabase é síncrono) —
    quem chama roda em asyncio.to_thread pra não travar o event loop.

    O texto usa só quebras simples de linha: send_whatsapp divide a mensagem em bolhas
    a cada linha em branco, e um relatório precisa chegar como uma mensagem só.
    """
    sb = mem.get_client()
    tenant_id = str(tenant["id"])
    inicio, fim, rotulo = _intervalo(periodo)
    ini_iso, fim_iso = inicio.isoformat(), fim.isoformat()

    leads_novos = _contar(
        sb.table("leads").select("id", count="exact")
        .eq("tenant_id", tenant_id)
        .gte("created_at", ini_iso).lt("created_at", fim_iso)
    )
    agendamentos = _contar(
        sb.table("appointments").select("id", count="exact")
        .eq("tenant_id", tenant_id)
        .is_("cancelled_at", "null")
        .gte("created_at", ini_iso).lt("created_at", fim_iso)
    )
    cancelamentos = _contar(
        sb.table("appointments").select("id", count="exact")
        .eq("tenant_id", tenant_id)
        .gte("cancelled_at", ini_iso).lt("cancelled_at", fim_iso)
    )
    aguardando_humano = _contar(
        sb.table("leads").select("id", count="exact")
        .eq("tenant_id", tenant_id)
        .eq("escalado", True)
    )

    agora_utc = datetime.now(timezone.utc)
    proximos = _contar(
        sb.table("appointments").select("id", count="exact")
        .eq("tenant_id", tenant_id)
        .is_("cancelled_at", "null")
        .gte("scheduled_at", agora_utc.isoformat())
        .lt("scheduled_at", (agora_utc + timedelta(days=DIAS_JANELA_FUTURA)).isoformat())
    )

    clinica = tenant.get("clinic_name") or tenant.get("name") or "sua clínica"
    linhas = [f"📊 Resumo {_TITULOS.get(periodo, _TITULOS['hoje'])} — {clinica}"]

    if leads_novos == 0 and agendamentos == 0 and cancelamentos == 0:
        # O dono pediu explicitamente: responder "nada aconteceu" é informação, silêncio
        # pareceria defeito.
        linhas.append(f"Nenhum movimento {rotulo}: nenhum lead novo, agendamento ou cancelamento.")
    else:
        linhas.append(f"Leads novos: {leads_novos}")
        if leads_novos > 0:
            taxa = round(agendamentos / leads_novos * 100)
            linhas.append(f"Agendamentos: {agendamentos} ({taxa}% dos leads)")
        else:
            linhas.append(f"Agendamentos: {agendamentos}")
        linhas.append(f"Cancelamentos: {cancelamentos}")

    if aguardando_humano > 0:
        linhas.append(f"⚠️ Aguardando você assumir: {aguardando_humano}")

    linhas.append(f"Próximos {DIAS_JANELA_FUTURA} dias: {proximos} agendamento(s) confirmado(s)")
    if periodo == "hoje":
        # Dica só no relatório padrão — repetir em todo pedido viraria ruído.
        linhas.append('Peça "resumo ontem" ou "resumo semana" para outros períodos.')

    return "\n".join(linhas)
