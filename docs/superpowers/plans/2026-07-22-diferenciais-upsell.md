# Diferenciais e Upsells — Roadmap aprovado (2026-07-22)

> Registro das decisões tomadas em sessão de 2026-07-22, a partir de pesquisa de mercado (ver AGENTE.md e histórico da conversa). Este documento é a fonte de verdade para o que vem depois da "Automação Completa" (docs/superpowers/plans/2026-04-19-automacao-completa.md, 100% implementada).

## Contexto que motivou o roadmap

A Meta lançou o **WhatsApp Business AI** nativo no Brasil em fev/2026 — de graça — fazendo qualificação de lead, agendamento e fechamento de venda dentro do chat, além de resumos diários pro dono do negócio. Isso comoditiza a parte "básica" do produto (qualificar + agendar via WhatsApp). O roadmap abaixo prioriza o que um agente genérico gratuito não cobre: follow-up proativo, upsell orientado a dados, ROI visível pro dono, e integração profunda com o operacional real da clínica.

## Itens aprovados (em ordem de prioridade)

1. **Transcrição de áudio (Groq Whisper)** — ✅ implementado em 2026-07-22 (ver detalhe abaixo). Falta só a `GROQ_API_KEY` em produção pra ativar.
2. **Cancelamento/remarcação self-service** — ✅ implementado em 2026-07-22. **Requer rodar `database/migration_v8.sql` no Supabase ANTES de subir o código.**
3. **Relatório pro dono via WhatsApp** — ✅ implementado em 2026-07-22. Virou **sob demanda** (dono pede) em vez de envio automático num horário fixo — ver justificativa abaixo.
4. **Cross-sell pós-procedimento** — ✅ implementado em 2026-07-22. **Requer `database/migration_v9.sql`.**
5. **Motor de referral com código rastreável** — hoje o job `pos_venda` só *pede* indicação em texto solto; falta gerar código + registrar + recompensar quando o indicado fecha.
6. **Integração com sistema de gestão da clínica** (Feegow via API aberta / Clinicorp, Shosp, iClinic via RPA quando não há API) — insight de upsell: a maioria dos sistemas de clínica no Brasil não tem API pública boa, o que torna essa integração um serviço vendável à parte (setup + manutenção recorrente), difícil de replicar por concorrentes genéricos.
7. **Pedido de review/reputação pós-venda** (D+2 pós-procedimento) — nenhum job de follow-up hoje pede avaliação.
8. **Sync com Google Calendar** — escolhido sobre Cal.com porque toda clínica já usa Google Calendar no dia a dia (zero fricção de adoção) e o produto é multi-tenant (OAuth por tenant, agenda já existente como espelho).

## Removido do roadmap

- ~~Ativar canal Instagram nativo (corrigir dedup)~~ — o ManyChat já cobre a ponta Instagram e já faz a migração de lead quente pro WhatsApp, que é exatamente o papel do tool `migrate_to_whatsapp`. Reconstruir isso é esforço duplicado enquanto o ManyChat funcionar. Só revisitar se: (a) quiserem cortar o custo da assinatura ManyChat, (b) precisarem centralizar dados de lead do IG no mesmo banco do agente, ou (c) perceberem que leads do IG estão se perdendo por nunca migrarem pro WhatsApp.

---

## Item 1: Transcrição de áudio via Groq — IMPLEMENTADO (2026-07-22)

Arquivos tocados:

1. `app/config.py` — nova setting `groq_api_key` (opcional, default `""`).
2. `app/services/transcription_service.py` (novo) — `transcrever_audio_whatsapp(media_id, wa_token)`: baixa o binário via Graph API (`GET /{media_id}` devolve URL assinada; o `GET` da URL também exige o Bearer) e manda pra Groq (`whisper-large-v3-turbo`, `language=pt` fixo, `response_format=text`). Nunca levanta exceção — devolve `""` em qualquer falha.
3. `main.py` — `_extrair_mensagem_whatsapp()` agora devolve 6-tupla, incluindo `media_id` para `audio`/`voice`. Novo branch no webhook antes do fallback de mídia; nova função `_transcrever_e_processar()`.
4. `.env.example` — documenta `GROQ_API_KEY`.

Decisões de implementação:

- **Transcrição roda em background** (`asyncio.create_task`), não inline no webhook. Baixar + transcrever leva segundos; se o 200 demorasse, a Meta reenviaria o webhook e o reenvio cairia no dedup de `wamid` — o áudio se perderia justamente por ter demorado.
- **O texto transcrito entra no mesmo debounce das mensagens digitadas**, então áudio seguido de texto na mesma rajada vira uma resposta só (não duas).
- **Degrada pro comportamento anterior**: sem `GROQ_API_KEY`, transcrição falhando, Groq fora do ar ou áudio acima de 20MB → cai no `resposta_midia_nao_suportada("audio")` que já existia. O lead nunca fica sem resposta.
- **Não mexe em `prompts.py`** — a transcrição chega no pipeline como texto comum, nenhuma instrução nova pro modelo.

