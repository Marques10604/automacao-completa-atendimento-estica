# Dashboard — Integração com dados reais

**Data:** 2026-08-02
**Repositório do dashboard:** https://github.com/Marques10604/aesthetic-dashboard-ai (gerado no Lovable, TanStack Start / React 19 / Vite / SSR)
**Backend:** este projeto (`main.py`, FastAPI), já em produção no Railway

## Contexto

O dashboard hoje (`src/lib/mock-leads.ts`) é 100% dados fictícios — nenhuma chamada de API, nenhum conceito de lead "escalado pra humano". O backend já expõe tudo que é necessário:

- `GET /leads/{tenant_name}` — retorna leads com todas as colunas (`status`, `canal`, `escalado`, `motivo_escalonamento`, `escalado_at`, `created_at`), protegido por header `x-admin-key`.
- `_processar_e_responder_whatsapp()` (main.py:266-268) já respeita `escalado`: se `true`, a IA fica muda.

Não existe hoje botão de "escalar manual" — escalação só acontece automaticamente (falha do agente ou a tool `escalate_to_human`). Decisão consciente: **não construir escalar nem desescalar manual agora** (ver decisão 4 abaixo).

## Decisões

1. **Single-tenant por enquanto.** Tenant fixo via env var (`TENANT_SLUG`), sem login/seleção de clínica. Generalizar pra multi-tenant é trabalho futuro, fora de escopo aqui.
2. **Hospedagem: Railway, junto com o backend.** O dashboard roda como servidor Node real (SSR do TanStack Start já suporta isso via `server.ts`/`start.ts`), não como export estático — isso é o que permite esconder a `x-admin-key`.
3. **Toda chamada ao backend acontece no servidor do dashboard (loader/server function), nunca no navegador.** O browser do cliente final recebe só os dados já processados/formatados — a `x-admin-key` nunca trafega pra fora do servidor.
4. **Sem botão de escalar nem de desescalar (`Devolver pra IA`) manual — decisão tomada em 2026-08-06.** Construir isso agora não tem retorno: ainda estamos em fase de teste, sem cliente pagante. O plano real pra escalação humana é diferente do dashboard: ao fechar um cliente, o dinheiro do setup financia a instalação de um Chatwoot próprio numa VPS do cliente — é o Chatwoot que vai resolver o handoff humano quando isso importar de verdade, não um botão customizado aqui. Revisitar quando essa oferta com Chatwoot existir.
5. **KPI novo: Taxa de resolução automática** (containment rate) — % de leads com `escalado=false` sobre o total. Métrica #1 em relevância segundo pesquisa de mercado (ver Sources abaixo) — reforça o argumento de venda "vendedor que nunca dorme, só interrompido quando realmente precisa". **Substitui** o KPI mock "Recuperado por follow-up automático" (não "soma" a ele) — o grid continua com 4 cards.
6. **Sininho do topbar passa a refletir dado real** — hoje mockado como "3", passa a mostrar `count(leads.escalado=true)`.
7. **KPI "Recuperado por follow-up automático" (R$) fica fora do V1 — decisão tomada em 2026-08-03.** Dois motivos, não um: (a) o produto está pausando o envio automático de link de pagamento pelo agente — nem toda clínica vai querer isso, e sem link não existe "recuperação de pagamento" pra medir; (b) mesmo reformulado como contagem (leads que remarcaram/agendaram avaliação ou fizeram o recall por causa de um follow-up), calcular isso exigiria cruzar `appointments` + `followup_jobs` por lead, e `GET /leads/{tenant_name}` não expõe essas duas tabelas — precisaria de endpoint novo no backend, fora de escopo hoje. Revisitar quando o backend expuser esses dados.

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
| `src/lib/backend-client.ts` (novo) | Cliente server-only: `getLeads()`. Lê `BACKEND_URL`/`ADMIN_API_KEY`/`TENANT_SLUG` do `process.env`. Nunca importado por código que roda no browser. |
| `src/lib/dashboard-metrics.ts` (novo) | Funções puras que recebem `Lead[]` e calculam KPIs (leads atendidos, agendamentos, taxa de fechamento, recuperado, taxa de resolução automática, série dos últimos 30 dias, top serviços, contagem de escalados). Mantém a UI desacoplada de como os dados chegam — testável sem rede. |
| `src/routes/index.tsx` | Ganha um loader (server-side) que chama `getLeads()` e `dashboard-metrics`, passa os dados prontos pros componentes via props em vez de importar `mock-leads` direto. |
| `src/components/dashboard/kpi-cards.tsx` | Recebe KPIs via props em vez de importar de `mock-leads`. Os 4 cards passam a ser: leads atendidos, agendamentos confirmados, taxa de fechamento, taxa de resolução automática (substitui "Recuperado por follow-up automático", que sai do V1). |
| `src/components/dashboard/topbar.tsx` | Recebe contagem de escalados via prop; sininho mostra o número real (ou fica sem badge se `0`). |
| `src/components/dashboard/leads-table.tsx` | Recebe `leads` via props. Linhas com `escalado=true` ganham badge "Aguardando humano" (somente leitura — sem botão de ação). |
| `src/components/dashboard/leads-chart.tsx`, `top-services.tsx`, `highlight-band.tsx` | Recebem dados via props em vez de importar `mock-leads` direto. `highlight-band` (textos como "184 contatos", "23 fora do horário") passa a usar números reais computados. |

