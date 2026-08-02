# Dashboard — Integração com dados reais + botão de desescalar

**Data:** 2026-08-02
**Repositório do dashboard:** https://github.com/Marques10604/aesthetic-dashboard-ai (gerado no Lovable, TanStack Start / React 19 / Vite / SSR)
**Backend:** este projeto (`main.py`, FastAPI), já em produção no Railway

## Contexto

O dashboard hoje (`src/lib/mock-leads.ts`) é 100% dados fictícios — nenhuma chamada de API, nenhum conceito de lead "escalado pra humano". O backend já expõe tudo que é necessário:

- `GET /leads/{tenant_name}` — retorna leads com todas as colunas (`status`, `canal`, `escalado`, `motivo_escalonamento`, `escalado_at`, `created_at`), protegido por header `x-admin-key`.
- `PATCH /lead/{tenant_name}/{phone}/desescalar` — devolve o lead pra IA responder de novo (`leads.escalado = false`).
- `_processar_e_responder_whatsapp()` (main.py:266-268) já respeita `escalado`: se `true`, a IA fica muda.

Não existe hoje botão de "escalar manual" — escalação só acontece automaticamente (falha do agente ou a tool `escalate_to_human`). Decisão consciente: **não construir escalar manual agora**, porque sem um chat unificado (tipo Chatwoot) não haveria onde o humano responder — o atendimento manual acontece fora do sistema (WhatsApp do celular da atendente). O botão de desescalar é exatamente o "encerrar esse desvio e devolver o controle pra IA".

## Decisões

1. **Single-tenant por enquanto.** Tenant fixo via env var (`TENANT_SLUG`), sem login/seleção de clínica. Generalizar pra multi-tenant é trabalho futuro, fora de escopo aqui.
2. **Hospedagem: Railway, junto com o backend.** O dashboard roda como servidor Node real (SSR do TanStack Start já suporta isso via `server.ts`/`start.ts`), não como export estático — isso é o que permite esconder a `x-admin-key`.
3. **Toda chamada ao backend acontece no servidor do dashboard (loader/server function), nunca no navegador.** O browser do cliente final recebe só os dados já processados/formatados — a `x-admin-key` nunca trafega pra fora do servidor.
4. **Sem botão de escalar manual.** Fora de escopo (ver Contexto acima).
5. **5º KPI: Taxa de resolução automática** (containment rate) — % de leads com `escalado=false` sobre o total. Métrica #1 em relevância segundo pesquisa de mercado (ver Sources abaixo) — reforça o argumento de venda "vendedor que nunca dorme, só interrompido quando realmente precisa".
6. **Sininho do topbar passa a refletir dado real** — hoje mockado como "3", passa a mostrar `count(leads.escalado=true)`.

## Arquitetura

```
Navegador do cliente
      │  (HTML/JSON já processado, sem segredos)
      ▼
Servidor SSR do dashboard (Railway, TanStack Start)
      │  fetch com header x-admin-key (env vars: BACKEND_URL, ADMIN_API_KEY, TENANT_SLUG)
      ▼
Backend FastAPI (Railway, já em produção)
      │
      ▼
Supabase (leads, appointments, followup_jobs)
```

### Novas env vars do dashboard (só server-side, nunca `VITE_*`/prefixo público)
- `BACKEND_URL` — ex: `https://automacao-completa-atendimento-estica-production.up.railway.app`
- `ADMIN_API_KEY` — mesmo valor já configurado no backend
- `TENANT_SLUG` — ex: `lumina`

## Componentes afetados