Pendente pra ativar em produção: gerar a chave em `console.groq.com/keys` e setar `GROQ_API_KEY` no Railway.

---

## Item 2: Cancelamento e remarcação — IMPLEMENTADO (2026-07-22)

> ⚠️ **Ordem de deploy obrigatória:** rodar `database/migration_v8.sql` no Supabase ANTES de subir o código. O código consulta a coluna `cancelled_at`; sem a migration, `check_availability` e `book_appointment` quebram.

Bug encontrado e corrigido junto: **remarcar deixava o lembrete na data antiga.** `book_appointment` já atualizava o agendamento existente (era assim que remarcação funcionava, sem ninguém ter documentado), mas o `followup_job` de `appointment_reminder` criado pra data anterior continuava `pending` — o lead remarcava de 20/07 pra 25/07 e recebia "seu agendamento é amanhã" no dia 19/07.

Arquivos tocados:

1. `database/migration_v8.sql` (novo) — coluna `cancelled_at`; troca o `UNIQUE (tenant_id, scheduled_at)` por índice parcial `WHERE cancelled_at IS NULL`, senão o horário cancelado ficaria bloqueado pra sempre.
2. `app/agent/tools.py` — nova tool `cancel_appointment` + `_cancel_appointment()`; helpers `_cancelar_lembretes_pendentes()`, `_mesmo_instante()`, `_formatar_quando()`; `_check_availability()` e `_book_appointment()` passam a ignorar cancelados.
3. `app/agent/prompts.py` — seção "CANCELAR E REMARCAR" + `cancel_appointment` na lista de tools. **Só instrução de tool — nada de vertical/nicho, o prompt segue 100% estética.**

Decisões de implementação:

- **Cancelamento é lógico** (`cancelled_at`), não `DELETE` — o histórico é o que vira taxa de cancelamento/no-show no item 3 (resumo diário pro dono).
- **Remarcar continua sendo `book_appointment`**, não uma tool nova: ele já movia o agendamento, e criar uma segunda tool só daria ao modelo mais chance de escolher errado. A tool nova é só a de cancelar.
- **Lembrete antigo só é cancelado se a data mudou de verdade** (`_mesmo_instante` compara instantes, não strings — o Postgres devolve offset em formato diferente do que gravamos). Se o lead só reconfirma o mesmo horário, o lembrete existente é preservado.
- `followup_jobs.status` ganha o valor `'cancelled'`. **A migration_v8 precisa ampliar o CHECK** — a migration_v2 criou a coluna com `CHECK (status IN ('pending','done','failed'))`, e sem ampliar, o UPDATE é rejeitado pelo Postgres e o lembrete velho continuaria disparando. `'cancelled'` é distinto de `'done'` de propósito: `done` = lembrete enviado, `cancelled` = nunca enviado porque o agendamento mudou.

---

## Item 3: Relatório pro dono — IMPLEMENTADO (2026-07-22)

**Mudança de desenho decidida pelo dono do produto:** era pra ser envio automático num horário fixo (push). Virou **sob demanda** (pull): o dono manda "relatório" no WhatsApp do atendimento e recebe os números na hora.

Motivo — isso mata o bloqueio do template HSM. A Meta só permite texto livre dentro da janela de 24h desde a última mensagem do destinatário, e o dono da clínica nunca manda mensagem pro próprio número de negócio: a janela dele viveria fechada, e o resumo automático seria rejeitado em produção sem template aprovado. No modelo pull é o próprio dono que inicia a conversa, a janela abre e a resposta em texto livre passa. De quebra, elimina scheduler, cron e idempotência de restart — e o dono se sente no controle.

Arquivos tocados:

1. `app/services/report_service.py` (novo) — `remetente_e_staff()`, `detectar_comando_relatorio()`, `montar_relatorio()`.
2. `main.py` — branch no webhook antes de `get_or_create_lead`.

Decisões de implementação:

