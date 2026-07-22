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

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
