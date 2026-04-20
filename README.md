# Produto Real — Agente de Atendimento IA

Versão de produção do agente. Multi-tenant, persistência no Supabase, orquestrador central com funções especializadas.

> **Escopo claro:** este agente **qualifica leads e agenda consultas via WhatsApp**. Ele NÃO fecha venda, NÃO envia link de pagamento e NÃO tem integração com Instagram.

---

## O que faz

| Estágio | O que acontece |
|---|---|
| `qualificacao` | Colhe nome e interesse da cliente |
| `apresentacao` | Sugere tratamentos adequados ao perfil |
| `agendamento` | Confirma dia e horário — encerra com "Nossa equipe vai entrar em contato para finalizar" |
| `escalado` | Marca lead para atendimento humano e notifica a equipe |

### O que este agente NÃO faz
- ❌ Não envia link de pagamento
- ❌ Não cobra sinal
- ❌ Não tem pós-venda automatizado
- ❌ Não integra com Instagram
- ❌ Não fecha a venda — a equipe humana finaliza após o agendamento

---

## Arquitetura

```
WhatsApp
    ↓
POST /webhook/whatsapp  (main.py)
    ↓
Identifica tenant pelo phone_number_id
    ↓
orchestrator.py
  ├── Lê estágio do lead no Supabase   (memory.py)
  ├── Busca histórico da conversa       (memory.py)
  ├── Monta prompt com dados do tenant  (prompts.py)
  ├── Chama Claude API com prompt caching
  ├── Extrai novo estágio
  └── Executa função especializada      (functions.py)
        ├── qualificar_lead
        ├── apresentar_servicos
        ├── checar_disponibilidade
        ├── criar_agendamento
        ├── enviar_lembrete_24h
        └── escalar_humano
```

---

## Estrutura do projeto

```
Produto Real/
├── database/
│   └── schema.sql        # Tabelas Supabase + RLS + triggers
├── main.py               # FastAPI — /chat, /webhook/whatsapp, /tenants, /leads
├── orchestrator.py       # Orquestrador central
├── functions.py          # Funções especializadas por estágio
├── prompts.py            # Template de prompt configurável por tenant
├── memory.py             # Acesso ao Supabase
├── requirements.txt
├── .env.example
└── README.md
```

---

## Configuração

### 1. Supabase
1. Crie um projeto em [supabase.com](https://supabase.com)
2. Execute `database/schema.sql` no SQL Editor
3. Copie a URL e a service_role key em Settings → API

### 2. .env
```bash
cp .env.example .env
```
```env
ANTHROPIC_API_KEY=sk-ant-...
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_SERVICE_ROLE_KEY=eyJ...
VERIFY_TOKEN=qualquer_string_secreta
```

### 3. Adicionar tenant
```sql
INSERT INTO tenants (name, clinic_name, professional_name, phone_number_id, whatsapp_token)
VALUES ('minha-clinica', 'Clínica X', 'Dra. Y', '123456789', 'EAAxxxx');
```

### 4. Rodar
```bash
python -m pip install -r requirements.txt
python main.py
```

### 5. Testar
```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"tenant_name": "minha-clinica", "phone": "5585999999999", "message": "oi"}'
```

---

## Endpoints

| Método | Rota | Descrição |
|---|---|---|
| GET | `/` | Status + total de tenants ativos |
| POST | `/chat` | Teste direto por tenant_name |
| GET | `/webhook/whatsapp` | Verificação Meta |
| POST | `/webhook/whatsapp` | Recebe mensagens WhatsApp |
| GET | `/tenants` | Lista tenants ativos |
| GET | `/leads/{tenant_name}` | Lista leads do tenant |
| DELETE | `/lead/{tenant_name}/{phone}` | Reseta lead (testes) |

---

## Variáveis de ambiente

| Variável | Descrição | Obrigatória |
|---|---|---|
| `ANTHROPIC_API_KEY` | Chave Anthropic | Sim |
| `SUPABASE_URL` | URL do projeto Supabase | Sim |
| `SUPABASE_SERVICE_ROLE_KEY` | Chave service role | Sim |
| `VERIFY_TOKEN` | Token verificação webhook | Produção |

> `WHATSAPP_TOKEN` e `PHONE_NUMBER_ID` ficam **no Supabase por tenant**, não no `.env`.

---

## O que falta para produção

- [ ] Implementar envio de resposta via WhatsApp Cloud API em `main.py`
- [ ] Integrar Google Calendar em `functions.checar_disponibilidade`
- [ ] Configurar template de lembrete 24h no Meta Business
- [ ] Deploy no Railway (`railway up`)
- [ ] Configurar webhook: `https://seu-dominio.railway.app/webhook/whatsapp`

---

## Custo estimado

| Item | Custo |
|---|---|
| Claude API | ~R$ 0,25 por conversa |
| Supabase Free tier | Grátis até 500MB |
| Railway | ~$5/mês |
| WhatsApp Cloud API | Grátis até 1.000 conversas/mês |
