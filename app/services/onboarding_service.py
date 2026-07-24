# app/services/onboarding_service.py
# Recebe o formulário de cadastro de um cliente novo (preenchido por ele mesmo, via
# link fixo protegido por segredo compartilhado — ver ONBOARDING_SECRET) e grava tenant
# + catálogo de serviços + FAQ direto no Supabase, sem passar por inserção manual.
#
# O que este endpoint NÃO faz: não mexe em phone_number_id nem token do WhatsApp — isso
# continua sendo passo manual (registrar o número na Meta), porque depende de coisa que
# só quem administra a conta Meta consegue fazer. O formulário cobre só o CONTEÚDO da
# clínica; a fiação técnica do WhatsApp é feita à parte, depois.

import re
import unicodedata
import memory as mem


def _slugificar(texto: str) -> str:
    """Vira um 'name' de tenant válido: minúsculo, sem acento, hífen no lugar de espaço.
    'Clínica Bela Estética' -> 'clinica-bela-estetica'."""
    sem_acento = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", sem_acento.lower()).strip("-")
    return slug or "clinica"


def _slug_disponivel(sb, slug: str, ignorar_tenant_id: str | None = None) -> str:
    """Garante slug único: se 'clinica-bela' já existir (de outro tenant), tenta
    'clinica-bela-2', 'clinica-bela-3'... Evita duas clínicas colidirem no mesmo name
    só porque têm nome parecido."""
    candidato = slug
    sufixo = 2
    while True:
        existente = (sb.table("tenants").select("id").eq("name", candidato).execute()).data
        if not existente or (ignorar_tenant_id and existente[0]["id"] == ignorar_tenant_id):
            return candidato
        candidato = f"{slug}-{sufixo}"
        sufixo += 1


def _validar_payload(payload: dict) -> list[str]:
    """Devolve a lista de problemas encontrados (vazia = payload ok). Validação mínima
    de negócio, não de schema completo — o formulário do lado do Lovable já deve exigir
    os campos, isso aqui é a segunda trava, do lado do servidor."""
    erros = []
    if not (payload.get("clinic_name") or "").strip():
        erros.append("clinic_name é obrigatório")
    if not (payload.get("professional_name") or "").strip():
        erros.append("professional_name é obrigatório")
    if not (payload.get("staff_phone") or "").strip():
        erros.append("staff_phone é obrigatório")

    for i, s in enumerate(payload.get("servicos") or []):
        if not (s.get("nome") or "").strip():
            erros.append(f"servicos[{i}].nome é obrigatório")
        if s.get("duracao_min") is not None and int(s.get("duracao_min") or 0) <= 0:
            erros.append(f"servicos[{i}].duracao_min precisa ser positivo")

    for i, f in enumerate(payload.get("faq") or []):
        if not (f.get("pergunta") or "").strip() or not (f.get("resposta") or "").strip():
            erros.append(f"faq[{i}] precisa de pergunta e resposta")

    for i, r in enumerate(payload.get("recall") or []):
        if not (r.get("servico") or "").strip():
            erros.append(f"recall[{i}].servico é obrigatório")
        if r.get("dias") is None or int(r.get("dias") or 0) <= 0:
            erros.append(f"recall[{i}].dias precisa ser positivo")

    for i, c in enumerate(payload.get("cross_sell") or []):
        if not (c.get("servico_feito") or "").strip():
            erros.append(f"cross_sell[{i}].servico_feito é obrigatório")
        if not (c.get("oferecer") or "").strip():
            erros.append(f"cross_sell[{i}].oferecer é obrigatório")
        if c.get("dias") is None or int(c.get("dias") or 0) <= 0:
            erros.append(f"cross_sell[{i}].dias precisa ser positivo")

    return erros


def _avisos_recall_cross_sell(payload: dict) -> list[str]:
    """Não bloqueia o envio, só avisa: recall/cross-sell casam por nome do serviço
    (substring, ver _casar_regra_procedimento em tools.py) — se o nome digitado aqui
    não bater com nenhum serviço da lista, a regra nunca vai disparar, em silêncio.
    Pega isso na hora do cadastro em vez de deixar a clínica descobrir 6 meses depois
    que o recall nunca funcionou."""
    nomes = [(s.get("nome") or "").strip().lower() for s in (payload.get("servicos") or [])]

    def _bate(alvo: str) -> bool:
        alvo = alvo.strip().lower()
        return any(alvo and nome and (nome in alvo or alvo in nome) for nome in nomes)

    avisos = []
    for r in payload.get("recall") or []:
        servico = r.get("servico") or ""
        if servico and not _bate(servico):
            avisos.append(f"recall: '{servico}' não bate com nenhum serviço cadastrado — a regra não vai disparar")
    for c in payload.get("cross_sell") or []:
        servico = c.get("servico_feito") or ""
        if servico and not _bate(servico):
            avisos.append(f"cross_sell: '{servico}' não bate com nenhum serviço cadastrado — a regra não vai disparar")
    return avisos