| Arquivo | Mudança |
|---|---|
| `src/lib/mock-leads.ts` | Mantém tipos (`Lead`, `LeadStatus`, `LeadCanal`) e formatadores (`formatBRL`, `formatData`, `statusLabel`). Remove os arrays de dados fictícios (`leads`, `kpis`, `leadsSerie`, `servicosMaisProcurados`) — esses agora vêm da API. |
| `src/lib/backend-client.ts` (novo) | Cliente server-only: `getLeads()`, `desescalarLead(phone)`. Lê `BACKEND_URL`/`ADMIN_API_KEY`/`TENANT_SLUG` do `process.env`. Nunca importado por código que roda no browser. |
| `src/lib/dashboard-metrics.ts` (novo) | Funções puras que recebem `Lead[]` e calculam KPIs (leads atendidos, agendamentos, taxa de fechamento, recuperado, taxa de resolução automática, série dos últimos 30 dias, top serviços, contagem de escalados). Mantém a UI desacoplada de como os dados chegam — testável sem rede. |
| `src/routes/index.tsx` | Ganha um loader (server-side) que chama `getLeads()` e `dashboard-metrics`, passa os dados prontos pros componentes via props em vez de importar `mock-leads` direto. |
| `src/components/dashboard/kpi-cards.tsx` | Recebe KPIs via props em vez de importar de `mock-leads`. Ganha o 5º card "Taxa de resolução automática". |
| `src/components/dashboard/topbar.tsx` | Recebe contagem de escalados via prop; sininho mostra o número real (ou fica sem badge se `0`). |
| `src/components/dashboard/leads-table.tsx` | Recebe `leads` via props. Linhas com `escalado=true` ganham badge "Aguardando humano" + botão "Devolver pra IA". Botão dispara uma server function que chama `desescalarLead(phone)` e revalida a rota. |
| `src/components/dashboard/leads-chart.tsx`, `top-services.tsx`, `highlight-band.tsx` | Recebem dados via props em vez de importar `mock-leads` direto. `highlight-band` (textos como "184 contatos", "23 fora do horário") passa a usar números reais computados. |

## Fluxo do botão "Devolver pra IA"

1. Atendente clica no botão na linha do lead escalado.
2. Server function (`app/agent`-style, roda no servidor do dashboard) chama `PATCH {BACKEND_URL}/lead/{TENANT_SLUG}/{phone}/desescalar` com `x-admin-key`.
3. Backend responde `{"status": "ok", "escalado": false}` ou 404 se o lead não existir mais.
4. Dashboard revalida a lista de leads (refaz o loader) — a linha desaparece da lista de "aguardando humano" e o sininho decrementa.
5. Erro (rede, 401, 404): mostra toast de erro (já tem `sonner` instalado no projeto) sem quebrar a página.

## Tratamento de erro / estados vazios

- Backend indisponível no load da página: dashboard mostra estado vazio com mensagem "Não foi possível carregar os dados agora" em vez de quebrar (sem try/catch silencioso — loga o erro no servidor).
- Zero leads (tenant novo): KPIs mostram `0` / `—`, sem gráfico quebrado.
- `ADMIN_API_KEY`/`BACKEND_URL` ausentes: falha rápido e visível no boot do servidor (mesmo padrão que `app/config.py` já usa no backend — nunca silencioso).

## Fora de escopo (explícito)

- Multi-tenant / login / seleção de clínica.
- Botão de escalar manual (sem Chatwoot ainda, não há onde o humano responder).
- Exportar relatório semanal (`weekly-report.ts`) — já existe e continua funcionando, não mexe.
- Navegação da sidebar ("Leads", "Agendamentos", "Configurações") — continuam como estão (não funcionais), fora de escopo desta rodada.

## Sources (pesquisa de mercado usada pra validar a métrica nova)

- [AI Chatbot KPIs: 15 Metrics That Actually Matter in 2026](https://heeya.fr/en/blog/ai-chatbot-kpis-metrics-guide-2026)
- [Enterprise Chatbot KPIs and Metrics to Track in 2026](https://viston.tech/enterprise-chatbot-kpis-and-metrics-what-businesses-should-track-in-2026/)
- [Como a IA transforma o atendimento em clínicas estéticas](https://blog.valentinsdigital.com.br/post/ia-transforma-atendimento-clinicas-esteticas)
- [Automação para WhatsApp em clínicas: Guia completo](https://clickmassa.com.br/automacao-para-whatsapp-em-clinicas-guia-completo-para-melhorar-o-atendimento/)