- **Os números vêm direto do Supabase, nunca do modelo.** Relatório com número inventado destruiria a confiança do cliente no produto inteiro. O caminho é 100% determinístico, sem chamar a Claude.
- **Exige a palavra "relatorio" ou "resumo"** — sem isso qualquer mensagem do dono viraria relatório e ele não conseguiria testar o agente se passando por cliente. Aceita "resumo ontem" e "resumo semana".
- **Fica antes de `get_or_create_lead`**: o dono não pode virar lead no funil nem sujar o histórico com mensagem administrativa.
- **Telefone comparado por sufixo, só dígitos** (mínimo 10) — `staff_phone` é digitado à mão no Supabase e vem com `+`, espaço ou hífen; a Meta entrega só dígitos, e um dos lados pode não ter o `55`.
- **Texto sem linha em branco**: `send_whatsapp` quebra a mensagem em bolhas a cada linha em branco, e relatório precisa chegar como mensagem única.
- **Dia sem movimento responde mesmo assim** ("nenhum movimento hoje"). No modelo push a decisão era não enviar; no pull o dono perguntou, e silêncio pareceria defeito.

Métricas: leads novos, agendamentos (com % de conversão), cancelamentos, leads aguardando atendimento humano, e agendamentos confirmados nos próximos 7 dias.

---

## Item 4: Cross-sell pós-procedimento — IMPLEMENTADO (2026-07-22)

> ⚠️ Requer `database/migration_v9.sql` antes do deploy (coluna `tenants.cross_sell` + ampliar o CHECK de `followup_jobs.job_type`).

**Descoberta antes de codar:** o `recall_procedimento` já existia e cobria metade disso. Recall = repetir o MESMO procedimento pra manter resultado (meses). Cross-sell = oferecer um procedimento DIFERENTE que combina (semanas). Motivações e prazos distintos, por isso config separada.

Configuração (`tenants.cross_sell`, JSONB):
```json
{"botox": {"oferecer": "preenchimento labial", "dias": 30}}
```
Coluna separada de `procedimentos_recall` de propósito: o mesmo botox pode gerar recall em 180 dias E cross-sell em 30. Num mapa só, uma regra sobrescreveria a outra.

**Sem preço na mensagem** (decisão do dono do produto): a oferta desperta interesse e o valor entra depois, quando o lead perguntar — mesma regra do prompt ("PREÇO SÓ QUANDO PERGUNTADO"). Com preço, o follow-up viraria anúncio.

### Dois bugs existentes corrigidos junto

1. **Cancelar não matava o recall.** O lead desmarcava o botox e, 6 meses depois, recebia "faz um tempinho desde o seu botox" — sobre um botox que nunca aconteceu. `_cancel_appointment` agora cancela os três tipos derivados do agendamento (`JOBS_DERIVADOS_DO_AGENDAMENTO`).
2. **Remarcar duplicava o recall.** `_agendar_recall_se_configurado` fazia INSERT incondicional a cada `book_appointment`; remarcar ou reconfirmar empilhava jobs pendentes e o lead receberia a mesma mensagem 2-3 vezes. Agora recall e cross-sell são cancelados antes de recriar.

Assimetria deliberada em `_book_appointment`: recall e cross-sell são **sempre** cancelados antes de recriar (código garante a recriação), mas o `appointment_reminder` só é cancelado quando a data muda de fato — quem o cria é o modelo via `schedule_followup`, e cancelar sempre deixaria o lead sem lembrete nas vezes em que o modelo esquecesse de recriar.

### Validado ponta a ponta (tenant `bia`, banco real)

| Cenário | Resultado |
|---|---|
| Agendar limpeza 27/07 | `cross_sell` criado pra 17/08 (+21 dias), payload correto |
| Remarcar pra 28/07 | 1 linha de appointment; cross-sell antigo `cancelled`, novo pra 18/08 — **1 pendente, não 2** |
| Cancelar | appointment com `cancelled_at`; **0 jobs pendentes** |

Observação sobre o schema: `leads` tem duas colunas de funil — `stage` (usada de verdade, escrita por `_update_lead_status`) e `status` (criada na migration_v2 e aparentemente morta, nada escreve nela). O relatório não depende de nenhuma das duas hoje, mas vale limpar isso antes de construir métrica por estágio.

**Por que Groq:** ~$0.04/hora de áudio (9x mais barato que OpenAI Whisper), ~200ms de latência e até 228x tempo real — para uma nota de voz de 15-30s isso fica bem dentro do orçamento de resposta em <3s que já é KPI do produto (ver CONTEXT.md). Não precisa de streaming (a nota de voz chega como arquivo fechado, não é fala ao vivo), então a vantagem de streaming da Deepgram não se aplica aqui.

**Por que Groq:** ~$0.04/hora de áudio (9x mais barato que OpenAI Whisper), ~200ms de latência e até 228x tempo real — para uma nota de voz de 15-30s isso fica bem dentro do orçamento de resposta em <3s que já é KPI do produto (ver CONTEXT.md). Não precisa de streaming (a nota de voz chega como arquivo fechado, não é fala ao vivo), então a vantagem de streaming da Deepgram não se aplica aqui.