def processar_intake(payload: dict) -> dict:
    """Cria (ou atualiza, se o slug já existir) o tenant, e substitui por completo os
    serviços e o FAQ pelos que vieram no formulário.

    Substituir em vez de mesclar é proposital: o formulário representa o estado atual
    completo da clínica. Se ela reenviar corrigindo um preço, mesclar deixaria lixo
    órfão de serviços removidos; substituir mantém o catálogo exatamente igual ao que
    ela informou por último.
    """
    erros = _validar_payload(payload)
    if erros:
        return {"success": False, "erros": erros}

    sb = mem.get_client()

    slug_pedido = (payload.get("tenant_slug") or "").strip() or payload["clinic_name"]
    slug = _slugificar(slug_pedido)

    existente = (sb.table("tenants").select("id").eq("name", slug).execute()).data
    tenant_id = existente[0]["id"] if existente else None
    slug = _slug_disponivel(sb, slug, ignorar_tenant_id=tenant_id)

    campos_tenant = {
        "name": slug,
        "clinic_name": payload["clinic_name"].strip(),
        "professional_name": payload["professional_name"].strip(),
        "staff_phone": payload["staff_phone"].strip(),
        "ativo": True,
    }
    if payload.get("horarios"):
        campos_tenant["horarios"] = payload["horarios"]

    # Mesmo tratamento "substitui por completo" dos serviços/FAQ: o formulário é o
    # estado atual da clínica. Grava {} (não None) quando vazio, pra reenvio sem
    # recall/cross-sell realmente limpar regras antigas, em vez de preservar lixo.
    campos_tenant["procedimentos_recall"] = {
        r["servico"].strip(): int(r["dias"])
        for r in (payload.get("recall") or [])
        if (r.get("servico") or "").strip() and r.get("dias") is not None
    }
    campos_tenant["cross_sell"] = {
        c["servico_feito"].strip(): {"oferecer": c["oferecer"].strip(), "dias": int(c["dias"])}
        for c in (payload.get("cross_sell") or [])
        if (c.get("servico_feito") or "").strip() and (c.get("oferecer") or "").strip() and c.get("dias") is not None
    }

    if tenant_id:
        sb.table("tenants").update(campos_tenant).eq("id", tenant_id).execute()
    else:
        row = sb.table("tenants").insert(campos_tenant).execute()
        tenant_id = row.data[0]["id"]

    # Substitui o catálogo por completo — ver docstring.
    sb.table("services").delete().eq("tenant_id", tenant_id).execute()
    servicos_para_gravar = [
        {
            "tenant_id": tenant_id,
            "nome": s["nome"].strip(),
            "descricao": (s.get("descricao") or "").strip() or None,
            "preco": s.get("preco"),
            "preco_a_partir_de": bool(s.get("preco_a_partir_de", False)),
            "duracao_min": int(s.get("duracao_min") or 60),
            "ativo": True,
        }
        for s in (payload.get("servicos") or [])
    ]
    if servicos_para_gravar:
        sb.table("services").insert(servicos_para_gravar).execute()

    sb.table("faq").delete().eq("tenant_id", tenant_id).execute()
    faq_para_gravar = [
        {
            "tenant_id": tenant_id,
            "pergunta": f["pergunta"].strip(),
            "resposta": f["resposta"].strip(),
            "ordem": i,
            "ativo": True,
        }
        for i, f in enumerate(payload.get("faq") or [])
    ]
    if faq_para_gravar:
        sb.table("faq").insert(faq_para_gravar).execute()

    resultado = {
        "success": True,
        "tenant_id": tenant_id,
        "tenant_slug": slug,
        "servicos_gravados": len(servicos_para_gravar),
        "faq_gravado": len(faq_para_gravar),
        "recall_gravado": len(campos_tenant["procedimentos_recall"]),
        "cross_sell_gravado": len(campos_tenant["cross_sell"]),
        # phone_number_id fica de fora de propósito — é passo manual, feito depois
        # de registrar o número na Meta.
        "proximo_passo": "Registrar o número de WhatsApp da clínica e vincular phone_number_id a este tenant.",
    }
    avisos = _avisos_recall_cross_sell(payload)
    if avisos:
        resultado["avisos"] = avisos
    return resultado
