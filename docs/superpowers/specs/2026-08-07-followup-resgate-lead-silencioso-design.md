# Follow-up de resgate — lead silencioso no meio da conversa

**Data:** 2026-08-07
**Backend:** este projeto (`main.py`, FastAPI), já em produção no Railway

## Contexto

Hoje o `schedule_followup` só dispara follow-up amarrado a um evento de negócio específico:

| `job_type` | Quando dispara |
|---|---|
| `appointment_reminder` | Automático ao agendar (D-1 do horário) |
| `payment_recovery` | Depois que o link de pagamento foi enviado e não pagou |
| `pos_venda` | Depois que a venda fechou |
| `recall_procedimento` | Tempo depois do procedimento (retorno) |
| `cross_sell` | Depois de um procedimento, oferecendo outro |

Nenhum desses cobre o caso de um lead que **simplesmente para de responder no meio da conversa**, antes de qualquer um desses eventos — ex: perguntou preço e sumiu, ou parou no meio da qualificação. Esse lead fica parado, sem ninguém puxar de volta.

Esta spec cobre um novo `job_type`, `resgate_silencio`, que detecta esse silêncio e reengaja o lead com uma mensagem personalizada, gerada pelo Claude, retomando o assunto de onde parou.

## Decisões

1. **Threshold de silêncio: 3 horas.** Depois de 3h sem mensagem do lead, considera abandono e dispara a primeira tentativa de resgate.
2. **3 tentativas, escalonadas: 3h → D+1 → D+3.** Cada tentativa só é agendada depois que a anterior foi enviada com sucesso (e o lead continuou em silêncio).
3. **Depois da 3ª tentativa sem resposta, o lead vira `status='frio'`** automaticamente — para de tentar de vez.
4. **Se aplica a qualquer lead cujo `stage` não seja `agendado`, `fechado` ou `frio`** (e que não esteja `escalado`). `agendado` já tem `appointment_reminder` cobrindo; `fechado`/`frio` são estados finais, não fazem sentido pra esse fluxo.
   **Correção pós-revisão final (2026-08-07):** a versão original desta decisão dizia "só se aplica a leads `novo` ou `qualificado`" (allowlist), e a implementação inicial replicou isso literalmente. Mas `database/schema.sql` define `leads.stage TEXT NOT NULL DEFAULT 'qualificacao'` — note: `'qualificacao'`, string diferente de `'qualificado'` — e `mem.get_or_create_lead()` insere o lead novo só com esse default; o vocabulário do funil (`novo`/`qualificado`/`agendado`/...) só passa a valer depois que o modelo chama a tool `update_lead_status`. Resultado: a allowlist excluía silenciosamente todo lead que sumisse **antes** de qualquer tool call — exatamente o cenário-título desta spec ("perguntou preço e sumiu"). A lógica correta é uma lista de exclusão: dispara pra qualquer `stage` que não seja `agendado`/`fechado`/`frio`, cobrindo o default `'qualificacao'` de graça.
5. **Não duplica com outro follow-up já pendente.** Se o lead já tem um `payment_recovery` (ou qualquer outro `job_type`) pendente, o `resgate_silencio` não é agendado — evita duas mensagens de reengajamento diferentes chegando juntas.
6. **Mensagem gerada pelo Claude, não template fixo.** Puxa o histórico da conversa e escreve algo personalizado (ex: "vi que você tava interessada no Botox — ainda quer que eu veja um horário?"), mais alinhado com a narrativa de "vendedor de alta performance" do que um texto genérico repetido. Tom escala por tentativa: 1ª leve ("ainda por aí?"), 2ª reforça valor/oferece horário, 3ª última tentativa, direta mas sem pressão.
7. **Agendamento reativo, não por varredura periódica.** O "relógio" de silêncio é resetado a cada mensagem recebida do lead, dentro do próprio fluxo que já processa a mensagem — sem job de varredura novo rodando em paralelo.
8. **Respeita `escalado=true`.** Lead escalado pra humano não recebe mensagem automática de resgate, mesmo que o status ainda seja `novo`/`qualificado` — checado tanto no agendamento quanto na execução.

## Arquitetura

Nenhuma tabela nova. Reusa `followup_jobs` (já existe), com `job_type='resgate_silencio'`. Número da tentativa e o "retrato" do último horário de mensagem do lead ficam no `payload` (JSONB), sem precisar de migration:

```json
{"tentativa": 1, "last_msg_at_snapshot": "2026-08-07T14:00:00Z"}
```

