# TROUBLESHOOTING — Meta WhatsApp Cloud API em produção

> Escrito em 2026-07-24, logo depois de colocar o primeiro número de produção no ar
> (2026-07-23). Registra os problemas reais que apareceram — muitos pareciam bugs
> novos e diferentes, mas boa parte era a MESMA causa raiz se manifestando de formas
> diferentes ao longo do dia. Se acontecer de novo, procure pelo sintoma na tabela
> abaixo antes de investigar do zero.

## Referência rápida — sintoma → causa → correção

| Sintoma | Causa real | Correção |
|---|---|---|
| Meta devolve 403 ao tentar verificar o webhook | `main.py` lia `VERIFY_TOKEN`, mas o `.env` define `META_VERIFY_TOKEN` — token ficava vazio | Ler de `settings.meta_verify_token` (pydantic-settings), não de `os.getenv` direto. Ver `_diagnostico_whatsapp()` em `main.py` — loga na subida o que falta |
| Railway sugere variáveis com nome/valor errado ao configurar o deploy | `.env.example` tinha nomes antigos (`WHATSAPP_TOKEN`, `PHONE_NUMBER_ID`, `VERIFY_TOKEN`) e placeholders (`sk-ant-api03-xxxxx`) — Railway auto-sugere a partir desse arquivo | Nunca usar "Suggested Variables" do Railway. Usar o Raw Editor e colar o `.env` real. `.env.example` corrigido pra usar os nomes reais que o código lê |
| Deploy no Railway trava/reinicia sem subir | Variável `PORT` deixada **vazia** (`PORT=`) em vez de removida — o start command usa `--port $PORT` | Nunca definir `PORT` nas variáveis do Railway — ele injeta sozinho |
| `502 Application failed to respond` com domínio público gerado | Domínio apontando pra porta diferente da que o Uvicorn realmente escuta (confirmar no deploy log: `Uvicorn running on http://0.0.0.0:XXXX`) | Settings → Networking → editar a porta do domínio pra bater com a do log (no nosso caso, 8080) |
| Mensagem do lead nunca chega no webhook, mesmo com tudo "verificado" | **Assinatura WABA→App ausente** ("shadow delivery") — na UI nova da Meta, registrar um número não cria mais essa assinatura sozinho | `POST https://graph.facebook.com/v19.0/{WABA_ID}/subscribed_apps` com o token. Confirmar com `GET` no mesmo endpoint |
| **Isso se repete pra CADA WABA nova** — registrar um número de produção pode criar uma WABA diferente da do número de teste, mesmo no mesmo app | Cada WABA tem sua própria assinatura. Corrigir uma não cobre a outra | Sempre checar `GET /{WABA_ID}/subscribed_apps` depois de registrar um número novo, mesmo que já tenha funcionado antes com outro número |
| Envio (`POST .../messages`) começa a falhar do nada, com erro **diferente a cada vez** (200 → 131030 → 131005 → 401) | Token de teste da Meta **expira a cada ~24h**. Cada erro era o token morrendo em um estágio diferente da sessão, não bugs distintos | Checar primeiro: `GET /debug_token?input_token=...`. Se `is_valid: false`, é isso — gerar token novo. Resolvido de vez com token de **System User** (não expira em 24h) |
| Erro `(#131030) Recipient phone number not in allowed list` | Celular brasileiro: a Meta reporta o remetente **sem** o 9º dígito (12 dígitos), mas o envio só é aceito **com** o 9º dígito (13 dígitos) — inconsistência conhecida do WhatsApp no Brasil | `alternar_nono_digito_br()` em `app/agent/dispatcher.py` — tenta o formato de 13 dígitos primeiro (orientação oficial da própria IA da Meta), cai pro de 12 se recusado |
| Erro `(#131005) Access denied`, token com escopo certo e número `CONNECTED` | Quase sempre é o token expirado de novo (ver linha do 24h acima) — não é erro de permissão de verdade | Mesma correção: `debug_token` primeiro, token de System User resolve |
| `POST /messages` devolve HTTP 200 "accepted" mas a mensagem nunca chega no celular | Combinação dos dois itens acima (token prestes a expirar + formato de número) — a Meta aceita a requisição mas falha silenciosamente na entrega | Não confiar só no HTTP 200. Testar o roundtrip completo (mandar do celular de verdade), não só simular envio via API |
| Token de System User criado mas envio continua falhando | Faltou **atribuir a WABA como ativo** ao System User (Configurações do Negócio → Usuários do sistema → Adicionar ativos → Contas do WhatsApp) — ter só o App atribuído não basta | Atribuir App **e** WABA com controle total, DEPOIS gerar o token (token gerado antes da atribuição não pega o acesso) |
| Lead escalado pra humano fica mudo, sem nenhuma resposta e **sem nenhum erro registrado** | Não é bug — é a proteção `escalar_por_falhas()` funcionando: 3 falhas de envio em 30min escalam o lead automaticamente e a IA para de responder de propósito | Checar `leads.escalado` antes de qualquer outra investigação quando "não responde nada, nem erro". Desescalar com `UPDATE leads SET escalado=false WHERE ...` |
| Duas conversas/leads diferentes pro mesmo número físico | Testes manuais usando formato de telefone diferente do que a Meta realmente reporta (12 dígitos vs 13) criam **duas linhas de lead separadas** pra mesma pessoa | Ao testar manualmente, usar sempre o número exatamente como a Meta reporta no webhook real, não um formato "corrigido" à mão |
| Mensagem de teste enviada via QR code chega em inglês genérico ("Hello! I am interested...") | Não é bug — é o texto padrão que o próprio WhatsApp preenche no "scan to chat" | Ignorar, é só texto de exemplo da Meta |

## Ordem de diagnóstico recomendada (da próxima vez)

Quando "não está funcionando" no WhatsApp, checar nesta ordem — cobre ~90% dos casos deste dia:

1. `GET /debug_token?input_token=X&access_token=X` — o token está `is_valid: true`?
2. `GET /{WABA_ID}/subscribed_apps` — o app aparece na lista?
3. `leads.escalado` do número em teste — está `true`?
4. Formato do número: tentando 13 dígitos (com o 9)?
5. Só depois disso, suspeitar de bug de código novo.

## Contexto de configuração desta conta (referência)

- App: **Automação Atendimento** (App ID `1048706104535294`)
- WABA do número de teste: `37628238166767277`
- WABA do número de produção real (`85987588339`): `1034874515683589` — **WABA diferente**, mesmo app
- Token de envio: System User, permissões `whatsapp_business_messaging` + `whatsapp_business_management`, expiração ~60 dias (⚠️ regenerar marcando "Nunca" antes de virar produto pra cliente pagante)

## O que ficou pendente (não é troubleshooting, é próximo passo)

- `WHATSAPP_APP_SECRET` continua vazio — assinatura do webhook não é validada. Tolerável em teste, resolver antes de escalar pra mais clientes
- Verificação de empresa na Meta — não feita, limite atual de 250 conversas/dia é suficiente pro cliente 1, mas trava crescimento
- Uptime monitoring — combinado, ainda não configurado
- Endpoint de resposta humana — desenhado, não construído

## Fontes usadas na investigação

- IA de suporte da própria Meta (via developers.facebook.com), consultada duas vezes durante o troubleshooting — confirmou o comportamento do 9º dígito e a necessidade de `subscribed_apps`
- Pesquisa externa sobre "shadow delivery" WhatsApp Cloud API — problema documentado da mudança de UI da Meta em 2025/2026 onde registrar número não cria mais a assinatura WABA→App automaticamente