## Tratamento de erro / estados vazios

- Backend indisponível no load da página: dashboard mostra estado vazio com mensagem "Não foi possível carregar os dados agora" em vez de quebrar (sem try/catch silencioso — loga o erro no servidor).
- Zero leads (tenant novo): KPIs mostram `0` / `—`, sem gráfico quebrado.
- `ADMIN_API_KEY`/`BACKEND_URL` ausentes: falha rápido e visível no boot do servidor (mesmo padrão que `app/config.py` já usa no backend — nunca silencioso).

## Fora de escopo (explícito)

- Multi-tenant / login / seleção de clínica.
- Botão de escalar manual (sem Chatwoot ainda, não há onde o humano responder).
- Botão de desescalar ("Devolver pra IA") — ver decisão 4 acima. Sem retorno construir agora, em teste; o handoff humano real vai ser resolvido via Chatwoot por cliente, financiado pelo próprio pagamento de setup do cliente, quando essa oferta existir.
- Exportar relatório semanal (`weekly-report.ts`) — já existe e continua funcionando, não mexe.
- Navegação da sidebar ("Leads", "Agendamentos", "Configurações") — continuam como estão (não funcionais), fora de escopo desta rodada.
- KPI "Recuperado por follow-up automático" (R$ ou contagem) — ver decisão 7 acima. Envio automático de link de pagamento pelo agente também está pausado no produto como um todo (decisão de negócio, não só do dashboard) — nem toda clínica vai querer isso habilitado.
- Sinalizador de "sinal de pagamento" ou botão manual pro dono da clínica marcar "cliente fez o procedimento" — considerado e descartado por enquanto (2026-08-03).

## Sources (pesquisa de mercado usada pra validar a métrica nova)

- [AI Chatbot KPIs: 15 Metrics That Actually Matter in 2026](https://heeya.fr/en/blog/ai-chatbot-kpis-metrics-guide-2026)
- [Enterprise Chatbot KPIs and Metrics to Track in 2026](https://viston.tech/enterprise-chatbot-kpis-and-metrics-what-businesses-should-track-in-2026/)
- [Como a IA transforma o atendimento em clínicas estéticas](https://blog.valentinsdigital.com.br/post/ia-transforma-atendimento-clinicas-esteticas)
- [Automação para WhatsApp em clínicas: Guia completo](https://clickmassa.com.br/automacao-para-whatsapp-em-clinicas-guia-completo-para-melhorar-o-atendimento/)
