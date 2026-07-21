# Estrutura — Produto Real

## Diferença da Demo
- Demo: agente simples, memória em dicionário Python
- Produto Real: orquestrador, Supabase multi-tenant, funções especializadas

## O que já está pronto
- Prompts base nos templates
- Estrutura FastAPI
- COMO_ADAPTAR.md
- DADOS_DO_CLIENTE.md

## Status atual

**Task 3 do plano de implementação (docs/superpowers/plans/2026-04-19-automacao-completa.md) está concluída.**
O orquestrador antigo baseado em `STAGE:` regex (`orchestrator.py`, `functions.py`, `prompts.py` na raiz)
foi removido — eram código morto, não referenciados por `main.py` desde a migração para `tool_use`.

O que essas funções (`checar_disponibilidade`, `criar_agendamento`, `enviar_link_pagamento`,
`fazer_followup`, `escalar_humano`) viraram no sistema atual:

| Função antiga (removida) | Tool atual |
|---|---|
| `checar_disponibilidade` | `check_availability` em `app/agent/tools.py` |
| `criar_agendamento` | `book_appointment` em `app/agent/tools.py` |
| `enviar_link_pagamento` | `generate_payment_link` em `app/agent/tools.py` |
| `fazer_followup` | `schedule_followup` em `app/agent/tools.py` |
| `escalar_humano` | `escalate_to_human` em `app/agent/tools.py` |

O orquestrador ativo é `app/agent/claude_client.py` (`processar_mensagem()`), que despacha as tools
via `execute_tool()` em `app/agent/tools.py` — importado por `main.py`.

## O que falta buildar ao fechar primeiro cliente
- WhatsApp Cloud API oficial com Templates para follow-up
- Painel Lovable + Supabase com login multi-tenant

## Schema Supabase
- tenants: id, name, clinic_name, professional_name, payment_link, whatsapp_token, ativo
- leads: id, tenant_id, phone, name, stage, procedimento, data_agendamento
- sessions: id, tenant_id, phone, messages, stage, last_activity
- conversations: id, tenant_id, phone, role, content, created_at

## Como entregar ao cliente
1. Preencher DADOS_DO_CLIENTE.md
2. Criar tenant no Supabase
3. Adaptar prompts com COMO_ADAPTAR.md
4. Subir no Railway
5. Conectar WhatsApp Cloud API
6. Testar fluxo completo
7. Entregar