```
Mensagem do lead chega
      │
      ▼
processar_mensagem() (app/agent/claude_client.py)
      │  ...processa normalmente, roda tools, gera resposta...
      │
      ▼ (fim do turno)
1. Cancela followup_jobs pendentes job_type='resgate_silencio' desse lead
2. Se status final ∉ {agendado, fechado, frio} E escalado=false E
   sem outro followup_job pendente de outro tipo:
      insere resgate_silencio tentativa=1, scheduled_at = agora + 3h
      │
      ▼ (até 60s depois do scheduled_at, no loop já existente)
executar_jobs_pendentes() → _executar_job() (app/services/followup_service.py)
      │
      ▼
1. Confere se o lead mandou mensagem mais recente que last_msg_at_snapshot,
   e se escalado=false → se não passar, cancela sem enviar
2. Gera mensagem via Claude (histórico + tentativa) — fallback pra texto
   genérico se a chamada falhar
3. Envia via send_message() (dispatcher já existente)
4. Marca job 'done'
5. Encadeia: tentativa 1→2 em +1 dia, 2→3 em +3 dias,
   3→ (nada) marca leads.status='frio'
```

## Componentes afetados

| Arquivo | Mudança |
|---|---|
| `app/agent/claude_client.py` | No fim de `processar_mensagem()`: cancela `resgate_silencio` pendente do lead, e agenda tentativa 1 se as condições da decisão 4/5/8 forem satisfeitas. |
| `app/agent/tools.py` | `TOOL_DEFINITIONS` de `schedule_followup` não muda — `resgate_silencio` nunca é chamado pelo Claude como tool, é 100% automático fora do loop de tool_use. |
| `app/services/followup_service.py` | Novo branch em `_executar_job()` (ou função dedicada) pra `job_type='resgate_silencio'`: checagem de segurança, geração de mensagem via Claude, envio, encadeamento da próxima tentativa ou marcação `frio`. Nova função `_gerar_mensagem_resgate(tenant, historico, tentativa)` que chama a API do Claude. |

## Tratamento de erro / edge cases

- **Chamada ao Claude falha** (erro de API): usa mensagem genérica fixa em vez de deixar o job cair em `failed` — mesmo padrão de segurança já usado hoje nos templates de `recall_procedimento`/`cross_sell` quando falta variável no payload.
- **Lead escalado pra humano** (`escalado=true`): não agenda, e se já tinha sido agendado antes de escalar, cancela na hora da execução — não faz sentido mandar mensagem automática enquanto humano está cuidando.
- **Comando SAIR:** já vira `status='frio'` antes da etapa de agendamento rodar (fim do mesmo turno) — nenhum job novo é criado, e qualquer pendente é cancelado pelo fluxo normal, sem tratamento especial.
- **Mudança de status fora do webhook** (ex: admin usa `/desescalar` ou outro endpoint que não passa por `processar_mensagem`): job pendente pode ficar "órfão" de um status que já mudou — coberto porque a checagem de status/escalado roda de novo no momento da execução, não só no agendamento.
- **Canal (WhatsApp/Instagram):** reusa `channel`/`phone`/`ig_user_id` já gravados no job, mesmo padrão dos outros `job_type` existentes.

## Como testar

Sem suite automatizada no projeto — mesmo padrão manual usado nas outras tasks do plano (`curl` + inspeção direta no Supabase):

1. Criar/usar um lead de teste com `status='novo'`, mandar uma mensagem pelo `/chat` ou WhatsApp real.
2. Confirmar no Supabase que um `followup_jobs` com `job_type='resgate_silencio'`, `payload.tentativa=1`, `scheduled_at ≈ agora + 3h` foi criado.
3. Editar manualmente `scheduled_at` pra um horário já passado (evita esperar 3h de verdade), esperar até 60s (próximo ciclo do scheduler), confirmar que a mensagem chegou e que a tentativa 2 foi agendada pra `+1 dia`.
4. Mandar uma nova mensagem do lead **antes** do job disparar → confirmar que o job pendente vira `cancelado` e nenhuma mensagem de resgate chega.
5. Repetir até a tentativa 3 e confirmar que o lead vira `status='frio'` e nenhum 4º job é criado.
6. Confirmar que um lead com `payment_recovery` pendente não recebe `resgate_silencio` também.
7. Confirmar que um lead com `escalado=true` não recebe mensagem de resgate.

## Fora de escopo (explícito)

- Ajuste automático do threshold de 3h por tipo de serviço/ticket — fixo por enquanto, pode virar configurável por tenant no futuro.
- Varredura periódica / job de scan — decisão consciente de usar agendamento reativo (ver decisão 7).
- Métricas/dashboard sobre taxa de recuperação por resgate de silêncio — pode ser um KPI futuro no dashboard (`aesthetic-dashboard-ai`), fora de escopo aqui.
