# Estrutura — Produto Real

## Diferença da Demo
- Demo: agente simples, memória em dicionário Python
- Produto Real: orquestrador, Supabase multi-tenant, funções especializadas

## O que já está pronto
- Prompts base nos templates
- Estrutura FastAPI
- COMO_ADAPTAR.md
- DADOS_DO_CLIENTE.md

## O que falta buildar ao fechar primeiro cliente
- Supabase: tabelas tenants, leads, sessions, conversations com RLS
- Orquestrador: reescrever agent.py para ler estágio do Supabase
- Funções: checar_disponibilidade, criar_agendamento, enviar_link_pagamento, fazer_followup, escalar_humano
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
