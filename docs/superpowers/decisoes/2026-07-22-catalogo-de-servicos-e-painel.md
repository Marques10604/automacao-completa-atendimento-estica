# Decisão: catálogo de serviços estruturado antes do painel de configuração

**Data:** 2026-07-22
**Status:** aceita — fundação (banco + API) primeiro, interface depois
**Decidido por:** Ivonilson Marques (dono do produto)

---

## Contexto

Hoje, cadastrar um cliente novo exige edição manual de JSONB no Supabase, feita pelo desenvolvedor:

| Campo | Formato atual |
|---|---|
| `tenants.servicos` | texto livre / JSONB sem estrutura definida — vai direto pro system prompt |
| `tenants.horarios` | JSONB `{"seg": ["09:00","19:00"], ...}` |
| `tenants.procedimentos_recall` | JSONB `{"botox": 180}` |
| `tenants.cross_sell` | JSONB `{"botox": {"oferecer": "...", "dias": 30}}` |
| `tenants.staff_phone`, credenciais Meta/Asaas | texto |

O dono da clínica não tem nenhuma autonomia: qualquer mudança de preço, horário ou serviço passa pelo desenvolvedor.

## Problema

**Isso é um teto de crescimento, não um incômodo.** Com 2 ou 3 clientes funciona. Com 15, cada venda nova gera trabalho manual do desenvolvedor, que vira o gargalo do próprio negócio. Um painel de autoatendimento é o que separa "atendo alguns clientes" de "tenho um produto".

Há também um problema técnico já presente, hoje invisível:

**O casamento de procedimento é por substring.** `_casar_regra_procedimento()` (`app/agent/tools.py`) compara o que o modelo gravou em `appointments.service` (ex.: "Limpeza de pele profunda") com a chave configurada pela clínica (ex.: "limpeza de pele"), testando se um contém o outro. Se o modelo escrever uma variação que não casa ("limpeza facial profunda"), a regra de recall/cross-sell **não dispara e ninguém percebe** — falha silenciosa. Um catálogo com id resolve isso: a regra aponta pro id do serviço, casamento exato.

## Opções consideradas

**(a) Terminar o roadmap (itens 5-8) e só depois atacar o painel.**
Entrega mais valor por feature no curto prazo, mas adia o gargalo de escala e acumula mais configuração manual a cada item novo.

**(b) Pausar o roadmap e construir o painel completo agora.**
Resolve o gargalo, mas é um projeto do tamanho dos itens 1-4 somados: frontend, autenticação multi-tenant, API de configuração, validação. Risco alto de ficar semanas sem entregar nada utilizável.

**(c) Estruturar o catálogo de serviços primeiro — banco + API, sem interface. ✅ ESCOLHIDA**
A parte difícil e demorada de um painel não é a tela: é decidir a estrutura de dados por baixo. Fazendo a fundação certa primeiro, a tela depois é rápida. Fazendo a tela primeiro, refaz-se as duas. Além disso, o catálogo estruturado já conserta o casamento frágil de procedimento independentemente do painel existir.

## Decisão

Adotada a opção **(c)**.

Escopo desta fase:
1. Modelagem do catálogo de serviços (tabela própria, não JSONB solto)
2. Migração dos dados atuais de `tenants.servicos` para a estrutura nova
3. Regras de `cross_sell` e `procedimentos_recall` passam a referenciar id de serviço em vez de string
4. Endpoints de leitura/escrita do catálogo, protegidos por tenant
5. Informações do negócio (FAQ) modeladas junto, já que alimentam o mesmo prompt

Fora de escopo desta fase: interface visual, autenticação de dono de clínica, self-service de credenciais Meta/Asaas.

## Decisão relacionada: FAQ sem RAG

O FAQ (endereço, estacionamento, formas de pagamento, política de atraso, contraindicações) vai como **texto estruturado no system prompt**, não como banco vetorial.

Motivo: uma clínica tem ~20 serviços e um punhado de políticas — cabe folgado no contexto. RAG só compensa quando a base não cabe, e adotá-lo agora custaria complexidade de infra e latência por turno sem nenhum ganho de qualidade. Se um dia um cliente tiver base grande demais, migra-se; não se começa por aí.

## Consequências

**Positivas**
- Destrava o crescimento além do punhado de clientes atuais
- Corrige a falha silenciosa do casamento por substring
- A tela do painel, quando vier, é trabalho rápido sobre fundação pronta
- FAQ deixa de exigir edição de prompt por cliente

**Negativas / custos**
- Fase sem entrega visível pro usuário final (é infraestrutura)
- Exige migração dos tenants já cadastrados (`minha-clinica`, `bia`, `lumina`)
- `tenants.servicos` fica temporariamente duplicado com a tabela nova até a migração completar

## Perguntas em aberto (a resolver no desenho)

- Serviço tem duração própria? Hoje `SLOT_DURATION_MINUTES = 60` é fixo pra todos — uma limpeza de 30min e um botox de 90min ocupam o mesmo slot.
- Preço é valor único ou faixa ("a partir de R$ 600")? O catálogo atual usa as duas formas.
- Serviço inativo: apaga ou desativa? (agendamentos antigos referenciam ele)
- O FAQ é campo livre por tenant ou tem categorias fixas?

## Referências

- Roadmap dos diferenciais: `docs/superpowers/plans/2026-07-22-diferenciais-upsell.md`
- Casamento por substring: `app/agent/tools.py::_casar_regra_procedimento`
- Montagem do prompt a partir do tenant: `app/agent/prompts.py::build_prompt`
