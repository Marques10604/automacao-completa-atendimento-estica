# CLAUDE.md — Automação Completa Atendimento Estica

## Regra de início de sessão obrigatória

Toda vez que uma sessão iniciar neste projeto, ANTES de qualquer ação:
1. Leia este CONTEXT.md inteiro
2. Leia docs/superpowers/plans/2026-04-19-automacao-completa.md
3. Informe quais tasks estão concluídas [x] e qual é a próxima pendente [ ]
4. Aguarde confirmação antes de executar qualquer coisa

## Regras de economia de tokens (obrigatório)

- Antes de ler qualquer arquivo, verifique se a informação já está no CONTEXT.md
- Nunca leia arquivos de dependências, logs ou compilados
- Use /compact quando o contexto estiver grande mas ainda for necessário
- Use /clear ao trocar de task ou assunto
- Leia apenas os arquivos diretamente necessários para a task atual
- Nunca faça varreduras globais com grep/glob sem necessidade clara
- Prefira ler funções específicas ao invés de arquivos inteiros
